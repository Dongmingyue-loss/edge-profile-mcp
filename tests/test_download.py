"""Verify download_dir isolation and click_and_wait_download on a local page."""
from __future__ import annotations

import http.server
import os
import shutil
import tempfile
import threading
import unittest

from edge_profile_mcp.browser import BrowserControlError, BrowserController


PAGE = b"""<!doctype html><html><body>
<a id="file" href="/sample.txt" download="sample.txt">download</a>
<a id="nav" href="/other.html">navigate</a>
</body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/sample.txt"):
            body = b"hello-download"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Disposition", 'attachment; filename="sample.txt"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(PAGE if self.path.startswith("/page") else b"other")

    def log_message(self, *args):
        pass


class DownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.daemon_threads = True
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.profile = tempfile.mkdtemp(prefix="edge-dl-profile-")
        self.addCleanup(shutil.rmtree, self.profile, True)

    def make_browser(self, **kwargs):
        options = {"headless": True, "timeout_ms": 20_000, "profile_dir": self.profile}
        options.update(kwargs)
        return BrowserController(**options)

    def test_download_disabled_without_dir(self):
        c = self.make_browser()
        tab = c.start()
        try:
            c.navigate(tab, f"http://127.0.0.1:{self.port}/page")
            with self.assertRaises(BrowserControlError):
                c.click_and_wait_download(tab, css="#file")
        finally:
            c.close()

    def test_download_saved_in_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = self.make_browser(download_dir=tmp)
            tab = c.start()
            try:
                c.navigate(tab, f"http://127.0.0.1:{self.port}/page")
                result = c.click_and_wait_download(tab, css="#file")
                self.assertTrue(os.path.isfile(result["path"]))
                self.assertEqual(os.path.dirname(os.path.abspath(result["path"])),
                                 os.path.abspath(tmp))
                with open(result["path"], "rb") as handle:
                    self.assertEqual(handle.read(), b"hello-download")
                # a second download must not overwrite the first
                second = c.click_and_wait_download(tab, css="#file")
                self.assertNotEqual(second["path"], result["path"])
            finally:
                c.close()

    def test_non_download_click_reports_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = self.make_browser(download_dir=tmp)
            tab = c.start()
            try:
                c.navigate(tab, f"http://127.0.0.1:{self.port}/page")
                with self.assertRaises(BrowserControlError):
                    c.click_and_wait_download(tab, css="#nav", timeout_ms=4000)
            finally:
                c.close()


if __name__ == "__main__":
    unittest.main()
