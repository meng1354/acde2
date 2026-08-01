# -*- coding: utf-8 -*-
"""
实现搜书吧论坛登入和发布空间动态
"""
import os
import re
import sys
from copy import copy

import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs
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


def get_refresh_url(url: str):
    try:
        response = requests.get(url, allow_redirects=True, timeout=20)
        # If a direct redirect happened, use response.url
        if response.history:
            redirect_url = response.url
            logger.info(f"Redirecting to: {redirect_url}")
            return redirect_url

        # otherwise try to find meta refresh
        soup = BeautifulSoup(response.text, 'html.parser')
        meta_tags = soup.find_all('meta', {'http-equiv': re.compile(r'(?i)refresh')})

        if meta_tags:
            content = meta_tags[0].get('content', '')
            m = re.search(r'url=(.+)', content, flags=re.I)
            if m:
                redirect_url = m.group(1).strip().strip('"').strip("'")
                logger.info(f"Redirecting to: {redirect_url}")
                return redirect_url
        else:
            logger.error("No meta refresh tag found.")
            return None
    except Exception as e:
        logger.exception(f'An unexpected error occurred when getting refresh url: {e}')
        return None


def get_url(url: str):
    try:
        resp = requests.get(url, timeout=20)
        soup = BeautifulSoup(resp.content, 'html.parser')

        links = soup.find_all('a', href=True)
        for link in links:
            # look for link text that contains "搜书吧" (tolerant)
            if link.text and "搜书吧" in link.text:
                return link['href']
        # fallback: try to find any link with hostname that looks like a site
        for link in links:
            href = link.get('href')
            if href and ('soushu' in href or 'soushuba' in href):
                return href
        return None
    except Exception:
        logger.exception("Failed to get url from page")
        return None


class SouShuBaClient:

    def __init__(self, hostname: str, username: str, password: str, questionid: str = '0', answer: str = None,
                 proxies: dict | None = None):
        self.session: requests.Session = requests.Session()
        self.hostname = hostname
        self.username = username
        self.password = password
        self.questionid = questionid
        self.answer = answer
        self._common_headers = {
            "Host": f"{ hostname }",
            "Connection": "keep-alive",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,cn;q=0.9",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        self.proxies = proxies

    def login_form_hash(self):
        rst = self.session.get(f'https://{self.hostname}/member.php?mod=logging&action=login', verify=False, timeout=20).text
        soup = BeautifulSoup(rst, 'html.parser')

        # Try to find a div id like 'main_message_<hash>' (tolerant of small typos)
        div = soup.find('div', id=re.compile(r'^main_message_|^main_messaqge_|^main_messa?ge_', re.I))
        loginhash = None
        if div and div.has_attr('id'):
            # id could be main_message_xxx
            parts = div['id'].split('_', 1)
            if len(parts) == 2:
                loginhash = parts[1]

        # fallback: sometimes loginhash is in javascript: search common patterns
        if not loginhash:
            m = re.search(r"loginhash\s*[:=]\s*[\"']([^\"']+)[\"']", rst)
            if m:
                loginhash = m.group(1)

        # find formhash input safely
        form_input = soup.find('input', attrs={'name': 'formhash'})
        formhash = None
        if form_input and form_input.get('value'):
            formhash = form_input.get('value')
        else:
            m2 = re.search(r'name=["\']formhash["\']\s+value=["\'](.+?)["\']', rst)
            if m2:
                formhash = m2.group(1)

        if not loginhash or not formhash:
            logger.error("Failed to extract loginhash/formhash. loginhash=%r formhash=%r", loginhash, formhash)
            raise RuntimeError("Could not find loginhash or formhash on login page; HTML structure may have changed.")

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
            'answer': self.answer
        }

        resp = self.session.post(login_url, proxies=self.proxies, data=payload, headers=headers, verify=False, timeout=20)
        if resp.status_code == 200:
            logger.info(f'Welcome {self.username}!')
        else:
            logger.error("Login POST returned status %s. Response snippet: %s", resp.status_code, resp.text[:300])
            raise ValueError('Verify Failed! Check your username and password!')

    def credit(self):
        credit_url = f"https://{self.hostname}/home.php?mod=spacecp&ac=credit&showcredit=1&inajax=1&ajaxtarget=extcreditmenu_menu"
        credit_rst = self.session.get(credit_url, verify=False, timeout=20).text

        # Try parsing as XML then fallback to HTML parsing
        try:
            root = ET.fromstring(credit_rst)
            cdata_content = root.text or ''
            cdata_soup = BeautifulSoup(cdata_content, features="lxml")
            span = cdata_soup.find("span", id="hcredit_2")
            if span and span.string:
                return span.string.strip()
        except Exception:
            # fallback to HTML search
            try:
                soup = BeautifulSoup(credit_rst, 'html.parser')
                span = soup.find("span", id="hcredit_2")
                if span:
                    return (span.string or span.get_text()).strip()
            except Exception:
                logger.exception("Failed to parse credit response")

        # final fallback: regex
        m = re.search(r'<span[^>]*id=["\']hcredit_2["\'][^>]*>([^<]+)</span>', credit_rst)
        if m:
            return m.group(1).strip()

        logger.warning("Could not determine credit value from response. Response snippet: %s", credit_rst[:300])
        return None

    def space_form_hash(self):
        rst = self.session.get(f'https://{self.hostname}/home.php', verify=False, timeout=20).text
        soup = BeautifulSoup(rst, 'html.parser')
        input_tag = soup.find('input', attrs={'name': 'formhash'})
        if input_tag and input_tag.get('value'):
            return input_tag.get('value')
        m = re.search(r'name=["\']formhash["\']\s+value=["\'](.+?)["\']', rst)
        if m:
            return m.group(1)
        logger.error("Failed to extract space formhash")
        raise RuntimeError("space formhash not found")

    def space(self):
        formhash = self.space_form_hash()
        space_url = f"https://{self.hostname}/home.php?mod=spacecp&ac=doing&handlekey=doing&inajax=1"

        headers = copy(self._common_headers)
        headers["origin"] = f'https://{self.hostname}'
        headers["referer"] = f'https://{self.hostname}/home.php'

        for x in range(5):
            payload = {
                "message": "开心赚银币 {0} 次".format(x + 1).encode("GBK"),
                "addsubmit": "true",
                "spacenote": "true",
                "referer": "home.php",
                "formhash": formhash
            }
            resp = self.session.post(space_url, proxies=self.proxies, data=payload, headers=headers, verify=False, timeout=20)
            if re.search("操作成功", resp.text):
                logger.info(f'{self.username} post {x + 1}nd successfully!')
                time.sleep(120)
            else:
                logger.warning(f'{self.username} post {x + 1}nd failed! Response snippet: %s', resp.text[:200])


if __name__ == '__main__':
    try:
        redirect_url = get_refresh_url('http://' + os.environ.get('SOUSHUBA_HOSTNAME', 'www.soushu2035.com'))
        if not redirect_url:
            raise RuntimeError("Initial redirect url not found")
        time.sleep(2)
        redirect_url2 = get_refresh_url(redirect_url)
        if not redirect_url2:
            raise RuntimeError("Second redirect url not found")
        url = get_url(redirect_url2)
        if not url:
            raise RuntimeError("Final target url not found from page")
        logger.info('%s', url)
        client = SouShuBaClient(urlparse(url).hostname,
                                os.environ.get('SOUSHUBA_USERNAME', "USERNAME"),
                                os.environ.get('SOUSHUBA_PASSWORD', "PASSWORD"))
        client.login()
        client.space()
        credit = client.credit()
        logger.info(f'{client.username} have {credit} coins!')
    except Exception:
        logger.exception("Unhandled exception in soushuba.py")
        sys.exit(1)
