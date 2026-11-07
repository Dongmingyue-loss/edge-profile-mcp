# edge-profile-mcp

[![CI](https://github.com/Dongmingyue-loss/edge-profile-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Dongmingyue-loss/edge-profile-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

[中文文档](README.zh-CN.md)

An MCP server that drives **the real Microsoft Edge installed on your machine**,
running in **a private, persistent profile** that is completely separated from
your daily browser data.

The point is simple: you get a normal-looking Edge window with working
extensions, audio, scrollbars and real window sizing — but its cookies, logins,
history and settings live in their own directory. Your everyday Edge profile is
never read and never modified, and the two browsers can run at the same time.

## Why a separate profile instead of a temp context

| | Temp context (typical Playwright MCP) | This project |
| --- | --- | --- |
| Browser binary | Real Edge | Real Edge |
| Data location | In-memory, discarded on close | Dedicated directory on disk |
| Login survives restart | No | **Yes** |
| Coexists with your daily Edge | Yes | Yes |
| Window looks like a normal browser | No (automation bar, muted, no scrollbars, extensions off) | **Yes** |
| Risk of touching daily data | None | None — paths are validated |

The trade-off is explicit: **the automation profile persists**. If you sign in
inside it, that session is still there next time. Delete the directory to reset.

## Install

### As a ZCode / Claude plugin

Add this repository as a plugin marketplace, then install `edge-profile-mcp`.
The server is launched automatically through `uv run`, so no manual Python
environment is required.

### As a Python package

```powershell
pip install git+https://github.com/Dongmingyue-loss/edge-profile-mcp.git
# or, for development:
git clone https://github.com/Dongmingyue-loss/edge-profile-mcp.git
cd edge-profile-mcp
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

Requirements: **Windows**, Python 3.10+, and Microsoft Edge installed
system-wide. `pip install` does not install Edge — install it the normal way
first.

### Manual MCP configuration

```json
{
  "mcpServers": {
    "edge-profile-mcp": {
      "command": "edge-profile-mcp",
      "timeoutMs": 60000
    }
  }
}
```

## Command line

An interactive line-oriented session is bundled for debugging:

```powershell
edge-profile-cli
```

```text
start {}
navigate {"tab_id": "tab-1", "url": "https://example.com"}
snapshot {"tab_id": "tab-1"}
screenshot {"tab_id": "tab-1", "path": "screenshot.png"}
close
exit
```

Each line is a command name plus an optional JSON object. Results go to stdout
as JSON, errors to stderr. Type `help` for all commands.

## Tools

MCP and CLI share operation names and most parameters. Differences: the CLI
`start` returns `{"tab_id": ...}` while MCP returns the id string, and the CLI
`screenshot` requires a `path`.

| Tool | Parameters | Result |
| --- | --- | --- |
| `start` | optional `headless`, `profile_dir`, `download_dir`, `timeout_ms`, `max_tabs`, `max_snapshot_chars`, `max_screenshot_bytes` | first tab id |
| `close` | — | confirmation; session can be started again |
| `list_tabs` | — | `[{id, url, title}]` |
| `new_tab` | optional `url` | new tab id |
| `close_tab` | `tab_id` | confirmation |
| `navigate` | `tab_id`, `url` | `{url, status}` after DOMContentLoaded |
| `snapshot` | `tab_id` | `{url, text, truncated}` — accessibility tree |
| `screenshot` | `tab_id` | viewport PNG (CLI also needs `path`) |
| `click` | `tab_id` + selector | confirmation |
| `fill` | `tab_id`, `value` + selector | confirmation |
| `select` | `tab_id` + selector + one of `value`/`label` | selected values |
| `press` | `tab_id`, `key` + selector | confirmation |
| `click_and_wait_download` | `tab_id` + selector, optional `timeout_ms` | `{path, filename, suggested_filename}` |
| `fetch_text` | `url`, optional `save`, `wait_ms` | `{url, status, title, chars, source, path, text}` |
| `fetch_links` | `url`, optional `limit` | `{url, status, title, total, truncated, links}` |
| `search_web` | `query`, optional `trusted_only`, `limit` | `{query, engine, total, results}` |

### Selector rules

Specify **exactly one** of `css` or `role`. `name` may only be used with `role`
and matches the accessible name exactly. A selector matching several elements
fails rather than guessing.

```text
fill {"tab_id": "tab-1", "css": "#query", "value": "example"}
click {"tab_id": "tab-1", "role": "button", "name": "Search"}
select {"tab_id": "tab-1", "css": "select", "label": "Option"}
press {"tab_id": "tab-1", "css": "input", "key": "Enter"}
```

### Defaults and limits

`start` defaults: visible browser, 10 000 ms timeout, 20 tabs, 30 000 snapshot
characters, 5 000 000 screenshot bytes. Ranges: timeout 1–60 000 ms, tabs 1–100,
snapshot 1–200 000 chars, screenshot 1–20 000 000 bytes. Oversized screenshots
error; oversized snapshots are truncated with `truncated: true`.

Headless mode uses a 1280×720 viewport. Headed mode maximises a real window and
uses the actual window size.

## Content extraction and search

Three tools go beyond page interaction, aimed at research work: turning pages
into text, harvesting links, and finding sources.

### `fetch_text` — clean article text

Fetches the page through the browser's own request context, decodes the raw
bytes explicitly, and prefers the rendered DOM. `source` reports which path was
taken: `rendered` or `static`.

Explicit decoding matters for real sites: several Chinese government pages
declare UTF-8 but serve GB18030, and Edge's text decoder turns those into
mojibake. Reading the body bytes and decoding with `gb18030` when the declared
charset is GB avoids that class of error entirely.

With `save=True` the text is written to the cache directory and `path` is
returned; the file starts with `URL:` and `TITLE:` header lines.

### `fetch_links` — anchors on a page

Returns `[{href, text}]`, capped by `limit`. Parsed in the DOM first, so anchors
created by scripts are included, with a raw-HTML regex as fallback.

### `search_web` — find sources

Searches by **typing into the engine's own search box** and submitting the form,
then filters results to trusted domains (government, academic, major health).

The form submission is not cosmetic. Measured behaviour: a bare
`/search?q=<full query>` request to Bing returns HTTP 200 with 10 result
containers, but every result is about the query's **first character** — a silent
degradation that looks like success. Submitting the home-page form returns correct
results (10/10 relevant across repeated trials). See [ANTI-BOT.md](ANTI-BOT.md)
for the measurements and for what Baidu, Yandex and Sogou do.

Every result set passes a relevance check before being accepted. When engines
serve captchas or truncated results, the tool returns `total: 0` with
`blocked_engines` / `degraded_engines` and a hint, rather than passing page
furniture off as results.

```text
search_web("流感疫苗 接种")  → engine: bing
  - 中国疾病预防控制中心发布《中国流感疫苗预防接种技术指南…》
    https://www.chinacdc.cn/jkts/202609/t20260903_1839780.html
```

## Command line tools

Beyond the interactive CLI, three research commands are installed:

```powershell
edge-fetch links <url> [url ...]     # anchors as JSON
edge-fetch text <url> [url ...]      # save page text into the cache
edge-fetch get <url>                 # print text to stdout
edge-fetch file urls.txt             # one URL per line

edge-search "query" ["query" ...]    # Bing/Sogou in a real tab
edge-search --all "query"            # do not restrict to trusted domains

edge-search-rss "query"              # Bing RSS, no browser at all
```

They share the same controller, profile and cache as the MCP server, so no
separate browser is launched and data isolation is identical.

Cache location defaults to `%LOCALAPPDATA%\edge-automation-mcp\cache`
(override with `EDGE_AUTOMATION_CACHE_DIR` or `cache_dir` on `start`).

## Profile and download locations

| Setting | Default | Override |
| --- | --- | --- |
| Profile | `%LOCALAPPDATA%\edge-automation-mcp\profile` | `profile_dir` param or `EDGE_AUTOMATION_PROFILE_DIR` |
| Downloads | disabled | `download_dir` param or `EDGE_AUTOMATION_DOWNLOAD_DIR` |
| Cache | `%LOCALAPPDATA%\edge-automation-mcp\cache` | `cache_dir` param or `EDGE_AUTOMATION_CACHE_DIR` |

Pointing `profile_dir` at a daily-driver browser data directory (Edge, Chrome,
Brave, Firefox) is rejected. One profile directory can be open by only one Edge
instance at a time; a second `start` on it reports `profile_dir is already in
use`.

Downloads are saved only inside `download_dir`, with the server-provided filename
sanitised into a path-free legal name; existing files are never overwritten
(`-1`, `-2`, … are appended instead).

## Session lifecycle

```
start  → launch Edge on the private profile, adopt the blank first tab
  ⋮
close  → context → browser → Playwright, in that order
```

Repeated `start` without `close` errors. `BrowserController.close()` is
idempotent and also runs after a partial startup failure. Process crashes, forced
termination or power loss cannot guarantee cleanup, so a stray Edge process may
remain and must be checked by the operator.

All browser calls run on one dedicated thread; the controller refuses calls from
any other thread. This is required because Playwright's synchronous API is bound
to its creating thread, and it means operations are serialised, not concurrent.

## Security model

**What is guaranteed**

- Never reads or writes your daily Edge profile; daily-profile paths are rejected.
- stdio only — no listening port, no HTTP surface.
- No arbitrary JavaScript execution tool; page content is read as an
  accessibility snapshot.
- Absolute `http`/`https` URLs only; embedded credentials, control characters and
  surrounding whitespace are rejected; non-HTTP(S) top-level navigation is
  blocked.
- All inputs and outputs are bounded; downloads are confined to one directory.

**What is explicitly not covered**

- **Not a sandbox.** The browser has the privileges and network access of the
  account running the server.
- **Not SSRF protection.** No allow-list for public, private, loopback or
  link-local targets, and redirects are not network-isolated.
- **Page content is untrusted.** Snapshots, titles and screenshots may contain
  prompt injection. Never treat page text as instructions or authorisation.
- **Actions have real side effects.** Submitted forms, sent requests and remote
  changes are not undone by closing the session. Timeouts do not prove that
  nothing happened — verify before retrying.
- **A signed-in automation profile is a credential store.** Anyone who can start
  the server can use those sessions.

See [SECURITY.md](SECURITY.md) for the full policy and private reporting route.

## Development

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
```

The suite starts real Edge instances against a local `http.server` fixture — no
public internet access is needed. `tests/test_tools.py` covers the parsing rules
with no browser at all and runs in under a second. See
[CONTRIBUTING.md](CONTRIBUTING.md), [ANTI-BOT.md](ANTI-BOT.md) and
[TECHNICAL-DESIGN.md](TECHNICAL-DESIGN.md) (Chinese) for architecture details.

## License

[MIT](LICENSE) © 2026 董铭月
