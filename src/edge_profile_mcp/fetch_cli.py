"""Command-line front ends for the content-extraction tools.

These reuse the same :class:`~edge_profile_mcp.browser.BrowserController` as the
MCP server, so they isolate data the same way and need no separate browser
launch.  Three entry points are installed:

``edge-fetch``
    ``edge-fetch links <url> [url ...]``   list anchors as JSON
    ``edge-fetch text <url> [url ...]``    save page text into the cache
    ``edge-fetch file <urls.txt>``         one URL per line
    ``edge-fetch get <url>``               print the text to stdout

``edge-search``
    Search Sogou then Bing inside a real tab and print trusted results.

``edge-search-rss``
    Query Bing's RSS endpoint directly, with no browser at all.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .browser import BrowserControlError, BrowserController
from . import tools


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _read_url_file(path: str) -> list[str]:
    text = Path(path).read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines() if line.strip()]


def fetch_main(argv: list[str] | None = None) -> int:
    """Entry point for ``edge-fetch``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2:
        print(__doc__)
        return 2
    mode, rest = args[0], args[1:]
    if mode not in {"links", "text", "file", "get"}:
        print(f"unknown mode: {mode}\n\n{__doc__}")
        return 2
    urls = _read_url_file(rest[0]) if mode == "file" else rest
    if not urls:
        print("no URLs given")
        return 2

    controller = BrowserController(headless=True)
    try:
        controller.start()
        for url in urls:
            try:
                if mode == "links":
                    _print(controller.fetch_links(url))
                elif mode == "get":
                    result = controller.fetch_text(url, save=False)
                    print(f"# {result['title']}\n# {url}\n")
                    print(result["text"])
                else:
                    result = controller.fetch_text(url, save=True)
                    print(f"[{result['status']}] {result['chars']:>7} "
                          f"{result['source']:<8} {result['title'][:48]}")
                    if result["path"]:
                        print(f"        -> {result['path']}")
            except BrowserControlError as exc:
                print(f"[ERR] {url}: {exc}", file=sys.stderr)
                continue
    finally:
        try:
            controller.close()
        except BrowserControlError:
            pass
    return 0


def search_main(argv: list[str] | None = None) -> int:
    """Entry point for ``edge-search``: Sogou/Bing inside a real tab."""
    args = list(sys.argv[1:] if argv is None else argv)
    trusted_only = True
    if args and args[0] == "--all":
        trusted_only, args = False, args[1:]
    if not args:
        print(__doc__)
        return 2

    controller = BrowserController(headless=True)
    try:
        controller.start()
        for query in args:
            result = controller.search_web(query, trusted_only=trusted_only)
            print(f"\n### {query}  ({result['total']} via {result['engine']})")
            for row in result["results"]:
                print(f"  - {row['title'][:76]}")
                print(f"    {row['url']}")
    finally:
        try:
            controller.close()
        except BrowserControlError:
            pass
    return 0


def search_rss_main(argv: list[str] | None = None) -> int:
    """Entry point for ``edge-search-rss``: Bing RSS, no browser required."""
    import urllib.parse
    import urllib.request
    import xml.etree.ElementTree as ET

    args = list(sys.argv[1:] if argv is None else argv)
    trusted_only = True
    if args and args[0] == "--all":
        trusted_only, args = False, args[1:]
    if not args:
        print("usage: edge-search-rss [--all] \"query\" [\"query\" ...]")
        return 2

    cache = tools.default_cache_dir()
    total = 0
    for query in args:
        url = ("https://cn.bing.com/search?format=rss&count=20&q="
               + urllib.parse.quote(query))
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            raw = urllib.request.urlopen(request, timeout=25).read()
        except Exception as exc:  # network errors are reported, not raised
            print(f"[ERR] {query}: {type(exc).__name__} {exc}", file=sys.stderr)
            continue
        rows = tools.parse_rss_items(raw.decode("utf-8", "replace"), query,
                                     trusted_only=trusted_only)
        print(f"\n### {query}  ({len(rows)} trusted)" if trusted_only
              else f"\n### {query}  ({len(rows)})")
        for row in rows:
            print(f"  - {row['title'][:70]}")
            print(f"    {row['url']}")
        if rows:
            tools.append_jsonl(cache, "search.jsonl", rows)
        total += len(rows)
    print(f"\n[total] {total} -> {Path(cache) / 'search.jsonl'}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(fetch_main())
