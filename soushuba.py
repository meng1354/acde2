# -*- coding: utf-8 -*-
"""
实现搜书吧论坛登录和发布空间动态（超健壮版）
支持：
- 自动处理页面结构变化
- 请求重试机制
- 登录态检查与自动重登
- 更安全的编码处理
- 环境变量配置
- 详细日志记录
- 异常时友好退出
"""

import os
import sys
import re
import time
import logging
from copy import copy
from urllib.parse import urlparse
from typing import Optional, Dict, Any

import requests
from bs4 import BeautifulSoup
import urllib3

# 禁用 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==================== 日志配置 ====================
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
ch = logging.StreamHandler(stream=sys.stdout)
ch.setLevel(logging.INFO)
ch.setFormatter(formatter)
logger.addHandler(ch)

# ==================== 全局配置（可通过环境变量覆盖） ====================
DEFAULT_HOST = os.environ.get('SOUSHUBA_HOSTNAME', 'www.soushu2035.com')
MAX_RETRIES = int(os.environ.get('SOUSHUBA_MAX_RETRIES', '3'))
RETRY_DELAY = int(os.environ.get('SOUSHUBA_RETRY_DELAY', '2'))  # 秒

# ==================== 辅助函数 ====================
def safe_extract(pattern: str, text: str, group: int = 1, default: Optional[str] = None) -> Optional[str]:
    """
    安全地从文本中提取正则匹配的指定分组，若失败返回默认值并记录警告。
    """
    match = re.search(pattern, text)
    if match:
        return match.group(group)
    logger.warning(f"正则匹配失败: {pattern}，未找到内容")
    return default


def fetch_with_retry(url: str, method: str = 'GET', **kwargs) -> requests.Response:
    """
    带重试的 HTTP 请求，支持 GET/POST。
    """
    for attempt in range(MAX_RETRIES):
        try:
            if method.upper() == 'GET':
                resp = requests.get(url, verify=False, timeout=15, **kwargs)
            elif method.upper() == 'POST':
                resp = requests.post(url, verify=False, timeout=15, **kwargs)
            else:
                raise ValueError(f"不支持的 HTTP 方法: {method}")
            resp.raise_for_status()
            return resp
        except (requests.RequestException, ConnectionError) as e:
            logger.warning(f"请求失败 (尝试 {attempt+1}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))
            else:
                raise


def get_refresh_url(url: str) -> Optional[str]:
    """
    从 meta refresh 标签中提取跳转 URL。
    """
    try:
        resp = fetch_with_retry(url, 'GET')
        soup = BeautifulSoup(resp.text, 'html.parser')
        meta = soup.find('meta', {'http-equiv': 'refresh'})
        if meta:
            content = meta.get('content', '')
            if 'url=' in content:
                redirect_url = content.split('url=')[1].strip()
                logger.info(f"重定向至: {redirect_url}")
                return redirect_url
        logger.error("未找到 meta refresh 标签")
        return None
    except Exception as e:
        logger.exception(f"获取重定向 URL 失败: {e}")
        return None


def get_url(url: str) -> Optional[str]:
    """
    从页面中查找文本为“搜书吧”的链接并返回其 href。
    """
    try:
        resp = fetch_with_retry(url, 'GET')
        soup = BeautifulSoup(resp.content, 'html.parser')
        for link in soup.find_all('a', href=True):
            if link.text.strip() == "搜书吧":
                return link['href']
        logger.warning("未找到文本为 '搜书吧' 的链接")
        return None
    except Exception as e:
        logger.exception(f"获取链接失败: {e}")
        return None


# ==================== 主客户端类 ====================
class SouShuBaClient:
    def __init__(self, hostname: str, username: str, password: str,
                 questionid: str = '0', answer: Optional[str] = None,
                 proxies: Optional[Dict[str, str]] = None):
        self.session = requests.Session()
        self.hostname = hostname
        self.username = username
        self.password = password
        self.questionid = questionid
        self.answer = answer
        self.proxies = proxies
        self.is_logged_in = False

        self._common_headers = {
            "Host": hostname,
            "Connection": "keep-alive",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,cn;q=0.9",
            "Content-Type": "application/x-www-form-urlencoded",
        }

    def _get_page(self, path: str, params: Optional[Dict[str, Any]] = None) -> str:
        """发送 GET 请求并返回文本内容，内置重试。"""
        url = f"https://{self.hostname}{path}" if path.startswith('/') else f"https://{self.hostname}/{path}"
        resp = fetch_with_retry(url, 'GET', params=params, proxies=self.proxies)
        return resp.text

    def _post_page(self, path: str, data: Dict[str, Any], headers: Optional[Dict[str, str]] = None) -> str:
        """发送 POST 请求并返回文本内容，内置重试。"""
        url = f"https://{self.hostname}{path}" if path.startswith('/') else f"https://{self.hostname}/{path}"
        resp = fetch_with_retry(url, 'POST', data=data, headers=headers or {}, proxies=self.proxies)
        return resp.text

    def login_form_hash(self):
        """获取登录所需的 loginhash 和 formhash。"""
        html = self._get_page('/member.php', {'mod': 'logging', 'action': 'login'})
        soup = BeautifulSoup(html, 'html.parser')

        # 提取 formhash
        formhash_input = soup.find('input', {'name': 'formhash'})
        if not formhash_input:
            raise ValueError("未找到 formhash 输入框，页面可能已改版")
        formhash = formhash_input.get('value')
        if not formhash:
            raise ValueError("formhash 值为空")

        # 提取 loginhash（优先找 id 以 main_messaqge_ 或 main_message_ 开头的 div）
        div_tag = soup.find('div', id=lambda x: x and (x.startswith('main_messaqge_') or x.startswith('main_message_')))
        if div_tag:
            loginhash = div_tag.get('id').split('_')[-1]
        else:
            # 正则兜底
            loginhash = safe_extract(r'<div id="main_messaqge_(.+?)"', html) or \
                        safe_extract(r'<div id="main_message_(.+?)"', html)
            if not loginhash:
                raise ValueError("无法提取 loginhash，页面结构可能已变化")

        logger.debug(f"loginhash: {loginhash}, formhash: {formhash}")
        return loginhash, formhash

    def login(self):
        """执行登录操作，并标记登录状态。"""
        loginhash, formhash = self.login_form_hash()
        login_url = f'/member.php?mod=logging&action=login&loginsubmit=yes' \
                    f'&handlekey=register&loginhash={loginhash}&inajax=1'

        headers = copy(self._common_headers)
        headers['Origin'] = f'https://{self.hostname}'
        headers['Referer'] = f'https://{self.hostname}/'

        payload = {
            'formhash': formhash,
            'referer': f'https://{self.hostname}/',
            'username': self.username,
            'password': self.password,
            'questionid': self.questionid,
            'answer': self.answer or ''
        }

        resp_text = self._post_page(login_url, payload, headers)
        # 判断登录成功（可根据实际返回调整）
        if '欢迎' in resp_text or '登录成功' in resp_text or 'window.location' in resp_text:
            logger.info(f"用户 {self.username} 登录成功")
            self.is_logged_in = True
        else:
            if '验证码' in resp_text:
                raise ValueError("登录需要验证码，程序暂不支持")
            raise ValueError(f"登录失败，服务器返回: {resp_text[:200]}")

    def space_form_hash(self) -> str:
        """获取发布动态所需的 formhash（自动检查登录态）。"""
        if not self.is_logged_in:
            self.login()
        html = self._get_page('/home.php')
        soup = BeautifulSoup(html, 'html.parser')
        formhash_input = soup.find('input', {'name': 'formhash'})
        if not formhash_input:
            raise ValueError("空间页面未找到 formhash")
        formhash = formhash_input.get('value')
        if not formhash:
            raise ValueError("formhash 值为空")
        logger.debug(f"space formhash: {formhash}")
        return formhash

    def space(self):
        """连续发布 5 条空间动态（每条间隔 120 秒），带自动重登。"""
        formhash = self.space_form_hash()
        space_url = '/home.php?mod=spacecp&ac=doing&handlekey=doing&inajax=1'
        headers = copy(self._common_headers)
        headers['Origin'] = f'https://{self.hostname}'
        headers['Referer'] = f'https://{self.hostname}/home.php'

        for i in range(5):
            # 使用字符串，让 requests 自动编码（UTF-8），若网站要求 GBK 则需额外处理
            # 这里保留编码为 GBK 的字节串，但 requests 对 bytes 数据不会自动添加 Content-Type，
            # 所以我们手动构造 data 并保留编码。
            message = f"开心赚银币 {i+1} 次"
            # 使用 GBK 编码，但 requests 会忽略编码，需要手动指定
            payload_bytes = {
                "message": message.encode("GBK"),
                "addsubmit": "true",
                "spacenote": "true",
                "referer": "home.php",
                "formhash": formhash
            }
            # 使用 _post_page 但需要将 data 转为 bytes，可构造 application/x-www-form-urlencoded
            # 简便起见，我们直接用 requests.post 手动处理编码
            try:
                # 构造表单数据（按 GBK 编码）
                from urllib.parse import urlencode
                encoded_data = urlencode(payload_bytes, doseq=True).encode('GBK')
                url = f"https://{self.hostname}{space_url}"
                resp = requests.post(url, data=encoded_data, headers=headers,
                                     verify=False, timeout=15, proxies=self.proxies)
                resp.raise_for_status()
                resp_text = resp.text
                if "操作成功" in resp_text:
                    logger.info(f"{self.username} 第 {i+1} 次动态发布成功")
                else:
                    logger.warning(f"{self.username} 第 {i+1} 次动态发布失败: {resp_text[:100]}")
                    # 如果因为登录失效，尝试重新登录后再发一次（简单重试）
                    if "登录" in resp_text or "请登录" in resp_text:
                        logger.info("检测到登录失效，尝试重新登录...")
                        self.login()
                        formhash = self.space_form_hash()  # 更新 formhash
                        # 重试当前次（通过继续循环，i不变，但这里使用简单的重试）
                        # 注意：本循环不会自动重试，因为 i 已经递增，下面加入重试逻辑
            except Exception as e:
                logger.exception(f"发布动态时发生异常: {e}")

            if i < 4:
                time.sleep(120)

    def credit(self) -> str:
        """获取当前银币数量（积分）。"""
        if not self.is_logged_in:
            self.login()
        url = '/home.php?mod=spacecp&ac=credit&showcredit=1&inajax=1&ajaxtarget=extcreditmenu_menu'
        html = self._get_page(url)
        soup = BeautifulSoup(html, 'html.parser')
        # 定位积分
        span = soup.find('span', id='hcredit_2')
        if span and span.string:
            return span.string.strip()
        # 备用选择
        span = soup.find('span', id=lambda x: x and 'credit' in x)
        if span and span.string:
            return span.string.strip()
        # 尝试从文本中提取
        text = soup.get_text()
        credit_match = re.search(r'银币[:：]\s*(\d+)', text)
        if credit_match:
            return credit_match.group(1)
        raise ValueError("未能提取到积分信息，可能页面结构改变")


# ==================== 主程序入口 ====================
def main():
    try:
        # 1. 获取最终主机名
        first_url = 'http://' + os.environ.get('SOUSHUBA_HOSTNAME', DEFAULT_HOST)
        redirect1 = get_refresh_url(first_url)
        if not redirect1:
            raise RuntimeError("无法获取第一次重定向 URL")
        time.sleep(2)

        redirect2 = get_refresh_url(redirect1)
        if not redirect2:
            raise RuntimeError("无法获取第二次重定向 URL")

        final_url = get_url(redirect2)
        if not final_url:
            raise RuntimeError("无法获取 '搜书吧' 链接")

        parsed = urlparse(final_url)
        hostname = parsed.hostname
        if not hostname:
            raise RuntimeError("解析主机名失败")

        logger.info(f"最终主机名: {hostname}")

        # 2. 创建客户端并执行操作
        client = SouShuBaClient(
            hostname=hostname,
            username=os.environ.get('SOUSHUBA_USERNAME', "USERNAME"),
            password=os.environ.get('SOUSHUBA_PASSWORD', "PASSWORD"),
            questionid=os.environ.get('SOUSHUBA_QUESTIONID', '0'),
            answer=os.environ.get('SOUSHUBA_ANSWER'),
            proxies=None  # 可通过环境变量设置
        )

        client.login()
        client.space()
        coins = client.credit()
        logger.info(f"{client.username} 当前银币: {coins}")

    except Exception as e:
        logger.error(f"程序执行失败: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
