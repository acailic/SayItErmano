"""Offscreen GTK smoke tests for the native settings/history app.

Skipped without GTK4/libadwaita or a display, so the default suite stays
green on headless boxes. Windows are driven with a stub client: no daemon,
no real config/history files.
"""
from __future__ import annotations

import copy
import os
import time

import pytest

gi = pytest.importorskip("gi", reason="PyGObject not installed")
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
except ValueError as e:  # pragma: no cover - depends on the machine
    pytest.skip(f"GTK4/Adw unavailable: {e}", allow_module_level=True)
if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
    pytest.skip("no display for GTK tests", allow_module_level=True)  # pragma: no cover

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from fluidvoice.config import DEFAULTS  # noqa: E402
from fluidvoice.gtkui.client import Client  # noqa: E402

Adw.init()


class StubClient(Client):
    """Deterministic client: fixture config/history, records saves."""

    def __init__(self, entries=None):
        super().__init__()
        self.entries = entries or []
        self.saved: list[dict] = []
        self.selected_models: list[str] = []
        self.inserted: list[str] = []
        self.updated: list[tuple] = []
        self.suggestions: list[dict] = []
        self.accepted: list[tuple] = []
        self.dismissed: list[tuple] = []
        self.profile_store: dict[str, str] = {}
        self.profile_calls: list[tuple] = []
        self.deleted_models: list[tuple] = []
        self.reruns: list[tuple] = []

    def status(self):
        return {"ok": True, "recording": False, "busy": False,
                "backend": "faster-whisper", "cuda": True,
                "warmup": {"running": False, "error": None, "model": None}}

    def mics(self):
        # hermetic override: the inherited Client.mics() would query the
        # control socket (the REAL daemon on a dev box) and fall back to
        # `pactl`. No gtkui test asserts the device list, so pin it empty.
        return []

    def daemon_alive(self):
        return True

    def get_config(self):
        cfg = copy.deepcopy(DEFAULTS)
        cfg["ai"]["per_app_prompts"] = [
            {"apps": ["zed"], "instructions": "keep it terse"}]
        cfg["processing"]["dictionary"] = [
            {"triggers": ["miro board"], "replacement": "Miro board"}]
        cfg["processing"]["filler_words"] = ["um", "uh", "eh"]
        cfg["recording"]["mic_priority"] = ["bluez", "usb-cam"]
        cfg["general"]["language"] = "sl"
        return cfg, True

    def set_config(self, body):
        self.saved.append(body)
        changed = [f"{s}.{k}" for s, keys in body.items() for k in keys]
        return {"ok": True, "changed": changed, "rejected": [],
                "restart_required": [], "errors": [], "note": ""}

    def select_model(self, name):
        self.selected_models.append(name)
        return {"ok": True, "model": name}

    def history(self, q="", limit=200):
        q = (q or "").lower()
        return [e for e in self.entries
                if q in str(e.get("text", "")).lower()
                or q in str(e.get("app", "")).lower()]

    def history_delete(self, ts):
        before = len(self.entries)
        self.entries = [e for e in self.entries if e.get("ts") != ts]
        return before - len(self.entries)

    def history_clear(self):
        n = len(self.entries)
        self.entries = []
        return n

    def history_update_text(self, ts, text):
        self.updated.append((ts, text))
        for e in self.entries:
            if abs(e.get("ts", 0) - ts) < 1e-6:
                e["text"] = text
                return True
        return False

    def insert_text(self, text):
        self.inserted.append(text)
        return {"ok": True}

    def command_rerun(self, command, purpose=None):
        self.reruns.append((command, purpose))
        return {"ok": True, "pending": True, "command": command}

    def today_stats(self):
        return {"dictations": 2, "seconds": 6.5, "words": 9}

    def export_zip(self, path):
        self.exported_to = path
        return len(self.entries), ["skipped missing audio: x.wav"]

    def test_dictation(self, seconds=3.0):
        return {"ok": True, "text": "hello world", "duration_s": 3.0}

    # -- dictionary auto-learning (mirrors Client's three methods) ---------

    def dict_suggestions(self):
        return list(self.suggestions)

    def dict_suggestion_accept(self, heard, corrected):
        from fluidvoice.processing import dict_learn
        cfg, _ = self.get_config()
        merged = dict_learn.accept_merge(
            cfg["processing"]["dictionary"], heard, corrected)
        self.set_config({"processing": {"dictionary": merged}})
        self.accepted.append((heard, corrected))
        self.suggestions = [s for s in self.suggestions
                            if (s.get("heard"), s.get("corrected"))
                            != (heard, corrected)]
        return {"ok": True, "dictionary": merged, "changed":
                ["processing.dictionary"], "rejected": [], "errors": []}

    def dict_suggestion_dismiss(self, heard, corrected):
        self.dismissed.append((heard, corrected))
        self.suggestions = [s for s in self.suggestions
                            if (s.get("heard"), s.get("corrected"))
                            != (heard, corrected)]

    # -- prompt profiles (mirrors Client's four methods) ----------------------

    def prompt_profiles(self):
        return dict(self.profile_store)

    def prompt_profile_save(self, name, prompt):
        self.profile_calls.append(("save", name, prompt))
        self.profile_store[name] = prompt
        return {"ok": True, "error": None,
                "profiles": dict(self.profile_store)}

    def prompt_profile_rename(self, old, new):
        self.profile_calls.append(("rename", old, new))
        if old not in self.profile_store:
            return {"ok": False, "error": f"no profile named {old!r}",
                    "profiles": dict(self.profile_store)}
        self.profile_store = {(new if k == old else k): v
                              for k, v in self.profile_store.items()}
        return {"ok": True, "error": None,
                "profiles": dict(self.profile_store)}

    def prompt_profile_delete(self, name):
        self.profile_calls.append(("delete", name))
        if name not in self.profile_store:
            return {"ok": False, "error": f"no profile named {name!r}",
                    "profiles": dict(self.profile_store)}
        del self.profile_store[name]
        return {"ok": True, "error": None,
                "profiles": dict(self.profile_store)}

    # -- model pruning (mirrors Client.model_delete) --------------------------

    def model_delete(self, kind, name):
        self.deleted_models.append((kind, name))
        return {"ok": True, "path": f"/cache/{kind}/{name}", "bytes": 1234}


@pytest.fixture()
def loop():
    return GLib.MainLoop()


def pump(loop, ms=150):
    GLib.timeout_add(ms, loop.quit)
    loop.run()


def pump_until(loop, cond, timeout_s=2.0):
    """Pump in short slices until cond() is truthy. A single fixed pump
    window is flaky under load: its quit timeout (priority 0) can starve
    GLib.idle_add sources (priority 200), so the idle callback may not
    have run by the time the loop quits."""
    deadline = time.monotonic() + timeout_s
    while not cond() and time.monotonic() < deadline:
        pump(loop, ms=20)
    return cond()


ENTRIES = [
    {"ts": 1756800000.0, "text": "first entry", "duration_s": 2.5,
     "app": "firefox", "ai": False},
    {"ts": 1756800600.0, "text": "polished entry", "duration_s": 4.0,
     "ai": True, "audio": True},
]


# ---------------------------------------------------------------------------
# Shared windows (audit D3). Constructing + presenting each window costs
# 0.5-1 s of NATIVE GTK first-frame work (style/layout/render against the
# real display), and ~50 tests each paying it made this file roughly half
# the suite's wall time. The two windows below are built once per module
# and handed to tests in a pristine state: per-class autouse resets
# install a fresh StubClient and re-run the production reload paths
# (SettingsWindow._load() — exactly what the Discard button runs — and
# HistoryWindow._load_history() + _apply_status()), so every assertion
# sees the same deterministic state a brand-new window would show. Tests
# needing a different client swap `w.c` and reload; the next test's reset
# wipes whatever they left behind. Windows whose CONSTRUCTION itself is
# under test (e.g. action registration, env-dependent wayland rows, the
# unknown-language combo refill) still build their own instance.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def settings_win():
    from fluidvoice.gtkui.settings_window import SettingsWindow
    w = SettingsWindow(client=StubClient())
    w.present()
    pump(GLib.MainLoop())
    yield w
    w._dirty = False  # never offer the discard dialog during teardown
    w.close()


def reset_settings(w, loop) -> StubClient:
    """Pristine settings window: fresh stub client + full reload.
    Returns the installed client. No main-loop pump needed: _load() is
    synchronous, schedules no idles on this path, and the tests assert
    stored row titles/subtitles/values, not laid-out geometry."""
    w.c = StubClient()
    w._load()
    w._apply_about_update({})  # About -> Update row back to "checks disabled"
    return w.c


@pytest.fixture(scope="module")
def hist_win():
    from fluidvoice.gtkui import main_window as mw

    class _GlibNoRecurring:
        """Delegates to GLib except timer arming: HistoryWindow.__init__
        starts a 2 s status poll and discards the source id, so a
        long-lived shared instance would keep mutating status widgets
        mid-test. Per-test windows never live 2 s, so no test can observe
        the poll — blocking it keeps the shared window equivalent."""
        def __getattr__(self, name):
            return getattr(GLib, name)

        @staticmethod
        def timeout_add_seconds(*_args, **_kwargs):
            return 0

    real = mw.GLib
    mw.GLib = _GlibNoRecurring()
    try:
        w = mw.HistoryWindow(client=StubClient(ENTRIES))
    finally:
        mw.GLib = real
    w.present()
    pump(GLib.MainLoop())
    yield w
    w.close()


def reset_history(w, loop, entries=ENTRIES) -> StubClient:
    """Pristine history window: fresh stub client + full reload (both
    listboxes, count, today line, status banner, visible page). No
    main-loop pump needed: the reload is synchronous and the tests assert
    stored strings/row counts, not laid-out geometry."""
    w.c = StubClient(entries)
    w._query = ""
    w._exporting = False
    w._load_history()
    w._apply_status(w.c.status())
    w.view_stack.set_visible_child(
        w.view_stack.get_child_by_name("transcripts"))
    return w.c


class TestHistoryWindow:
    @pytest.fixture(autouse=True)
    def _fresh(self, hist_win, loop):
        reset_history(hist_win, loop)

    def test_populates_and_search_filters(self, hist_win):
        w = hist_win
        assert w._entries and len(w._entries) == 2
        w._query = "polished"
        w._load_history()
        assert len(w._entries) == 1
        w._query = "firefox"
        w._load_history()
        assert len(w._entries) == 1 and w._entries[0]["app"] == "firefox"

    def test_status_reflects_daemon(self, hist_win):
        w = hist_win
        w._apply_status({"recording": True, "busy": False,
                         "backend": "b", "cuda": False})
        assert w.state_lbl.get_text() == "recording"
        w._apply_status(None)
        assert w.down_banner.get_revealed() is True

    def test_today_line_renders(self, hist_win):
        w = hist_win
        assert w.today_lbl.get_text() == "today: 2 dictations, 0:06 minutes, 9 words"
        w._load_history()  # refresh path updates it too
        assert w.today_lbl.get_text() == "today: 2 dictations, 0:06 minutes, 9 words"

    def test_today_line_survives_client_error(self, hist_win):
        w = hist_win
        c = StubClient(ENTRIES)

        def boom():
            raise RuntimeError("unreadable")

        c.today_stats = boom
        w.today_lbl.set_text("")  # the empty label a fresh window starts with
        w.c = c
        w.refresh()  # construction-time path: status + today line refresh
        assert w.today_lbl.get_text() == ""  # unset, not a crash

    def test_export_action_registered(self, loop, monkeypatch):
        # patches install_action on the CLASS -> must wrap this window's
        # own construction; cannot reuse the shared instance
        from fluidvoice.gtkui import main_window as mw
        installed = {}

        def spy(self, name, param, handler):
            installed[name] = handler  # record; no need to install here

        monkeypatch.setattr(mw.HistoryWindow, "install_action", spy)
        w = mw.HistoryWindow(client=StubClient(ENTRIES))
        w.present()
        pump(loop)
        # GTK 4.14 offers no lookup for widget-installed actions, so the
        # registration is verified through the install call itself
        assert callable(installed.get("hist.export"))
        assert installed["hist.export"] == w._on_export
        assert w._exporting is False
        # user-visible wiring: the menu offers Export… -> win.hist.export
        assert w.menu_model.get_n_items() == 2
        s = GLib.VariantType.new("s")
        label = w.menu_model.get_item_attribute_value(0, "label", s).get_string()
        action = w.menu_model.get_item_attribute_value(0, "action", s).get_string()
        assert label == "Export…" and action == "win.hist.export"
        w.close()

    def test_export_smoke(self, hist_win, loop, tmp_path, monkeypatch):
        w = hist_win
        c = w.c  # fresh client installed by the reset
        enabled: list[tuple[str, bool]] = []
        monkeypatch.setattr(w, "action_set_enabled",
                            lambda name, on: enabled.append((name, on)))
        target = tmp_path / "h.zip"
        w._export_to(str(target))
        assert w._exporting is True  # busy until the idle callback runs
        assert enabled == [("hist.export", False)]
        assert pump_until(loop, lambda: not w._exporting)  # run the idle callback
        assert c.exported_to == str(target)
        assert w._exporting is False
        assert enabled == [("hist.export", False), ("hist.export", True)]

    def test_export_failure_toasts_and_reenables(self, hist_win, loop, tmp_path,
                                                 monkeypatch):
        w = hist_win
        c = StubClient(ENTRIES)

        def broken(path):
            raise OSError("no space")

        c.export_zip = broken
        w.c = c
        enabled: list[tuple[str, bool]] = []
        monkeypatch.setattr(w, "action_set_enabled",
                            lambda name, on: enabled.append((name, on)))
        w._export_to(str(tmp_path / "h.zip"))
        assert pump_until(loop, lambda: not w._exporting)
        assert w._exporting is False  # released even on failure
        assert enabled[-1] == ("hist.export", True)


COMMAND_ENTRIES = [
    {"ts": 1756801200.0, "mode": "command", "text": "$ ls -la",
     "command": "ls -la", "purpose": "checking files", "exit_code": 0,
     "success": True, "output": "total 0\n", "duration_ms": 12.0},
    {"ts": 1756801300.0, "mode": "command", "text": "$ rm -rf /tmp/x",
     "command": "rm -rf /tmp/x", "purpose": "cleanup", "exit_code": 1,
     "success": False, "output": "rm: cannot remove", "duration_ms": 5.0,
     "destructive": True},
]


class TestCommandsView:
    """History window Commands page (v2): command rows, collapsible
    output, Copy, confirm-gated Re-run."""

    @pytest.fixture(autouse=True)
    def _fresh(self, hist_win, loop):
        reset_history(hist_win, loop, ENTRIES + COMMAND_ENTRIES)

    def _rows(self, w):
        rows = []
        row = w.cmd_listbox.get_first_child()
        while row is not None:
            rows.append(row)
            row = row.get_next_sibling()
        return rows

    def _show_commands(self, w):
        w.view_stack.set_visible_child(
            w.view_stack.get_child_by_name("commands"))

    def test_commands_page_lists_rows_excluding_dictations(self, hist_win):
        w = hist_win
        rows = self._rows(w)
        assert len(rows) == 2                   # dictations excluded
        first = rows[0]
        assert first.command == "ls -la"
        # the transcripts page still renders everything (headers excluded
        # from the count - they are plain ListBoxRows, not entry cards)
        from fluidvoice.gtkui.main_window import HistoryEntryRow
        t_rows = []
        r = w.listbox.get_first_child()
        while r is not None:
            if isinstance(r, HistoryEntryRow):
                t_rows.append(r)
            r = r.get_next_sibling()
        assert len(t_rows) == len(ENTRIES) + len(COMMAND_ENTRIES)

    def test_count_label_follows_visible_page(self, hist_win):
        w = hist_win
        assert w.count_lbl.get_text().startswith(f"{len(ENTRIES) + 2}")
        self._show_commands(w)
        assert w.count_lbl.get_text() == "2 commands"

    def test_search_filters_command_rows(self, hist_win):
        w = hist_win
        w._query = "rm -rf"
        w._load_history()
        rows = self._rows(w)
        assert len(rows) == 1 and rows[0].command == "rm -rf /tmp/x"
        w._query = ""
        w._load_history()
        assert len(self._rows(w)) == 2

    def test_output_toggle_reveals_collapsible_output(self, hist_win):
        w = hist_win
        row = self._rows(w)[0]
        assert row.output_revealer.get_reveal_child() is False
        row.output_btn.set_active(True)
        assert row.output_revealer.get_reveal_child() is True
        lbl = row.output_revealer.get_child()
        assert "total 0" in lbl.get_text()
        row.output_btn.set_active(False)
        assert row.output_revealer.get_reveal_child() is False

    def test_copy_puts_command_on_clipboard(self, hist_win, monkeypatch):
        from fluidvoice.gtkui import main_window as mw
        copied = []

        class FakeClipboard:
            def set_text(self, text):
                copied.append(text)

        class FakeDisplay:
            @staticmethod
            def get_default():
                return FakeDisplay()

            def get_clipboard(self):
                return FakeClipboard()

        monkeypatch.setattr(mw.Gdk, "Display", FakeDisplay)
        toasts = []
        w = hist_win
        monkeypatch.setattr(w, "_toast", lambda text: toasts.append(text))
        row = self._rows(w)[1]
        row.copy_btn.emit("clicked")
        assert copied == ["rm -rf /tmp/x"]     # the command, not the label
        assert any("copied" in t.lower() for t in toasts)

    def test_rerun_calls_client_and_toasts(self, hist_win, monkeypatch):
        toasts = []
        w = hist_win
        c = w.c  # fresh client installed by the reset
        monkeypatch.setattr(w, "_toast", lambda text: toasts.append(text))
        row = self._rows(w)[0]
        row.rerun_btn.emit("clicked")
        assert c.reruns == [("ls -la", "checking files")]
        assert any("confirm" in t.lower() for t in toasts)

    def test_rerun_failure_toasts_error(self, hist_win, monkeypatch):
        from fluidvoice.gtkui.client import ClientError
        toasts = []
        w = hist_win
        c = w.c
        monkeypatch.setattr(w, "_toast", lambda text: toasts.append(text))

        def broken(command, purpose=None):
            raise ClientError("daemon not running")

        c.command_rerun = broken
        row = self._rows(w)[0]
        row.rerun_btn.emit("clicked")
        assert any("Re-run failed" in t for t in toasts)
        assert c.reruns == []


class TestSettingsWindow:
    @pytest.fixture(autouse=True)
    def _fresh(self, settings_win, loop):
        reset_settings(settings_win, loop)

    def test_loads_every_section(self, settings_win):
        w = settings_win
        # every whitelisted settings family has a row registered
        fams = {sec for sec, _k in w._rows}
        assert {"general", "hotkey", "recording", "model", "processing",
                "ai", "insertion", "sounds", "notifications",
                "history"} <= fams
        assert len(w._rows) >= 40
        # command mode rows: hotkey capture + the [command] group (AI page)
        titles = {r.get_title() for r in w._rows.values()
                  if hasattr(r, "get_title")}
        assert any("Command key" in t for t in titles)
        assert any("Max agent turns" in t for t in titles)
        assert any("Working directory" in t for t in titles)
        assert ("command", "max_turns") in w._rows
        assert ("command", "confirm_timeout_s") in w._rows
        # About page reflects the daemon status poll (spec: backend, CUDA)
        assert w.about_backend_row.get_title() == "Backend"
        assert w.about_backend_row.get_subtitle() == "faster-whisper"
        assert w.about_gpu_row.get_title() == "GPU (CUDA)"
        assert w.about_gpu_row.get_subtitle() == "yes"

    def test_collect_roundtrip_and_save(self, settings_win):
        w = settings_win
        c = w.c  # fresh client installed by the reset
        body = w._collect()
        assert body["general"]["language"] == "sl"  # from the stub cfg
        assert body["sounds"]["volume"] == 1.0
        assert body["hotkey"]["modifiers"] == []
        assert body["ai"]["per_app_prompts"] == [
            {"apps": ["zed"], "instructions": "keep it terse"}]
        # flip two controls -> dirty -> save posts them
        w._rows[("sounds", "enabled")].set_active(False)
        w._rows[("recording", "preview_enabled")].set_active(False)
        assert w._dirty is True
        w.save()
        assert c.saved and c.saved[-1]["sounds"]["enabled"] is False
        assert c.saved[-1]["recording"]["preview_enabled"] is False

    def test_idle_unload_spinbutton_roundtrip(self, settings_win, loop):
        class CfgClient(StubClient):
            def __init__(self):
                super().__init__()
                self._cfg = copy.deepcopy(DEFAULTS)

            def get_config(self):
                return copy.deepcopy(self._cfg), True

        w = settings_win
        c = CfgClient()
        w.c = c
        w._load()
        pump(loop)
        # default off: row 0, collect sends 0 seconds
        assert w._idle_unload_row.get_value() == 0
        assert w._collect()["model"]["idle_unload_s"] == 0
        # 5 minutes -> 300 seconds, and the row marks the page dirty
        w._idle_unload_row.set_value(5)
        assert w._dirty is True
        assert w._collect()["model"]["idle_unload_s"] == 300
        # a config in seconds loads back as whole minutes
        c._cfg["model"]["idle_unload_s"] = 300
        w._load()
        pump(loop)
        assert w._idle_unload_row.get_value() == 5
        # hand-edited sub-minute values (30-89 s) round up to 1 minute
        c._cfg["model"]["idle_unload_s"] = 45
        w._load()
        pump(loop)
        assert w._idle_unload_row.get_value() == 1

    def test_remote_stt_group(self, settings_win, loop):
        """Models page: Remote (OpenAI-compatible) rows, empty-URL-is-off
        collect, password hygiene, timeout spin, backend combo."""
        class CfgClient(StubClient):
            def __init__(self):
                super().__init__()
                self._cfg = copy.deepcopy(DEFAULTS)

            def get_config(self):
                return copy.deepcopy(self._cfg), True

        w = settings_win
        c = CfgClient()
        w.c = c
        w._load()
        pump(loop)
        # rows registered + backend combo offers remote
        assert ("model", "remote_url") in w._rows
        assert ("model", "remote_model") in w._rows
        assert ("model", "remote_api_key") in w._rows
        assert ("model", "remote_timeout_s") in w._rows
        assert "remote" in w._combo_values[("model", "backend")]
        # unconfigured: empty URL still POSTs (off is a real value), the
        # key is omitted when the password field is empty
        body = w._collect()
        assert body["model"]["remote_url"] == ""
        assert "remote_api_key" not in body["model"]
        assert body["model"]["remote_timeout_s"] == 30
        # typing values -> they post
        w._rows[("model", "remote_url")].set_text("http://lan:8000")
        w._rows[("model", "remote_model")].set_text("whisper-large-v3")
        w._rows[("model", "remote_api_key")].entry.set_text("sk-typed")
        w._rows[("model", "remote_timeout_s")].set_value(45)
        body = w._collect()
        assert body["model"]["remote_url"] == "http://lan:8000"
        assert body["model"]["remote_model"] == "whisper-large-v3"
        assert body["model"]["remote_api_key"] == "sk-typed"
        assert body["model"]["remote_timeout_s"] == 45
        # a configured cfg loads back; the State row names the remote host
        c._cfg["model"].update(remote_url="http://lan:8000",
                                remote_model="whisper-large-v3")
        w._load()
        pump(loop)
        assert w._rows[("model", "remote_url")].get_text() == "http://lan:8000"
        assert "remote (whisper-large-v3 @ lan:8000)" in \
            w.warmup_row.get_subtitle()
        # masking: a stored key NEVER renders, whatever its type
        c._cfg["model"]["remote_api_key"] = True  # masked bool (daemon up)
        w._load()
        pump(loop)
        assert w._rows[("model", "remote_api_key")].get_text() == ""
        assert "saved" in w._rows[("model", "remote_api_key")].row.get_subtitle()
        c._cfg["model"]["remote_api_key"] = "sk-real-secret"  # file-only mode
        w._load()
        pump(loop)
        assert w._rows[("model", "remote_api_key")].get_text() == ""
        assert "sk-real-secret" not in w._rows[
            ("model", "remote_api_key")].row.get_subtitle()

    def test_per_app_rule_editing(self, settings_win):
        w = settings_win
        assert len(w._rule_rows) == 1  # loaded from cfg
        w._add_rule({"apps": ["firefox"], "instructions": "bullets"})
        w._rule_rows[0]["apps"].set_text("zed, code")
        w._rule_rows[0]["buf"].set_text("be terse")
        rules = w._collect_rules()
        assert {"apps": ["zed", "code"], "instructions": "be terse"} in rules
        assert {"apps": ["firefox"], "instructions": "bullets"} in rules

    def test_dictionary_and_filler_editing(self, settings_win):
        w = settings_win
        # loaded from cfg
        assert len(w._dict_rows) == 1
        body = w._collect()
        assert body["processing"]["dictionary"] == [
            {"triggers": ["miro board"], "replacement": "Miro board"}]
        assert body["processing"]["filler_words"] == ["um", "uh", "eh"]
        assert body["general"]["language"] == "sl"
        # edit: add a word, change the filler list
        w._add_dict_word({"triggers": ["k8s"], "replacement": "Kubernetes"})
        w._dict_rows[0]["trig"].set_text("miro board, miro")
        w._dict_rows[0]["repl"].set_text("Miro board")
        w._rows[("processing", "filler_words")].row.set_text("um, ehm")
        body = w._collect()
        assert {"triggers": ["miro board", "miro"],
                "replacement": "Miro board"} in body["processing"]["dictionary"]
        assert {"triggers": ["k8s"], "replacement": "Kubernetes"} in \
            body["processing"]["dictionary"]
        assert body["processing"]["filler_words"] == ["um", "ehm"]
        # removal prunes the edited (first) entry, keeps the rest
        w._remove_dict_word(None, w._dict_rows[0])
        remaining = w._collect()["processing"]["dictionary"]
        assert {"triggers": ["miro board", "miro"],
                "replacement": "Miro board"} not in remaining
        assert remaining == [{"triggers": ["k8s"],
                              "replacement": "Kubernetes"}]

    def test_mic_priority_editor(self, settings_win):
        w = settings_win
        c = w.c  # fresh client installed by the reset
        assert len(w._mic_prio_rows) == 2  # loaded from cfg
        assert w._collect()["recording"]["mic_priority"] == \
            ["bluez", "usb-cam"]
        w._add_mic_prio("")
        w._mic_prio_rows[2]["row"].set_text("pci")
        assert w._collect()["recording"]["mic_priority"] == \
            ["bluez", "usb-cam", "pci"]
        assert w._dirty is True
        w._move_mic_prio(w._mic_prio_rows[2], -1)
        assert w._collect()["recording"]["mic_priority"] == \
            ["bluez", "pci", "usb-cam"]
        w._move_mic_prio(w._mic_prio_rows[0], -1)  # edge: ignored
        assert w._collect()["recording"]["mic_priority"] == \
            ["bluez", "pci", "usb-cam"]
        w._remove_mic_prio(w._mic_prio_rows[1])
        assert w._collect()["recording"]["mic_priority"] == \
            ["bluez", "usb-cam"]
        w.save()
        assert c.saved[-1]["recording"]["mic_priority"] == \
            ["bluez", "usb-cam"]

    def test_unknown_language_stays_selectable(self, loop):
        # own window: the unknown-language combo REFILL (appending
        # "zz (saved)") is never undone by a later _load, so it must not
        # leak into the shared instance
        from fluidvoice.gtkui.settings_window import SettingsWindow
        c = StubClient()
        c.get_config = lambda: (dict(copy.deepcopy(DEFAULTS), general={
            **copy.deepcopy(DEFAULTS)["general"], "language": "zz"}), True)
        w = SettingsWindow(client=c)
        w.present()
        pump(loop)
        values = w._combo_values[("general", "language")]
        assert "zz" in values and "auto" in values
        assert w._collect()["general"]["language"] == "zz"
        w.close()

    def test_key_capture_maps_to_config_names(self):
        from gi.repository import Gdk

        from fluidvoice.gtkui.settings_window import _keyname
        assert _keyname(Gdk.keyval_from_name("Control_R")) == "Right_Control"
        assert _keyname(Gdk.keyval_from_name("F9")) == "F9"
        assert _keyname(Gdk.keyval_from_name("space")) == "space"
        assert _keyname(Gdk.keyval_from_name("q")) == "q"

    # -- whisper.cpp GGUF group --------------------------------------------------

    def test_gguf_group_rows_built(self, settings_win):
        from fluidvoice import model_catalog
        w = settings_win
        assert len(model_catalog.GGUF_CATALOG) == 7
        assert len(w._gguf_rows) == 7
        assert {r.get_title() for r in w._gguf_rows} == set(model_catalog.GGUF_CATALOG)

    def test_active_gguf_marker(self, settings_win, loop):
        w = settings_win
        c = StubClient()

        def gg_cfg():
            cfg = copy.deepcopy(DEFAULTS)
            cfg["model"] = {**cfg["model"], "backend": "whisper.cpp",
                            "whispercpp_model": "ggml-small.bin"}
            return cfg, True

        c.get_config = gg_cfg
        w.c = c
        w._load()
        pump(loop)
        assert w._active_gguf() == "ggml-small.bin"

        def walk(widget):  # find suffix widgets under a row
            yield widget
            child = widget.get_first_child()
            while child:
                yield from walk(child)
                child = child.get_next_sibling()

        active_row = next(r for r in w._gguf_rows
                          if r.get_title() == "ggml-small.bin")
        labels = [x.get_text() for x in walk(active_row)
                  if isinstance(x, Gtk.Label)]
        assert "Active" in labels

    def test_download_flow_uses_worker_and_polls(self, settings_win, loop,
                                                 monkeypatch):
        from fluidvoice import model_catalog
        from fluidvoice.gtkui import settings_window as sw
        w = settings_win
        downloaded = {"now": False}
        monkeypatch.setattr(sw.model_catalog, "gguf_downloaded",
                            lambda n: downloaded["now"])
        calls: list[tuple[str, list]] = []

        def fake_download(name, progress=None):
            seen: list = []
            calls.append((name, seen))
            if progress:
                seen.append(progress(50, 100))
                seen.append(progress(100, 100))
            return model_catalog.gguf_path(name)

        monkeypatch.setattr(sw.model_download, "download_gguf", fake_download)
        w._download_gguf(None, "ggml-small.bin")
        assert pump_until(loop, lambda: w._gguf_dl["ggml-small.bin"].get("done"))
        st = w._gguf_dl["ggml-small.bin"]
        assert calls and calls[0][0] == "ggml-small.bin"
        assert st["total"] == 100 and st["error"] is None
        downloaded["now"] = True
        w._refresh_models()

        def walk(widget):
            yield widget
            child = widget.get_first_child()
            while child:
                yield from walk(child)
                child = child.get_next_sibling()

        row = next(r for r in w._gguf_rows if r.get_title() == "ggml-small.bin")
        buttons = [x.get_label() for x in walk(row) if isinstance(x, Gtk.Button)]
        assert buttons == ["Use"]

    def test_download_failure_toasts(self, settings_win, loop, monkeypatch):
        from fluidvoice.gtkui import settings_window as sw
        w = settings_win
        monkeypatch.setattr(sw.model_catalog, "gguf_downloaded", lambda n: False)

        def broken(name, progress=None):
            raise OSError("net down")

        monkeypatch.setattr(sw.model_download, "download_gguf", broken)
        toasts: list[str] = []
        monkeypatch.setattr(w, "toast", lambda text, timeout=5: toasts.append(text))
        w._download_gguf(None, "ggml-base.bin")
        assert pump_until(loop, lambda: w._gguf_dl["ggml-base.bin"].get("error"))
        assert w._gguf_dl["ggml-base.bin"]["error"] == "net down"
        assert pump_until(loop, lambda: any("net down" in t for t in toasts))

    def test_use_gguf_posts_config(self, settings_win, loop, monkeypatch):
        from fluidvoice.gtkui import settings_window as sw
        w = settings_win
        c = w.c  # fresh client installed by the reset
        monkeypatch.setattr(sw.model_catalog, "gguf_downloaded", lambda n: True)
        w._use_gguf(None, "ggml-base.bin")
        assert c.saved[-1]["model"] == {
            "backend": "whisper.cpp", "whispercpp_model": "ggml-base.bin"}
        pump(loop, 1300)  # let the scheduled warmup poll run once and stop

    def test_use_gguf_rejected_toasts(self, settings_win, loop, monkeypatch):
        from fluidvoice.gtkui import settings_window as sw
        w = settings_win
        c = w.c  # fresh client installed by the reset
        monkeypatch.setattr(sw.model_catalog, "gguf_downloaded", lambda n: True)

        def reject(body):
            return {"ok": False, "changed": [], "rejected": ["model.backend"],
                    "restart_required": [], "errors": [], "note": ""}

        c.set_config = reject
        toasts: list[str] = []
        monkeypatch.setattr(w, "toast", lambda text, timeout=5: toasts.append(text))
        w._use_gguf(None, "ggml-base.bin")
        assert any("model.backend" in t for t in toasts)
        assert c.saved == []

    # -- Parakeet (ONNX) group ---------------------------------------------------

    def test_parakeet_group_rows_built(self, settings_win):
        from fluidvoice import model_catalog
        w = settings_win
        assert len(w._parakeet_rows) == len(model_catalog.PARAKEET_CATALOG)
        assert {r.get_title() for r in w._parakeet_rows} == \
            set(model_catalog.PARAKEET_CATALOG)

    def test_active_parakeet_marker(self, settings_win, loop):
        w = settings_win
        c = StubClient()

        def pk_cfg():
            cfg = copy.deepcopy(DEFAULTS)
            cfg["model"] = {**cfg["model"], "backend": "parakeet",
                            "name": "parakeet-tdt-0.6b-v2"}
            return cfg, True

        c.get_config = pk_cfg
        w.c = c
        w._load()
        pump(loop)
        assert w._active_parakeet() == "parakeet-tdt-0.6b-v2"

        def walk(widget):
            yield widget
            child = widget.get_first_child()
            while child:
                yield from walk(child)
                child = child.get_next_sibling()

        row = next(r for r in w._parakeet_rows
                   if r.get_title() == "parakeet-tdt-0.6b-v2")
        labels = [x.get_text() for x in walk(row) if isinstance(x, Gtk.Label)]
        assert "Active" in labels

    def test_parakeet_download_flow(self, settings_win, loop, monkeypatch):
        from fluidvoice.gtkui import settings_window as sw
        w = settings_win
        downloaded = {"now": False}
        monkeypatch.setattr(sw.model_catalog, "parakeet_downloaded",
                            lambda n: downloaded["now"])
        calls: list[tuple[str, list]] = []

        def fake_download(name, progress=None):
            calls.append((name, []))
            if progress:
                progress(50, 100)
                progress(100, 100)
            return sw.model_catalog.parakeet_model_dir(name)

        monkeypatch.setattr(sw.model_download, "download_parakeet",
                            fake_download)
        w._download_parakeet(None, "parakeet-tdt-0.6b-v2")
        assert pump_until(
            loop, lambda: w._parakeet_dl["parakeet-tdt-0.6b-v2"].get("done"))
        st = w._parakeet_dl["parakeet-tdt-0.6b-v2"]
        assert calls and calls[0][0] == "parakeet-tdt-0.6b-v2"
        assert st["total"] == 100 and st["error"] is None
        downloaded["now"] = True
        w._refresh_models()

        def walk(widget):
            yield widget
            child = widget.get_first_child()
            while child:
                yield from walk(child)
                child = child.get_next_sibling()

        row = next(r for r in w._parakeet_rows if r.get_title() == "parakeet-tdt-0.6b-v2")
        buttons = [x.get_label() for x in walk(row) if isinstance(x, Gtk.Button)]
        assert buttons == ["Use"]

    def test_parakeet_download_failure_toasts(self, settings_win, loop,
                                              monkeypatch):
        from fluidvoice.gtkui import settings_window as sw
        w = settings_win
        monkeypatch.setattr(sw.model_catalog, "parakeet_downloaded",
                            lambda n: False)

        def broken(name, progress=None):
            raise OSError("net down")

        monkeypatch.setattr(sw.model_download, "download_parakeet", broken)
        toasts: list[str] = []
        monkeypatch.setattr(w, "toast", lambda text, timeout=5: toasts.append(text))
        w._download_parakeet(None, "parakeet-tdt-0.6b-v2")
        assert pump_until(
            loop, lambda: w._parakeet_dl["parakeet-tdt-0.6b-v2"].get("error"))
        assert w._parakeet_dl["parakeet-tdt-0.6b-v2"]["error"] == "net down"
        assert pump_until(loop, lambda: any("net down" in t for t in toasts))

    def test_use_parakeet_posts_config(self, settings_win, loop, monkeypatch):
        from fluidvoice.gtkui import settings_window as sw
        w = settings_win
        c = w.c  # fresh client installed by the reset
        monkeypatch.setattr(sw.model_catalog, "parakeet_downloaded",
                            lambda n: True)
        w._use_parakeet(None, "parakeet-tdt-0.6b-v2")
        assert c.saved[-1]["model"] == {
            "backend": "parakeet", "name": "parakeet-tdt-0.6b-v2"}
        pump(loop, 1300)  # let the scheduled warmup poll run once and stop

class TestDictionarySuggestions:
    """Settings -> Dictation "Suggested words" group (dict_learn):
    suggest-only, threshold-2, permanent dismiss, Accept merges through
    the client's validated save path."""

    @pytest.fixture(autouse=True)
    def _fresh(self, settings_win, loop):
        reset_settings(settings_win, loop)

    def test_rows_render_with_count(self, settings_win):
        w = settings_win
        c = w.c  # fresh client installed by the reset
        c.suggestions = [
            {"heard": "flud voice", "corrected": "fluid voice", "count": 3},
            {"heard": "gnu plot", "corrected": "gnuplot", "count": 2},
        ]
        w._load()  # suggestion rows are (re)built on load
        assert w.suggest_group.get_visible() is True
        assert [r["row"].get_title() for r in w._suggest_rows] == [
            "flud voice → fluid voice", "gnu plot → gnuplot"]
        assert w._suggest_rows[0]["row"].get_subtitle() == "seen 3×"

    def test_group_hidden_when_nothing_pending(self, settings_win):
        w = settings_win  # reset client: no suggestions
        assert w._suggest_rows == []
        assert w.suggest_group.get_visible() is False

    def test_accept_posts_merges_and_refreshes_editor(self, settings_win,
                                                      monkeypatch):
        w = settings_win
        c = w.c  # fresh client installed by the reset
        c.suggestions = [
            {"heard": "flud voice", "corrected": "fluid voice", "count": 2}]
        w._load()
        toasts: list[str] = []
        monkeypatch.setattr(w, "toast",
                            lambda text, timeout=5: toasts.append(text))
        w._on_suggestion_accept(None, w._suggest_rows[0])
        assert c.accepted == [("flud voice", "fluid voice")]
        # posted through the validated save path
        assert c.saved[-1]["processing"]["dictionary"] == [
            {"triggers": ["miro board"], "replacement": "Miro board"},
            {"triggers": ["flud voice"], "replacement": "fluid voice"}]
        # dictionary editor refreshed without discarding other unsaved edits
        assert len(w._dict_rows) == 2
        assert {"triggers": ["flud voice"],
                "replacement": "fluid voice"} in w._collect_dictionary()
        # row removed, group emptied and hidden, toast shown
        assert w._suggest_rows == []
        assert w.suggest_group.get_visible() is False
        assert "Added to dictionary" in toasts

    def test_accept_rejected_keeps_row(self, settings_win, monkeypatch):
        w = settings_win
        c = w.c  # fresh client installed by the reset

        def reject(heard, corrected):
            return {"ok": False, "dictionary": [], "changed": [],
                    "rejected": ["processing.dictionary"], "errors": []}

        c.dict_suggestion_accept = reject
        c.suggestions = [
            {"heard": "flud voice", "corrected": "fluid voice", "count": 2}]
        w._load()
        toasts: list[str] = []
        monkeypatch.setattr(w, "toast",
                            lambda text, timeout=5: toasts.append(text))
        w._on_suggestion_accept(None, w._suggest_rows[0])
        assert len(w._suggest_rows) == 1  # row stays on a rejected save
        assert any("Could not add" in t for t in toasts)

    def test_dismiss_removes_row_and_records_pair(self, settings_win):
        w = settings_win
        c = w.c  # fresh client installed by the reset
        c.suggestions = [
            {"heard": "gnu plot", "corrected": "gnuplot", "count": 2}]
        w._load()
        w._on_suggestion_dismiss(None, w._suggest_rows[0])
        assert c.dismissed == [("gnu plot", "gnuplot")]
        assert w._suggest_rows == []
        assert w.suggest_group.get_visible() is False


class TestOnboardingWindow:
    def test_populates_and_tryout(self, loop):
        from fluidvoice.gtkui.onboarding import OnboardingWindow
        w = OnboardingWindow(client=StubClient())
        w.present()
        pump(loop)
        assert "Right_Control" in w.hotkey_lbl.get_text() or \
            "Right_Control" in w.hotkey_lbl.get_label()
        w._show_tryout({"ok": True, "text": "hello", "duration_s": 3})
        assert "hello" in w.try_out.get_text()
        w.close()


class TestUiPolish:
    """Presentation pass: friendly app names, metadata tag pills, the
    reactive settings save bar, the onboarding checklist rows."""

    def test_prettify_app(self):
        from fluidvoice.gtkui.main_window import prettify_app
        assert prettify_app("org.gnome.TextEditor") == "Text Editor"
        assert prettify_app("google-chrome.desktop") == "Google Chrome"
        assert prettify_app("code-oss") == "Code Oss"
        assert prettify_app("zed") == "Zed"
        assert prettify_app("firefox") == "Firefox"
        assert prettify_app("") == ""

    def _walk_labels(self, widget, out):
        if isinstance(widget, Gtk.Label):
            out.append(widget)
        child = widget.get_first_child()
        while child is not None:
            self._walk_labels(child, out)
            child = child.get_next_sibling()

    def test_entry_meta_uses_pill_and_friendly_app(self, hist_win, loop):
        from fluidvoice.gtkui.main_window import HistoryEntryRow
        w = hist_win
        reset_history(w, loop)
        labels: list[Gtk.Label] = []
        row = w.listbox.get_first_child()
        while row is not None:
            if isinstance(row, HistoryEntryRow):
                self._walk_labels(row, labels)
            row = row.get_next_sibling()
        texts = [l.get_text() for l in labels]
        assert "Firefox" in texts          # prettified app name
        assert "org.gnome" not in "".join(texts)
        ai = next(l for l in labels if l.get_text() == "AI polished")
        assert "tag" in ai.get_css_classes()
        assert "accent" in ai.get_css_classes()

    def test_save_bar_reflects_dirty_state(self, settings_win, loop):
        w = settings_win
        reset_settings(w, loop)
        assert w._dirty is False
        assert all(not b.get_sensitive() for b in w._save_btns)
        assert all(not b.get_sensitive() for b in w._discard_btns)
        assert "All changes saved" in w._save_rows[0].get_title()
        assert "warning" not in w._save_rows[0].get_css_classes()
        row = w._rows[("general", "copy_to_clipboard")]
        row.set_active(not row.get_active())
        assert w._dirty is True
        assert all(b.get_sensitive() for b in w._save_btns)
        assert "Unsaved changes" in w._save_rows[0].get_title()
        assert "warning" in w._save_rows[0].get_css_classes()
        w.save()  # stub save + reload -> clean again
        assert w._dirty is False
        assert all(not b.get_sensitive() for b in w._save_btns)

    def test_style_loader_idempotent(self):
        from fluidvoice.gtkui import style
        style.load_style()
        style.load_style()
        assert style._loaded is True

    def test_window_construction_registers_bundled_icons(self):
        """Fresh-process tripwire: a window built WITHOUT the app shell
        (tests, screenshot drivers, embedding) must still make the
        fluidvoice-* tab icons resolvable. do_activate never runs in such
        processes, and without the system deb's hicolor copies the
        settings tabs would render missing-image placeholders (the
        2026-09-08 screenshot bug)."""
        import os
        import subprocess
        import sys
        code = (
            "import sys; sys.path.insert(0, 'tests')\n"
            "import gi\n"
            "gi.require_version('Gtk', '4.0')\n"
            "gi.require_version('Adw', '1')\n"
            "from gi.repository import Adw, Gdk, GLib, Gtk\n"
            "Adw.init()\n"
            "from test_gtkui import StubClient\n"
            "from fluidvoice.gtkui.main_window import HistoryWindow\n"
            "from fluidvoice.gtkui.onboarding import OnboardingWindow\n"
            "from fluidvoice.gtkui.settings_window import SettingsWindow\n"
            "wins = [HistoryWindow(client=StubClient()),\n"
            "        OnboardingWindow(client=StubClient()),\n"
            "        SettingsWindow(client=StubClient())]\n"
            "for w in wins:\n"
            "    w.present()\n"
            "loop = GLib.MainLoop()\n"
            "GLib.timeout_add(300, loop.quit)\n"
            "loop.run()\n"
            "theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())\n"
            "names = [f'fluidvoice-{n}-symbolic' for n in "
            "('general', 'models', 'polish', 'dictation', 'history', 'about')]\n"
            "missing = [n for n in names if not theme.has_icon(n)]\n"
            "print('MISSING ' + ','.join(missing) if missing else 'OK')\n"
        )
        env = dict(os.environ)
        r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True, timeout=60, env=env, cwd=os.getcwd())
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip().endswith("OK"), \
            f"unresolved icons: {r.stdout.strip()}"

    def test_onboarding_checklist_rows(self, loop):
        from fluidvoice.gtkui.onboarding import OnboardingWindow
        w = OnboardingWindow(client=StubClient())
        w.present()
        pump(loop)
        assert w.hotkey_row.status.get_icon_name() == \
            "dialog-information-symbolic"
        assert w.mic_row.status.get_icon_name() == "dialog-warning-symbolic"
        assert w.mic_lbl.get_text()
        w.close()


class TestHistoryScience:
    """UI-science uplift phases 3b+4: confidence dots, inline repair
    (edit + insert at cursor), date-header grouping."""

    def _rows(self, w):
        out = []
        row = w.listbox.get_first_child()
        while row is not None:
            out.append(row)
            row = row.get_next_sibling()
        return out

    def _labels(self, w):
        texts = []

        def walk(wIDGET):
            if isinstance(wIDGET, Gtk.Label):
                texts.append(wIDGET.get_text())
            child = wIDGET.get_first_child() if hasattr(wIDGET, "get_first_child") else None
            while child is not None:
                walk(child)
                child = child.get_next_sibling()

        for row in self._rows(w):
            walk(row)
        return texts

    def _first_entry_row(self, w):
        from fluidvoice.gtkui.main_window import HistoryEntryRow
        for row in self._rows(w):
            if isinstance(row, HistoryEntryRow):
                return row
        raise AssertionError("no entry rows")

    def test_date_headers_group_renders(self, hist_win, loop):
        midnight = time.mktime(time.localtime(time.time())[:3]
                               + (0, 0, 0, 0, 0, -1))
        entries = [
            {"ts": time.time(), "text": "newest", "app": "zed"},
            {"ts": midnight - 3600, "text": "yesterdays words", "app": "zed"},
            {"ts": midnight - 7200, "text": "also yesterday", "app": "zed"},
        ]
        w = hist_win
        reset_history(w, loop, entries)
        labels = self._labels(w)
        assert "Today" in labels
        assert "Yesterday" in labels

    def test_confidence_dots_render(self, hist_win, loop):
        entries = [{"ts": time.time(), "text": "shaky words",
                    "confidence": 1}]
        w = hist_win
        reset_history(w, loop, entries)
        assert "●●○" in self._labels(w)

    def test_no_dots_without_confidence(self, hist_win, loop):
        w = hist_win
        reset_history(w, loop)
        assert not any("●" in t for t in self._labels(w))

    def test_inline_edit_round_trip(self, hist_win, loop):
        entries = [{"ts": 1234.5, "text": "original words"}]
        w = hist_win
        c = reset_history(w, loop, entries)
        row = self._first_entry_row(w)
        row._start_edit(None)
        assert row.editor is not None
        view = row.editor.get_first_child()
        view.get_buffer().set_text("edited words")
        row._end_edit(True, view)
        assert c.updated == [(1234.5, "edited words")]
        assert row.entry["text"] == "edited words"
        assert "edited words" in self._labels(w)
        assert row.editor is None

    def test_edit_cancel_keeps_text(self, hist_win, loop):
        w = hist_win
        c = reset_history(w, loop, [{"ts": 99.0, "text": "keep me"}])
        row = self._first_entry_row(w)
        row._start_edit(None)
        view = row.editor.get_first_child()
        view.get_buffer().set_text("discarded")
        row._end_edit(False, view)
        assert c.updated == []
        assert "keep me" in self._labels(w)

    def test_insert_at_cursor_uses_client(self, hist_win, loop):
        w = hist_win
        c = reset_history(w, loop, [{"ts": 7.0, "text": "insert me"}])
        row = self._first_entry_row(w)
        w._on_insert_row(None, row)
        assert c.inserted == ["insert me"]

    def test_edit_failure_keeps_editor_open(self, hist_win, loop):
        w = hist_win
        c = StubClient([{"ts": 55.0, "text": "original"}])

        def fail_update(ts, text):
            c.updated.append((ts, text))
            return False

        c.history_update_text = fail_update
        w.c = c
        w._load_history()
        row = self._first_entry_row(w)
        row._start_edit(None)
        view = row.editor.get_first_child()
        view.get_buffer().set_text("risky edit")
        row._end_edit(True, view)
        assert row.editor is not None      # editor stays open...
        assert not row.text_lbl.get_visible()  # ...with the user's text
        assert c.updated == [(55.0, "risky edit")]


class TestUpdateSurfacing:
    """Update check-and-assist surfaces (fluidvoice/update.py): the
    history-window status label, the Settings About row, and the single
    onboarding sentence."""

    def test_status_row_hidden_without_update(self, hist_win, loop):
        w = hist_win
        reset_history(w, loop)
        w._apply_status({"recording": False, "busy": False,
                         "backend": "b", "cuda": False})
        assert w.update_lbl.get_visible() is False
        w._apply_status(None)  # daemon down: still hidden
        assert w.update_lbl.get_visible() is False

    def test_status_row_shows_update_with_tooltip(self, hist_win, loop):
        w = hist_win
        reset_history(w, loop)
        w._apply_status({"recording": False, "busy": False,
                         "backend": "b", "cuda": False,
                         "update_available": "0.6.0",
                         "update": {"enabled": True, "method": "deb",
                                    "upgrade_command":
                                        "sudo apt install -y ./a.deb"}})
        assert w.update_lbl.get_visible() is True
        assert w.update_lbl.get_text() == "update available: v0.6.0"
        assert "warning" in w.update_lbl.get_css_classes()
        assert w.update_lbl.get_tooltip_text() == "sudo apt install -y ./a.deb"
        # cleared again when the payload says so
        w._apply_status({"recording": False, "busy": False,
                         "backend": "b", "cuda": False})
        assert w.update_lbl.get_visible() is False

    def test_settings_about_update_row_states(self, settings_win, loop):
        w = settings_win
        reset_settings(w, loop)  # pristine state for the shared window
        # StubClient status: no checker reported -> "checks disabled"
        assert w.about_update_row.get_subtitle() == "checks disabled"
        w._apply_about_update({"update_available": None, "update":
                               {"enabled": True, "checked": True}})
        assert w.about_update_row.get_subtitle() == "up to date"
        w._apply_about_update({"update_available": "0.6.0", "update":
                               {"enabled": True, "checked": True,
                                "method": "deb"}})
        assert w.about_update_row.get_subtitle() == \
            "v0.6.0 available — run 'sayit-ermano update'"
        w._apply_about_update({"update_available": None, "update":
                               {"enabled": True, "checked": False}})
        assert w.about_update_row.get_subtitle() == "checking…"

    def test_onboarding_has_updates_sentence(self, loop):
        from fluidvoice.gtkui.onboarding import OnboardingWindow
        w = OnboardingWindow(client=StubClient())
        w.present()
        pump(loop)
        text = w.updates_lbl.get_text()
        assert "Checks GitHub once a day" in text
        assert "sayit-ermano update" in text
        assert "updates.check = false" in text
        w.close()


class TestSettingsWaylandPage:
    """Settings -> Wayland: capability rows, the bindable command and the
    per-DE instructions (v0.3 wayland port)."""

    def test_page_renders_bindable_command_and_caps(self, loop, monkeypatch):
        from fluidvoice.gtkui.settings_window import SettingsWindow
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.setattr("fluidvoice.session.shutil.which",
                            lambda n: "/usr/bin/" + n if n in
                            ("ydotool", "wl-copy", "wl-paste") else None)
        w = SettingsWindow(client=StubClient())
        w.present()
        pump(loop)
        assert w.wayland_page.get_name() == "wayland"
        # the three setting rows exist (validated save path)
        assert ("insertion", "wayland_tool") in w._rows
        assert ("hotkey", "wayland_evdev") in w._rows
        assert ("hotkey", "wayland_evdev_device") in w._rows
        assert ("hotkey", "wayland_evdev_key") in w._rows
        # capability rows resolved for the wayland session
        assert w._wayland_cap_rows["session"].get_subtitle() == "wayland (gnome)"
        assert w._wayland_cap_rows["insertion"].get_subtitle() == "ydotool"
        assert w._wayland_cap_rows["hotkey"].get_subtitle() == "de-shortcut"
        # the bindable command is the generated toggle script
        command = w.wayland_script_row.get_subtitle()
        assert "sayit-ermano-toggle" in command
        # GNOME instructions render (the bind steps the doctor prints too)
        assert any("GNOME" in r.get_title()
                   for r in w._wayland_instruction_rows)
        assert any(command in r.get_title()
                   for r in w._wayland_instruction_rows)
        # GNOME has a known panel -> the open button row is visible
        assert w.wayland_open_row.get_visible() is True
        w.close()

    def test_unknown_desktop_hides_open_row(self, loop, monkeypatch):
        from fluidvoice.gtkui.settings_window import SettingsWindow
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        monkeypatch.setenv("XDG_CURRENT_DESKTOP", "sway")
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        w = SettingsWindow(client=StubClient())
        w.present()
        pump(loop)
        assert w.wayland_open_row.get_visible() is False
        assert any("custom command shortcut" in r.get_title()
                   for r in w._wayland_instruction_rows)
        w.close()
