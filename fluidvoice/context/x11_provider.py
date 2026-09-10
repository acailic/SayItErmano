"""X11 ContextProvider: app identity via the existing xdotool/xprop path.

This is the same probing `insertion.active_window_class()` performs at
take start, lifted behind the context seam so the insertion-time read
(one query, immediately before typing) can reuse it without a second
code path. X11 has no accessibility text surface, so role, selection
and preceding text are always ``None`` — identity-only context; the
continuation/GAAV-role consumers degrade to today's behavior on X11.

Never raises: any failure degrades to a ``missing`` FocusContext. All
subprocess work goes through an injectable runner so tests never spawn
a real X connection.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from typing import Callable

from .. import session as session_mod
from .base import FocusContext


def _default_runner(args: list[str], timeout: float
                    ) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, timeout=timeout)


class X11Provider:
    """Active-window identity on X11 (WM_CLASS + window title)."""

    name = "x11"

    def __init__(self, runner: Callable[[list[str], float],
                                        subprocess.CompletedProcess] | None = None,
                 which: Callable[[str], str | None] | None = None):
        self._runner = runner or _default_runner
        self._which = which or shutil.which

    def available(self) -> bool:
        info = session_mod.current()
        if info.is_wayland:
            return False  # xdotool under Xwayland misreports the focus
        return bool(self._which("xdotool"))

    def read_focus_context(self, max_preceding: int = 120) -> FocusContext:
        # max_preceding accepted for protocol symmetry; X11 cannot read
        # field text at all.
        if not self.available():
            return FocusContext(provider_name=self.name, missing=True)
        try:
            wid = self._runner(
                ["xdotool", "getactivewindow"], 3).stdout.decode().strip()
            if not wid:
                return FocusContext(provider_name=self.name, missing=True)
            app_id: str | None = None
            if self._which("xprop"):
                out = self._runner(
                    ["xprop", "-id", wid, "WM_CLASS"], 3).stdout.decode()
                # WM_CLASS is "instance, class" quoted; the class is last
                for part in reversed(re.findall(r'"([^"]*)"', out)):
                    if part:
                        app_id = part
                        break
            title = self._runner(
                ["xdotool", "getwindowname", wid], 3).stdout.decode().strip()
            if app_id is None and not title:
                return FocusContext(provider_name=self.name, missing=True)
            return FocusContext(app_id=app_id,
                                window_title=title or None,
                                provider_name=self.name)
        except Exception:
            return FocusContext(provider_name=self.name, missing=True)
