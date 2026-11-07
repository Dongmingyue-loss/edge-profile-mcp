"""Interactive, line-oriented Edge session. Enter 'help' for command syntax."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

from .browser import BrowserControlError, BrowserController


HELP = """Commands use a command name followed by an optional JSON object.
  start {"headless": true}       Start real Edge on the private profile
                                 optional: profile_dir, download_dir, timeout_ms,
                                 max_tabs, max_snapshot_chars, max_screenshot_bytes
  close                          Close the session
  list_tabs                      List tabs
  new_tab {"url": "https://example.com"}
  close_tab {"tab_id": "tab-1"}
  navigate {"tab_id": "tab-1", "url": "https://example.com"}
  snapshot {"tab_id": "tab-1"}
  screenshot {"tab_id": "tab-1", "path": "screenshot.png"}
  click {"tab_id": "tab-1", "role": "button", "name": "Submit"}
  fill {"tab_id": "tab-1", "css": "#query", "value": "text"}
  select {"tab_id": "tab-1", "css": "select", "label": "Option"}
  press {"tab_id": "tab-1", "css": "input", "key": "Enter"}
  click_and_wait_download {"tab_id": "tab-1", "css": "#file"}
  help | quit | exit
Use exactly one of css or role for element actions; name is optional with role.
Screenshot paths must not already exist. JSON strings use JSON escaping.
Downloads require start with download_dir; they are saved only there.
The profile is separate from your daily Edge; set EDGE_AUTOMATION_PROFILE_DIR
to move it.
"""


class Session:
    def __init__(self) -> None:
        self.controller: BrowserController | None = None

    def run(self, command: str, args: dict[str, Any]) -> Any:
        if command == "start":
            if self.controller is not None:
                raise BrowserControlError("browser session is already running")
            controller = BrowserController(**args)
            tab_id = controller.start()
            self.controller = controller
            return {"tab_id": tab_id}
        if command == "close":
            if args:
                raise ValueError("close takes no arguments")
            self.close()
            return {"closed": True}
        if command not in {"list_tabs", "new_tab", "close_tab", "navigate", "snapshot",
                           "screenshot", "click", "fill", "select", "press",
                           "click_and_wait_download"}:
            raise ValueError("unknown command; enter help for usage")
        if self.controller is None:
            raise BrowserControlError("browser session is not running; call start first")
        if command == "screenshot":
            if set(args) != {"tab_id", "path"} or not isinstance(args["path"], str) or not args["path"]:
                raise ValueError("screenshot requires tab_id and a nonempty path")
            path = Path(args["path"])
            image = self.controller.screenshot(args["tab_id"])
            with path.open("xb") as output:
                output.write(image)
            return {"path": str(path.resolve()), "bytes": len(image)}
        result = getattr(self.controller, command)(**args)
        return result if result is not None else {"ok": True}

    def close(self) -> None:
        controller, self.controller = self.controller, None
        if controller is not None:
            controller.close()


def main() -> None:
    session = Session()
    interactive = sys.stdin.isatty()
    try:
        if interactive:
            print("Edge automation. Enter help for commands.")
        while True:
            try:
                line = input("edge> " if interactive else "").strip()
            except EOFError:
                break
            if not line:
                continue
            command, _, raw_args = line.partition(" ")
            if command in {"quit", "exit", "help"}:
                if raw_args.strip():
                    print("Error: this command takes no arguments", file=sys.stderr)
                    continue
                if command == "help":
                    print(HELP, end="")
                    continue
                break
            try:
                args = json.loads(raw_args) if raw_args.strip() else {}
                if not isinstance(args, dict):
                    raise ValueError("arguments must be a JSON object")
                result = session.run(command, args)
                print(json.dumps(result, ensure_ascii=True))
            except (BrowserControlError, ValueError, TypeError, OSError) as exc:
                detail = str(exc)
                print(f"Error: {detail[:4000]}{'... [truncated]' if len(detail) > 4000 else ''}", file=sys.stderr)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
    finally:
        try:
            session.close()
        except Exception as exc:
            print(f"Browser cleanup failed: {str(exc)[:4000]}", file=sys.stderr)


if __name__ == "__main__":
    main()
