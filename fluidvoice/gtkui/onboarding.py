"""First-run onboarding window (macOS Welcome flow counterpart).

Checks mic, model, hotkeys and AI; offers a real 3-second dictation tryout
through the daemon's test-dictation socket action (nothing is typed into
apps). Writing the .onboarded marker only happens on "Start dictating".

v2 presentation: the checks render as a proper checklist (boxed rows with
status glyphs) instead of a wall of bold markup — the window is the app's
first impression.
"""
from __future__ import annotations

from gi.repository import Adw, GLib, Gtk

from .. import paths
from .client import Client
from .style import load_style

# row status -> (symbolic icon, css classes)
_OK = ("object-select-symbolic", ["success"])
_WARN = ("dialog-warning-symbolic", ["warning"])
_INFO = ("dialog-information-symbolic", ["dim-label"])


class _CheckRow(Gtk.ListBoxRow):
    """One checklist line: bold title, dim value (wrap), status glyph."""

    def __init__(self, icon_name: str, title: str, value: Gtk.Label):
        super().__init__(activatable=False, selectable=False)
        self.status = Gtk.Image()
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
                      margin_top=12, margin_bottom=12,
                      margin_start=14, margin_end=12)
        row.append(Gtk.Image(icon_name=icon_name,
                             valign=Gtk.Align.START, margin_top=2))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                       hexpand=True)
        text.append(Gtk.Label(label=title, xalign=0.0, hexpand=True,
                              css_classes=["heading"]))
        text.append(value)
        row.append(text)
        row.append(self.status)
        self.set_child(row)

    def set_state(self, state: tuple[str, list[str]]) -> None:
        icon, css = state
        self.status.set_from_icon_name(icon)
        self.status.set_css_classes(css)


class OnboardingWindow(Adw.ApplicationWindow):
    def __init__(self, application=None, client=None):
        super().__init__(application=application, title="Welcome to SayItErmano",
                         default_width=560, default_height=640)
        load_style()  # after super(): display is open, icons resolve
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
        clamp = Adw.Clamp(maximum_size=520, tightening_threshold=400,
                          margin_start=24, margin_end=24, margin_bottom=18)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        clamp.set_child(box)
        page.set_child(clamp)
        vbox.append(page)

        checks = Gtk.ListBox(css_classes=["boxed-list"])
        checks.set_selection_mode(Gtk.SelectionMode.NONE)

        self.mic_lbl = Gtk.Label(wrap=True, xalign=0.0,
                                 css_classes=["dim-label"])
        self.model_lbl = Gtk.Label(wrap=True, xalign=0.0,
                                   css_classes=["dim-label"])
        self.hotkey_lbl = Gtk.Label(wrap=True, xalign=0.0,
                                    css_classes=["dim-label"])
        self.ai_lbl = Gtk.Label(wrap=True, xalign=0.0,
                                css_classes=["dim-label"])
        # the ONLY onboarding addition for the update feature (scope rule):
        # a static sentence so new installs know a check happens
        self.updates_lbl = Gtk.Label(wrap=True, xalign=0.0,
                                     css_classes=["dim-label"])
        self.updates_lbl.set_text(
            "Checks GitHub once a day for a newer release and notifies you — "
            "`sayit-ermano update` prints the upgrade command; disable with "
            "`updates.check = false` in the config.")

        self.mic_row = _CheckRow("audio-input-microphone-symbolic",
                                 "Microphone", self.mic_lbl)
        self.model_row = _CheckRow("fluidvoice-models-symbolic",
                                   "Speech engine", self.model_lbl)
        self.hotkey_row = _CheckRow("fluidvoice-general-symbolic",
                                    "Hotkeys", self.hotkey_lbl)
        self.ai_row = _CheckRow("fluidvoice-polish-symbolic",
                                "AI polish", self.ai_lbl)
        self.updates_row = _CheckRow("software-update-available-symbolic",
                                     "Updates", self.updates_lbl)
        for r in (self.mic_row, self.model_row, self.hotkey_row,
                  self.ai_row, self.updates_row):
            checks.append(r)
        box.append(checks)

        try_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.try_btn = Gtk.Button(label="Record 3 seconds",
                                  css_classes=["suggested-action", "pill"],
                                  halign=Gtk.Align.CENTER)
        self.try_btn.connect("clicked", self._try_dictation)
        self.try_out = Gtk.Label(wrap=True, xalign=0.0, visible=False,
                                 css_classes=["card"])
        try_box.append(self.try_btn)
        try_box.append(self.try_out)
        box.append(try_box)

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
        self.mic_lbl.set_text(mic_text)
        self.mic_row.set_state(_OK if default else _WARN)

        from .. import backends
        name = str(cfg.get("model", {}).get("name", "auto"))
        active = (backends.resolve_model_name(name) if name in ("", "auto")
                  else backends.ALIASES.get(name.lower(), name.lower()))
        self.model_lbl.set_text(
            (f"{active} — switch or download from Settings → Models"
             if active else
             "none yet — open Settings to download one (tiny is ~75 MB)"))
        self.model_row.set_state(_OK if active else _WARN)

        hk = cfg.get("hotkey", {})
        cancel = hk.get("cancel_key", "") or "Escape"
        self.hotkey_lbl.set_text(
            f"dictate {hk.get('key', '?')} · cancel {cancel} "
            "(works while the pill is up)")
        self.hotkey_row.set_state(_INFO)

        ai = cfg.get("ai", {})
        configured = bool(ai.get("api_key") or ai.get("base_url"))
        self.ai_lbl.set_text(
            "active — fine-tune in Settings" if ai.get("enabled")
            else "configured but off — enable in Settings" if configured
            else "optional — add an OpenAI-compatible endpoint in Settings")
        self.ai_row.set_state(_OK if ai.get("enabled") else _INFO)
        self.updates_row.set_state(_INFO)

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
