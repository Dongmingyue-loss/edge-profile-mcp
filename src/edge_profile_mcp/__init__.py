"""Real Microsoft Edge automation on a private, isolated, persistent profile."""

from __future__ import annotations

from .browser import (
    BrowserControlError,
    BrowserController,
    default_download_dir,
    default_profile_dir,
)

__version__ = "0.1.0"

__all__ = [
    "BrowserControlError",
    "BrowserController",
    "default_download_dir",
    "default_profile_dir",
    "__version__",
]
