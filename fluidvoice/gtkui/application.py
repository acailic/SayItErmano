"""Gtk.Application shell: single instance, remote opening of windows.

`fluidvoice app --open settings` from a second process reaches the primary
instance through GApplication's built-in forwarding; the primary just raises
the requested window.
"""
from __future__ import annotations

import sys

from .style import register_icons

APP_ID = "io.github.acailic.SayItErmano"
APP_ICON_NAME = "sayit-ermano"

GTK_HINT = ("GTK 4 / libadwaita not available - install with:\n"
            "  apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1")

#: `app --open` targets. `history` and `settings` name the windows; the
#: rest are Settings page names (the Adw.PreferencesPage names the
#: settings_pages/ builders set - tests/test_settings_sections.py pins
#: this set against the built sidebar), so a tray item or CLI call lands
#: on the page it is about. The Settings page named "history" is shadowed
#: by the History window on purpose: `--open history` keeps opening it.
SETTINGS_PAGE_TARGETS = ("general", "dictation", "models", "ai", "wayland",
                         "about")
OPEN_TARGETS = ("history", "settings", *SETTINGS_PAGE_TARGETS)


def normalize_open_target(value: str | None) -> str:
    """The window a `--open` value names: a Settings page name opens
    Settings on that page; anything unknown opens History (the default
    window). GTK-free - the CLI and the tray pass names through this."""
    target = (value or "").strip().strip("/").lower()
    return target if target in OPEN_TARGETS else "history"


def run(argv: list[str] | None = None) -> int:
    """Entry point (`fluidvoice app`). Returns a process exit code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gio, GLib
    except (ImportError, ValueError) as e:
        print(f"{GTK_HINT}\n(detail: {e})", file=sys.stderr)
        return 1

    class SayItErmanoApp(Adw.Application):
        def __init__(self) -> None:
            super().__init__(application_id=APP_ID,
                             flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
            self._open: str = "history"
            self._onboard: bool = False
            self._windows: dict[str, Adw.ApplicationWindow] = {}
            self._factories: dict[str, callable] = {}
            # UI language before the first window builds its strings; a
            # later ui_language settings save rebuilds every open window
            from .. import i18n
            from ..config import load_config
            i18n.init_i18n(load_config())
            i18n.register_on_change(self._on_language_change)
            self.add_main_option(
                "open", ord("o"), GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                "window to open: history (default), settings, or a Settings "
                "page name (dictation, models, ai, …)", "TARGET")
            self.add_main_option(
                "onboard", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                "run the first-run onboarding flow", None)

        def do_command_line(self, cmdline):  # noqa: N802
            opts = cmdline.get_options_dict()
            open_target = opts.lookup_value("open", GLib.VariantType("s"))
            if open_target is not None:
                self._open = normalize_open_target(open_target.get_string())
            self._onboard = opts.lookup_value("onboard", None) is not None
            self.activate()
            return 0

        def do_activate(self) -> None:  # noqa: N802
            from gi.repository import Gtk  # versions pinned in run()
            register_icons()  # earliest possible; windows re-guarantee it
            Gtk.Window.set_default_icon_name(APP_ICON_NAME)
            self._ensure_sandbox_daemon()
            if self._onboard:
                self.show_onboarding()
            elif self._open == "history":
                self.show_history()
            elif self._open == "settings":
                self.show_settings()
            else:
                self.show_settings(self._open)

        # -- sandbox daemon lifecycle (ADR-0006/#11) --------------------------

        def _ensure_sandbox_daemon(self) -> None:
            """Inside the Flatpak sandbox no systemd user unit exists:
            this process owns the daemon. Ensure it on every activate
            (login autostart usually started it already; the flock
            singleton makes the overlap harmless)."""
            from ..daemon_owner import ensure_daemon
            state = ensure_daemon(
                log=lambda m: print(f"[sayit-ermano] {m}", flush=True))
            if state.startswith("spawn-failed"):
                print(f"WARN daemon owner: {state}", file=sys.stderr,
                      flush=True)

        # -- window registry (raise instead of re-create) ----------------------

        def _window(self, key: str, factory):
            self._factories[key] = factory
            win = self._windows.get(key)
            if win is None:
                win = factory(self)  # associate with the app or it exits
                self._windows[key] = win
            win.present()
            return win

        def _on_language_change(self, _code: str) -> None:
            """Live UI language switch: rebuild every open window (old
            widgets keep their build-time strings). Idle-scheduled — the
            change usually fires from inside a Settings save callback."""
            from gi.repository import GLib

            def rebuild():
                for key, win in list(self._windows.items()):
                    factory = self._factories.get(key)
                    if factory is None:
                        continue
                    was_visible = win.get_visible()
                    del self._windows[key]
                    try:
                        win.close()
                    except Exception:  # noqa: S110 — cosmetic; rebuild continues
                        pass
                    win.destroy()
                    # only re-create windows that were on screen: a
                    # user-closed window must not pop back open
                    if was_visible:
                        new = factory(self)
                        self._windows[key] = new
                        new.present()
                return False  # one-shot idle

            GLib.idle_add(rebuild)

        def show_history(self):
            from .main_window import HistoryWindow
            return self._window("history",
                                lambda app: HistoryWindow(application=app))

        def show_settings(self, page: str | None = None):
            """Raise Settings; `page` (a Settings page name) lands on that
            page even when the window was already open elsewhere."""
            from .settings_window import SettingsWindow
            win = self._window("settings",
                               lambda app: SettingsWindow(application=app))
            if page:
                win.open_page(page)
            return win

        def show_onboarding(self):
            from .onboarding import OnboardingWindow
            return self._window("onboarding",
                                lambda app: OnboardingWindow(application=app))

    app = SayItErmanoApp()
    return app.run([sys.argv[0], *argv])
