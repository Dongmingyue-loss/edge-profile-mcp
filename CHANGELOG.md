# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `fetch_text` MCP tool and `edge-fetch` command: fetch a page as clean text,
  decoding GB18030 correctly and preferring the rendered DOM, with optional
  caching and a `source` field reporting which path was used.
- `fetch_links` MCP tool: return a page's anchors, parsed in the DOM first.
- `search_web` MCP tool (`edge-search`) and `edge-search-rss` command: find
  sources on trusted domains.
- `cache_dir` setting (`EDGE_AUTOMATION_CACHE_DIR`) for fetched text and search
  logs.
- [ANTI-BOT.md](ANTI-BOT.md): measured anti-automation behaviour per engine, with
  the handling policy for each.
- `scripts/probe_engines.py` to reproduce those measurements.
- 360 (So.com) as the second search path. It answers a plain URL without
  challenging, including from a cold profile, so it sits directly after Bing.
  Real targets are read from the `data-mdurl` attribute that So.com attaches to
  each wrapped outbound link, so no redirect is followed; internal 360
  properties (so.com, 360.com, 360.cn, 360kan.com) are filtered out.
- Challenge detection for DuckDuckGo's explicit "bots use DuckDuckGo too" page.
- Search guards: relevance filtering, captcha/interstitial detection, and a
  `blocked_engines` / `degraded_engines` report instead of fabricated results.

### Changed

- `ANTI-BOT.md` no longer describes Bing truncation as a deterministic rule.
  Repeated measurement showed it is intermittent and correlates with session
  state, not with the URL shape: a fresh profile's first request was the most
  likely to degrade, later requests in the same session were fine, and adding
  `form=` parameters made no difference. The documented guarantee is now the
  relevance check, not the request shape.

### Fixed

- Duplicate `looks_like_challenge` definition: the older body shadowed the newer
  one, so the added markers never took effect.

## [0.1.0] - 2026-11-08

### Added

- MCP server (stdio) exposing 13 browser tools: `start`, `close`, `list_tabs`,
  `new_tab`, `close_tab`, `navigate`, `snapshot`, `screenshot`, `click`, `fill`,
  `select`, `press`, `click_and_wait_download`.
- Real Microsoft Edge driven through Playwright's `msedge` channel, running on a
  private, persistent user-data directory that is never the daily-use profile.
- Persistent profile support: cookies, logins, history and settings survive
  `start`/`close` cycles while staying invisible to the daily browser.
- Refusal to use any daily-driver browser data directory (Edge, Chrome, Brave,
  Firefox) as `profile_dir`.
- Normal-window behaviour: drops Playwright's automation-suppression defaults
  such as `--enable-automation`, `--mute-audio`, `--hide-scrollbars`,
  `--disable-extensions` and `--disable-popup-blocking`; maximized window in
  headed mode with real window dimensions.
- Bounded output everywhere: navigation timeout, tab limit, snapshot length,
  screenshot size, selected-value size, and 4000-character error truncation.
- Selector policy: exactly one of `css` or `role`, optional exact accessible
  `name` with `role`, and hard failure on ambiguous matches.
- Optional downloads confined to a single `download_dir` with filename
  sanitisation and no overwriting.
- Interactive line-oriented CLI (`edge-profile-cli`) sharing the same controller.
- ZCode/Claude plugin packaging: `.zcode-plugin/plugin.json`, `.claude-plugin/`,
  `.mcp.json` (auto-launched via `uv run`), and a bundled skill.
- Test suite covering session lifecycle, tab handling, interaction, limits,
  error paths, profile persistence, cross-profile isolation, download isolation
  and an MCP stdio end-to-end flow.

### Security

- No arbitrary JavaScript execution tool.
- Only absolute `http`/`https` URLs; credentials, control characters and
  surrounding whitespace rejected; non-HTTP(S) top-level navigation blocked.
- MCP runs over stdio only; no listening port.

[Unreleased]: https://github.com/Dongmingyue-loss/edge-profile-mcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Dongmingyue-loss/edge-profile-mcp/releases/tag/v0.1.0
