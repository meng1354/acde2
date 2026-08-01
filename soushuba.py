# -*- coding: utf-8 -*-
"""
实现搜书吧论坛登入和发布空间动态（改进版）
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

# 禁用 SSL 警告（仅用于测试环境）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
ch = logging.StreamHandler(stream=sys.stdout)
ch.setLevel(logging.INFO)
ch.setFormatter(formatter)
logger.addHandler(ch)


def get_refresh_url(url: str) -> str | None:
    """
    从给定的 URL 页面中提取 meta refresh 跳转地址。
    若页面返回 403 或未找到 refresh 标签，返回 None。
    """
    try:
        response = requests.get(url, timeout=10, verify=False)
        # 若状态码为 403，可能是触发了反爬，尝试解析 refresh
        if response.status_code == 403:
            # 仍尝试解析页面内容
            pass
        elif response.status_code != 200:
            logger.error(f"Failed to fetch {url}, status: {response.status_code}")
            return None

        soup = BeautifulSoup(response.text, 'html.parser')
        meta_tags = soup.find_all('meta', attrs={'http-equiv': 'refresh'})
        for meta in meta_tags:
            content = meta.get('content', '')
            if 'url=' in content:
                redirect_url = content.split('url=')[1].strip()
                logger.info(f"Found redirect URL: {redirect_url}")
                return redirect_url
        logger.warning(f"No meta refresh tag found in {url}")
        return None
    except Exception as e:
        logger.exception(f"Error fetching refresh URL from {url}: {e}")
        return None


def get_url(url: str) -> str | None:
    """
    从页面中查找文本为“搜书吧”的链接，返回其 href 属性。
    若未找到，返回 None。
    """
    try:
        resp = requests.get(url, timeout=10, verify=False)
        resp.encoding = resp.apparent_encoding or 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')
        links = soup.find_all('a', href=True)
        for link in links:
            if link.get_text(strip=True) == "搜书吧":
                return link['href']
        logger.warning(f"No link with text '搜书吧' found in {url}")
        return None
    except Exception as e:
        logger.exception(f"Error fetching URL {url}: {e}")
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

        # 基础请求头
        self._common_headers = {
            "Host": hostname,
            "Connection": "keep-alive",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,cn;q=0.9",
            "Content-Type": "application/x-www-form-urlencoded",
        }

    def _get_formhash_from_page(self, url: str) -> str | None:
        """
        从指定页面中提取 formhash 值，使用 BeautifulSoup 解析。
        """
        try:
            resp = self.session.get(url, verify=False, proxies=self.proxies, timeout=10)
            resp.encoding = resp.apparent_encoding or 'utf-8'
            soup = BeautifulSoup(resp.text, 'html.parser')
            input_tag = soup.find('input', {'name': 'formhash'})
            if input_tag and input_tag.get('value'):
                return input_tag['value']
            logger.warning(f"formhash not found in {url}")
            return None
        except Exception as e:
            logger.exception(f"Failed to extract formhash from {url}: {e}")
            return None

    def _get_loginhash_from_login_page(self) -> tuple[str | None, str | None]:
        """
        访问登录页面，提取 loginhash 和 formhash。
        返回 (loginhash, formhash) 元组，失败返回 (None, None)。
        """
        login_page_url = f'https://{self.hostname}/member.php?mod=logging&action=login'
        try:
            resp = self.session.get(login_page_url, verify=False, proxies=self.proxies, timeout=10)
            resp.encoding = resp.apparent_encoding or 'utf-8'
            soup = BeautifulSoup(resp.text, 'html.parser')

            # 查找 loginhash（在 id 中包含 main_messaqge_ 的 div）
            loginhash = None
            div = soup.find('div', id=re.compile(r'^main_messaqge_'))
            if div:
                match = re.search(r'main_messaqge_(.+)', div.get('id', ''))
                if match:
                    loginhash = match.group(1)

            # 查找 formhash
            formhash = None
            input_tag = soup.find('input', {'name': 'formhash'})
            if input_tag and input_tag.get('value'):
                formhash = input_tag['value']

            if loginhash is None or formhash is None:
                logger.error("Failed to extract loginhash or formhash from login page")
                return None, None
            return loginhash, formhash
        except Exception as e:
            logger.exception(f"Error extracting loginhash/formhash: {e}")
            return None, None

    def login(self) -> bool:
        """
        执行登录，返回是否成功。
        """
        loginhash, formhash = self._get_loginhash_from_login_page()
        if loginhash is None or formhash is None:
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
            # 检查响应中的成功提示（常见 Discuz! 返回 XML 包含 "欢迎" 或 "登录成功"）
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
        """
        获取用户银币数量，返回字符串，失败返回 None。
        """
        credit_url = (f"https://{self.hostname}/home.php?mod=spacecp&ac=credit"
                      f"&showcredit=1&inajax=1&ajaxtarget=extcreditmenu_menu")
        try:
            resp = self.session.get(credit_url, verify=False, proxies=self.proxies, timeout=10)
            resp.encoding = resp.apparent_encoding or 'utf-8'
            # 使用 BeautifulSoup 直接解析响应（响应包含 CDATA 包裹的 HTML）
            soup = BeautifulSoup(resp.text, 'html.parser')
            # 查找 span id="hcredit_2"
            span = soup.find('span', id='hcredit_2')
            if span:
                return span.get_text(strip=True)
            else:
                logger.warning("Credit span not found")
                return None
        except Exception as e:
            logger.exception(f"Error fetching credit: {e}")
            return None

    def space_form_hash(self) -> str | None:
        """
        从个人空间页面获取 formhash。
        """
        url = f'https://{self.hostname}/home.php'
        return self._get_formhash_from_page(url)

    def space(self) -> bool:
        """
        发布 5 条空间动态，每条间隔约 120 秒。
        返回是否全部成功（实际可部分成功，这里只要有一条成功就算成功？此处返回 True 表示所有尝试均无异常）。
        """
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
            # 注意：message 保持为字符串，由 requests 自动 URL 编码
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
                    logger.info(f"{self.username} post {i+1}nd successfully!")
                    success_count += 1
                else:
                    logger.warning(f"{self.username} post {i+1}nd failed: {resp.text[:100]}")
            except Exception as e:
                logger.exception(f"Exception during posting {i+1}: {e}")

            # 除了最后一次，等待 120 秒
            if i < 4:
                time.sleep(120)

        return success_count > 0


def main():
    # 1. 获取最终的真实域名
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

    # 提取主机名
    parsed = urlparse(final_page_url)
    hostname = parsed.hostname
    if not hostname:
        logger.error(f"Invalid final URL: {final_page_url}")
        sys.exit(1)

    logger.info(f"Final hostname: {hostname}")

    # 2. 从环境变量读取凭据，若无则报错
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
        logger.warning("Some space posts may have failed")

    # 5. 查询银币
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
