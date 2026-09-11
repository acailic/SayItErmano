"""First-use funnel, insertion half (F-05/F-18): no stranded first
insertion, honest machine-readable errors.

Failure-path tests only - no real X server, no real tools: env/PATH and
the subprocess seams are stubbed per the suite's established patterns
(tests/test_insertion.py's runner, test_wayland_capabilities.py's
wl_runner). Success paths stay covered by those files; this one pins the
honest-failure contract: cached capability pre-flight (startup +
insertion time), structured InsertResult emission, automatic clipboard
preservation of the transcript, and History named as the last resort.
"""
from __future__ import annotations

import subprocess

import pytest

from fluidvoice import insertion


def ok(args=None, stdout=b""):
    return subprocess.CompletedProcess(args or [], 0, stdout, b"")


@pytest.fixture(autouse=True)
def _fresh_capability_cache():
    """Each test sees an uncached capability check (and leaves none)."""
    insertion.reset_capability_cache()
    yield
    insertion.reset_capability_cache()


@pytest.fixture(autouse=True)
def _no_result_listeners():
    """Listener tests add their own; no leakage between tests."""
    saved = list(insertion._result_listeners)
    insertion._result_listeners.clear()
    yield
    insertion._result_listeners[:] = saved


def cfg(**over):
    import copy

    from fluidvoice.config import DEFAULTS
    c = copy.deepcopy(DEFAULTS)
    c["insertion"].update(over)
    return c


@pytest.fixture()
def x11(monkeypatch):
    """X11-pinned capture of every subprocess insertion makes, with a
    controllable installed-tool set (like test_insertion.py's runner,
    plus which() control so capability checks are deterministic)."""
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("DISPLAY", ":99")
    calls: dict = {"run": [], "popen": [], "writes": [], "notices": []}
    installed = {"xdotool", "xclip"}

    def fake_run(args, timeout=15.0, stdin=None):
        calls["run"].append(list(args))
        return ok(args)

    class P:
        def communicate(self, data=None):
            calls["writes"].append(data)
            return (data, None)

    def fake_popen(args, **kwargs):
        calls["popen"].append(list(args))
        return P()

    def fake_which(name):
        return f"/usr/bin/{name}" if name in installed else None

    monkeypatch.setattr(insertion, "_run", fake_run)
    monkeypatch.setattr(insertion.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(insertion.time, "sleep", lambda s: None)
    monkeypatch.setattr(insertion.shutil, "which", fake_which)
    monkeypatch.setattr(insertion, "_make_hold", lambda *a, **k: None)
    calls["set_installed"] = lambda s: (installed.clear(),
                                        installed.update(s))
    return calls


@pytest.fixture()
def wl(monkeypatch):
    """Wayland-pinned twin of x11 (sway desktop, wtype preferred)."""
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "sway")
    monkeypatch.delenv("DISPLAY", raising=False)
    calls: dict = {"run": [], "popen": [], "writes": [], "notices": []}
    installed = {"wtype", "ydotool", "wl-copy", "wl-paste"}

    def fake_run(args, timeout=15.0, stdin=None):
        calls["run"].append(list(args))
        return ok(args)

    class P:
        def communicate(self, data=None):
            calls["writes"].append(data)
            return (data, None)

    def fake_popen(args, **kwargs):
        calls["popen"].append(list(args))
        return P()

    def fake_which(name):
        return f"/usr/bin/{name}" if name in installed else None

    monkeypatch.setattr(insertion, "_run", fake_run)
    monkeypatch.setattr(insertion.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(insertion.time, "sleep", lambda s: None)
    monkeypatch.setattr(insertion.shutil, "which", fake_which)
    calls["set_installed"] = lambda s: (installed.clear(),
                                        installed.update(s))
    return calls


# ---------------------------------------------------------------------------
# Pre-flight capability check (F-05 fix (a)): strategy table vs reality
# ---------------------------------------------------------------------------

class TestCapabilityPreflight:
    def test_x11_full(self, x11):
        rep = insertion.check_insertion_capability(cfg())
        assert rep.ok and rep.fallback_ok and rep.typing_tool == "xdotool"
        assert rep.missing_tools == () and rep.install_hint == ""
        assert "ready" in rep.detail

    def test_x11_nothing_installed(self, x11):
        x11["set_installed"](set())
        rep = insertion.check_insertion_capability(cfg())
        assert not rep.ok and not rep.fallback_ok
        assert set(rep.missing_tools) == {"xdotool", "xclip"}
        assert rep.install_hint  # exact install command computed
        assert "History" in rep.detail  # the honest recovery path

    def test_x11_clipboard_only(self, x11):
        x11["set_installed"]({"xclip"})
        rep = insertion.check_insertion_capability(cfg())
        assert not rep.ok and rep.fallback_ok  # degrade, don't strand
        assert rep.missing_tools == ("xdotool",)
        assert "xdotool" in rep.install_hint

    def test_wayland_wtype(self, x11, monkeypatch):
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        monkeypatch.setenv("XDG_CURRENT_DESKTOP", "sway")
        x11["set_installed"]({"wtype", "wl-copy", "wl-paste"})
        rep = insertion.check_insertion_capability(cfg())
        assert rep.ok and rep.typing_tool == "wtype" and rep.fallback_ok

    def test_wayland_gnome_wtype_only_names_ydotool(self, x11, monkeypatch):
        # auto skips wtype on GNOME: the actionable gap is ydotool
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
        x11["set_installed"]({"wtype", "wl-copy", "wl-paste"})
        rep = insertion.check_insertion_capability(cfg())
        assert not rep.ok
        assert rep.missing_tools == ("ydotool",)
        assert "ydotool" in rep.install_hint

    def test_wayland_nothing(self, x11, monkeypatch):
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        x11["set_installed"](set())
        rep = insertion.check_insertion_capability(cfg())
        assert not rep.ok and not rep.fallback_ok
        assert set(rep.missing_tools) == {
            "wtype", "ydotool", "wl-copy", "wl-paste"}
        assert "wl-clipboard" in rep.install_hint
        assert "History" in rep.detail


class TestCapabilityCache:
    def test_second_lookup_served_from_cache(self, x11, monkeypatch):
        calls = {"n": 0}
        real = insertion.check_insertion_capability

        def counting(c=None, **kw):
            calls["n"] += 1
            return real(c, **kw)

        monkeypatch.setattr(insertion, "check_insertion_capability",
                            counting)
        insertion.capability_status(cfg())
        insertion.capability_status(cfg())
        assert calls["n"] == 1

    def test_refresh_recomputes_after_a_change(self, x11):
        first = insertion.capability_status(cfg())
        x11["set_installed"](set())
        assert insertion.capability_status(cfg()) is first  # cached
        second = insertion.capability_status(cfg(), refresh=True)
        assert second is not first and not second.ok

    def test_reset_clears(self, x11):
        insertion.capability_status(cfg())
        insertion.reset_capability_cache()
        assert insertion._capability_cache == {}


class TestStartupCheck:
    def test_issue_reported_when_unavailable(self, x11):
        x11["set_installed"](set())
        got = []
        rep = insertion.startup_capability_check(cfg(),
                                                 on_issue=got.append)
        assert got == [rep] and not rep.ok
        assert "History" in rep.detail and rep.install_hint

    def test_no_issue_when_ready_and_cache_primed(self, x11):
        got = []
        rep = insertion.startup_capability_check(cfg(),
                                                 on_issue=got.append)
        assert rep.ok and got == []
        # insertion-time lookups reuse the startup report (one check)
        assert insertion.capability_status(cfg()) is rep


# ---------------------------------------------------------------------------
# Exact install commands per detected distro
# ---------------------------------------------------------------------------

class TestInstallHint:
    def test_distro_commands(self):
        assert insertion.install_hint(["xdotool", "xclip"], "debian") \
            == "sudo apt install xdotool xclip"
        assert insertion.install_hint(["xdotool"], "fedora") \
            == "sudo dnf install xdotool"
        assert insertion.install_hint(["ydotool"], "arch") \
            == "sudo pacman -S --needed ydotool"

    def test_wl_tools_collapse_to_one_package(self):
        assert insertion.install_hint(["wtype", "wl-copy", "wl-paste"],
                                      "ubuntu") \
            == "sudo apt install wtype wl-clipboard"

    def test_unknown_distro_gets_generic_instruction(self):
        hint = insertion.install_hint(["xdotool"], "plan9")
        assert "xdotool" in hint and "package manager" in hint
        assert "sudo" not in hint

    def test_empty_when_nothing_missing(self):
        assert insertion.install_hint([], "debian") == ""

    def test_id_like_fallback(self, monkeypatch):
        monkeypatch.setattr(insertion, "_read_os_release",
                            lambda path="/etc/os-release":
                            {"ID": "", "ID_LIKE": "fedora"})
        assert insertion.install_hint(["xdotool"]) \
            == "sudo dnf install xdotool"

    def test_detect_lowercases_the_id(self, monkeypatch):
        monkeypatch.setattr(insertion, "_read_os_release",
                            lambda path="/etc/os-release": {"ID": "Fedora"})
        assert insertion._detect_distro() == "fedora"


# ---------------------------------------------------------------------------
# Failure path: tool missing (the F-05 stranded first insertion)
# ---------------------------------------------------------------------------

class TestToolMissingAtInsertion:
    def test_empty_path_failure_is_structured_and_names_history(
            self, monkeypatch, tmp_path):
        """PATH stubbed empty, REAL subprocess layer: the exact
        user-space-install-without-tools scenario from the ledger."""
        monkeypatch.setenv("PATH", str(tmp_path))  # zero tools reachable
        monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.setenv("DISPLAY", ":99")
        results = []
        insertion.add_result_listener(results.append)
        with pytest.raises(insertion.InsertionFailure) as ei:
            insertion.insert_text("first dictation", cfg(),
                                  on_result=results.append)
        failure = ei.value
        assert failure.kind is insertion.FailureKind.TOOL_MISSING
        assert set(failure.missing_tools) == {"xdotool", "xclip"}
        assert failure.install_hint  # exact install command for the distro
        # the honest part: no false "copied to clipboard" - History named
        assert "History" in failure.result.message
        assert (failure.result.clipboard.outcome
                is insertion.ClipboardOutcome.TOOL_MISSING)
        assert failure.result.ok is False
        assert results[-1] is failure.result

    def test_missing_typer_but_xclip_present_copies_transcript(
            self, x11, monkeypatch):
        x11["set_installed"]({"xclip"})  # fallback possible

        def no_xdotool(args, timeout=15.0, stdin=None):
            x11["run"].append(list(args))
            if args[0] == "xdotool":
                raise insertion.ToolMissing("xdotool")
            return ok(args)

        monkeypatch.setattr(insertion, "_run", no_xdotool)
        notices, results = [], []
        with pytest.raises(insertion.InsertionFailure) as ei:
            insertion.insert_text("the transcript", cfg(),
                                  on_notice=notices.append,
                                  on_result=results.append)
        assert ei.value.kind is insertion.FailureKind.TOOL_MISSING
        # automatic preservation: the transcript BYTES hit xclip
        assert b"the transcript" in x11["writes"]
        assert any(c[0] == "xclip" for c in x11["popen"])
        assert ei.value.result.clipboard.written
        assert "copied to the clipboard" in ei.value.result.message
        assert "Fix:" in ei.value.result.message
        assert any("copied to the clipboard" in n for n in notices)
        assert results[-1].clipboard.written

    def test_paste_mode_missing_xclip_names_history(self, x11):
        x11["set_installed"](set())
        with pytest.raises(insertion.InsertionFailure) as ei:
            insertion.insert_text("words", cfg(mode="paste"))
        assert ei.value.kind is insertion.FailureKind.TOOL_MISSING
        assert "xclip" in ei.value.missing_tools
        assert (ei.value.result.clipboard.outcome
                is insertion.ClipboardOutcome.TOOL_MISSING)
        assert "History" in str(ei.value)


# ---------------------------------------------------------------------------
# Failure path: tool present but broken / focus lost / paste unverified
# ---------------------------------------------------------------------------

class TestToolExitsNonzero:
    def _failing_typer(self, x11, monkeypatch, stderr):
        def failing(args, timeout=15.0, stdin=None):
            x11["run"].append(list(args))
            if args[:2] == ["xdotool", "type"]:
                return subprocess.CompletedProcess(args, 1, b"", stderr)
            return ok(args)

        monkeypatch.setattr(insertion, "_run", failing)

    def test_nonzero_exit_is_tool_failed_and_preserves_text(
            self, x11, monkeypatch):
        self._failing_typer(x11, monkeypatch, b"boom")
        results = []
        with pytest.raises(insertion.InsertionFailure) as ei:
            insertion.insert_text("hello", cfg(), on_result=results.append)
        assert ei.value.kind is insertion.FailureKind.TOOL_FAILED
        assert b"hello" in x11["writes"]  # never stranded
        assert ei.value.result.clipboard.written
        assert results[-1].failure is insertion.FailureKind.TOOL_FAILED

    def test_focus_lost_signature_classified(self, x11, monkeypatch):
        self._failing_typer(x11, monkeypatch,
                            b"xdotool: Failed to get active window")
        with pytest.raises(insertion.InsertionFailure) as ei:
            insertion.insert_text("hello", cfg())
        assert ei.value.kind is insertion.FailureKind.FOCUS_LOST
        assert b"hello" in x11["writes"]

    def test_paste_keystroke_x_error_is_focus_lost(self, x11, monkeypatch):
        def failing(args, timeout=15.0, stdin=None):
            x11["run"].append(list(args))
            if args[:2] == ["xdotool", "key"]:
                return subprocess.CompletedProcess(args, 1, b"",
                                                   b"X error: BadWindow")
            return ok(args)

        monkeypatch.setattr(insertion, "_run", failing)
        with pytest.raises(insertion.InsertionFailure) as ei:
            insertion.insert_text("long text " * 300, cfg(mode="paste"))
        assert ei.value.kind is insertion.FailureKind.FOCUS_LOST
        assert b"long text " in b"".join(x11["writes"])


class TestPasteUnverifiedFailure:
    def test_paste_mode_unverified_is_structured_and_copies(
            self, x11, monkeypatch):
        class DeadHold:
            """A hold whose target never reads the selection (focus went
            elsewhere / timeout) - test_insertion.py's FakeHold pattern."""

            def quiesce(self, seconds, interval=None):
                return set()

            @property
            def lost_ownership(self):
                return False

            def wait_read(self, timeout, exclude_windows=(), interval=None):
                return None

            def release(self):
                pass

        monkeypatch.setattr(insertion, "_make_hold",
                            lambda *a, **k: DeadHold())
        results = []
        with pytest.raises(insertion.InsertionFailure) as ei:
            insertion.insert_text("short text", cfg(mode="paste"),
                                  on_result=results.append)
        assert ei.value.kind is insertion.FailureKind.PASTE_UNVERIFIED
        assert getattr(ei.value, "not_verified", False)
        assert b"short text" in x11["writes"]  # preserved after restore
        assert results[-1].failure is insertion.FailureKind.PASTE_UNVERIFIED


# ---------------------------------------------------------------------------
# The machine-readable seam (enum/event other components consume)
# ---------------------------------------------------------------------------

class TestResultEvents:
    def test_enum_values_are_stable_wire_format(self):
        assert insertion.FailureKind.TOOL_MISSING.value == "tool_missing"
        assert insertion.ClipboardOutcome.WRITTEN.value == "written"

    def test_success_emits_ok_result(self, x11):
        got = []
        assert insertion.insert_text("hi", cfg(),
                                     on_result=got.append) == "typed"
        assert [(r.ok, r.strategy) for r in got] == [(True, "typed")]

    def test_listener_and_callback_get_the_same_failure(self, x11,
                                                        monkeypatch):
        def failing(args, timeout=15.0, stdin=None):
            x11["run"].append(list(args))
            if args[:2] == ["xdotool", "type"]:
                return subprocess.CompletedProcess(args, 1, b"", b"boom")
            return ok(args)

        monkeypatch.setattr(insertion, "_run", failing)
        via_listener, via_cb = [], []
        insertion.add_result_listener(via_listener.append)
        with pytest.raises(insertion.InsertError):
            insertion.insert_text("hi", cfg(), on_result=via_cb.append)
        assert via_listener == via_cb
        assert via_listener[-1].ok is False
        assert via_listener[-1].failure is insertion.FailureKind.TOOL_FAILED

    def test_broken_listener_never_breaks_insertion(self, x11):
        def bad(_):
            raise RuntimeError("listener bug")

        got = []
        insertion.add_result_listener(bad)
        insertion.add_result_listener(got.append)
        assert insertion.insert_text("hi", cfg()) == "typed"
        assert [(r.ok, r.strategy) for r in got] == [(True, "typed")]

    def test_remove_listener(self, x11):
        got = []
        insertion.add_result_listener(got.append)
        insertion.remove_result_listener(got.append)
        insertion.insert_text("hi", cfg())
        assert got == []


# ---------------------------------------------------------------------------
# Honest clipboard outcomes (F-18): no silent no-ops
# ---------------------------------------------------------------------------

class TestClipboardHonesty:
    def test_written_reports_the_tool_and_bytes(self, x11):
        r = insertion.copy_to_clipboard("emergency text")
        assert r.written and r.tool == "xclip"
        assert b"emergency text" in x11["writes"]

    def test_missing_tool_reported_not_silent(self, x11):
        x11["set_installed"](set())
        r = insertion.copy_to_clipboard("text")  # must not raise
        assert r.outcome is insertion.ClipboardOutcome.TOOL_MISSING
        assert r.tool == "xclip" and not r.written

    def test_write_failure_reported(self, x11, monkeypatch):
        def broken_popen(args, **kwargs):
            raise OSError("no fds")

        monkeypatch.setattr(insertion.subprocess, "Popen", broken_popen)
        r = insertion.copy_to_clipboard("text")
        assert r.outcome is insertion.ClipboardOutcome.WRITE_FAILED
        assert "no fds" in r.detail

    def test_fallback_propagates_the_result(self, x11):
        assert insertion.clipboard_fallback("emergency").written

    def test_wayland_missing_wl_copy(self, x11, monkeypatch):
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        x11["set_installed"](set())
        r = insertion.clipboard_fallback("t")  # must not raise
        assert r.outcome is insertion.ClipboardOutcome.TOOL_MISSING
        assert r.tool == "wl-copy"


# ---------------------------------------------------------------------------
# Wayland failure paths (degradation ladder's honest floor)
# ---------------------------------------------------------------------------

class TestWaylandFailures:
    def test_no_backend_at_all_is_honest(self, wl):
        wl["set_installed"](set())
        results = []
        with pytest.raises(insertion.InsertionFailure) as ei:
            insertion.insert_text("hello", cfg(), on_result=results.append)
        failure = ei.value
        assert failure.kind is insertion.FailureKind.NO_BACKEND
        assert "install wtype or ydotool" in str(failure)  # legacy pin
        assert (failure.result.clipboard.outcome
                is insertion.ClipboardOutcome.TOOL_MISSING)
        assert "History" in failure.result.message
        assert "wl-clipboard" in failure.install_hint
        assert results[-1].ok is False

    def test_tool_fails_but_wl_copy_preserves_transcript(self, wl,
                                                         monkeypatch):
        def failing(args, timeout=15.0, stdin=None):
            wl["run"].append(list(args))
            return subprocess.CompletedProcess(args, 1, b"", b"daemon dead")

        monkeypatch.setattr(insertion, "_run", failing)
        notices = []
        assert insertion.insert_text(
            "the words", cfg(),
            on_notice=notices.append) == "clipboard-fallback"
        assert any("paste manually" in n for n in notices)
        assert b"the words" in wl["writes"]

    def test_paste_mode_without_wl_clipboard(self, wl):
        wl["set_installed"]({"wtype"})  # typer only
        with pytest.raises(insertion.InsertionFailure) as ei:
            insertion.insert_text("text", cfg(mode="paste"))
        assert ei.value.kind is insertion.FailureKind.TOOL_MISSING
        assert "wl-clipboard" in str(ei.value)
        assert (ei.value.result.clipboard.outcome
                is insertion.ClipboardOutcome.TOOL_MISSING)
        assert "History" in ei.value.result.message


# ---------------------------------------------------------------------------
# F-05 verification: the transcript must remain in History no matter what
# ---------------------------------------------------------------------------

class TestTranscriptSurvivesInHistory:
    def test_failed_insertion_still_records_the_take(self, tmp_path,
                                                     monkeypatch):
        """The real pipeline with zero insertion tooling (PATH empty, real
        subprocess layer): the take is still in History - the recovery
        path the honest error names - and the failure surfaces through
        the machine-readable result seam."""
        import fluidvoice.pipeline as pl
        from fluidvoice.pipeline import DictationPipeline
        from tests.test_daemon import StubBackend, make_wav

        monkeypatch.setenv("PATH", str(tmp_path))  # zero tools reachable
        monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.setenv("DISPLAY", ":99")
        notified = []
        monkeypatch.setattr(pl.ui, "notify",
                            lambda title, body="", **kw:
                            notified.append((title, body)))
        history = []
        results = []
        insertion.add_result_listener(results.append)
        pipe = DictationPipeline(cfg(), StubBackend("the stranded take"),
                                 logger=lambda m: None,
                                 history_writer=lambda e, w:
                                 history.append(e))
        out = pipe.run(make_wav(tmp_path / "u.wav"), "TestApp")
        # the take survived - never stranded
        assert history and history[0]["text"] == "the stranded take"
        assert out["text"] == "the stranded take"
        # the pipeline labels it clipboard-fallback (its own seam); the
        # insertion layer's truth is in the emitted result
        failed = [r for r in results if not r.ok]
        assert failed and failed[-1].failure \
            is insertion.FailureKind.TOOL_MISSING
        assert "History" in failed[-1].message
        # ... and the honest message reached the notification path
        assert any("History" in body for _, body in notified)
