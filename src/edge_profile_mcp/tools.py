"""Pure helpers for turning fetched pages into clean text and finding sources.

Nothing in this module touches a browser.  It is deliberately side-effect free
so the parsing rules can be unit tested without launching Edge.

The encoding handling exists because several Chinese government sites declare
UTF-8 but are actually GB18030, and Edge's text decoder turns them into
mojibake.  Fetching raw bytes and decoding explicitly (``decode_bytes``) avoids
that whole class of error.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Iterable

# Tags whose entire content is noise for reading an article.
_DROP_TAGS = r"script|style|noscript|iframe|svg|nav|header|footer|form|select|button|aside"

# Tags that imply a line break in the extracted text.
_BLOCK_TAGS = r"p|div|br|tr|li|h1|h2|h3|h4|h5|h6|section|article|table|td|th|blockquote|dd|dt|pre"

# Narrow the extraction to a main content container when the site has one.
_CONTENT_PATTERNS = (
    r'<div[^>]+id="UCAP-CONTENT"[^>]*>(.*?)</div>\s*(?:<div|<footer)',
    r'<div[^>]+class="[^"]*(?:TRS_Editor|Custom_UnionStyle|article|content)[^"]*"[^>]*>(.*?)</div>\s*</div>',
    r"<article[^>]*>(.*?)</article>",
    r'<div[^>]+class="[^"]*(?:markdown-body|post-content|entry-content)[^"]*"[^>]*>(.*?)</div>',
)

_ENTITIES = (
    ("&nbsp;", " "),
    ("&emsp;", " "),
    ("&ensp;", " "),
    ("&amp;", "&"),
    ("&lt;", "<"),
    ("&gt;", ">"),
    ("&quot;", '"'),
    ("&#39;", "'"),
    ("&apos;", "'"),
    ("&ldquo;", "\u201c"),
    ("&rdquo;", "\u201d"),
    ("&mdash;", "\u2014"),
)

# Domains treated as authoritative by default when filtering search results.
DEFAULT_TRUSTED_SUFFIXES: tuple[str, ...] = (
    ".gov.cn",
    ".chinacdc.cn",
    ".cma.org.cn",
    ".org.cn",
    ".edu.cn",
    ".ac.cn",
    "who.int",
    "cdc.gov",
    "nhs.uk",
    "nih.gov",
    "nature.com",
    "sciencedirect.com",
    "springer.com",
)


def decode_bytes(raw: bytes) -> str:
    """Decode an HTTP body, honouring a declared GB charset and falling back safely."""
    head = raw[:4096].lower()
    declares_gb = b"charset=gb" in head or b'charset="gb' in head
    if declares_gb:
        return raw.decode("gb18030", "replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("gb18030", "replace")


def page_title(html: str) -> str:
    """Return a whitespace-collapsed ``<title>``, or an empty string."""
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()


def html_to_text(html: str) -> str:
    """Convert an HTML document into plain, line-oriented text."""
    body = html
    for pattern in _CONTENT_PATTERNS:
        match = re.search(pattern, html, re.S | re.I)
        if match and len(match.group(1)) > 400:
            body = match.group(1)
            break
    body = re.sub(rf"<({_DROP_TAGS})[^>]*>.*?</\1>", " ", body, flags=re.S | re.I)
    body = re.sub(rf"<({_BLOCK_TAGS})[^>]*>", "\n", body, flags=re.I)
    body = re.sub(r"<[^>]+>", "", body)
    for entity, replacement in _ENTITIES:
        body = body.replace(entity, replacement)
    body = body.replace("\u3000", " ")
    lines = (re.sub(r"[ \t]+", " ", line).strip() for line in body.splitlines())
    return "\n".join(line for line in lines if line)


def extract_links(html: str) -> list[dict[str, str]]:
    """Return ``[{"href": ..., "text": ...}]`` for every anchor in the document."""
    found = re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S | re.I)
    links: list[dict[str, str]] = []
    for href, inner in found:
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", inner)).strip()
        links.append({"href": href, "text": text})
    return links


def slugify_url(url: str, *, limit: int = 60) -> str:
    """Build a stable, filesystem-safe cache name from a URL."""
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    tail = re.sub(r"[^0-9A-Za-z._-]+", "_", url.rstrip("/").rsplit("/", 1)[-1])[:limit]
    return f"{tail or 'page'}_{digest}"


def is_trusted(url: str, suffixes: Iterable[str] = DEFAULT_TRUSTED_SUFFIXES) -> bool:
    """Return whether the URL's host ends with one of the trusted suffixes."""
    from urllib.parse import urlsplit

    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    return any(host == suffix.lstrip(".") or host.endswith(suffix) for suffix in suffixes)


# Hosts that are So.com's own surfaces (navigation portal, video, wenku, image,
# news aggregator). Results pointing at these are cards, not organic hits.
_SO360_INTERNAL_SUFFIXES: tuple[str, ...] = (
    "so.com",
    "360.com",
    "360.cn",
    "360kan.com",
)


def _is_so360_internal(host: str) -> bool:
    """Return whether the host belongs to So.com or another 360 property."""
    host = (host or "").lower()
    if not host:
        return True
    return any(host == suffix or host.endswith("." + suffix)
               for suffix in _SO360_INTERNAL_SUFFIXES)


def parse_so360_containers(rows: Iterable[dict[str, Any]], *, limit: int,
                           trusted_only: bool,
                           suffixes: Iterable[str] = DEFAULT_TRUSTED_SUFFIXES
                           ) -> list[dict[str, str]]:
    """Map 360 (So.com) result anchors into result records.

    So.com wraps outbound links in ``/link?m=...`` redirects, but every wrapped
    anchor carries the real target in ``data-mdurl``. Preferring that attribute
    avoids one redirect per result, which would be slow and indistinguishable
    from the click-tracking the site expects.

    Extraction is driven by ``a[data-mdurl]`` rather than ``li.res-list``: the
    ``li`` containers hold only a handful of items (aggregate cards for news,
    images and wenku), while the organic results live in other blocks. Rows whose
    target is a 360 property are dropped.
    """
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        href = str(row.get("realUrl") or "").strip() or str(row.get("href") or "").strip()
        title = re.sub(r"\s+", " ", str(row.get("title") or "")).strip()
        if not href.startswith("http") or len(title) < 8 or href in seen:
            continue
        if _is_so360_internal(urlsplit_safe(href)):
            continue
        if trusted_only and not is_trusted(href, suffixes):
            continue
        seen.add(href)
        out.append({
            "title": title[:120],
            "url": href,
            "snippet": re.sub(r"\s+", " ", str(row.get("snippet") or "")).strip()[:300],
        })
        if len(out) >= limit:
            break
    return out


def urlsplit_safe(url: str) -> str:
    """Return the lowercase host of a URL, or an empty string when malformed."""
    from urllib.parse import urlsplit

    try:
        return (urlsplit(url).hostname or "")
    except ValueError:
        return ""


def search_urls(query: str) -> list[tuple[str, str]]:
    """Return ``(engine_name, url)`` pairs, in order of preference.

    Only useful as a direct-navigation fallback: several engines truncate the
    query when it arrives as a bare URL without a browsing context, so the real
    path is :func:`search_plans` (type into the search box instead).
    """
    from urllib.parse import quote

    encoded = quote(query)
    return [
        ("bing", f"https://cn.bing.com/search?q={encoded}&count=20"),
        ("sogou", f"https://www.sogou.com/web?query={encoded}"),
        ("bing-rss", f"https://cn.bing.com/search?format=rss&count=20&q={encoded}"),
    ]


# Search engines differ in how they accept a query, and each has its own
# anti-automation behaviour (measured, see ANTI-BOT.md):
#
#   Bing     Intermittently degrades a bare /search?q= request to results about
#            the query's first character only. Submitting the home-page form
#            improves this substantially but is not a hard guarantee.
#   Baidu    Redirects to wappass.baidu.com/html/captcha with an image-rotation
#            slider. Not solvable programmatically; skipped, not retried.
#   Yandex   Redirects to yandex.com/showcaptcha with SmartCaptcha and sets a
#            `spravka` cookie. The cookie is issued only after a human solves it.
#   Sogou    Serves /antispider/ with a click-the-characters captcha under load.
#   So.com   Works with a plain URL, including from a cold profile. Results are
#            wrapped in so.com redirects that carry the real target in a
#            data-mdurl attribute, so no redirect needs to be followed.
#
# So the working order is: Bing via form submission, then 360 (So.com) via URL,
# then Sogou via form submission, then Bing RSS (no browsing context,
# relevance-checked), and finally a clear "all engines blocked" report.

SEARCH_PLANS: tuple[dict[str, str], ...] = (
    {"engine": "bing", "home": "https://cn.bing.com/", "input": "input[name=q]",
     "results": "#b_results li.b_algo", "mode": "form"},
    {"engine": "so360", "home": "https://www.so.com/", "input": "#input",
     "results": "li.res-list", "mode": "url"},
    {"engine": "sogou", "home": "https://www.sogou.com/", "input": "#query",
     "results": ".vrwrap, .rb", "mode": "form"},
)

# Query prefixes for the engines that accept a plain URL.
SEARCH_URL_TEMPLATES: dict[str, str] = {
    "so360": "https://www.so.com/s?q={query}",
}


def search_url(engine: str, query: str) -> str:
    """Build a direct search URL for engines that accept one."""
    from urllib.parse import quote

    return SEARCH_URL_TEMPLATES[engine].format(query=quote(query))


def search_plans() -> tuple[dict[str, str], ...]:
    """Return the engine plans to try, in order."""
    return SEARCH_PLANS


def looks_like_challenge(page_url: str, title: str, body_text: str) -> bool:
    """Detect captcha, consent or access-denied interstitials.

    Treating an interstitial as "no results" keeps the tool honest instead of
    returning footer and licence links as if they were search hits.
    """
    url = (page_url or "").lower()
    heading = (title or "").strip().lower()
    if any(marker in url for marker in (
            "captcha", "/antispider", "/verify", "wappass", "showcaptcha", "consent.")):
        return True
    if any(marker in heading for marker in (
            "captcha", "验证码", "access denied", "安全验证", "not a robot")):
        return True
    probe = (body_text or "")[:3000].lower()
    return any(marker in probe for marker in (
        "请输入验证码", "unusual traffic", "安全验证", "请完成下方验证",
        "confirm that you are not a robot", "not a robot",
        "bots use duckduckgo too", "complete the following challenge"))


def query_terms(query: str) -> list[str]:
    """Split a query into terms worth matching, dropping one-character noise."""
    return [term for term in re.split(r"\s+", query.strip()) if len(term) >= 2]


def is_relevant(text: str, query: str) -> bool:
    """Return whether ``text`` mentions at least one meaningful query term."""
    terms = query_terms(query)
    if not terms:
        return True
    return any(term in (text or "") for term in terms)


def merge_cjk_terms(query: str) -> list[str]:
    """Return additional terms for CJK queries that contain no spaces.

    Bing accepts ``大学生健康教育指南`` but a human would search
    ``大学生 健康 教育 指南``; generating 2-character windows catches results
    that only match part of the phrase.
    """
    if " " in query.strip():
        return []
    cjk = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", query)
    if len(cjk) < 4:
        return []
    return [cjk[i:i + 2] for i in range(0, len(cjk) - 1, 2)][:4]


def parse_bing_containers(rows: Iterable[dict[str, Any]], *, limit: int,
                          trusted_only: bool,
                          suffixes: Iterable[str] = DEFAULT_TRUSTED_SUFFIXES
                          ) -> list[dict[str, str]]:
    """Map Bing ``li.b_algo`` rows into result records."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        href = str(row.get("href") or "").strip()
        title = re.sub(r"\s+", " ", str(row.get("title") or "")).strip()
        if not href.startswith("http") or not title or href in seen:
            continue
        if trusted_only and not is_trusted(href, suffixes):
            continue
        seen.add(href)
        snippet = re.sub(r"\s+", " ", str(row.get("snippet") or "")).strip()
        out.append({"title": title[:120], "url": href, "snippet": snippet[:300]})
        if len(out) >= limit:
            break
    return out


def parse_search_results(anchors: Iterable[dict[str, str]], *, limit: int,
                         trusted_only: bool, query: str = "",
                         suffixes: Iterable[str] = DEFAULT_TRUSTED_SUFFIXES) -> list[dict[str, str]]:
    """Turn raw result anchors into rows, dropping page furniture.

    Search result pages mix real hits with navigation, footer and licence links.
    Real hits are long-labelled, on a deeper path, and — the decisive filter —
    actually mention the query.  Without the relevance check a degraded or
    blocked engine returns nothing but boilerplate that still looks like a hit.
    """
    from urllib.parse import urlsplit

    terms = [term for term in re.split(r"\s+", query.strip()) if len(term) >= 2]
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for anchor in anchors:
        href = str(anchor.get("href") or "").strip()
        label = re.sub(r"\s+", " ", str(anchor.get("text") or "")).strip()
        if not href.startswith("http") or len(label) < 8 or href in seen:
            continue
        try:
            parts = urlsplit(href)
        except ValueError:
            continue
        if len(parts.path.strip("/")) < 3:
            continue
        if trusted_only and not is_trusted(href, suffixes):
            continue
        if terms and not any(term in label for term in terms):
            continue
        seen.add(href)
        rows.append({"title": label[:120], "url": href})
        if len(rows) >= limit:
            break
    return rows


def parse_rss_items(xml_text: str, query: str, *, trusted_only: bool = True,
                    suffixes: Iterable[str] = DEFAULT_TRUSTED_SUFFIXES) -> list[dict[str, str]]:
    """Parse a Bing RSS response into result rows, optionally filtered by domain."""
    from xml.etree import ElementTree

    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []

    # Bing's RSS endpoint degrades to matching a single leading character when
    # the request carries no search context, which produces useless hits.  Only
    # keep rows that actually relate to the query.
    terms = [term for term in re.split(r"\s+", query.strip()) if len(term) >= 2]
    rows: list[dict[str, str]] = []
    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip()
        title = (item.findtext("title") or "").strip()
        if not link or not title:
            continue
        if trusted_only and not is_trusted(link, suffixes):
            continue
        description = re.sub(r"<[^>]+>", " ", item.findtext("description") or "").strip()
        haystack = f"{title} {description}"
        if terms and not any(term in haystack for term in terms):
            continue
        rows.append({
            "title": title,
            "url": link,
            "description": re.sub(r"\s+", " ", description)[:400],
            "query": query,
        })
    return rows


def default_cache_dir() -> str:
    """Return the directory where fetched text and search results are cached."""
    override = os.environ.get("EDGE_AUTOMATION_CACHE_DIR")
    if override and override.strip():
        return os.path.abspath(os.path.expanduser(os.path.expandvars(override.strip())))
    base = os.environ.get("LOCALAPPDATA")
    if base and base.strip():
        return os.path.join(base.strip(), "edge-automation-mcp", "cache")
    return os.path.join(os.path.expanduser("~"), ".edge-automation-mcp", "cache")


def write_cached_text(cache_dir: str, url: str, title: str, text: str) -> Path:
    """Write one page's text into the cache and return the file path."""
    directory = Path(cache_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (slugify_url(url) + ".txt")
    target.write_text(f"URL: {url}\nTITLE: {title}\n\n{text}", encoding="utf-8")
    return target


def append_jsonl(cache_dir: str, name: str, rows: Iterable[dict[str, Any]]) -> Path:
    """Append rows as JSON Lines to ``cache_dir/name`` and return the path."""
    import json

    directory = Path(cache_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    with target.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return target
