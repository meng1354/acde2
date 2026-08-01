# -*- coding: utf-8 -*-
"""
实现搜书吧论坛登录和发布空间动态（健壮版）
"""
import os
import sys
import time
import logging
from copy import copy
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
import urllib3

# 禁用 SSL 警告（若不需要可移除）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==================== 日志配置 ====================
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
ch = logging.StreamHandler(stream=sys.stdout)
ch.setLevel(logging.INFO)
ch.setFormatter(formatter)
logger.addHandler(ch)

# ==================== 辅助函数 ====================
def safe_extract(pattern: str, text: str, group: int = 1, default: str = None) -> str | None:
    """
    安全地从文本中提取正则匹配的指定分组，若失败返回默认值并记录警告。
    """
    match = re.search(pattern, text)
    if match:
        return match.group(group)
    logger.warning(f"正则匹配失败: {pattern}，未找到内容")
    return default


def get_refresh_url(url: str) -> str | None:
    """
    从 meta refresh 标签中提取跳转 URL。
    """
    try:
        response = requests.get(url, verify=False, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        meta = soup.find('meta', {'http-equiv': 'refresh'})
        if meta:
            content = meta.get('content', '')
            if 'url=' in content:
                redirect_url = content.split('url=')[1].strip()
                logger.info(f"重定向至: {redirect_url}")
                return redirect_url
        logger.error("未找到 meta refresh 标签")
        return None
    except requests.RequestException as e:
        logger.exception(f"获取重定向 URL 失败: {e}")
        return None


def get_url(url: str) -> str | None:
    """
    从页面中查找文本为“搜书吧”的链接并返回其 href。
    """
    try:
        resp = requests.get(url, verify=False, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, 'html.parser')
        for link in soup.find_all('a', href=True):
            if link.text.strip() == "搜书吧":
                return link['href']
        logger.warning("未找到文本为 '搜书吧' 的链接")
        return None
    except requests.RequestException as e:
        logger.exception(f"获取链接失败: {e}")
        return None


# ==================== 主客户端类 ====================
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

    def _get_page(self, path: str, params: dict = None) -> str:
        """发送 GET 请求并返回文本内容，统一处理异常。"""
        url = f"https://{self.hostname}{path}" if path.startswith('/') else f"https://{self.hostname}/{path}"
        try:
            resp = self.session.get(url, params=params, verify=False, timeout=15, proxies=self.proxies)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            logger.error(f"请求 {url} 失败: {e}")
            raise

    def _post_page(self, path: str, data: dict, headers: dict = None) -> str:
        """发送 POST 请求并返回文本内容。"""
        url = f"https://{self.hostname}{path}" if path.startswith('/') else f"https://{self.hostname}/{path}"
        try:
            resp = self.session.post(url, data=data, headers=headers or {},
                                     verify=False, timeout=15, proxies=self.proxies)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            logger.error(f"POST 请求 {url} 失败: {e}")
            raise

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

        # 提取 loginhash（原正则：<div id="main_messaqge_数字">）
        # 更安全：查找 id 以 "main_messaqge_" 开头的 div
        div_tag = soup.find('div', id=lambda x: x and x.startswith('main_messaqge_'))
        if not div_tag:
            # 尝试另一种常见写法（可能是 main_message_）
            div_tag = soup.find('div', id=lambda x: x and x.startswith('main_message_'))
        if div_tag:
            loginhash = div_tag.get('id').split('_')[-1]
        else:
            # 最后尝试正则兜底
            loginhash = safe_extract(r'<div id="main_messaqge_(.+?)"', html)
            if not loginhash:
                raise ValueError("无法提取 loginhash，页面结构可能已变化")

        logger.debug(f"loginhash: {loginhash}, formhash: {formhash}")
        return loginhash, formhash

    def login(self):
        """执行登录操作。"""
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
        # 登录成功一般返回包含“欢迎”或“登录成功”的信息，可自行判断
        if '欢迎' in resp_text or '登录成功' in resp_text:
            logger.info(f"用户 {self.username} 登录成功")
        else:
            # 进一步检查是否失败
            if '验证码' in resp_text:
                raise ValueError("登录需要验证码，程序暂不支持")
            raise ValueError(f"登录失败，服务器返回: {resp_text[:200]}")

    def space_form_hash(self):
        """获取发布动态所需的 formhash。"""
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
        """连续发布 5 条空间动态（每条间隔 120 秒）。"""
        formhash = self.space_form_hash()
        space_url = '/home.php?mod=spacecp&ac=doing&handlekey=doing&inajax=1'
        headers = copy(self._common_headers)
        headers['Origin'] = f'https://{self.hostname}'
        headers['Referer'] = f'https://{self.hostname}/home.php'

        for i in range(5):
            payload = {
                "message": f"开心赚银币 {i+1} 次".encode("GBK"),  # 原代码用 GBK 编码
                "addsubmit": "true",
                "spacenote": "true",
                "referer": "home.php",
                "formhash": formhash
            }
            try:
                resp_text = self._post_page(space_url, payload, headers)
                if "操作成功" in resp_text:
                    logger.info(f"{self.username} 第 {i+1} 次动态发布成功")
                else:
                    logger.warning(f"{self.username} 第 {i+1} 次动态发布失败: {resp_text[:100]}")
            except Exception as e:
                logger.exception(f"发布动态时发生异常: {e}")

            # 最后一次不需要等待
            if i < 4:
                time.sleep(120)

    def credit(self) -> str:
        """获取当前银币数量（积分）。"""
        url = '/home.php?mod=spacecp&ac=credit&showcredit=1&inajax=1&ajaxtarget=extcreditmenu_menu'
        html = self._get_page(url)
        # 原代码用 XML 解析，但返回的可能是 HTML 片段，直接使用 BeautifulSoup 更稳妥
        soup = BeautifulSoup(html, 'html.parser')
        # 根据原代码，目标为 <span id="hcredit_2"> 的内容
        span = soup.find('span', id='hcredit_2')
        if span and span.string:
            return span.string.strip()
        # 若未找到，尝试查找其他可能 id
        span = soup.find('span', id=lambda x: x and 'credit' in x)
        if span and span.string:
            return span.string.strip()
        raise ValueError("未能提取到积分信息，可能页面结构改变")

# ==================== 主程序入口 ====================
if __name__ == '__main__':
    try:
        # 1. 通过 meta refresh 获取最终主机名
        first_url = 'http://' + os.environ.get('SOUSHUBA_HOSTNAME', 'www.soushu2035.com')
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
            # 可选问题与答案（环境变量可自行添加）
            questionid=os.environ.get('SOUSHUBA_QUESTIONID', '0'),
            answer=os.environ.get('SOUSHUBA_ANSWER')
        )

        client.login()
        client.space()
        coins = client.credit()
        logger.info(f"{client.username} 当前银币: {coins}")

    except Exception as e:
        logger.error(f"程序执行失败: {e}")
        sys.exit(1)
