# -*- coding: utf-8 -*-
"""
实现搜书吧论坛登入和发布空间动态（修正版）
"""
import os
import rea
import sys
from copy import copy
from typing import Optional
from urllib.parse import urlparse, urljoin, urlencode

import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET
import time
import logging
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

ch = logging.StreamHandler(stream=sys.stdout)
ch.setLevel(logging.INFO)
ch.setFormatter(formatter)
logger.addHandler(ch)


def get_refresh_url(url: str) -> Optional[str]:
    """
    解析可能包含 meta refresh 的页面并返回重定向后的完整 URL（如果存在）。
    更稳健地处理响应码、大小写和相对 URL。
    """
    try:
        response = requests.get(url, verify=False)
        # 403 可能是被站点防护拦截但页面仍包含 meta refresh，故只对非 403 的错误码抛出
        if response.status_code >= 400 and response.status_code != 403:
            response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')
        # 寻找 http-equiv=refresh，忽略大小写
        meta_tag = soup.find('meta', attrs={'http-equiv': lambda v: v and v.lower() == 'refresh'})
        if not meta_tag:
            logger.debug("No meta refresh tag found at %s", url)
            return None

        content = meta_tag.get('content', '')
        m = re.search(r'url\s*=\s*(.+)', content, re.I)
        if not m:
            logger.debug("Meta refresh content does not contain url= at %s: %r", url, content)
            return None

        redirect = m.group(1).strip().strip('\'"')
        # 处理相对 URL
        redirect_full = urljoin(url, redirect)
        logger.info(f"Redirecting to: {redirect_full}")
        return redirect_full
    except Exception:
        logger.exception("An unexpected error occurred while getting refresh URL from %s", url)
        return None


def get_url(url: str) -> Optional[str]:
    """
    在页面中寻找文本包含 '搜书吧' 的链接，返回绝对 URL（若存在）。
    """
    try:
        resp = requests.get(url, verify=False)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, 'html.parser')

        links = soup.find_all('a', href=True)
        for link in links:
            txt = (link.text or "").strip()
            if "搜书吧" in txt:
                return urljoin(url, link['href'])
        return None
    except Exception:
        logger.exception("Failed to get URL from %s", url)
        return None


class SouShuBaClient:

    def __init__(self, hostname: str, username: str, password: str, questionid: str = '0', answer: Optional[str] = None,
                 proxies: Optional[dict] = None):
        self.session: requests.Session = requests.Session()
        self.hostname = hostname
        self.username = username
        self.password = password
        self.questionid = questionid
        self.answer = answer
        self._common_headers = {
            "Host": f"{hostname}",
            "Connection": "keep-alive",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,cn;q=0.9",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        self.proxies = proxies

    def login_form_hash(self):
        """
        获取登录页面中的 loginhash 和 formhash，做存在性检查，若找不到则抛出明确错误。
        """
        rst = self.session.get(f'https://{self.hostname}/member.php?mod=logging&action=login', verify=False).text

        # 先尝试匹配常见拼写（original repo 疑似 main_messaqge_*）
        loginhash = None
        match = re.search(r'<div\s+id=["\']main_messaqge_(.+?)["\']', rst)
        if not match:
            match = re.search(r'<div\s+id=["\']main_message_(.+?)["\']', rst)
        if match:
            loginhash = match.group(1)

        if not loginhash:
            raise ValueError("Could not find loginhash in login form")

        # 更稳健地匹配隐藏输入 formhash
        match = re.search(r'<input[^>]*name=["\']formhash["\'][^>]*value=["\'](.+?)["\']', rst)
        formhash = match.group(1) if match else None
        if not formhash:
            raise ValueError("Could not find formhash in login form")

        return loginhash, formhash

    def login(self):
        """Login with username and password"""
        loginhash, formhash = self.login_form_hash()
        login_url = f'https://{self.hostname}/member.php?mod=logging&action=login&loginsubmit=yes' \
                    f'&handlekey=register&loginhash={loginhash}&inajax=1'

        headers = copy(self._common_headers)
        headers["origin"] = f'https://{self.hostname}'
        headers["referer"] = f'https://{self.hostname}/'
        payload = {
            'formhash': formhash,
            'referer': f'https://{self.hostname}/',
            'username': self.username,
            'password': self.password,
            'questionid': self.questionid,
            'answer': self.answer or ''
        }

        resp = self.session.post(login_url, proxies=self.proxies, data=payload, headers=headers, verify=False)
        # 状态码 200 不代表一定成功，尝试做简单的响应体检查
        if resp.status_code == 200:
            body = resp.text or ''
            # 如果明显的失败提示存在则认为失败
            if re.search(r'用户名|密码|错误|登录失败', body):
                raise ValueError('Verify Failed! Check your username and password!')
            logger.info(f'Welcome {self.username}!')
        else:
            raise ValueError('Verify Failed! Check your username and password!')

    def credit(self):
        """
        获取积分信息：尝试解析 XML CDATA，回退到正则/BeautifulSoup。
        """
        credit_url = f"https://{self.hostname}/home.php?mod=spacecp&ac=credit&showcredit=1&inajax=1&ajaxtarget=extcreditmenu_menu"
        credit_rst = self.session.get(credit_url, verify=False).text

        cdata = None
        try:
            root = ET.fromstring(credit_rst)
            cdata = root.text or ''
        except ET.ParseError:
            # 尝试从 CDATA 中提取
            m = re.search(r'<!\[CDATA\[(.*?)\]\]>', credit_rst, re.S)
            if m:
                cdata = m.group(1)
            else:
                # 回退：整页当作 HTML 处理
                cdata = credit_rst

        cdata_soup = BeautifulSoup(cdata, features="lxml")
        span = cdata_soup.find("span", id="hcredit_2")
        if not span:
            raise ValueError("Could not find hcredit_2 in credit response")
        hcredit_2 = span.get_text(strip=True)
        return hcredit_2

    def space_form_hash(self):
        rst = self.session.get(f'https://{self.hostname}/home.php', verify=False).text
        match = re.search(r'<input[^>]*name=["\']formhash["\'][^>]*value=["\'](.+?)["\']', rst)
        formhash = match.group(1) if match else None
        if not formhash:
            raise ValueError("Could not find formhash in space form")
        return formhash

    def space(self):
        """
        发布空间动态：如果目标站点需要 GBK 编码，会以 gbk 编码整个 body 后发送。
        """
        formhash = self.space_form_hash()
        space_url = f"https://{self.hostname}/home.php?mod=spacecp&ac=doing&handlekey=doing&inajax=1"

        headers = copy(self._common_headers)
        headers["origin"] = f'https://{self.hostname}'
        headers["referer"] = f'https://{self.hostname}/home.php'
        # 明确声明 GBK 编码（如果服务器需要）
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=GBK"

        for x in range(5):
            payload_dict = {
                "message": "开心赚银币 {0} 次".format(x + 1),
                "addsubmit": "true",
                "spacenote": "true",
                "referer": "home.php",
                "formhash": formhash
            }
            # 将表单以 GBK 编码发送（requests 对 dict 值的默认编码为 utf-8，
            # 若目标站点需要 GBK 则用下面方式）
            body = urlencode(payload_dict, encoding='gbk')
            resp = self.session.post(space_url, proxies=self.proxies, data=body.encode('gbk'),
                                     headers=headers, verify=False)
            if re.search("操作成功", resp.text):
                logger.info(f'{self.username} post {x + 1}nd successfully!')
                time.sleep(120)
            else:
                logger.warning(f'{self.username} post {x + 1}nd failed!')

if __name__ == '__main__':
    try:
        base = 'http://' + os.environ.get('SOUSHUBA_HOSTNAME', 'www.soushu2035.com')
        redirect_url = get_refresh_url(base)
        if not redirect_url:
            raise RuntimeError("Failed to get redirect URL from base %s" % base)
        time.sleep(2)
        redirect_url2 = get_refresh_url(redirect_url)
        if not redirect_url2:
            raise RuntimeError("Failed to get second redirect URL from %s" % redirect_url)
        url = get_url(redirect_url2)
        if not url:
            raise RuntimeError("Could not find target site link on %s" % redirect_url2)
        logger.info(f'{url}')
        client = SouShuBaClient(urlparse(url).hostname,
                                os.environ.get('SOUSHUBA_USERNAME', "USERNAME"),
                                os.environ.get('SOUSHUBA_PASSWORD', "PASSWORD"))
        client.login()
        client.space()
        credit = client.credit()
        logger.info(f'{client.username} have {credit} coins!')
    except Exception as e:
        logger.error(e)
        sys.exit(1)
