# Contributing

Thanks for taking a look at this project.

## Scope

This project targets **Windows with the real Microsoft Edge installed**. Linux
and macOS support is out of scope: the isolation guarantees and the profile
paths assume Edge's Windows user-data layout.

## Development setup

```powershell
git clone https://github.com/Dongmingyue-loss/edge-profile-mcp.git
cd edge-profile-mcp
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

The test suite launches Edge, so it needs a desktop-capable Windows session.
It always uses temporary profile directories and never touches your daily Edge
data.

## Running the tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
```

Individual suites:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_download -v
.\.venv\Scripts\python.exe -m unittest tests.test_integration -v
```

The integration suite takes roughly two to three minutes because every test
starts a real browser.

## Guidelines

- Keep the three layers separate: `server.py` / `cli.py` are adapters,
  `BrowserWorker` owns the single browser thread, `BrowserController` owns
  browser semantics and validation.
- Any new tool must have bounded input, bounded output and a clear failure mode.
- Never add a tool that executes arbitrary JavaScript.
- Never write to, or read from, a user's daily browser profile.
- Do not launch a session on an existing profile path; profile isolation is the
  core guarantee of the project.
- Add or extend a test for every behaviour change. Tests must not depend on the
  public internet: use a local `http.server` fixture like the existing suites.
- Update `CHANGELOG.md` under `## [Unreleased]`.

## Reporting bugs

Please include:

1. Windows version and Edge version (`msedge --version`).
2. Python version and whether the package was installed with `pip` or `uv`.
3. The exact tool call and the returned error text.
4. Whether the daily Edge browser was running at the time.

Never paste cookies, tokens or page content containing personal data into an
issue.

## Pull requests

- Keep changes focused; one concern per pull request.
- Describe the behaviour change and how you verified it.
- Confirm the full test suite passes locally on Windows.
