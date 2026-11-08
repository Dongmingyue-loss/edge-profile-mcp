"""Probe how search engines respond to automated requests.

Records, per engine: HTTP status, final URL (redirect detection), page title,
result-container counts, challenge markers, cookies and ``navigator.webdriver``.

This is a diagnostic, not part of the server. It launches a throwaway profile so
it never touches real data.

Usage:
    python scripts/probe_engines.py "your query"
    python scripts/probe_engines.py            # uses a default query
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from edge_profile_mcp.browser import BrowserController  # noqa: E402

DEFAULT_QUERY = "大学生健康教育指南"

ENGINES = {
    "bing": ("https://cn.bing.com/search?q=", ["#b_results li.b_algo"]),
    "bing-rss": None,  # handled without a page
    "so360": ("https://www.so.com/s?q=", ["li.res-list", "a[data-mdurl]"]),
    "baidu": ("https://www.baidu.com/s?wd=", ["#content_left .result", ".c-container"]),
    "yandex": ("https://yandex.com/search/?text=", [".serp-item", ".Organic"]),
    "sogou": ("https://www.sogou.com/web?query=", [".vrwrap", ".rb"]),
    "ddg-html": ("https://html.duckduckgo.com/html/?q=", [".result", ".result__a"]),
    "brave": ("https://search.brave.com/search?q=", [".snippet", "#results"]),
}

CHALLENGE_MARKERS = (
    "captcha", "antispider", "verify", "challenge", "smartcaptcha", "showcaptcha",
    "wappass", "安全检查", "验证码", "unusual traffic", "not a robot", "安全验证",
    "bots use duckduckgo too",
)


def probe_page(controller: BrowserController, name: str, url: str,
               containers: list[str]) -> dict:
    """Load one engine URL in a fresh tab and report what came back."""
    assert controller._context is not None
    page = controller._context.new_page()
    record: dict = {"engine": name}
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(3000)
        record["status"] = response.status if response else None
        record["final_url"] = page.url[:200]
        record["redirected"] = page.url.split("?")[0] != url.split("?")[0]
        record["title"] = page.title()[:100]
        try:
            record["body_head"] = page.locator("body").inner_text(timeout=4000)[:200]
        except Exception:
            record["body_head"] = ""
        record["containers"] = {sel: page.locator(sel).count() for sel in containers}
        html = page.content()
        record["markers"] = sorted({m for m in CHALLENGE_MARKERS if m in html.lower()})
        record["cookies"] = sorted(c["name"] for c in controller._context.cookies())
        record["webdriver"] = page.evaluate("navigator.webdriver")
        record["html_len"] = len(html)
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    finally:
        try:
            page.close()
        except Exception:
            pass
    return record


def probe_rss(controller: BrowserController, query: str) -> dict:
    """Check whether Bing's RSS endpoint degrades to single-character matching."""
    assert controller._context is not None
    url = f"https://cn.bing.com/search?format=rss&count=20&q={quote(query)}"
    record: dict = {"engine": "bing-rss"}
    try:
        response = controller._context.request.get(url, timeout=30_000)
        body = response.text()
        from edge_profile_mcp import tools

        raw_rows = tools.parse_rss_items(body, query, trusted_only=False)
        kept = tools.parse_rss_items(body, query, trusted_only=True)
        record.update({
            "status": response.status,
            "items": len(raw_rows),
            "relevant_items": len(kept),
            "first_titles": [row["title"][:60] for row in raw_rows[:3]],
        })
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    return record


def main() -> int:
    query = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUERY
    controller = BrowserController(
        headless=True, timeout_ms=30_000,
        profile_dir=tempfile.mkdtemp(prefix="probe-profile-"),
        cache_dir=tempfile.mkdtemp(prefix="probe-cache-"),
    )
    try:
        controller.start()
        for name, spec in ENGINES.items():
            if spec is None:
                print(json.dumps(probe_rss(controller, query), ensure_ascii=False, indent=1))
                continue
            prefix, containers = spec
            url = prefix + quote(query)
            print(json.dumps(probe_page(controller, name, url, containers),
                             ensure_ascii=False, indent=1))
            print("-" * 70)
    finally:
        try:
            controller.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
