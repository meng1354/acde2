# -*- coding: utf-8 -*-
"""
搜书吧论坛自动登录 + 发布空间动态（最终修复版）

"""
import os
import re
import sys
import time
import logging
from copy import copy
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
import urllib3

# 禁用 SSL 警告（测试环境）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
ch = logging.StreamHandler(stream=sys.stdout)
ch.setLevel(logging.INFO)
ch.setFormatter(formatter)
logger.addHandler(ch)


def get_refresh_url(url: str) -> str | None:
    """从页面中提取 meta refresh 跳转地址"""
    try:
        response = requests.get(url, timeout=10, verify=False)
        if response.status_code != 200:
            logger.warning(f"Fetch {url} status: {response.status_code}, still trying to parse")
        soup = BeautifulSoup(response.text, 'html.parser')
        meta_tags = soup.find_all('meta', attrs={'http-equiv': 'refresh'})
        for meta in meta_tags:
            content = meta.get('content', '')
            if 'url=' in content:
                redirect_url = content.split('url=')[1].strip()
                logger.info(f"Found redirect: {redirect_url}")
                return redirect_url
        logger.warning(f"No refresh meta in {url}")
        return None
    except Exception as e:
        logger.exception(f"Error in get_refresh_url: {e}")
        return None


def get_url(url: str) -> str | None:
    """从页面中查找文本为“搜书吧”的链接"""
    try:
        resp = requests.get(url, timeout=10, verify=False)
        resp.encoding = resp.apparent_encoding or 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')
        for link in soup.find_all('a', href=True):
            if link.get_text(strip=True) == "搜书吧":
                return link['href']
        logger.warning(f"No '搜书吧' link in {url}")
        return None
    except Exception as e:
        logger.exception(f"Error in get_url: {e}")
        return None


class SouShuBaClient:
    def __init__(self, hostname: str, username: str, password: str,
                 questionid: str = '0', answer: str = None,
                 proxies: dict | None = None):
        self.session = requests.Session()
        self.hostname = hostname
        self.username = username
        self.password = password
        self.questionid = questionid
        self.answer = answer
        self.proxies = proxies

        self._common_headers = {
            "Host": hostname,
            "Connection": "keep-alive",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,cn;q=0.9",
            "Content-Type": "application/x-www-form-urlencoded",
        }

    def _get_formhash_from_page(self, url: str) -> str | None:
        """从指定页面提取 formhash（使用 BeautifulSoup）"""
        try:
            resp = self.session.get(url, verify=False, proxies=self.proxies, timeout=10)
            resp.encoding = resp.apparent_encoding or 'utf-8'
            # 调试时可打印前 500 字符
            # logger.debug(resp.text[:500])
            soup = BeautifulSoup(resp.text, 'html.parser')
            input_tag = soup.find('input', {'name': 'formhash'})
            if input_tag and input_tag.get('value'):
                return input_tag['value']
            logger.warning(f"formhash not found in {url}")
            return None
        except Exception as e:
            logger.exception(f"Error extracting formhash from {url}: {e}")
            return None

    def _get_loginhash_from_login_page(self) -> tuple[str | None, str | None]:
        """从登录页提取 loginhash 和 formhash"""
        login_page_url = f'https://{self.hostname}/member.php?mod=logging&action=login'
        try:
            resp = self.session.get(login_page_url, verify=False, proxies=self.proxies, timeout=10)
            resp.encoding = resp.apparent_encoding or 'utf-8'
            soup = BeautifulSoup(resp.text, 'html.parser')

            loginhash = None
            div = soup.find('div', id=re.compile(r'^main_messaqge_'))
            if div:
                match = re.search(r'main_messaqge_(.+)', div.get('id', ''))
                if match:
                    loginhash = match.group(1)

            formhash = None
            input_tag = soup.find('input', {'name': 'formhash'})
            if input_tag and input_tag.get('value'):
                formhash = input_tag['value']

            if loginhash is None or formhash is None:
                logger.error("Failed to extract loginhash or formhash from login page")
                return None, None
            return loginhash, formhash
        except Exception as e:
            logger.exception(f"Error getting login hashes: {e}")
            return None, None

    def login(self) -> bool:
        """执行登录，返回是否成功"""
        loginhash, formhash = self._get_loginhash_from_login_page()
        if not loginhash or not formhash:
            return False

        login_url = (f'https://{self.hostname}/member.php?mod=logging&action=login'
                     f'&loginsubmit=yes&handlekey=register&loginhash={loginhash}&inajax=1')

        headers = copy(self._common_headers)
        headers["Origin"] = f'https://{self.hostname}'
        headers["Referer"] = f'https://{self.hostname}/'

        payload = {
            'formhash': formhash,
            'referer': f'https://{self.hostname}/',
            'username': self.username,
            'password': self.password,
            'questionid': self.questionid,
            'answer': self.answer if self.answer else ''
        }

        try:
            resp = self.session.post(login_url, proxies=self.proxies, data=payload,
                                     headers=headers, verify=False, timeout=10)
            resp.encoding = resp.apparent_encoding or 'utf-8'
            # 判断登录成功：包含“欢迎”或“登录成功”或跳转
            if "欢迎" in resp.text or "登录成功" in resp.text or "window.location" in resp.text:
                logger.info(f"Login successful for {self.username}")
                return True
            else:
                logger.error(f"Login failed: {resp.text[:200]}")
                return False
        except Exception as e:
            logger.exception(f"Exception during login: {e}")
            return False

    def credit(self) -> str | None:
        """获取银币数量，使用 XML 解析器提取"""
        credit_url = (f"https://{self.hostname}/home.php?mod=spacecp&ac=credit"
                      f"&showcredit=1&inajax=1&ajaxtarget=extcreditmenu_menu")
        try:
            resp = self.session.get(credit_url, verify=False, proxies=self.proxies, timeout=10)
            resp.encoding = resp.apparent_encoding or 'utf-8'
            # ★★★ 关键修复：使用 XML 解析器（需要安装 lxml）★★★
            soup = BeautifulSoup(resp.text, 'xml')   # features='xml' 亦可
            span = soup.find('span', id='hcredit_2')
            if span:
                return span.get_text(strip=True)
            else:
                logger.warning("Credit span not found in XML response")
                return None
        except Exception as e:
            logger.exception(f"Error fetching credit: {e}")
            return None

    def space_form_hash(self) -> str | None:
        """获取发布动态所需的 formhash（必须访问 home.php?mod=spacecp）"""
        # ★★★ 修复：使用带参数的 URL ★★★
        url = f'https://{self.hostname}/home.php?mod=spacecp'
        return self._get_formhash_from_page(url)

    def space(self) -> bool:
        """发布 5 条空间动态，返回是否至少有一条成功"""
        formhash = self.space_form_hash()
        if formhash is None:
            logger.error("Cannot get formhash for space")
            return False

        space_url = f"https://{self.hostname}/home.php?mod=spacecp&ac=doing&handlekey=doing&inajax=1"
        headers = copy(self._common_headers)
        headers["Origin"] = f'https://{self.hostname}'
        headers["Referer"] = f'https://{self.hostname}/home.php'

        success_count = 0
        for i in range(5):
            payload = {
                "message": f"开心赚银币 {i+1} 次",
                "addsubmit": "true",
                "spacenote": "true",
                "referer": "home.php",
                "formhash": formhash
            }
            try:
                resp = self.session.post(space_url, proxies=self.proxies, data=payload,
                                         headers=headers, verify=False, timeout=10)
                resp.encoding = resp.apparent_encoding or 'utf-8'
                if "操作成功" in resp.text:
                    logger.info(f"{self.username} post {i+1} successfully!")
                    success_count += 1
                else:
                    logger.warning(f"{self.username} post {i+1} failed: {resp.text[:100]}")
            except Exception as e:
                logger.exception(f"Exception during posting {i+1}: {e}")

            if i < 4:
                time.sleep(120)

        return success_count > 0


def main():
    # 1. 解析重定向获取最终 hostname
    initial_host = os.environ.get('SOUSHUBA_HOSTNAME', 'www.soushu2035.com')
    redirect1 = get_refresh_url(f'http://{initial_host}')
    if redirect1 is None:
        logger.error("Failed to get first redirect URL")
        sys.exit(1)
    time.sleep(2)

    redirect2 = get_refresh_url(redirect1)
    if redirect2 is None:
        logger.error("Failed to get second redirect URL")
        sys.exit(1)

    final_page_url = get_url(redirect2)
    if final_page_url is None:
        logger.error("Failed to find '搜书吧' link")
        sys.exit(1)

    parsed = urlparse(final_page_url)
    hostname = parsed.hostname
    if not hostname:
        logger.error(f"Invalid final URL: {final_page_url}")
        sys.exit(1)

    logger.info(f"Final hostname: {hostname}")

    # 2. 读取凭据
    username = os.environ.get('SOUSHUBA_USERNAME')
    password = os.environ.get('SOUSHUBA_PASSWORD')
    if not username or not password:
        logger.error("Environment variables SOUSHUBA_USERNAME and SOUSHUBA_PASSWORD must be set")
        sys.exit(1)

    client = SouShuBaClient(hostname, username, password)

    # 3. 登录
    if not client.login():
        logger.error("Login failed, exiting")
        sys.exit(1)

    # 4. 发布动态
    post_ok = client.space()
    if not post_ok:
        logger.warning("All space posts may have failed")

    # 5. 获取银币
    credit = client.credit()
    if credit is not None:
        logger.info(f"{client.username} have {credit} coins!")
    else:
        logger.warning("Could not retrieve credit info")

    logger.info("Script finished.")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        sys.exit(1)
