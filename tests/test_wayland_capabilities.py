"""Wayland session support (v0.3): probe, capability matrix, tool
resolution, insertion/clipboard paths, DE-shortcut assist, evdev PTT.

The suite pins XDG_SESSION_TYPE=x11 globally (tests/conftest.py) so the
pre-existing xdotool/xclip paths stay covered; wayland tests re-pin the
env per-test with monkeypatch and fake shutil.which/_run — no real
display server or external tool is ever needed.
"""
from __future__ import annotations

import subprocess

import pytest

from fluidvoice import insertion
from fluidvoice import session as session_mod
from fluidvoice.config import DEFAULTS

X11_ENV = {"XDG_SESSION_TYPE": "x11"}
WL_ENV = {"XDG_SESSION_TYPE": "wayland"}


def set_session(monkeypatch, env: dict):
    for var in ("XDG_SESSION_TYPE", "WAYLAND_DISPLAY", "DISPLAY",
                "XDG_CURRENT_DESKTOP"):
        monkeypatch.delenv(var, raising=False)
    for k, v in env.items():
        if v is not None:
            monkeypatch.setenv(k, v)


def which_from(installed: set[str]):
    def which(name: str):
        return f"/usr/bin/{name}" if name in installed else None
    return which


# ---------------------------------------------------------------------------
# Phase 1 — probe precedence
# ---------------------------------------------------------------------------

class TestProbe:
    def test_explicit_wayland_wins_over_display(self):
        info = session_mod.probe({"XDG_SESSION_TYPE": "wayland",
                                  "DISPLAY": ":1", "WAYLAND_DISPLAY": ""})
        assert info.type == "wayland" and info.is_wayland
        assert info.x11_display and not info.wayland_display

    def test_explicit_x11_wins_over_wayland_display(self):
        # the systemd unit's baked Environment=DISPLAY case: XDG says x11
        info = session_mod.probe({"XDG_SESSION_TYPE": "x11",
                                  "WAYLAND_DISPLAY": "wayland-0"})
        assert info.type == "x11" and not info.is_wayland

    def test_wayland_display_only(self):
        info = session_mod.probe({"WAYLAND_DISPLAY": "wayland-0"})
        assert info.type == "wayland" and info.wayland_display

    def test_display_only_is_x11(self):
        info = session_mod.probe({"DISPLAY": ":0"})
        assert info.type == "x11" and info.x11_display

    def test_neither_is_unknown(self):
        info = session_mod.probe({})
        assert info.type == "unknown" and not info.is_wayland

    def test_non_session_values_fall_through(self, monkeypatch):
        set_session(monkeypatch, {"XDG_SESSION_TYPE": "tty",
                                  "WAYLAND_DISPLAY": "wayland-0"})
        assert session_mod.current().type == "wayland"

    def test_desktop_first_token_lowercased(self):
        info = session_mod.probe({"XDG_SESSION_TYPE": "wayland",
                                  "XDG_CURRENT_DESKTOP": "ubuntu:GNOME"})
        assert info.desktop == "ubuntu"

    def test_current_reads_live_env(self, monkeypatch):
        set_session(monkeypatch, WL_ENV)
        assert session_mod.current().is_wayland
        set_session(monkeypatch, X11_ENV)
        assert not session_mod.current().is_wayland


# ---------------------------------------------------------------------------
# Phase 1 — capability matrix
# ---------------------------------------------------------------------------

class TestCapabilities:
    def test_x11_full(self):
        info = session_mod.probe(X11_ENV)
        caps = session_mod.capabilities(
            info, which=which_from({"xdotool", "xclip"}))
        assert caps == {"hotkey": "x11-grab", "insertion": "xdotool",
                        "clipboard": "xclip", "overlay": "x11-pill",
                        "preview": "x11-pill", "tray": "sni",
                        "app-hint": "xdotool-wmclass"}

    def test_unknown_session_behaves_as_x11(self):
        info = session_mod.probe({})
        caps = session_mod.capabilities(info, which=which_from({"xdotool"}))
        assert caps["hotkey"] == "x11-grab" and caps["insertion"] == "xdotool"

    def test_wayland_wtype(self):
        info = session_mod.probe(dict(WL_ENV, XDG_CURRENT_DESKTOP="sway"))
        caps = session_mod.capabilities(
            info, which=which_from({"wtype", "wl-copy", "wl-paste"}))
        assert caps["insertion"] == "wtype"
        assert caps["clipboard"] == "wl-clipboard"
        assert caps["hotkey"] == "de-shortcut"
        assert caps["overlay"] == caps["preview"] == "notifications"

    def test_wayland_ydotool_only(self):
        info = session_mod.probe(WL_ENV)
        caps = session_mod.capabilities(
            info, which=which_from({"ydotool", "wl-copy", "wl-paste"}))
        assert caps["insertion"] == "ydotool"

    def test_wayland_gnome_excludes_wtype_in_auto(self):
        info = session_mod.probe(dict(WL_ENV, XDG_CURRENT_DESKTOP="GNOME"))
        caps = session_mod.capabilities(
            info, which=which_from({"wtype", "ydotool", "wl-copy", "wl-paste"}))
        assert caps["insertion"] == "ydotool"  # auto skips wtype on GNOME

    def test_ubuntu_gnome_token_still_detected(self):
        # XDG_CURRENT_DESKTOP="ubuntu:GNOME" -> first token "ubuntu" must
        # still mean GNOME Shell (no wtype) and print GNOME bind steps
        info = session_mod.probe(dict(WL_ENV,
                                      XDG_CURRENT_DESKTOP="ubuntu:GNOME"))
        assert info.desktop == "ubuntu"
        caps = session_mod.capabilities(
            info, which=which_from({"wtype", "ydotool", "wl-copy", "wl-paste"}))
        assert caps["insertion"] == "ydotool"
        assert session_mod.de_shortcut_instructions(
            info.desktop_all, "/tmp/t")[0].startswith("GNOME:")

    def test_wayland_no_tools(self):
        info = session_mod.probe(WL_ENV)
        caps = session_mod.capabilities(info, which=which_from(set()))
        assert caps["insertion"] == "unavailable"
        assert caps["clipboard"] == "unavailable"

    def test_wayland_wl_clipboard_only(self):
        info = session_mod.probe(WL_ENV)
        caps = session_mod.capabilities(
            info, which=which_from({"wl-copy", "wl-paste"}))
        assert caps["insertion"] == "wl-clipboard-only"

    def test_tray_is_always_sni(self):
        for env in (X11_ENV, WL_ENV, {}):
            caps = session_mod.capabilities(session_mod.probe(env),
                                            which=which_from(set()))
            assert caps["tray"] == "sni"


# ---------------------------------------------------------------------------
# Phase 1 — tool resolution (auto/explicit + GNOME exclusion)
# ---------------------------------------------------------------------------

class TestResolveWaylandTool:
    def test_auto_prefers_wtype(self):
        tool, reason = session_mod.resolve_wayland_tool(
            "auto", "kde", which=which_from({"wtype", "ydotool"}))
        assert tool == "wtype" and reason == ""

    def test_auto_gnome_skips_wtype(self):
        tool, reason = session_mod.resolve_wayland_tool(
            "auto", "gnome", which=which_from({"wtype", "ydotool"}))
        assert tool == "ydotool"
        assert "GNOME" in reason

    def test_explicit_wtype_honored_on_gnome(self):
        tool, reason = session_mod.resolve_wayland_tool(
            "wtype", "gnome", which=which_from({"wtype", "ydotool"}))
        assert tool == "wtype"
        assert "does NOT work on GNOME" in reason

    def test_missing_preferred_falls_back(self):
        tool, reason = session_mod.resolve_wayland_tool(
            "wtype", "sway", which=which_from({"ydotool"}))
        assert tool == "ydotool"
        assert "preferred tool 'wtype' not found" in reason

    def test_neither_installed(self):
        tool, reason = session_mod.resolve_wayland_tool(
            "auto", "", which=which_from(set()))
        assert tool is None
        assert "wtype not found" in reason and "ydotool not found" in reason


# ---------------------------------------------------------------------------
# Phase 1 — the bindable toggle script
# ---------------------------------------------------------------------------

class TestToggleScript:
    def test_writes_executable_wrapper(self, tmp_path, monkeypatch):
        from fluidvoice import paths
        target = tmp_path / "bin" / "sayit-ermano-toggle"
        monkeypatch.setattr(paths, "toggle_script", lambda: target)
        # a sayit-ermano binary next to a fake interpreter wins the
        # resolution order (sibling -> PATH -> module fallback)
        (tmp_path / "python").write_text("")
        (tmp_path / "sayit-ermano").write_text("")
        monkeypatch.setattr(session_mod.sys, "executable",
                            str(tmp_path / "python"))
        monkeypatch.setattr(session_mod.shutil, "which", lambda n: None)
        result = session_mod.ensure_toggle_script()
        assert result == target
        text = target.read_text()
        assert text.startswith("#!/bin/sh")
        assert text.strip().endswith(
            f"exec {tmp_path / 'sayit-ermano'} toggle")

    def test_idempotent_and_never_raises(self, tmp_path, monkeypatch):
        import os

        from fluidvoice import paths
        target = tmp_path / "bin" / "sayit-ermano-toggle"
        monkeypatch.setattr(paths, "toggle_script", lambda: target)
        monkeypatch.setattr(session_mod.shutil, "which", lambda n: None)
        first = session_mod.ensure_toggle_script()
        assert first is not None and first.exists()
        before = (first.stat().st_mtime_ns,
                  first.stat().st_mode & 0o777)
        again = session_mod.ensure_toggle_script()
        assert again == first
        assert (first.stat().st_mtime_ns,
                first.stat().st_mode & 0o777) == before  # no rewrite
        assert os.access(first, os.X_OK)

    def test_unwritable_path_returns_none(self, tmp_path, monkeypatch):
        from fluidvoice import paths
        monkeypatch.setattr(paths, "toggle_script",
                            lambda: tmp_path / "no" / "dir" / "s")
        # a failing mkdir must surface as None, never an exception
        monkeypatch.setattr(session_mod.Path, "mkdir",
                            lambda *a, **k: (_ for _ in ()).throw(OSError()))
        assert session_mod.ensure_toggle_script() is None


# ---------------------------------------------------------------------------
# Phase 1 — daemon status surface + startup log
# ---------------------------------------------------------------------------

class TestDaemonStatus:
    def _daemon(self, monkeypatch, env):
        import copy

        from fluidvoice import daemon as dm

        class StubRecorder:
            def start(self, path):
                pass

            def stop(self):
                return None

            def cancel(self):
                pass

        set_session(monkeypatch, env)
        return dm.Daemon(copy.deepcopy(DEFAULTS), recorder=StubRecorder())

    def test_status_carries_session_and_capabilities(self, monkeypatch):
        d = self._daemon(monkeypatch, dict(WL_ENV, XDG_CURRENT_DESKTOP="GNOME"))
        monkeypatch.setattr(session_mod.shutil, "which",
                            which_from({"ydotool", "wl-copy", "wl-paste"}))
        resp = d.handle_request({"action": "status"})
        assert resp["session"] == {"type": "wayland", "desktop": "gnome"}
        assert resp["capabilities"]["insertion"] == "ydotool"
        assert resp["capabilities"]["hotkey"] == "de-shortcut"

    def test_status_x11_reports_x11(self, monkeypatch):
        d = self._daemon(monkeypatch, X11_ENV)
        resp = d.handle_request({"action": "status"})
        assert resp["session"]["type"] == "x11"
        assert resp["capabilities"]["hotkey"] == "x11-grab"

    def test_startup_log_line_and_script(self, monkeypatch, tmp_path):
        from fluidvoice import paths
        logs: list[str] = []
        monkeypatch.setattr("fluidvoice.daemon.log", logs.append)
        script = tmp_path / "toggle"
        monkeypatch.setattr(paths, "toggle_script", lambda: script)
        d = self._daemon(monkeypatch, dict(WL_ENV, XDG_CURRENT_DESKTOP="sway"))
        d._log_session()
        assert any("session: wayland (sway)" in line for line in logs)
        assert any("insertion=" in line and "overlay=notifications" in line
                   for line in logs)
        # the wayland hotkey path writes the bindable script and says so
        assert d._start_hotkey() is None
        assert any(str(script) in line for line in logs)  # bind hint logged
        assert any("global grabs do not exist" in line for line in logs)
        assert script.exists()
        # x11 logs the full-experience line and writes nothing
        logs.clear()
        script.unlink()
        d = self._daemon(monkeypatch, X11_ENV)
        d._log_session()
        assert any("session: x11" in line for line in logs)
        assert not any("global grabs" in line for line in logs)
        assert not script.exists()


# ---------------------------------------------------------------------------
# Phase 1 — doctor matrix + CLI describe
# ---------------------------------------------------------------------------

class TestDoctorMatrix:
    def test_wayland_matrix_rows(self, monkeypatch):
        from fluidvoice import doctor
        set_session(monkeypatch, dict(WL_ENV, XDG_CURRENT_DESKTOP="GNOME"))
        monkeypatch.setattr(doctor.session_mod.shutil, "which",
                            which_from({"ydotool", "wl-copy", "wl-paste"}))
        lines = doctor._session_matrix_lines({})
        joined = "\n".join(lines)
        assert "session: wayland (gnome)" in joined
        assert "insertion: ydotool" in joined
        assert "wtype missing" in joined
        assert "hotkey: de-shortcut" in joined
        assert "overlay: notifications" in joined
        assert "impossible on Wayland" in joined  # paste-verification note
    def test_wayland_unavailable_row_names_the_fix(self, monkeypatch):
        from fluidvoice import doctor
        set_session(monkeypatch, WL_ENV)
        monkeypatch.setattr(doctor.session_mod.shutil, "which",
                            which_from(set()))
        lines = doctor._session_matrix_lines({})
        assert any("insertion: UNAVAILABLE" in line
                   and "install wtype or ydotool" in line for line in lines)

    def test_x11_matrix_is_full_experience(self, monkeypatch):
        from fluidvoice import doctor
        set_session(monkeypatch, X11_ENV)
        monkeypatch.setattr(doctor.session_mod.shutil, "which",
                            which_from({"xdotool", "xclip"}))
        joined = "\n".join(doctor._session_matrix_lines({}))
        assert "insertion: xdotool" in joined
        assert "overlay: x11-pill" in joined

    def test_run_exit_code_honesty(self, monkeypatch, capsys):
        # insertion unavailable on wayland -> non-zero; resolving it -> 0
        from fluidvoice import doctor
        set_session(monkeypatch, WL_ENV)
        monkeypatch.setattr(doctor, "_gtk_available", lambda: True)
        monkeypatch.setattr(doctor.session_mod.shutil, "which",
                            which_from({"pw-record", "parecord"}))
        assert doctor.run() == 1
        monkeypatch.setattr(doctor.session_mod.shutil, "which",
                            which_from({"pw-record", "parecord", "wtype",
                                        "wl-copy", "wl-paste"}))
        assert doctor.run() == 0
        out = capsys.readouterr().out
        assert "session:" in out and "insertion: wtype" in out


class TestCliDescribe:
    def test_wayland_line(self):
        from fluidvoice.cli import _describe
        resp = {"recording": False,
                "session": {"type": "wayland", "desktop": "sway"},
                "capabilities": {"insertion": "wtype",
                                 "overlay": "notifications"}}
        text = _describe(resp)
        assert "session: wayland" in text
        assert "insertion: wtype" in text
        assert "hotkey: DE shortcut" in text

    def test_x11_describe_unchanged(self):
        from fluidvoice.cli import _describe
        resp = {"recording": True}
        assert _describe(resp) == "recording"

    def test_toggle_response_has_no_session_line(self):
        from fluidvoice.cli import _describe
        assert "session:" not in _describe({"ok": True, "recording": False})


# ---------------------------------------------------------------------------
# Phase 2 — wayland insertion (fake runner: _run/Popen/sleep/which faked)
# ---------------------------------------------------------------------------

def ok_proc(args=None, stdout=b""):
    return subprocess.CompletedProcess(args or [], 0, stdout, b"")


@pytest.fixture()
def wl_runner(monkeypatch):
    """Wayland-pinned capture of every subprocess call insertion makes."""
    set_session(monkeypatch, dict(WL_ENV, XDG_CURRENT_DESKTOP="sway"))
    calls: dict = {"run": [], "popen": [], "sleeps": [], "notices": []}
    installed = {"wtype", "ydotool", "wl-copy", "wl-paste"}

    def fake_run(args, timeout=15.0, stdin=None):
        calls["run"].append(list(args))
        if args[0] == "wl-paste" and "--list-types" in args:
            return ok_proc(args, b"text/plain;charset=utf-8\n")
        if args[:2] == ["wl-paste", "--no-newline"]:
            return ok_proc(args, b"previous clipboard")
        return ok_proc(args)

    class P:
        def communicate(self, data=None):
            return (data, None)

    def fake_popen(args, **kwargs):
        calls["popen"].append(list(args))
        return P()

    def fake_which(name):
        return f"/usr/bin/{name}" if name in installed else None

    monkeypatch.setattr(insertion, "_run", fake_run)
    monkeypatch.setattr(insertion.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(insertion.time, "sleep",
                        lambda s: calls["sleeps"].append(s))
    monkeypatch.setattr(insertion.shutil, "which", fake_which)
    calls["installed"] = installed
    calls["set_installed"] = lambda s: installed.clear() or installed.update(s)
    return calls


def wl_cfg(**over):
    import copy
    cfg = copy.deepcopy(DEFAULTS)
    cfg["insertion"].update(over)
    return cfg


class TestWaylandToolResolution:
    def test_auto_picks_wtype(self, wl_runner):
        tool, reason = insertion._resolve_wayland_tool(wl_cfg())
        assert tool == "wtype" and reason == ""

    def test_gnome_exclusion_and_override(self, monkeypatch):
        set_session(monkeypatch, dict(WL_ENV, XDG_CURRENT_DESKTOP="GNOME"))
        monkeypatch.setattr(insertion.shutil, "which",
                            which_from({"wtype", "ydotool"}))
        tool, reason = insertion._resolve_wayland_tool(wl_cfg())
        assert tool == "ydotool" and "GNOME" in reason
        tool, _ = insertion._resolve_wayland_tool(wl_cfg(wayland_tool="wtype"))
        assert tool == "wtype"  # explicit override honored (with warning)

    def test_config_preference_honored(self, wl_runner):
        tool, _ = insertion._resolve_wayland_tool(wl_cfg(wayland_tool="ydotool"))
        assert tool == "ydotool"


class TestWaylandTyped:
    def test_wtype_argv(self, wl_runner):
        assert insertion.insert_text("hello world", wl_cfg()) == "typed"
        assert wl_runner["run"] == [["wtype", "-d", "8", "hello world"]]

    def test_ydotool_argv(self, wl_runner):
        wl_runner["set_installed"]({"ydotool", "wl-copy", "wl-paste"})
        assert insertion.insert_text("hi there", wl_cfg()) == "typed"
        assert wl_runner["run"] == [["ydotool", "type", "-d", "8", "hi there"]]

    def test_leading_dash_routes_to_paste(self, wl_runner):
        # wtype (like xdotool) parses a leading '-' as an option
        assert insertion.insert_text("--verbose", wl_cfg()) == "paste"
        assert any(c[0] == "wtype" and "-k" in c for c in wl_runner["run"])

    def test_no_trailing_terminal_space(self, wl_runner):
        # wm_class is unavailable on wayland: the autocomplete space stays
        # off even with terminal_apps matching everything
        cfg = wl_cfg()
        cfg["general"]["terminal_apps"] = ["*"]
        assert insertion.insert_text("git checkout", cfg) == "typed"
        assert wl_runner["run"][0][-1] == "git checkout"


class TestWaylandPaste:
    def test_sequence_snapshot_write_keystroke_settle_restore(self, wl_runner):
        assert insertion.insert_text("x" * 2000, wl_cfg()) == "paste"
        runs = wl_runner["run"]
        # 1) snapshot: plain read then the type probe
        assert runs[0] == ["wl-paste", "--no-newline"]
        assert runs[1] == ["wl-paste", "--list-types"]
        # 2) clipboard write of the dictation text
        assert wl_runner["popen"][0] == ["wl-copy"]
        # 3) the paste keystroke via the typing tool (fixed settle after)
        assert runs[2] == ["wtype", "-k", "ctrl+v"]
        assert wl_runner["sleeps"][:3] == [
            insertion.WAYLAND_CLIPBOARD_SETTLE_S,   # after the text write
            insertion.WAYLAND_PASTE_SETTLE_S,       # after the keystroke
            insertion.WAYLAND_CLIPBOARD_SETTLE_S]   # after the restore
        # 4) restore with the original mime type
        assert wl_runner["popen"][1] == [
            "wl-copy", "--type", "text/plain;charset=utf-8"]

    def test_plain_ctrl_v_even_for_terminal_config(self, wl_runner):
        # no wm_class on wayland: the terminal paste key is never chosen
        cfg = wl_cfg(terminal_paste_key="ctrl+shift+v")
        insertion.insert_text("x" * 2000, cfg)
        key = next(c for c in wl_runner["run"] if c[:2] == ["wtype", "-k"])
        assert key[-1] == "ctrl+v"

    def test_restore_failure_notifies_not_raises(self, wl_runner, monkeypatch):
        state = {"n": 0}

        def flaky_write(data, type_=None):
            state["n"] += 1
            if state["n"] > 1:  # the text write works, the restore fails
                raise OSError("no wl-copy")

        monkeypatch.setattr(insertion, "_wl_clipboard_write", flaky_write)
        assert insertion.insert_text("x" * 2000, wl_cfg(),
                                     on_notice=wl_runner["notices"].append) \
            == "paste"
        assert any("Clipboard restore failed" in n
                   for n in wl_runner["notices"])

    def test_paste_mode_without_tools_raises(self, wl_runner):
        wl_runner["set_installed"]({"wl-copy", "wl-paste"})
        with pytest.raises(insertion.InsertError) as ei:
            insertion.insert_text("short", wl_cfg(mode="paste"))
        assert "install wtype or ydotool" in str(ei.value)


class TestWaylandPressKey:
    def test_wtype_key(self, wl_runner):
        insertion.press_key("enter", tool="wtype")
        assert wl_runner["run"][-1] == ["wtype", "-k", "enter"]

    @pytest.mark.parametrize("spec,events", [
        ("ctrl+v", ["29:1", "47:1", "47:0", "29:0"]),
        ("ctrl+shift+v", ["29:1", "42:1", "47:1", "47:0", "42:0", "29:0"]),
        ("enter", ["28:1", "28:0"]),
        ("shift+enter", ["42:1", "28:1", "28:0", "42:0"]),
        ("ctrl+enter", ["29:1", "28:1", "28:0", "29:0"]),
        ("escape", ["1:1", "1:0"]),
        ("ctrl+c", ["29:1", "46:1", "46:0", "29:0"]),
    ])
    def test_ydotool_key_table(self, wl_runner, spec, events):
        assert insertion._ydotool_key_cmd(spec) == ["ydotool", "key"] + events

    def test_ydotool_unknown_spec_raises(self, wl_runner):
        with pytest.raises(insertion.InsertError):
            insertion._ydotool_key_cmd("ctrl+f13")  # not in the table

    def test_auto_resolution_on_wayland_session(self, wl_runner):
        # spoken-send / paste-last call press_key(spec) bare: the wayland
        # session makes it resolve the tool itself
        insertion.press_key("enter")
        assert wl_runner["run"][-1] == ["wtype", "-k", "enter"]

    def test_no_tool_raises_wayland_error(self, wl_runner):
        wl_runner["set_installed"]({"wl-copy", "wl-paste"})
        with pytest.raises(insertion.InsertError) as ei:
            insertion.press_key("enter")
        assert "install wtype or ydotool" in str(ei.value)


class TestWaylandClipboard:
    def test_copy_to_clipboard_uses_wl_copy(self, wl_runner):
        insertion.copy_to_clipboard("emergency text", wayland=True)
        assert wl_runner["popen"] == [["wl-copy"]]

    def test_clipboard_fallback_wayland_branch(self, wl_runner):
        insertion.clipboard_fallback("emergency text")
        assert any(c[0] == "wl-copy" for c in wl_runner["popen"])

    def test_copy_silent_without_wl_copy(self, wl_runner):
        wl_runner["set_installed"]({"wtype"})
        insertion.copy_to_clipboard("x", wayland=True)  # must not raise

    def test_active_window_class_always_none(self, wl_runner):
        # wayland: no misleading Xwayland WM_CLASS is ever consulted
        assert insertion.active_window_class() is None


class TestWaylandDegradationLadder:
    def test_no_tool_wl_copy_only_notice(self, wl_runner):
        wl_runner["set_installed"]({"wl-copy", "wl-paste"})
        result = insertion.insert_text(
            "hello", wl_cfg(), on_notice=wl_runner["notices"].append)
        assert result == "clipboard-fallback"
        assert any("paste manually" in n for n in wl_runner["notices"])
        assert wl_runner["popen"] == [["wl-copy"]]

    def test_nothing_at_all_raises(self, wl_runner):
        wl_runner["set_installed"](set())
        with pytest.raises(insertion.InsertError) as ei:
            insertion.insert_text("hello", wl_cfg())
        assert "install wtype or ydotool" in str(ei.value)

    def test_tool_but_no_wl_clipboard_paste_falls_to_typed(self, wl_runner):
        wl_runner["set_installed"]({"wtype"})
        assert insertion.insert_text("x" * 2000, wl_cfg()) == "typed"


class TestWaylandRewriteCapture:
    def test_capture_sequence(self, wl_runner, monkeypatch):
        from fluidvoice import rewrite
        reads = {"n": 0}

        def stateful_read():
            reads["n"] += 1
            return b"previous clipboard" if reads["n"] == 1 \
                else b"the selected text"

        monkeypatch.setattr(insertion, "_wl_clipboard_read",
                            lambda: stateful_read())
        # rewrite imports subprocess lazily inside the function - patch the
        # global module that import resolves to
        monkeypatch.setattr(
            insertion.subprocess, "run",
            lambda args, **kw: (wl_runner["run"].append(list(args))
                                or ok_proc(args)))
        text = rewrite.capture_selection()
        assert text == "the selected text"
        # ctrl+c via the tool, then the restore of the snapshot
        assert ["wtype", "-k", "ctrl+c"] in wl_runner["run"]
        assert wl_runner["popen"][-1] == [
            "wl-copy", "--type", "text/plain;charset=utf-8"]

    def test_capture_without_tools_returns_empty(self, wl_runner, monkeypatch):
        from fluidvoice import rewrite
        wl_runner["set_installed"](set())
        assert rewrite.capture_selection() == ""


class TestX11RegressionPin:
    """The gate itself: identical fakes, x11 session -> the xdotool paths
    exactly as before the port (never branch on tool absence)."""

    def test_typed_still_xdotool(self, monkeypatch):
        set_session(monkeypatch, X11_ENV)
        calls: dict = {"run": [], "popen": []}

        def fake_run(args, timeout=15.0, stdin=None):
            calls["run"].append(list(args))
            if args[0] == "xclip" and "-t" in args:
                return ok_proc(args, b"UTF8_STRING\ntext/plain\n")
            if args[0] == "xclip" and "-o" in args:
                return ok_proc(args, b"previous clipboard")
            return ok_proc(args)

        class P:
            def communicate(self, data=None):
                return (data, None)

        monkeypatch.setattr(insertion, "_run", fake_run)

        def fake_popen(args, **kwargs):
            calls["popen"].append(list(args))
            return P()

        monkeypatch.setattr(insertion.subprocess, "Popen", fake_popen)
        monkeypatch.setattr(insertion, "_make_hold", lambda *a, **k: None)
        monkeypatch.setattr(insertion.time, "sleep", lambda s: None)
        # NOTE: no which() fake at all - headless, nothing installed:
        # the xdotool branch is taken regardless (session-gated only)
        monkeypatch.setattr(insertion.shutil, "which", lambda n: None)
        assert insertion.insert_text("hello world", wl_cfg()) == "typed"
        assert calls["run"] == [["xdotool", "type", "--delay", "8",
                                 "--clearmodifiers", "hello world"]]

    def test_press_key_x11_uses_xdotool(self, monkeypatch):
        set_session(monkeypatch, X11_ENV)
        seen: list = []

        def fake_run(args, timeout=15.0, stdin=None):
            seen.append(list(args))
            return ok_proc(args)

        monkeypatch.setattr(insertion, "_run", fake_run)
        monkeypatch.setattr(insertion.shutil, "which", lambda n: None)
        insertion.press_key("enter")
        assert seen == [["xdotool", "key", "--clearmodifiers", "enter"]]


class TestDaemonWaylandInsert:
    def test_insert_via_real_shims_on_path(self, monkeypatch, tmp_path):
        """Daemon-level gate: XDG_SESSION_TYPE=wayland + fake tools on PATH
        -> insert-text runs the resolved tool's constructed command line
        (real subprocess spawn against argv-logging shims, no display)."""
        import os

        import fluidvoice.daemon as dm

        class StubRecorder:
            def start(self, path):
                pass

            def stop(self):
                return None

            def cancel(self):
                pass

        bindir = tmp_path / "fakebin"
        bindir.mkdir()
        argv_log = tmp_path / "argv.log"
        for tool in ("wtype", "ydotool", "wl-copy", "wl-paste"):
            shim = bindir / tool
            shim.write_text("#!/bin/sh\n"
                            f"printf '%s\\n' \"$0 $*\" >> {argv_log}\n"
                            "exit 0\n")
            shim.chmod(0o755)
        monkeypatch.setenv("PATH", str(bindir) + os.pathsep
                           + os.environ.get("PATH", ""))
        set_session(monkeypatch, dict(WL_ENV, XDG_CURRENT_DESKTOP="sway"))
        d = dm.Daemon(wl_cfg(), recorder=StubRecorder())
        resp = d.handle_request({"action": "status"})
        assert resp["capabilities"]["insertion"] == "wtype"
        ok, err = d.insert_text_action("hello wayland")
        assert ok and err is None
        lines = argv_log.read_text().splitlines()
        assert any("wtype" in line and line.endswith("-d 8 hello wayland")
                   for line in lines)

    def test_insert_action_reports_failure_without_tools(self, monkeypatch):
        import fluidvoice.daemon as dm

        class StubRecorder:
            def start(self, path):
                pass

            def stop(self):
                return None

            def cancel(self):
                pass

        set_session(monkeypatch, WL_ENV)
        monkeypatch.setattr(insertion.shutil, "which", lambda n: None)
        d = dm.Daemon(wl_cfg(), recorder=StubRecorder())
        ok, err = d.insert_text_action("hello wayland")
        assert not ok and "wtype or ydotool" in (err or "")


# ---------------------------------------------------------------------------
# Phase 3 — evdev push-to-talk (fake device stream; no real /dev/input)
# ---------------------------------------------------------------------------

class FakeEvent:
    def __init__(self, type_, code, value):
        self.type = type_
        self.code = code
        self.value = value


class FakeDevice:
    """Streams queued events; read_one() returns None when drained."""

    def __init__(self, name, fn, events=()):
        self.name = name
        self.fn = fn
        self.events = list(events)
        self.closed = False

    def read_one(self):
        if self.closed:
            raise OSError("device closed")
        if self.events:
            return self.events.pop(0)
        return None

    def close(self):
        self.closed = True


class TestEvdevPTT:
    KEY = 29  # the code the resolver fake reports for any KEY_* name

    @pytest.fixture()
    def ptt_env(self, monkeypatch):
        import fluidvoice.evdev_ptt as ep
        monkeypatch.setattr(ep, "_resolve_key_code", lambda name: self.KEY)
        devices: dict[str, FakeDevice] = {}

        def fake_list():
            return list(devices)

        def fake_open(path):
            if path not in devices:
                raise PermissionError(path)
            return devices[path]

        monkeypatch.setattr(ep, "_list_devices", fake_list)
        monkeypatch.setattr(ep, "_open_device", fake_open)
        # satisfy `import evdev` in start()
        import types
        monkeypatch.setitem(__import__("sys").modules, "evdev",
                            types.ModuleType("evdev"))
        return devices

    def _ptt(self, events=()):
        import fluidvoice.evdev_ptt as ep
        calls = {"press": 0, "release": 0}
        logs: list[str] = []
        p = ep.EvdevPTT("Keyboard", "KEY_RIGHTCTRL",
                        on_press=lambda: calls.__setitem__("press",
                                                           calls["press"] + 1),
                        on_release=lambda: calls.__setitem__(
                            "release", calls["release"] + 1),
                        log=logs.append)
        return p, calls, logs, events

    def test_press_release_lifecycle(self, ptt_env):
        p, calls, logs, _ = self._ptt()
        ptt_env["/dev/input/event3"] = FakeDevice(
            "AT Translated Set 2 Keyboard", "/dev/input/event3")
        assert p.start() is True
        assert p._pressed is False
        p._handle(FakeEvent(1, self.KEY, 1))    # press
        assert p._pressed and calls["press"] == 1
        p._handle(FakeEvent(1, self.KEY, 1))    # duplicate press: no re-fire
        assert calls["press"] == 1
        p._handle(FakeEvent(1, self.KEY, 0))    # release
        assert not p._pressed and calls["release"] == 1
        p._handle(FakeEvent(1, self.KEY, 0))    # duplicate release
        assert calls["release"] == 1
        p.stop()
        assert ptt_env["/dev/input/event3"].closed

    def test_auto_repeat_filtered(self, ptt_env):
        p, calls, _, _ = self._ptt()
        for _ in range(5):
            p._handle(FakeEvent(1, self.KEY, 2))  # kernel auto-repeat
        assert calls == {"press": 0, "release": 0}
        assert p._pressed is False

    def test_other_keys_and_events_ignored(self, ptt_env):
        p, calls, _, _ = self._ptt()
        p._handle(FakeEvent(1, 46, 1))   # 'c' pressed
        p._handle(FakeEvent(0x02, 29, 1))  # EV_REL with the same code
        assert calls == {"press": 0, "release": 0}

    def test_fake_device_stream_drives_callbacks(self, ptt_env):
        import time as _time
        p, calls, logs, events = self._ptt(
            events=[FakeEvent(1, self.KEY, 1), FakeEvent(1, self.KEY, 2),
                    FakeEvent(1, self.KEY, 0)])
        ptt_env["/dev/input/event4"] = FakeDevice(
            "Sony USB Keyboard", "/dev/input/event4", events)
        assert p.start() is True
        deadline = _time.monotonic() + 2.0
        while calls["release"] < 1 and _time.monotonic() < deadline:
            _time.sleep(0.01)
        p.stop()
        assert calls == {"press": 1, "release": 1}  # repeat between edges

    def test_unplug_stops_listener(self, ptt_env):
        p, calls, logs, _ = self._ptt()
        dev = FakeDevice("Keyboard K480", "/dev/input/event9")
        ptt_env["/dev/input/event9"] = dev
        assert p.start() is True
        dev.close()  # read_one now raises (the unplug path)
        p._thread.join(timeout=2.0)
        assert any("read failed" in line for line in logs)

    def test_no_matching_device_degrades(self, ptt_env):
        ptt_env["/dev/input/event1"] = FakeDevice(
            "Logitech Mouse", "/dev/input/event1")
        p, calls, logs, _ = self._ptt()
        assert p.start() is False  # WARN + disabled, never a raise
        assert any("no /dev/input device" in line for line in logs)
        p.stop()

    def test_empty_pattern_degrades(self, ptt_env):
        import fluidvoice.evdev_ptt as ep
        p = ep.EvdevPTT("", "KEY_RIGHTCTRL", on_press=lambda: None,
                        on_release=lambda: None, log=lambda m: None)
        assert p.start() is False

    def test_missing_evdev_module_degrades(self, ptt_env, monkeypatch):
        import sys
        monkeypatch.setitem(sys.modules, "evdev", None)  # import fails
        p, calls, logs, _ = self._ptt()
        assert p.start() is False
        assert any("python-evdev not installed" in line for line in logs)


class TestDeShortcutInstructions:
    def test_gnome(self):
        lines = session_mod.de_shortcut_instructions("gnome", "/tmp/toggle")
        assert any("GNOME" in line for line in lines)
        assert any("/tmp/toggle" in line for line in lines)

    def test_kde(self):
        lines = session_mod.de_shortcut_instructions("kde", "/tmp/toggle")
        assert any("KDE Plasma" in line for line in lines)

    def test_cosmic(self):
        lines = session_mod.de_shortcut_instructions("cosmic", "/tmp/toggle")
        assert any("COSMIC" in line for line in lines)

    def test_unknown_desktop_generic(self):
        lines = session_mod.de_shortcut_instructions("hyprland", "/tmp/t")
        assert any("custom command shortcut" in line for line in lines)
        assert "/tmp/t" in lines[0]


class TestEvdevDaemonWiring:
    def _daemon(self, monkeypatch, cfg_over=None):

        import fluidvoice.daemon as dm

        class StubRecorder:
            def start(self, path):
                pass

            def stop(self):
                return None

            def cancel(self):
                pass

        cfg = wl_cfg()
        cfg["hotkey"].update(cfg_over or {})
        return dm.Daemon(cfg, recorder=StubRecorder())

    def test_start_and_restart(self, monkeypatch):
        import fluidvoice.daemon as dm
        import fluidvoice.evdev_ptt as ep
        set_session(monkeypatch, WL_ENV)
        started: list = []
        stopped: list = []

        class StubPTT:
            def __init__(self, *a, **k):
                pass

            def start(self):
                started.append(1)
                self.summary = ["stub evdev ptt"]
                return True

            def stop(self):
                stopped.append(1)

        monkeypatch.setattr(ep, "EvdevPTT", StubPTT)
        monkeypatch.setattr(dm, "log", lambda m: None)
        d = self._daemon(monkeypatch,
                         {"wayland_evdev": True,
                          "wayland_evdev_device": "Keyboard"})
        assert d._start_evdev_ptt() is None
        assert d._evdev_ptt is not None and started == [1]
        # a hotkey.* settings change restarts the listener
        d.apply_config(["hotkey.wayland_evdev_key"])
        assert started == [1, 1] and stopped == [1]

    def test_disabled_by_default(self, monkeypatch):
        set_session(monkeypatch, WL_ENV)
        d = self._daemon(monkeypatch)
        assert d._start_evdev_ptt() is None
        assert d._evdev_ptt is None

    def test_x11_session_never_starts_it(self, monkeypatch):
        set_session(monkeypatch, X11_ENV)
        d = self._daemon(monkeypatch, {"wayland_evdev": True})
        assert d._start_evdev_ptt() is None
        assert d._evdev_ptt is None

    def test_wayland_start_hotkey_is_declared_not_attempted(self, monkeypatch):
        set_session(monkeypatch, WL_ENV)
        logs: list[str] = []
        monkeypatch.setattr("fluidvoice.daemon.log", logs.append)
        d = self._daemon(monkeypatch)
        assert d._start_hotkey() is None
        assert any("global grabs do not exist" in line for line in logs)
        assert any("DE custom shortcut" in line for line in logs)
        # and the restart hook does not raise on wayland
        d._restart_hotkey()


class TestWaylandConfigRoundtrip:
    def test_defaults(self):
        assert DEFAULTS["hotkey"]["wayland_evdev"] is False
        assert DEFAULTS["hotkey"]["wayland_evdev_device"] == ""
        assert DEFAULTS["hotkey"]["wayland_evdev_key"] == "KEY_RIGHTCTRL"
        assert DEFAULTS["insertion"]["wayland_tool"] == "auto"

    def test_apply_and_save(self, tmp_path, monkeypatch):
        import copy

        from fluidvoice import paths as p
        from fluidvoice.config import apply_settings, load_config, save_config
        target = tmp_path / "c.toml"
        monkeypatch.setattr(p, "config_file", lambda: target)
        cfg = copy.deepcopy(DEFAULTS)
        changed, rejected = apply_settings(
            cfg, {"hotkey": {"wayland_evdev": True,
                             "wayland_evdev_device": "AT Translated",
                             "wayland_evdev_key": "KEY_F9"},
                  "insertion": {"wayland_tool": "ydotool"}})
        assert rejected == [] and len(changed) == 4
        save_config(cfg)
        on_disk = load_config(target)
        assert on_disk["hotkey"]["wayland_evdev_device"] == "AT Translated"
        assert on_disk["hotkey"]["wayland_evdev_key"] == "KEY_F9"
        assert on_disk["hotkey"]["wayland_evdev"] is True
        assert on_disk["insertion"]["wayland_tool"] == "ydotool"

    def test_bad_values_rejected(self):
        import copy

        from fluidvoice.config import apply_settings
        cfg = copy.deepcopy(DEFAULTS)
        _, rejected = apply_settings(
            cfg, {"hotkey": {"wayland_evdev": "yes",
                             "wayland_evdev_key": "x" * 100},
                  "insertion": {"wayland_tool": "xdotool"}})
        assert set(rejected) == {"hotkey.wayland_evdev",
                                 "hotkey.wayland_evdev_key",
                                 "insertion.wayland_tool"}

    def test_template_documents_the_keys(self):
        from fluidvoice.config import TEMPLATE
        for key in ("wayland_evdev", "wayland_evdev_device",
                    "wayland_evdev_key", "wayland_tool"):
            assert key in TEMPLATE


class TestPipelineWaylandTranscript:
    def test_transcript_inserts_via_resolved_tool(self, monkeypatch, tmp_path):
        """The full pipeline path on a wayland session: record -> stub
        transcribe -> insert_text routes to the resolved tool's command."""
        import math
        import struct
        import wave

        import fluidvoice.daemon as dm
        set_session(monkeypatch, dict(WL_ENV, XDG_CURRENT_DESKTOP="sway"))
        monkeypatch.setattr(dm.ui, "notify", lambda *a, **k: None)
        monkeypatch.setattr(dm.ui, "play_sound", lambda *a, **k: None)
        monkeypatch.setattr(insertion.shutil, "which",
                            which_from({"wtype", "wl-copy", "wl-paste"}))
        seen: list = []

        def fake_run(args, timeout=15.0, stdin=None):
            seen.append(list(args))
            return ok_proc(args)

        monkeypatch.setattr(insertion, "_run", fake_run)

        class P:
            def communicate(self, data=None):
                return (data, None)

        monkeypatch.setattr(insertion.subprocess, "Popen",
                            lambda args, **kw: P())
        monkeypatch.setattr(insertion.time, "sleep", lambda s: None)

        wav = tmp_path / "utt.wav"
        with wave.open(str(wav), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"".join(
                struct.pack("<h", int(12000 * math.sin(i * 0.1)))
                for i in range(3200)))
        with open(wav, "ab") as fh:
            fh.write(b"\0" * 300)

        class StubBackend:
            name = "stub"

            def transcribe(self, w, language=None):
                return {"text": "typed on wayland", "language": "en",
                        "duration": 0.2}

        writer: list = []
        pipe = dm.DictationPipeline(
            wl_cfg(), StubBackend(),
            history_writer=lambda e, w: writer.append(e))
        out = pipe.run(wav, app_hint=None)
        assert out["strategy"] == "typed"
        assert seen[0] == ["wtype", "-d", "8", "typed on wayland"]
        assert writer and writer[0]["text"] == "typed on wayland"
