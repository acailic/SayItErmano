"""First-run onboarding window (macOS Welcome flow counterpart).

Checks mic, model, hotkeys and AI; offers a real 3-second dictation tryout
through the daemon's test-dictation socket action (nothing is typed into
apps). Writing the .onboarded marker only happens on "Start dictating".

v2 presentation: the checks render as a proper checklist (boxed rows with
status glyphs) instead of a wall of bold markup — the window is the app's
first impression.

First-use funnel (F-02/F-19): the speech-engine row is honest about the
model's real state (ready / downloading with live progress / not on disk
yet — observed through the shared models cache, so the daemon needs no
changes), the tryout waits out a cold model visibly and cancel-safely,
and a successful tryout reveals the guided final step: one REAL dictation
into an app of the user's choice with self-report buttons (graceful
degradation — we never automate the user's apps) routing "It didn't work"
to doctor-style actionable hints. Setup progress is counted in a
strictly local JSON (see onboarding_funnel) — opt-out checkbox, no
network, ever.
"""
from __future__ import annotations

import threading

from gi.repository import Adw, GLib, Gtk

from .. import model_catalog, model_download, paths
from .. import session as session_mod
from .client import Client
from .onboarding_funnel import FunnelCounters
from .onboarding_hints import insertion_failure_hints
from .style import load_style

# row status -> (symbolic icon, css classes)
_OK = ("object-select-symbolic", ["success"])
_WARN = ("dialog-warning-symbolic", ["warning"])
_INFO = ("dialog-information-symbolic", ["dim-label"])
_BUSY = ("emblem-synchronizing-symbolic", ["accent"])


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
        self.funnel = FunnelCounters()
        self.funnel.record("onboarding_started")

        # tryout state machine: "idle" | "waiting" (a model download is
        # running; the 1 s ticker advances us) | "busy" (socket call in
        # flight). _try_gen invalidates in-flight responses on cancel.
        self._try_state = "idle"
        self._try_gen = 0
        self._try_cancelled = False
        self._busy_retries = 0
        self._model_recorded = False
        self._tick_source = GLib.timeout_add(1000, self._first_use_tick)
        self.connect("close-request", self._on_close)

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
        self.try_cancel_btn = Gtk.Button(label="Stop waiting",
                                         visible=False,
                                         halign=Gtk.Align.CENTER)
        self.try_cancel_btn.connect("clicked", self._try_cancel_clicked)
        try_box.append(self.try_btn)
        try_box.append(self.try_out)
        try_box.append(self.try_cancel_btn)
        box.append(try_box)

        # -- guided transition (F-19): revealed by a successful tryout ----
        self.final_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                 spacing=10, visible=False)
        self.final_box.append(Gtk.Label(
            xalign=0.0, css_classes=["heading"],
            label="One last step — dictate for real"))
        self.final_lbl = Gtk.Label(wrap=True, xalign=0.0,
                                   css_classes=["dim-label"])
        self.final_box.append(self.final_lbl)
        report_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10,
                             halign=Gtk.Align.CENTER)
        self.report_ok_btn = Gtk.Button(label="I did it — text appeared",
                                        css_classes=["suggested-action"])
        self.report_ok_btn.connect("clicked", self._report_ok)
        self.report_fail_btn = Gtk.Button(label="It didn't work")
        self.report_fail_btn.connect("clicked", self._report_fail)
        report_row.append(self.report_ok_btn)
        report_row.append(self.report_fail_btn)
        self.final_box.append(report_row)
        self.final_out = Gtk.Label(wrap=True, xalign=0.0, visible=False,
                                   css_classes=["card"])
        self.final_box.append(self.final_out)
        box.append(self.final_box)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10,
                          halign=Gtk.Align.END)
        self.funnel_check = Gtk.CheckButton(
            label="keep local setup counters (JSON in the config dir; "
                  "no network, ever)",
            active=not self.funnel.opted_out())
        self.funnel_check.connect("toggled", self._funnel_toggled)
        buttons.append(self.funnel_check)
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
        self._cfg = self.c.masked_config()
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

        self._refresh_engine_row()

        hk = self._cfg.get("hotkey", {})
        cancel = hk.get("cancel_key", "") or "Escape"
        self.hotkey_lbl.set_text(
            f"dictate {hk.get('key', '?')} · cancel {cancel} "
            "(works while the pill is up)")
        self.hotkey_row.set_state(_INFO)

        ai = self._cfg.get("ai", {})
        configured = bool(ai.get("api_key") or ai.get("base_url"))
        self.ai_lbl.set_text(
            "active — fine-tune in Settings" if ai.get("enabled")
            else "configured but off — enable in Settings" if configured
            else "optional — add an OpenAI-compatible endpoint in Settings")
        self.ai_row.set_state(_OK if ai.get("enabled") else _INFO)
        self.updates_row.set_state(_INFO)

        self._final_step_text()
        self.try_btn.set_sensitive(self.c.daemon_alive())

    def _refresh_engine_row(self) -> None:
        """Honest engine row (F-02): the model NAME resolving says nothing
        about first-use readiness — surface downloaded / downloading with
        live progress / missing, watched through the shared models cache
        (the daemon's eager-warmup download is visible there)."""
        try:
            ready = model_download.model_readiness(self._cfg)
        except Exception:  # noqa: BLE001 - the row must never crash setup
            self.model_lbl.set_text("engine state unknown — Settings → Models")
            self.model_row.set_state(_WARN)
            return
        kind, name = ready["kind"], ready["name"]
        if kind == "remote":
            self.model_lbl.set_text(
                "remote STT endpoint — recordings go to the configured URL")
            self.model_row.set_state(_OK)
            self._record_model_ready(kind, name)
            return
        if ready["downloaded"]:
            self.model_lbl.set_text(f"{name} — ready")
            self.model_row.set_state(_OK)
            self._record_model_ready(kind, name)
            return
        prog = ready.get("progress")
        if prog is not None:
            self.model_lbl.set_text(
                f"{prog.describe()}\nthe tryout and your first dictation "
                "continue automatically once it's ready")
            self.model_row.set_state(_BUSY)
            return
        size = _size_note(kind, name)
        size_txt = f" ({size})" if size else ""
        self.model_lbl.set_text(
            f"{name} — not downloaded yet{size_txt}; the first dictation "
            "downloads it once, then everything runs locally")
        self.model_row.set_state(_WARN)

    def _record_model_ready(self, kind: str, name: str) -> None:
        if not self._model_recorded:
            self._model_recorded = True
            self.funnel.record("model_downloaded", model=name, kind=kind)

    def _first_use_tick(self) -> bool:
        """The 1 s heartbeat: refresh the engine row (download progress),
        advance a waiting tryout. Removed on window close."""
        self._refresh_engine_row()
        if self._try_state == "waiting":
            self._advance_tryout()
        return True

    def _on_close(self, *_a) -> bool:
        if self._tick_source is not None:
            GLib.source_remove(self._tick_source)
            self._tick_source = None
        return False  # propagate: the window closes

    # -- tryout -------------------------------------------------------------------

    def _try_dictation(self, _btn=None) -> None:
        self._try_gen += 1
        self._try_cancelled = False
        self._busy_retries = 0
        self.try_btn.set_sensitive(False)
        self.try_out.set_visible(True)
        self._try_state = "waiting"
        self._advance_tryout()

    def _advance_tryout(self) -> None:
        """One step of the tryout state machine: wait out a cold model
        visibly (the daemon's socket has a 15 s timeout — a first-use
        download outlives it, so we key off the observable cache instead
        of the socket response), then record."""
        if self._try_cancelled or self._try_state == "idle":
            return
        ready = model_download.model_readiness(self._cfg)
        if ready["kind"] != "remote" and not ready["downloaded"]:
            prog = ready.get("progress")
            if prog is not None:
                self.try_out.set_text(
                    f"{prog.describe()}\nthe tryout continues automatically "
                    "when the model is ready — or stop waiting and try "
                    "again later")
                self.try_cancel_btn.set_visible(True)
                return  # the 1 s ticker re-advances us
            size = _size_note(ready["kind"], ready["name"])
            size_txt = f" (~{size})" if size else ""
            self.try_out.set_text(
                f"first use: downloading the {ready['name']} model"
                f"{size_txt} — one-time, then everything runs locally")
            self.try_cancel_btn.set_visible(True)
        self._run_tryout_call()

    def _run_tryout_call(self) -> None:
        self._try_state = "busy"
        self.try_out.set_text("recording… speak now")
        gen = self._try_gen

        def work():
            try:
                resp = self.c.test_dictation(3.0)
            except Exception as e:  # noqa: BLE001 - surfaced as a payload
                resp = {"ok": False, "error": str(e)}
            GLib.idle_add(self._on_tryout_result, resp, gen)
        threading.Thread(target=work, daemon=True).start()

    def _on_tryout_result(self, resp: dict, gen: int) -> bool:
        if gen != self._try_gen:
            return False  # cancelled/superseded attempt: drop silently
        ready = model_download.model_readiness(self._cfg)
        cold = ready["kind"] != "remote" and not ready["downloaded"]
        err = str(resp.get("error") or "").lower()
        if not resp.get("ok"):
            if cold and (ready.get("progress") is not None
                         or "timed out" in err or "busy" in err):
                # a cold model explains it (15 s socket timeout / daemon
                # busy in its own ensure_backend download): wait it out
                # visibly, keyed off the observable cache
                self._try_state = "waiting"
                self._advance_tryout()
                return False
            if not cold and ("busy" in err or "timed out" in err) \
                    and self._busy_retries < 5:
                self._busy_retries += 1  # daemon finishing the last attempt
                self._try_state = "waiting"
                self.try_out.set_text(
                    "the engine is still finishing the previous attempt — "
                    "continuing automatically…")
                return False
            self._busy_retries = 0
            self._show_tryout(resp, hint=_tryout_hint(resp, ready))
            return False
        self._busy_retries = 0
        self._show_tryout(resp)
        return False

    def _show_tryout(self, resp: dict, hint: str | None = None) -> None:
        """Terminal tryout display: success reveals the guided final step;
        failure shows the error plus an actionable hint when we have one."""
        self._try_state = "idle"
        self.try_btn.set_sensitive(True)
        self.try_cancel_btn.set_visible(False)
        if resp.get("ok"):
            text = str(resp.get("text") or "").strip() or "(silence — nothing transcribed)"
            self.try_out.set_markup(
                f"<b>heard you ({resp.get('duration_s', 0)} s):</b>\n{text}")
            self.funnel.record("tryout_ok")
            self.final_box.set_visible(True)  # the guided real-insertion step
        else:
            msg = f"failed: {resp.get('error', 'unknown')}"
            self.try_out.set_text(f"{msg}\n{hint}" if hint else msg)

    def _try_cancel_clicked(self, _btn=None) -> None:
        """Abandon the tryout wait. Safe by construction: the pending
        socket response is dropped (generation bump), the daemon finishes
        harmlessly in the background, and a cancelled download leaves no
        partial file (model_download's .part discipline)."""
        self._try_gen += 1
        self._try_cancelled = True
        self._try_state = "idle"
        self.try_btn.set_sensitive(True)
        self.try_cancel_btn.set_visible(False)
        self.try_out.set_visible(True)
        self.try_out.set_text(
            "cancelled — nothing was half-written; the model download "
            "(if any) continues in the background")

    # -- guided transition to a real insertion (F-19) ------------------------------

    def _hotkey_phrase(self) -> str:
        key = self._cfg.get("hotkey", {}).get("key", "?")
        info = session_mod.probe()
        if info.is_wayland:
            return (f"press the shortcut you bound in your desktop "
                    f"settings to the toggle script (the on-screen "
                    f"`{key}` grab only exists on X11) — or run "
                    f"`sayit-ermano toggle` in a terminal")
        return f"press {key}"

    def _final_step_text(self) -> None:
        self.final_lbl.set_text(
            "Open any app you like — an editor, a chat, the browser "
            f"address bar. {self._hotkey_phrase()}, say a phrase, and watch "
            "the text appear where the cursor is. Same engine you just "
            "tried, same privacy: everything stays on this machine.")

    def _report_ok(self, _btn=None) -> None:
        self.funnel.record("real_insertion_reported_ok")
        self.final_out.set_visible(True)
        self.final_out.set_text(
            "That's the whole loop — press the key whenever you want to "
            "dictate. Every take is saved in History if you ever need it "
            "again.")
        self.report_fail_btn.set_sensitive(False)

    def _report_fail(self, _btn=None) -> None:
        self.funnel.record("real_insertion_reported_failed")
        try:
            status = self.c.status()
        except Exception:  # noqa: BLE001 - hints must render regardless
            status = None
        hints = insertion_failure_hints(status, self._cfg)
        self.final_out.set_visible(True)
        self.final_out.set_text(
            "Let's fix it — most first-run failures are one of these:\n"
            + "\n".join(hints)
            + "\n\nAfter fixing, dictate once more and report back.")

    def _funnel_toggled(self, btn) -> None:
        if not btn.get_active():
            self.funnel.opt_out()

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


def _size_note(kind: str, name: str) -> str | None:
    """Catalog size blurb ("~145 MB") for the model about to download."""
    if kind == "faster-whisper":
        return model_catalog.MODEL_CATALOG.get(name, {}).get("size")
    if kind == "whisper.cpp":
        return model_catalog.GGUF_CATALOG.get(name, {}).get("size")
    if kind == "parakeet":
        return model_catalog.PARAKEET_CATALOG.get(name, {}).get("size")
    return None


def _tryout_hint(resp: dict, ready: dict) -> str | None:
    """Actionable follow-up line for a failed tryout (None when the raw
    error already says what to do - mic guidance etc.)."""
    err = str(resp.get("error") or "").lower()
    cold = ready.get("kind") != "remote" and not ready.get("downloaded")
    if cold:
        return ("the speech model is not on disk yet — the one-time "
                "download may have failed; check the network and press "
                "Record again (partial data is never kept), or run "
                "`sayit-ermano doctor`")
    if "timed out" in err:
        return "the engine took too long to answer — press Record again"
    return None
