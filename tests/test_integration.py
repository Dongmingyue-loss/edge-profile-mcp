"""Integration tests against a local HTTP fixture and the installed system Edge."""

import asyncio
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from edge_profile_mcp.browser import BrowserControlError, BrowserController

ROOT = Path(__file__).resolve().parents[1]


PAGE = b'''<!doctype html><html><head><title>Edge Fixture</title></head><body>
<h1>Fixture heading</h1><label for="message">Message</label><input id="message">
<button id="show" onclick="document.querySelector('#output').textContent=document.querySelector('#message').value">Show</button>
<p id="output">Waiting</p><button class="duplicate">Duplicate</button><button class="duplicate">Duplicate</button>
<label for="choice">Choice</label><select id="choice"><option value="a">Alpha</option><option value="b">Beta</option></select>
<button id="store" onclick="localStorage.setItem('marker','secret');document.cookie='marker=secret; path=/';document.querySelector('#storage').textContent='stored'">Store</button>
<button id="read" onclick="document.querySelector('#storage').textContent=(localStorage.getItem('marker')||'empty')+' '+(document.cookie||'no-cookie')">Read storage</button>
<p id="storage">unknown</p><a href="/other">Other page</a>
</body></html>'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/redirect':
            self.send_response(302)
            self.send_header('Location', '/other')
            self.end_headers()
            return
        body = b'<title>Other</title><h1>Second page</h1>' if self.path == '/other' else PAGE
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


class HTTPFixture:
    @classmethod
    def setUpClass(cls):
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.http.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join(timeout=5)


class BrowserIntegration(HTTPFixture, unittest.TestCase):
    """End-to-end checks that launch the installed Microsoft Edge."""

    def setUp(self):
        self.profile = tempfile.mkdtemp(prefix='edge-test-profile-')
        self.addCleanup(shutil.rmtree, self.profile, True)
        self.browser = self.make_browser()
        self.addCleanup(self.browser.close)
        self.tab = self.browser.start()

    def make_browser(self, **kwargs):
        options = {'headless': True, 'timeout_ms': 15000, 'profile_dir': self.profile}
        options.update(kwargs)
        return BrowserController(**options)

    def test_session_tabs_navigation_and_cleanup(self):
        self.assertEqual(len(self.browser.list_tabs()), 1)
        with self.assertRaisesRegex(BrowserControlError, 'already running'):
            self.browser.start()
        result = self.browser.navigate(self.tab, self.url + '/redirect')
        self.assertEqual(result, {'url': self.url + '/other', 'status': 200})
        self.assertIn('Second page', self.browser.snapshot(self.tab)['text'])
        other = self.browser.new_tab(self.url + '/')
        self.assertEqual(len(self.browser.list_tabs()), 2)
        self.assertEqual(self.browser.list_tabs()[1]['title'], 'Edge Fixture')
        self.browser.close_tab(other)
        with self.assertRaisesRegex(BrowserControlError, 'unknown or closed tab'):
            self.browser.snapshot(other)
        actual_browser = self.browser._browser
        self.browser.close()
        self.assertFalse(actual_browser.is_connected())
        with self.assertRaisesRegex(BrowserControlError, 'not running'):
            self.browser.list_tabs()
        self.browser.close()

    def test_text_input_click_select_press_and_screenshot(self):
        self.assertEqual(self.browser.navigate(self.tab, self.url + '/')['status'], 200)
        self.assertIn('Fixture heading', self.browser.snapshot(self.tab)['text'])
        self.browser.fill(self.tab, 'hello Edge', role='textbox', name='Message')
        self.browser.click(self.tab, role='button', name='Show')
        self.assertIn('hello Edge', self.browser.snapshot(self.tab)['text'])
        self.assertEqual(self.browser.select(self.tab, css='#choice', label='Beta'), ['b'])
        self.browser.fill(self.tab, 'cleared', css='#message')
        self.browser.press(self.tab, 'Control+A', css='#message')
        self.browser.press(self.tab, 'Backspace', css='#message')
        self.browser.click(self.tab, css='#show')
        self.assertNotIn('cleared', self.browser.snapshot(self.tab)['text'])
        image = self.browser.screenshot(self.tab)
        self.assertTrue(image.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertGreater(len(image), 1000)

    def test_profile_data_persists_across_restart(self):
        self.browser.navigate(self.tab, self.url + '/')
        self.browser.click(self.tab, css='#store')
        second = self.browser.new_tab(self.url + '/')
        self.browser.click(second, css='#read')
        self.assertIn('secret marker=secret', self.browser.snapshot(second)['text'])

        # The profile is persistent on purpose: restarting keeps the login state.
        self.browser.close()
        restarted = self.make_browser()
        self.addCleanup(restarted.close)
        new_tab = restarted.start()
        restarted.navigate(new_tab, self.url + '/')
        restarted.click(new_tab, css='#read')
        text = restarted.snapshot(new_tab)['text']
        # localStorage is always persisted on disk; the fixture's cookie is
        # session-scoped, so only the storage marker is asserted across restart.
        self.assertIn('secret', text)
        self.assertNotIn('empty', text)

    def test_other_profile_sees_no_data(self):
        self.browser.navigate(self.tab, self.url + '/')
        self.browser.click(self.tab, css='#store')
        self.browser.click(self.tab, css='#read')
        self.assertIn('secret', self.browser.snapshot(self.tab)['text'])

        # A second profile is checked in its own process: the Playwright sync
        # API only supports one live instance per process.
        other_profile = tempfile.mkdtemp(prefix='edge-test-profile-other-')
        self.addCleanup(shutil.rmtree, other_profile, True)
        script = (
            'import sys, json\n'
            f'sys.path.insert(0, {str(ROOT / "src")!r})\n'
            'from edge_profile_mcp.browser import BrowserController\n'
            'c = BrowserController(headless=True, timeout_ms=20000,\n'
            f'                      profile_dir={other_profile!r})\n'
            'tab = c.start()\n'
            'try:\n'
            f'    c.navigate(tab, {self.url + "/"!r})\n'
            "    c.click(tab, css='#read')\n"
            "    print(json.dumps(c.snapshot(tab)['text']))\n"
            'finally:\n'
            '    c.close()\n'
        )
        completed = subprocess.run([sys.executable, '-c', script], capture_output=True,
                                   text=True, timeout=180)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        text = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertIn('empty no-cookie', text)
        self.assertNotIn('marker=secret', text)

    def test_daily_use_profile_dir_is_rejected(self):
        for path in (r'%LOCALAPPDATA%\Microsoft\Edge\User Data\Default',
                     r'%LOCALAPPDATA%\Microsoft\Edge\User Data',
                     r'%LOCALAPPDATA%\Google\Chrome\User Data\Default'):
            with self.subTest(path=path), self.assertRaisesRegex(
                    BrowserControlError, 'dedicated directory'):
                BrowserController(headless=True, profile_dir=path)

    def test_errors_and_limits(self):
        self.browser.navigate(self.tab, self.url + '/')
        for url in ('file:///etc/passwd', 'http://user:pass@localhost/', ' javascript:alert(1)', 'http://localhost:99999/'):
            with self.subTest(url=url), self.assertRaises(BrowserControlError):
                self.browser.navigate(self.tab, url)
        with self.assertRaisesRegex(BrowserControlError, 'exactly one'):
            self.browser.click(self.tab)
        with self.assertRaisesRegex(BrowserControlError, 'name can only'):
            self.browser.click(self.tab, css='#show', name='Show')
        with self.assertRaises(BrowserControlError):
            self.browser.click(self.tab, css='.duplicate')
        with self.assertRaises(BrowserControlError):
            self.browser.click(self.tab, css='#missing')
        with self.assertRaisesRegex(BrowserControlError, 'exactly one'):
            self.browser.select(self.tab, css='#choice', value='a', label='Alpha')
        self.browser._max_screenshot_bytes = 1
        with self.assertRaisesRegex(BrowserControlError, 'screenshot exceeds'):
            self.browser.screenshot(self.tab)
        self.browser._max_snapshot_chars = 10
        snap = self.browser.snapshot(self.tab)
        self.assertEqual(len(snap['text']), 10)
        self.assertTrue(snap['truncated'])

    def test_tab_limit_and_failed_navigation_cleanup(self):
        self.browser._max_tabs = 1
        with self.assertRaisesRegex(BrowserControlError, 'tab limit'):
            self.browser.new_tab()
        self.browser._max_tabs = 2
        with self.assertRaises(BrowserControlError):
            self.browser.new_tab('http://127.0.0.1:1/')
        self.assertEqual(len(self.browser.list_tabs()), 1)

    def test_cross_thread_access_rejected(self):
        errors = []
        def run():
            try:
                self.browser.list_tabs()
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=run)
        thread.start()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], BrowserControlError)
        self.assertIn('thread', str(errors[0]))


class MCPIntegration(HTTPFixture, unittest.IsolatedAsyncioTestCase):
    async def test_stdio_initialize_list_and_real_tool_calls(self):
        profile = tempfile.mkdtemp(prefix='edge-test-mcp-profile-')
        self.addCleanup(shutil.rmtree, profile, True)
        params = StdioServerParameters(
            command=sys.executable, args=['-m', 'edge_profile_mcp'], cwd=ROOT)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                self.assertEqual(init.serverInfo.name, 'Edge Automation')
                listing = await session.list_tools()
                self.assertEqual({tool.name for tool in listing.tools}, {
                    'start', 'close', 'list_tabs', 'new_tab', 'close_tab', 'navigate',
                    'snapshot', 'screenshot', 'click', 'fill', 'select', 'press',
                    'click_and_wait_download'})

                async def call(name, arguments=None, error=False):
                    result = await asyncio.wait_for(session.call_tool(name, arguments or {}), timeout=30)
                    self.assertEqual(result.isError, error, f'{name}: {result}')
                    return result

                await call('list_tabs', error=True)
                started = await call('start', {'headless': True, 'timeout_ms': 15000,
                                               'profile_dir': profile})
                tab = started.content[0].text
                self.assertTrue(tab.startswith('tab-'), started)
                await call('start', {'headless': True}, error=True)
                nav = await call('navigate', {'tab_id': tab, 'url': self.url + '/'})
                self.assertIn('200', str(nav))
                await call('fill', {'tab_id': tab, 'role': 'textbox', 'name': 'Message', 'value': 'MCP input'})
                await call('click', {'tab_id': tab, 'role': 'button', 'name': 'Show'})
                snapshot = await call('snapshot', {'tab_id': tab})
                self.assertIn('MCP input', str(snapshot))
                await call('select', {'tab_id': tab, 'css': '#choice', 'value': 'b'})
                await call('press', {'tab_id': tab, 'css': '#message', 'key': 'End'})
                image = await call('screenshot', {'tab_id': tab})
                self.assertEqual(image.content[0].mimeType, 'image/png')
                self.assertTrue(base64.b64decode(image.content[0].data).startswith(b'\x89PNG\r\n\x1a\n'))
                tabs = await call('list_tabs')
                self.assertIn('Edge Fixture', str(tabs))
                extra = await call('new_tab', {'url': self.url + '/other'})
                extra_id = extra.content[0].text
                await call('close_tab', {'tab_id': extra_id})
                await call('navigate', {'tab_id': tab, 'url': 'file:///etc/passwd'}, error=True)
                await call('close')
                await call('snapshot', {'tab_id': tab}, error=True)
                await call('close')


if __name__ == '__main__':
    unittest.main(verbosity=2)
