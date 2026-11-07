# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
