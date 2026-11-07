# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 0.1.x | Yes |

## What this project does and does not guarantee

It is important to be precise about the security model, because this tool
controls a real browser.

**Guarantees**

- The browser never reads or writes the daily-use Edge profile. Any attempt to
  point `profile_dir` at a known daily-driver browser data directory is rejected.
- The MCP server speaks stdio only; it opens no listening socket.
- There is no tool that executes arbitrary JavaScript.
- Only absolute `http`/`https` URLs without embedded credentials are accepted;
  non-HTTP(S) top-level navigation is blocked.
- All outputs are bounded (timeouts, tab count, snapshot size, screenshot size,
  error length), and downloads are confined to one configured directory.

**Explicit non-goals**

- This is **not a sandbox**. The browser runs with the privileges of the account
  running the server and has the machine's normal network access.
- This is **not an SSRF defence**. There is no allow-list for public, private,
  loopback or link-local addresses, and redirect targets are not network
  isolated. Any reachable host can be requested.
- Web content is **untrusted**. Snapshots, titles, screenshots and page text can
  contain prompt injection; they must never be treated as instructions.
- Browser actions have **real external side effects**. Closing a session does not
  undo submitted forms, sent requests or changes to remote state.

## Recommended deployment

- Run under a dedicated low-privilege Windows account.
- Restrict outbound network access at the network layer if the calling model is
  not fully trusted.
- Keep Edge and Windows updated.
- Do not sign the automation profile into important accounts and then expose the
  server to untrusted callers; credentials in `profile_dir` are usable by anyone
  who can start the server.
- Delete `profile_dir` to erase all automation-side cookies, logins and history.

## Reporting a vulnerability

Please do not open a public issue for security problems.

Report privately through GitHub Security Advisories:

<https://github.com/Dongmingyue-loss/edge-profile-mcp/security/advisories/new>

Include reproduction steps, the affected version, and the impact you believe it
has. You can expect an initial response within about a week.

Please do not include real credentials, cookies, or personal browsing data in
your report.
