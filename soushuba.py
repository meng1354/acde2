@@
     def space_form_hash(self):
-        rst = self.session.get(f'https://{self.hostname}/home.php', verify=False, timeout=20).text
-        soup = BeautifulSoup(rst, 'html.parser')
-        input_tag = soup.find('input', attrs={'name': 'formhash'})
-        if input_tag and input_tag.get('value'):
-            return input_tag.get('value')
-        m = re.search(r'name=["\']formhash["\']\s+value=["\'](.+?)["\']', rst)
-        if m:
-            return m.group(1)
-        logger.error("Failed to extract space formhash")
-        raise RuntimeError("space formhash not found")
+        """Robustly extract the space formhash; on failure store page snippet and return None."""
+        try:
+            resp = self.session.get(f'https://{self.hostname}/home.php', verify=False, timeout=20)
+            rst = resp.text
+        except Exception as e:
+            logger.exception("Request to home.php failed: %s", e)
+            self._last_space_page = ""
+            return None
+
+        self._last_space_page = rst  # keep for diagnostics
+
+        # 1) HTML input lookup
+        try:
+            soup = BeautifulSoup(rst, 'html.parser')
+            input_tag = soup.find('input', attrs={'name': 'formhash'})
+            if input_tag and input_tag.get('value'):
+                return input_tag.get('value')
+        except Exception:
+            logger.exception("BeautifulSoup parsing error when searching for formhash")
+
+        # 2) fallback regex patterns
+        patterns = [
+            r'name=["\']formhash["\']\s+value=["\'](.+?)["\']',
+            r'formhash["\']\s*[:=]\s*["\']([^"\']+)["\']',
+            r'formhash\s*=\s*"([^"]+)"',
+            r'formhash\s*=\s*\'([^\']+)\'',
+        ]
+        for pat in patterns:
+            m = re.search(pat, rst, flags=re.I)
+            if m:
+                return m.group(1)
+
+        # not found — log truncated page for debugging and return None
+        snippet = (rst or "")[:4000]
+        logger.error("Failed to extract space formhash from https://%s/home.php. Response snippet:\n%s", self.hostname, snippet)
+        return None
@@
     def space(self):
-        formhash = self.space_form_hash()
+        formhash = self.space_form_hash()
+        if not formhash:
+            # surface clearer diagnostic info so the workflow logs show the page that was fetched
+            last = getattr(self, "_last_space_page", "") or ""
+            logger.error("space formhash not found for host %s. First 2000 chars of fetched page:\n%s", self.hostname, last[:2000])
+            raise RuntimeError("space formhash not found; check SOUSHUBA_HOSTNAME and whether the site HTML has changed.")
@@
 if __name__ == '__main__':
     try:
-        redirect_url = get_refresh_url('http://' + os.environ.get('SOUSHUBA_HOSTNAME', 'www.soushu2035.com'))
+        sh = os.environ.get('SOUSHUBA_HOSTNAME', '')
+        if not sh:
+            raise RuntimeError("SOUSHUBA_HOSTNAME is not set (check repository secrets)")
+        redirect_url = get_refresh_url('http://' + sh)
         if not redirect_url:
             raise RuntimeError("Initial redirect url not found")
         time.sleep(2)
         redirect_url2 = get_refresh_url(redirect_url)
         if not redirect_url2:
             raise RuntimeError("Second redirect url not found")
         url = get_url(redirect_url2)
         if not url:
             raise RuntimeError("Final target url not found from page")
-        logger.info('%s', url)
+        logger.info('%s', url)
+        # safety check: if final hostname looks unrelated (e.g., spammy redirect), abort and ask to verify secrets
+        target_host = urlparse(url).hostname or ""
+        if 'soushu' not in target_host.lower() and 'soushuba' not in target_host.lower():
+            logger.error("Resolved target hostname appears unexpected: %s. Aborting. Please verify SOUSHUBA_HOSTNAME secret.", target_host)
+            raise RuntimeError(f"Unsafe/Unexpected target hostname: {target_host}")
