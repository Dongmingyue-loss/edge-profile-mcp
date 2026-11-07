"""MCP server exposing a real Edge browser on a private, isolated profile.

Run with the ``edge-profile-mcp`` console script, ``python -m
edge_profile_mcp``, or by pointing an MCP client at this module.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sys
import threading
from typing import Any

from .browser import BrowserControlError, BrowserController
from mcp.server.fastmcp import FastMCP, Image


class BrowserWorker:
    """Own the controller and all its operations on one dedicated thread."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="edge")
        self._lock = threading.Lock()
        self._closing = False
        self._controller: BrowserController | None = None

    def _invoke(self, operation: str, **kwargs: Any) -> Any:
        if operation == "start":
            if self._controller is not None:
                raise BrowserControlError("browser session is already running")
            controller = BrowserController(**kwargs)
            tab_id = controller.start()
            self._controller = controller
            return tab_id
        if operation == "close":
            controller, self._controller = self._controller, None
            if controller is not None:
                controller.close()
            return None
        if self._controller is None:
            raise BrowserControlError("browser session is not running; call start first")
        return getattr(self._controller, operation)(**kwargs)

    def call(self, operation: str, **kwargs: Any) -> Any:
        with self._lock:
            if self._closing:
                raise BrowserControlError("browser server is shutting down")
            future = self._executor.submit(self._invoke, operation, **kwargs)
        try:
            return future.result()
        except BrowserControlError as exc:
            detail = str(exc)
            raise BrowserControlError(detail[:4000] + ("... [truncated]" if len(detail) > 4000 else "")) from None

    def shutdown(self) -> None:
        with self._lock:
            if self._closing:
                return
            self._closing = True
            future = self._executor.submit(self._invoke, "close")
        try:
            future.result()
        finally:
            self._executor.shutdown(wait=True)


worker = BrowserWorker()
mcp = FastMCP("Edge Automation")


@mcp.tool()
def start(headless: bool = False, timeout_ms: int = 10_000,
          max_tabs: int = 20, max_snapshot_chars: int = 30_000,
          max_screenshot_bytes: int = 5_000_000,
          download_dir: str | None = None,
          profile_dir: str | None = None,
          cache_dir: str | None = None) -> str:
    """Start the real installed Edge on a private, persistent profile.

    Cookies, logins, history and settings live in profile_dir and never mix with
    the Edge profile you use day to day. The directory defaults to
    %LOCALAPPDATA%\\edge-automation-mcp\\profile (overridable with the
    EDGE_AUTOMATION_PROFILE_DIR environment variable) and survives restarts.
    Pass download_dir to allow file downloads; they are saved only there.
    cache_dir stores fetched page text and search results.
    """
    return worker.call("start", headless=headless, timeout_ms=timeout_ms,
                       max_tabs=max_tabs, max_snapshot_chars=max_snapshot_chars,
                       max_screenshot_bytes=max_screenshot_bytes,
                       download_dir=download_dir, profile_dir=profile_dir,
                       cache_dir=cache_dir)


@mcp.tool()
def close() -> str:
    """Close the browser session; it can be started again."""
    worker.call("close")
    return "Browser session closed."


@mcp.tool()
def list_tabs() -> list[dict[str, str]]:
    """List open tabs with their IDs, URLs, and titles."""
    return worker.call("list_tabs")


@mcp.tool()
def new_tab(url: str | None = None) -> str:
    """Open a tab, optionally navigating to an HTTP(S) URL."""
    return worker.call("new_tab", url=url)


@mcp.tool()
def close_tab(tab_id: str) -> str:
    """Close the specified tab."""
    worker.call("close_tab", tab_id=tab_id)
    return "Tab closed."


@mcp.tool()
def navigate(tab_id: str, url: str) -> dict[str, Any]:
    """Navigate a tab to an HTTP(S) URL and return its final URL and status."""
    return worker.call("navigate", tab_id=tab_id, url=url)


@mcp.tool()
def snapshot(tab_id: str) -> dict[str, Any]:
    """Return a bounded accessibility snapshot and its truncation flag."""
    return worker.call("snapshot", tab_id=tab_id)


@mcp.tool()
def screenshot(tab_id: str) -> Image:
    """Return a size-limited PNG screenshot of the current viewport."""
    return Image(data=worker.call("screenshot", tab_id=tab_id), format="png")


@mcp.tool()
def click(tab_id: str, css: str | None = None, role: str | None = None,
          name: str | None = None) -> str:
    """Click one element; specify exactly one of css or role, with optional role name."""
    worker.call("click", tab_id=tab_id, css=css, role=role, name=name)
    return "Clicked."


@mcp.tool()
def fill(tab_id: str, value: str, css: str | None = None,
         role: str | None = None, name: str | None = None) -> str:
    """Fill one element; specify exactly one of css or role, with optional role name."""
    worker.call("fill", tab_id=tab_id, value=value, css=css, role=role, name=name)
    return "Filled."


@mcp.tool()
def select(tab_id: str, value: str | None = None, label: str | None = None,
           css: str | None = None, role: str | None = None,
           name: str | None = None) -> list[str]:
    """Select an option by value or label; target it by css or role and optional name."""
    return worker.call("select", tab_id=tab_id, value=value, label=label,
                       css=css, role=role, name=name)


@mcp.tool()
def press(tab_id: str, key: str, css: str | None = None,
          role: str | None = None, name: str | None = None) -> str:
    """Press a key on one element, targeted by css or role and optional name."""
    worker.call("press", tab_id=tab_id, key=key, css=css, role=role, name=name)
    return "Key pressed."


@mcp.tool()
def click_and_wait_download(tab_id: str, css: str | None = None,
                            role: str | None = None, name: str | None = None,
                            timeout_ms: int = 30_000) -> dict[str, Any]:
    """Click an element that triggers a file download and save it to download_dir."""
    return worker.call("click_and_wait_download", tab_id=tab_id, css=css,
                       role=role, name=name, timeout_ms=timeout_ms)


@mcp.tool()
def fetch_text(url: str, save: bool = True, wait_ms: int = 2500) -> dict[str, Any]:
    """Fetch one page as clean text, decoding GB18030 and UTF-8 correctly.

    Prefers the rendered DOM and falls back to a raw-body parse when the page is
    too thin. With save=True the text is cached under the cache directory and the
    returned path is included.
    """
    return worker.call("fetch_text", url=url, save=save, wait_ms=wait_ms)


@mcp.tool()
def fetch_links(url: str, limit: int = 200) -> dict[str, Any]:
    """Return the anchors on a page as {href, text} records, capped by limit."""
    return worker.call("fetch_links", url=url, limit=limit)


@mcp.tool()
def search_web(query: str, trusted_only: bool = True, limit: int = 12) -> dict[str, Any]:
    """Search Sogou then Bing in a real tab and return matching results.

    With trusted_only=True only government, academic and major health domains are
    kept. Falls back to Bing RSS when neither search page yields results.
    """
    return worker.call("search_web", query=query, trusted_only=trusted_only, limit=limit)


def main() -> None:
    """Run the MCP server over stdio and always try to close the browser."""
    try:
        mcp.run(transport="stdio")
    finally:
        try:
            worker.shutdown()
        except Exception as exc:
            print(f"Browser cleanup failed: {str(exc)[:4000]}", file=sys.stderr)


if __name__ == "__main__":
    main()
