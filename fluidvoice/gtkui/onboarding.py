"""First-run onboarding window (macOS Welcome flow counterpart).

Checks mic, model, hotkeys and AI; offers a real 3-second dictation tryout
through the daemon's test-dictation socket action (nothing is typed into
apps). Writing the .onboarded marker only happens on "Start dictating".
"""
from __future__ import annotations

from gi.repository import Adw, GLib, Gtk

from .. import paths
from .client import Client


class OnboardingWindow(Adw.ApplicationWindow):
    def __init__(self, application=None, client=None):
        super().__init__(application=application, title="Welcome to SayItErmano",
                         default_width=560, default_height=560)
        self.c = client or Client()

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(vbox)
        vbox.append(Adw.HeaderBar())

        page = Adw.StatusPage(
            title="Welcome to SayItErmano",
            description="A one-pass setup, same as on the Mac: check your mic, "
                        "the engine, then try a real dictation. Nothing gets "
                        "typed into your apps.",
            icon_name="sayit-ermano", vexpand=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                      margin_start=24, margin_end=24, margin_bottom=18)
        page.set_child(box)
        vbox.append(page)

        self.mic_lbl = Gtk.Label(wrap=True, xalign=0.0, css_classes=["dim-label"])
        self.model_lbl = Gtk.Label(wrap=True, xalign=0.0, css_classes=["dim-label"])
        self.hotkey_lbl = Gtk.Label(wrap=True, xalign=0.0, css_classes=["dim-label"])
        self.ai_lbl = Gtk.Label(wrap=True, xalign=0.0, css_classes=["dim-label"])
        # the ONLY onboarding addition for the update feature (scope rule):
        # a static sentence so new installs know a check happens
        self.updates_lbl = Gtk.Label(wrap=True, xalign=0.0,
                                     css_classes=["dim-label"])
        self.updates_lbl.set_text(
            "Updates: SayItErmano checks GitHub once a day for a newer "
            "release and notifies you (`sayit-ermano update` prints the "
            "upgrade command; disable with `updates.check = false` in "
            "the config).")
        for lbl in (self.mic_lbl, self.model_lbl, self.hotkey_lbl,
                    self.ai_lbl, self.updates_lbl):
            box.append(lbl)

        self.try_btn = Gtk.Button(label="Record 3 seconds",
                                  css_classes=["suggested-action"])
        self.try_btn.connect("clicked", self._try_dictation)
        self.try_out = Gtk.Label(wrap=True, xalign=0.0, visible=False,
                                 css_classes=["card"])
        box.append(self.try_btn)
        box.append(self.try_out)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10,
                          halign=Gtk.Align.END)
        settings_btn = Gtk.Button(label="Open Settings", css_classes=["flat"])
        settings_btn.connect("clicked", lambda *_: self._open("settings"))
        go = Gtk.Button(label="Start dictating",
                        css_classes=["suggested-action"])
        go.connect("clicked", self._finish)
        buttons.append(settings_btn)
        buttons.append(go)
        box.append(buttons)

        self._populate()

    # -- checks ------------------------------------------------------------------

    def _populate(self) -> None:
        cfg = self.c.masked_config()
        mics = self.c.mics()
        default = next((m["description"] for m in mics if m.get("default")),
                       mics[0]["description"] if mics else None)
        if default:
            extra = f" ({len(mics)} inputs)" if len(mics) > 1 else ""
            mic_text = default + extra
        else:
            mic_text = "none found — connect one and check your sound settings"
        self.mic_lbl.set_markup(f"<b>Microphone:</b> {mic_text}")

        from .. import backends
        name = str(cfg.get("model", {}).get("name", "auto"))
        active = (backends.resolve_model_name(name) if name in ("", "auto")
                  else backends.ALIASES.get(name.lower(), name.lower()))
        self.model_lbl.set_markup(
            f"<b>Speech engine:</b> {active} "
            + ("(download more from Settings → Models)" if active else
               "— open Settings to download one (tiny is ~75 MB)"))

        hk = cfg.get("hotkey", {})
        cancel = hk.get("cancel_key", "") or "Escape"
        self.hotkey_lbl.set_markup(
            f"<b>Hotkeys:</b> dictate <tt>{hk.get('key', '?')}</tt> · "
            f"cancel <tt>{cancel}</tt> (works while the pill is up)")

        ai = cfg.get("ai", {})
        configured = bool(ai.get("api_key") or ai.get("base_url"))
        self.ai_lbl.set_markup(
            "<b>AI polish:</b> "
            + ("active — fine-tune in Settings" if ai.get("enabled")
               else "configured but off — enable in Settings" if configured
               else "optional — add an OpenAI-compatible endpoint in Settings"))

        self.try_btn.set_sensitive(self.c.daemon_alive())

    # -- tryout -------------------------------------------------------------------

    def _try_dictation(self, _btn) -> None:
        self.try_btn.set_sensitive(False)
        self.try_out.set_visible(True)
        self.try_out.set_text("recording… speak now")

        def work():
            try:
                resp = self.c.test_dictation(3.0)
            except Exception as e:
                resp = {"ok": False, "error": str(e)}
            GLib.idle_add(self._show_tryout, resp)
        import threading
        threading.Thread(target=work, daemon=True).start()

    def _show_tryout(self, resp: dict) -> None:
        self.try_btn.set_sensitive(True)
        if resp.get("ok"):
            text = str(resp.get("text") or "").strip() or "(silence — nothing transcribed)"
            self.try_out.set_markup(
                f"<b>heard you ({resp.get('duration_s', 0)} s):</b>\n{text}")
        else:
            self.try_out.set_text(f"failed: {resp.get('error', 'unknown')}")

    # -- finish --------------------------------------------------------------------

    def _open(self, target: str) -> None:
        app = self.get_application()
        if app is not None:
            getattr(app, f"show_{target}")()

    def _finish(self, _btn) -> None:
        try:
            marker = paths.data_dir() / ".onboarded"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("done\n")
        except OSError:
            pass
        app = self.get_application()
        if app is not None:
            app.show_history()
            self.close()
