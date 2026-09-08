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
            self.add_main_option(
                "open", ord("o"), GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                "window to open: history (default) or settings", "TARGET")
            self.add_main_option(
                "onboard", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                "run the first-run onboarding flow", None)

        def do_command_line(self, cmdline):  # noqa: N802
            opts = cmdline.get_options_dict()
            open_target = opts.lookup_value("open", GLib.VariantType("s"))
            if open_target is not None:
                target = open_target.get_string()
                self._open = target if target in ("history", "settings") else "history"
            self._onboard = opts.lookup_value("onboard", None) is not None
            self.activate()
            return 0

        def do_activate(self) -> None:  # noqa: N802
            from gi.repository import Gtk  # versions pinned in run()
            register_icons()  # earliest possible; windows re-guarantee it
            Gtk.Window.set_default_icon_name(APP_ICON_NAME)
            if self._onboard:
                self.show_onboarding()
            elif self._open == "settings":
                self.show_settings()
            else:
                self.show_history()

        # -- window registry (raise instead of re-create) ----------------------

        def _window(self, key: str, factory):
            win = self._windows.get(key)
            if win is None:
                win = factory(self)  # associate with the app or it exits
                self._windows[key] = win
            win.present()
            return win

        def show_history(self):
            from .main_window import HistoryWindow
            return self._window("history",
                                lambda app: HistoryWindow(application=app))

        def show_settings(self):
            from .settings_window import SettingsWindow
            return self._window("settings",
                                lambda app: SettingsWindow(application=app))

        def show_onboarding(self):
            from .onboarding import OnboardingWindow
            return self._window("onboarding",
                                lambda app: OnboardingWindow(application=app))

    app = SayItErmanoApp()
    return app.run([sys.argv[0], *argv])
