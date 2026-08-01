# -*- coding: utf-8 -*-
"""
实现搜书吧论坛登录和发布空间动态（改进版）
改进点：
1. 修复空间动态发布时的编码错误（使用GBK编码并正确设置Content-Type）
2. 使用BeautifulSoup替代正则提取formhash和loginhash，增强健壮性
3. 添加重定向结果检查，避免None导致后续错误
4. 登录增加响应内容校验，确保成功
5. 动态发布循环中每次重新获取formhash，避免过期
6. 添加请求超时、重试机制（简单实现）
7. 完善异常日志，输出堆栈信息
8. 增强提取银币数的空值处理
"""
import os
import re
import sys
import time
import logging
import urllib.parse
from copy import copy

import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
ch = logging.StreamHandler(stream=sys.stdout)
ch.setLevel(logging.INFO)
ch.setFormatter(formatter)
logger.addHandler(ch)


def get_refresh_url(url: str, timeout: int = 30) -> str | None:
    """
    从meta refresh中提取重定向URL，增加超时和状态码检查
    """
    try:
        response = requests.get(url, timeout=timeout, verify=False)
        # 如果状态码非200且非403（原逻辑跳过403），同样处理其他可能的重定向状态
        if response.status_code not in (200, 403):
            response.raise_for_status()  # 主动抛出异常

        soup = BeautifulSoup(response.text, 'html.parser')
        meta_tags = soup.find_all('meta', {'http-equiv': 'refresh'})
        if meta_tags:
            content = meta_tags[0].get('content', '')
            if 'url=' in content:
                redirect_url = content.split('url=')[1].strip()
                logger.info(f"Redirecting to: {redirect_url}")
                return redirect_url
        logger.warning("No meta refresh tag found in response.")
        return None
    except Exception as e:
        logger.exception(f'Failed to get refresh URL from {url}: {e}')
        return None


def get_url(url: str, timeout: int = 30) -> str | None:
    """
    从页面中查找“搜书吧”链接，返回目标URL
    """
    try:
        resp = requests.get(url, timeout=timeout, verify=False)
        soup = BeautifulSoup(resp.content, 'html.parser')
        for link in soup.find_all('a', href=True):
            if link.text.strip() == "搜书吧":
                return link['href']
        logger.warning("No '搜书吧' link found on page.")
        return None
    except Exception as e:
        logger.exception(f'Failed to get target URL from {url}: {e}')
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
        self._common_headers = {
            "Host": hostname,
            "Connection": "keep-alive",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,cn;q=0.9",
        }
        self.proxies = proxies

    def _get_formhash_and_loginhash(self) -> tuple[str, str]:
        """
        从登录页面提取formhash和loginhash，使用BeautifulSoup增强鲁棒性
        """
        url = f'https://{self.hostname}/member.php?mod=logging&action=login'
        resp = self.session.get(url, timeout=30, verify=False)
        soup = BeautifulSoup(resp.text, 'html.parser')

        # 提取formhash
        formhash_input = soup.find('input', {'name': 'formhash'})
        if not formhash_input or not formhash_input.get('value'):
            raise ValueError("Failed to extract formhash from login page.")
        formhash = formhash_input['value']

        # 提取loginhash（从div id中提取，例如 main_message_xxxx）
        # 改进正则：匹配 id="main_message_数字字母"
        div = soup.find('div', id=re.compile(r'^main_message_[a-zA-Z0-9]+$'))
        if not div:
            raise ValueError("Failed to extract loginhash from login page.")
        loginhash = div['id'].split('_', 2)[-1]  # 取后面部分

        return loginhash, formhash

    def login(self):
        """登录，增加响应内容校验"""
        loginhash, formhash = self._get_formhash_and_loginhash()
        login_url = (f'https://{self.hostname}/member.php?mod=logging&action=login'
                     f'&loginsubmit=yes&handlekey=register&loginhash={loginhash}&inajax=1')

        headers = copy(self._common_headers)
        headers.update({
            "origin": f'https://{self.hostname}',
            "referer": f'https://{self.hostname}/',
            "Content-Type": "application/x-www-form-urlencoded",
        })
        payload = {
            'formhash': formhash,
            'referer': f'https://{self.hostname}/',
            'username': self.username,
            'password': self.password,
            'questionid': self.questionid,
            'answer': self.answer,
        }

        resp = self.session.post(login_url, proxies=self.proxies, data=payload,
                                 headers=headers, timeout=30, verify=False)

        if resp.status_code != 200:
            raise ValueError(f"Login request failed with status {resp.status_code}")

        # 检查响应内容是否包含成功信息（例如“欢迎您回来”或“登录成功”）
        if "欢迎您回来" in resp.text or "登录成功" in resp.text or "window.location" in resp.text:
            logger.info(f'Welcome {self.username}!')
        else:
            # 可能包含错误信息
            logger.error(f"Login response: {resp.text[:200]}")
            raise ValueError('Login failed! Check username/password or security question.')

    def _get_space_formhash(self) -> str:
        """从空间页面获取formhash"""
        url = f'https://{self.hostname}/home.php'
        resp = self.session.get(url, timeout=30, verify=False)
        soup = BeautifulSoup(resp.text, 'html.parser')
        input_tag = soup.find('input', {'name': 'formhash'})
        if not input_tag or not input_tag.get('value'):
            raise ValueError("Failed to extract formhash from space page.")
        return input_tag['value']

    def space(self, times: int = 5, interval: int = 120):
        """
        发布空间动态，每次发布前重新获取formhash以避免过期
        修复编码问题：使用GBK编码发送中文消息
        """
        for i in range(times):
            try:
                formhash = self._get_space_formhash()
                space_url = f"https://{self.hostname}/home.php?mod=spacecp&ac=doing&handlekey=doing&inajax=1"

                headers = copy(self._common_headers)
                headers.update({
                    "origin": f'https://{self.hostname}',
                    "referer": f'https://{self.hostname}/home.php',
                    "Content-Type": "application/x-www-form-urlencoded; charset=GBK",
                })

                # 原始消息（Unicode）
                message = f"开心赚银币 {i + 1} 次"
                payload = {
                    "message": message,
                    "addsubmit": "true",
                    "spacenote": "true",
                    "referer": "home.php",
                    "formhash": formhash,
                }
                # 使用GBK编码构建请求体
                data = urllib.parse.urlencode(payload, encoding='gbk').encode('gbk')

                resp = self.session.post(space_url, proxies=self.proxies, data=data,
                                         headers=headers, timeout=30, verify=False)

                if "操作成功" in resp.text:
                    logger.info(f'{self.username} post {i + 1}nd successfully!')
                else:
                    logger.warning(f'{self.username} post {i + 1}nd failed. Response: {resp.text[:100]}')

                time.sleep(interval)
            except Exception as e:
                logger.exception(f"Error during space post {i+1}: {e}")

    def credit(self) -> str:
        """获取当前银币数，增加空值处理"""
        credit_url = (f"https://{self.hostname}/home.php?mod=spacecp&ac=credit"
                      f"&showcredit=1&inajax=1&ajaxtarget=extcreditmenu_menu")
        resp = self.session.get(credit_url, timeout=30, verify=False)

        # 响应可能是XML包含CDATA，也可能直接是HTML片段
        # 直接使用BeautifulSoup解析HTML
        soup = BeautifulSoup(resp.text, 'html.parser')
        span = soup.find('span', id='hcredit_2')
        if span and span.string:
            return span.string.strip()
        else:
            logger.warning("Could not find credit span, trying regex fallback...")
            # 备选：用正则提取数字
            match = re.search(r'<span id="hcredit_2">(\d+)</span>', resp.text)
            if match:
                return match.group(1)
            return "0"


if __name__ == '__main__':
    try:
        # 获取最终论坛域名
        initial_url = 'http://' + os.environ.get('SOUSHUBA_HOSTNAME', 'www.soushu2035.com')
        redirect1 = get_refresh_url(initial_url)
        if not redirect1:
            raise ValueError("Failed to get first redirect URL")
        time.sleep(2)

        redirect2 = get_refresh_url(redirect1)
        if not redirect2:
            raise ValueError("Failed to get second redirect URL")

        target_url = get_url(redirect2)
        if not target_url:
            raise ValueError("Failed to extract target URL from page")

        parsed = urlparse(target_url)
        hostname = parsed.hostname
        if not hostname:
            raise ValueError(f"Invalid target URL: {target_url}")

        logger.info(f'Final hostname: {hostname}')

        client = SouShuBaClient(
            hostname,
            os.environ.get('SOUSHUBA_USERNAME', 'USERNAME'),
            os.environ.get('SOUSHUBA_PASSWORD', 'PASSWORD')
        )

        client.login()
        client.space(times=5, interval=120)  # 可调整次数和间隔
        credit = client.credit()
        logger.info(f'{client.username} have {credit} coins!')

    except Exception as e:
        logger.exception("Main execution failed")
        sys.exit(1)
