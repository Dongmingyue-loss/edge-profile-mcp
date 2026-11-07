"""Bounded, synchronous control of an isolated system Edge session.

The browser is the real Microsoft Edge installed on the machine, launched with
its own dedicated user-data directory so that cookies, logins, history and
settings never mix with the profile you use day to day.  That directory is
persistent: signing in once keeps you signed in on the next ``start``.

All methods must be called from the thread that started the controller.  Page
content and actions may have side effects on external websites; callers are
responsible for obtaining any necessary user consent before invoking them.
"""

from __future__ import annotations

from functools import wraps
import os
import re
import threading
from typing import Any, Callable, TypeVar
from urllib.parse import urlsplit

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Locator,
    Page,
    Playwright,
    sync_playwright,
)


class BrowserControlError(RuntimeError):
    """An invalid request or a browser operation that could not be completed."""


_F = TypeVar("_F", bound=Callable[..., Any])


# Playwright injects this list by default.  The entries below are dropped so the
# window behaves like an ordinary Edge window: no "controlled by automated
# software" bar, audible media, normal scrollbars, extensions allowed, popups
# blocked as usual, back/forward cache on and the OS credential store used.
# Arguments that keep automation deterministic (input handling, paint
# scheduling, feature overrides) are intentionally left untouched.
_NORMAL_BROWSER_ARGS: tuple[str, ...] = (
    "--enable-automation",
    "--mute-audio",
    "--hide-scrollbars",
    "--disable-extensions",
    "--disable-popup-blocking",
    "--disable-back-forward-cache",
    "--disable-component-extensions-with-background-pages",
    "--disable-default-apps",
    "--password-store=basic",
    "--no-service-autorun",
)

# Directories that belong to a real, daily-driver browser profile.  Writing an
# automation session into one of these would defeat data separation, so they are
# rejected even when the caller passes them explicitly.
_FORBIDDEN_PROFILE_ROOTS: tuple[str, ...] = (
    os.path.join("%LOCALAPPDATA%", "Microsoft", "Edge"),
    os.path.join("%LOCALAPPDATA%", "Microsoft", "Edge", "User Data"),
    os.path.join("%APPDATA%", "Microsoft", "Edge"),
    os.path.join("%LOCALAPPDATA%", "Google", "Chrome", "User Data"),
    os.path.join("%LOCALAPPDATA%", "Google", "Chrome"),
    os.path.join("%LOCALAPPDATA%", "BraveSoftware", "Brave-Browser", "User Data"),
    os.path.join("%APPDATA%", "Mozilla", "Firefox"),
)


def default_profile_dir() -> str:
    """Return the dedicated, per-user Edge profile directory for automation."""
    override = os.environ.get("EDGE_AUTOMATION_PROFILE_DIR")
    if override and override.strip():
        return os.path.abspath(os.path.expanduser(override.strip()))
    base = os.environ.get("LOCALAPPDATA")
    if base and base.strip():
        return os.path.join(base.strip(), "edge-automation-mcp", "profile")
    return os.path.join(os.path.expanduser("~"), ".edge-automation-mcp", "profile")


def default_download_dir() -> str | None:
    """Return the configured download directory, or None when downloads are off."""
    value = os.environ.get("EDGE_AUTOMATION_DOWNLOAD_DIR")
    if value and value.strip():
        return value.strip()
    return None


def _expand_env(path: str) -> str:
    """Expand ``%VAR%`` and ``~`` so configured paths match Windows conventions."""
    return os.path.expanduser(os.path.expandvars(path))


def _operation(method: _F) -> _F:
    @wraps(method)
    def wrapped(self: BrowserController, *args: Any, **kwargs: Any) -> Any:
        self._check_thread()
        try:
            return method(self, *args, **kwargs)
        except PlaywrightError as exc:
            detail = str(exc)
            if len(detail) > 4000:
                detail = detail[:4000] + "... [truncated]"
            raise BrowserControlError(f"{method.__name__} failed: {detail}") from exc

    return wrapped  # type: ignore[return-value]


class BrowserController:
    """Manage one real Edge instance backed by a private, persistent profile.

    The session never attaches to, and never writes into, the Edge profile used
    interactively on the machine.  Selectors are either a CSS selector supplied
    as ``css``, or an accessible ``role`` with an optional exact accessible
    ``name``. Exactly one of ``css`` and ``role`` is required. Ambiguous matches
    fail rather than choosing one.
    """

    def __init__(
        self, *, headless: bool = False, timeout_ms: int = 10_000,
        max_tabs: int = 20, max_snapshot_chars: int = 30_000,
        max_screenshot_bytes: int = 5_000_000,
        download_dir: str | None = None, profile_dir: str | None = None,
    ) -> None:
        if not isinstance(headless, bool):
            raise BrowserControlError("headless must be a boolean")
        self._timeout_ms = self._bounded_int("timeout_ms", timeout_ms, 1, 60_000)
        self._max_tabs = self._bounded_int("max_tabs", max_tabs, 1, 100)
        self._max_snapshot_chars = self._bounded_int(
            "max_snapshot_chars", max_snapshot_chars, 1, 200_000
        )
        self._max_screenshot_bytes = self._bounded_int(
            "max_screenshot_bytes", max_screenshot_bytes, 1, 20_000_000
        )
        self._download_dir = self._prepare_download_dir(
            download_dir if download_dir is not None else default_download_dir()
        )
        self._profile_dir = self._prepare_profile_dir(profile_dir)
        self._headless = headless
        self._owner: int | None = None
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._tabs: dict[str, Page] = {}
        self._next_tab = 1

    @staticmethod
    def _prepare_download_dir(download_dir: str | None) -> str | None:
        """Resolve and create the only directory downloads may be saved into."""
        if download_dir is None:
            return None
        if not isinstance(download_dir, str) or not download_dir.strip():
            raise BrowserControlError("download_dir must be a nonempty path string")
        try:
            path = os.path.abspath(_expand_env(download_dir))
            os.makedirs(path, exist_ok=True)
        except OSError as exc:
            raise BrowserControlError(f"cannot use download_dir: {exc}") from exc
        return path

    @staticmethod
    def _prepare_profile_dir(profile_dir: str | None) -> str:
        """Resolve the private profile directory and refuse shared browser data."""
        raw = profile_dir if profile_dir is not None else default_profile_dir()
        if not isinstance(raw, str) or not raw.strip():
            raise BrowserControlError("profile_dir must be a nonempty path string")
        try:
            path = os.path.abspath(_expand_env(raw.strip()))
        except (OSError, ValueError) as exc:
            raise BrowserControlError(f"cannot use profile_dir: {exc}") from exc
        normalized = os.path.normcase(path)
        for forbidden in _FORBIDDEN_PROFILE_ROOTS:
            root = os.path.normcase(os.path.abspath(_expand_env(forbidden)))
            if normalized == root or normalized.startswith(root + os.sep):
                raise BrowserControlError(
                    "profile_dir must be a dedicated directory; "
                    f"{path} belongs to a daily-use browser profile"
                )
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as exc:
            raise BrowserControlError(f"cannot use profile_dir: {exc}") from exc
        if not os.path.isdir(path):
            raise BrowserControlError(f"profile_dir is not a directory: {path}")
        return path

    @staticmethod
    def _safe_filename(suggested: str) -> str:
        """Reduce a server-provided name to a bounded, path-free file name."""
        name = os.path.basename(suggested or "").strip().replace("\x00", "")
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
        if not name:
            name = "download"
        stem, suffix = os.path.splitext(name)
        return (stem[:150] or "download") + suffix[:20]

    @staticmethod
    def _bounded_int(name: str, value: int, minimum: int, maximum: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise BrowserControlError(f"{name} must be an integer between {minimum} and {maximum}")
        return value

    @staticmethod
    def _text(name: str, value: str, limit: int, *, empty: bool = False) -> str:
        if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
            raise BrowserControlError(f"{name} must be a {'possibly empty ' if empty else 'nonempty '}string of at most {limit} characters")
        return value

    @staticmethod
    def _url(url: str) -> str:
        BrowserController._text("url", url, 8192)
        if any(ord(char) < 32 or ord(char) == 127 for char in url) or url != url.strip():
            raise BrowserControlError("url must not contain control characters or surrounding whitespace")
        try:
            parts = urlsplit(url)
            if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
                raise ValueError
            if parts.username is not None or parts.password is not None:
                raise ValueError
            _ = parts.port
        except ValueError as exc:
            raise BrowserControlError("url must be an absolute http or https URL without credentials") from exc
        return url

    def _check_thread(self) -> None:
        if self._owner is not None and self._owner != threading.get_ident():
            raise BrowserControlError("the controller must be used from the thread that started it")

    @staticmethod
    def _looks_like_profile_conflict(text: str) -> bool:
        """Detect Edge refusing to start because the profile is already open.

        Edge enforces a per-user-data-dir singleton lock, and depending on the
        version Playwright only surfaces it as a bare "browser has been closed".
        """
        lowered = text.lower()
        return any(
            marker in lowered
            for marker in ("processsingleton", "user data dir", "profile is already",
                           "browser has been closed")
        )

    def _require_context(self) -> BrowserContext:
        if self._context is None or self._browser is None or not self._browser.is_connected():
            raise BrowserControlError("browser session is not running; call start() first")
        return self._context

    def _register_page(self, page: Page) -> None:
        if len(self._tabs) >= self._max_tabs:
            page.close()
            return
        tab_id = f"tab-{self._next_tab}"
        self._next_tab += 1
        self._tabs[tab_id] = page
        page.on("close", lambda _: self._tabs.pop(tab_id, None))

    @staticmethod
    def _route_navigation(route: Any) -> None:
        request = route.request
        if request.is_navigation_request() and urlsplit(request.url).scheme.lower() not in ("http", "https"):
            route.abort("blockedbyclient")
        else:
            route.continue_()

    def _page(self, tab_id: str) -> Page:
        self._require_context()
        self._text("tab_id", tab_id, 100)
        page = self._tabs.get(tab_id)
        if page is None or page.is_closed():
            raise BrowserControlError(f"unknown or closed tab: {tab_id}")
        return page

    def _locator(self, tab_id: str, *, css: str | None, role: str | None, name: str | None) -> Locator:
        page = self._page(tab_id)
        if (css is None) == (role is None):
            raise BrowserControlError("provide exactly one of css or role")
        if css is not None:
            if name is not None:
                raise BrowserControlError("name can only be used with role")
            self._text("css", css, 4096)
            return page.locator(f"css={css}")
        self._text("role", role, 100)  # type: ignore[arg-type]
        if name is not None:
            self._text("name", name, 4096, empty=True)
        return page.get_by_role(role, name=name, exact=True)  # type: ignore[arg-type]

    @_operation
    def start(self) -> str:
        """Launch real Edge on the private profile and return the first tab ID."""
        if self._playwright is not None:
            raise BrowserControlError("browser session is already running")
        self._owner = threading.get_ident()
        try:
            self._playwright = sync_playwright().start()
            options: dict[str, Any] = {
                "channel": "msedge",
                "headless": self._headless,
                "timeout": self._timeout_ms,
                "accept_downloads": self._download_dir is not None,
                "ignore_default_args": list(_NORMAL_BROWSER_ARGS),
            }
            if self._headless:
                options["viewport"] = {"width": 1280, "height": 720}
            else:
                # Use the real window size so the window resizes like Edge does.
                options["no_viewport"] = True
                options["args"] = ["--start-maximized"]
            self._context = self._playwright.chromium.launch_persistent_context(
                self._profile_dir, **options
            )
            self._browser = self._context.browser
            self._context.set_default_timeout(self._timeout_ms)
            self._context.set_default_navigation_timeout(self._timeout_ms)
            self._context.route("**/*", self._route_navigation)
            self._context.on("page", self._register_page)
            # A persistent context always opens one blank page before the page
            # handler exists, so adopt it instead of creating a second tab.
            for page in list(self._context.pages):
                self._register_page(page)
            if not self._tabs:
                return self.new_tab()
            return next(iter(self._tabs))
        except BaseException as exc:
            try:
                self.close()
            except Exception:
                pass
            if isinstance(exc, PlaywrightError) and self._looks_like_profile_conflict(str(exc)):
                raise BrowserControlError(
                    "profile_dir is already in use by another Edge instance; "
                    f"close it or pass a different profile_dir: {self._profile_dir}"
                ) from exc
            raise

    @_operation
    def close(self) -> None:
        """Close the context, browser and Playwright, even after partial startup."""
        errors: list[str] = []
        if self._context is not None:
            try:
                self._context.close()
            except Exception as exc:
                errors.append(f"context: {exc}")
        if self._browser is not None:
            try:
                if self._browser.is_connected():
                    self._browser.close()
            except Exception as exc:
                errors.append(f"browser: {exc}")
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception as exc:
                errors.append(f"Playwright: {exc}")
        self._context = None
        self._browser = None
        self._playwright = None
        self._tabs.clear()
        self._owner = None
        if errors:
            raise BrowserControlError("cleanup failed: " + "; ".join(errors))

    def __enter__(self) -> BrowserController:
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    @_operation
    def list_tabs(self) -> list[dict[str, str]]:
        """Return IDs, URLs and titles for the currently open tabs."""
        self._require_context()
        return [
            {"id": tab_id, "url": page.url[:8192], "title": page.title()[:1000]}
            for tab_id, page in list(self._tabs.items()) if not page.is_closed()
        ]

    @_operation
    def new_tab(self, url: str | None = None) -> str:
        """Open a tab, optionally navigate to an HTTP(S) URL, and return its ID."""
        if url is not None:
            self._url(url)
        context = self._require_context()
        if len(self._tabs) >= self._max_tabs:
            raise BrowserControlError(f"tab limit reached ({self._max_tabs})")
        page = context.new_page()
        tab_id = next((key for key, value in self._tabs.items() if value == page), None)
        if tab_id is None:
            page.close()
            raise BrowserControlError("new tab could not be registered")
        if url is not None:
            try:
                self.navigate(tab_id, url)
            except Exception:
                page.close()
                raise
        return tab_id

    @_operation
    def close_tab(self, tab_id: str) -> None:
        self._page(tab_id).close()

    @_operation
    def navigate(self, tab_id: str, url: str) -> dict[str, Any]:
        """Navigate to HTTP(S); return the final URL and HTTP status, if any."""
        self._url(url)
        page = self._page(tab_id)
        response = page.goto(url, wait_until="domcontentloaded", timeout=self._timeout_ms)
        return {"url": page.url[:8192], "status": response.status if response else None}

    @_operation
    def snapshot(self, tab_id: str) -> dict[str, Any]:
        """Return an accessible tree, with an explicit truncation indicator."""
        page = self._page(tab_id)
        text = page.locator(":root").aria_snapshot(timeout=self._timeout_ms)
        return {"url": page.url[:8192], "text": text[:self._max_snapshot_chars],
                "truncated": len(text) > self._max_snapshot_chars}

    @_operation
    def screenshot(self, tab_id: str) -> bytes:
        """Return a viewport PNG; reject images exceeding the configured limit."""
        image = self._page(tab_id).screenshot(type="png", timeout=self._timeout_ms)
        if len(image) > self._max_screenshot_bytes:
            raise BrowserControlError(f"screenshot exceeds {self._max_screenshot_bytes} bytes")
        return image

    @_operation
    def click(self, tab_id: str, *, css: str | None = None, role: str | None = None, name: str | None = None) -> None:
        self._locator(tab_id, css=css, role=role, name=name).click(timeout=self._timeout_ms)

    @_operation
    def fill(self, tab_id: str, value: str, *, css: str | None = None, role: str | None = None, name: str | None = None) -> None:
        self._text("value", value, 100_000, empty=True)
        self._locator(tab_id, css=css, role=role, name=name).fill(value, timeout=self._timeout_ms)

    @_operation
    def select(self, tab_id: str, *, value: str | None = None, label: str | None = None,
               css: str | None = None, role: str | None = None, name: str | None = None) -> list[str]:
        """Select one HTML option by value or label and return selected values."""
        if (value is None) == (label is None):
            raise BrowserControlError("provide exactly one of value or label")
        if value is not None:
            self._text("value", value, 4096, empty=True)
        if label is not None:
            self._text("label", label, 4096, empty=True)
        selected = self._locator(tab_id, css=css, role=role, name=name).select_option(
            value=value, label=label, timeout=self._timeout_ms
        )
        if len(selected) > 100 or sum(len(item) for item in selected) > 10_000:
            raise BrowserControlError("selected option values exceed the output limit")
        return selected

    @_operation
    def click_and_wait_download(self, tab_id: str, *, css: str | None = None,
                               role: str | None = None, name: str | None = None,
                               timeout_ms: int | None = None) -> dict[str, Any]:
        """Click one element and save the resulting download inside download_dir."""
        if self._download_dir is None:
            raise BrowserControlError("downloads are disabled; start a session with download_dir")
        wait_ms = self._timeout_ms if timeout_ms is None else self._bounded_int(
            "timeout_ms", timeout_ms, 1, 120_000
        )
        locator = self._locator(tab_id, css=css, role=role, name=name)
        page = locator.page
        try:
            with page.expect_download(timeout=wait_ms) as info:
                locator.click(timeout=self._timeout_ms)
            download = info.value
        except PlaywrightError as exc:
            raise BrowserControlError(
                "click did not produce a download; the element may navigate instead"
            ) from exc
        filename = self._safe_filename(download.suggested_filename)
        target = os.path.join(self._download_dir, filename)
        stem, suffix = os.path.splitext(target)
        counter = 1
        while os.path.exists(target):
            target = f"{stem}-{counter}{suffix}"
            counter += 1
        try:
            download.save_as(target)
        except PlaywrightError as exc:
            raise BrowserControlError(f"download could not be saved: {exc}") from exc
        return {"path": target, "filename": os.path.basename(target),
                "suggested_filename": download.suggested_filename}

    @_operation
    def press(self, tab_id: str, key: str, *, css: str | None = None, role: str | None = None, name: str | None = None) -> None:
        self._text("key", key, 100)
        self._locator(tab_id, css=css, role=role, name=name).press(key, timeout=self._timeout_ms)
