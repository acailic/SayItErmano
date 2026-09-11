"""CommandCoordinator (P1.2): the command-conversation state machine
(proposal / strong confirm / timeout / cancel) tested with fake deps - no
Daemon, no overlay, no LLM. The daemon wiring (hotkey routing, pipeline
handoff) stays covered by test_command.py through the daemon's
delegation."""
from __future__ import annotations

import copy
import threading
import time

import pytest

from fluidvoice.command_coord import CommandCoordinator
from fluidvoice.config import DEFAULTS
from fluidvoice.runtime_tasks import RuntimeTasks


class FakeProposal:
    def __init__(self, command, purpose=None, destructive=False):
        self.command = command
        self.purpose = purpose
        self.destructive = destructive


class FakeOutcome:
    def __init__(self, command, exit_code=0, output="ok"):
        self.command = command
        self.success = exit_code == 0
        self.exit_code = exit_code
        self.output = output
        self.error = None


class FakeSession:
    """The CommandSession surface the coordinator touches - scripted."""

    def __init__(self, cfg, context_store=None, app=None, **kw):
        self.context_store = context_store
        self.app = app
        self.instruction = ""
        self.summary = "all done"
        self.exhausted = False
        self.pending = None
        self.executed: list = []
        self.cancelled = False
        self.finished = False
        self.started: list = []
        self.presets: list = []
        self.outcome = None
        self.next_proposal = None

    def start(self, instruction):
        self.instruction = instruction
        self.started.append(instruction)
        return self.pending

    def preset(self, command, purpose=None):
        self.presets.append((command, purpose))
        self.instruction = f"re-run: {command}"
        return self.pending

    def confirm(self):
        assert self.pending is not None, "confirm without a pending proposal"
        outcome = self.outcome
        self.executed.append(outcome)
        self.pending = self.next_proposal
        return self.pending

    def cancel(self):
        self.cancelled = True
        self.finished = True


def make(session=None):
    """Coordinator with recorded callbacks and injectable daemon state."""
    cfg = copy.deepcopy(DEFAULTS)
    cfg["ai"]["enabled"] = True
    cfg["ai"]["base_url"] = "http://localhost:11434/v1"
    cfg["ai"]["model"] = "qwen3:8b"
    state = {"busy": False, "recording": False}
    calls = {"notify": [], "arm": [], "log": []}
    session = session or FakeSession(cfg)
    coord = CommandCoordinator(
        cfg, RuntimeTasks(), threading.Lock(),
        is_busy=lambda: state["busy"],
        set_busy=lambda v: state.__setitem__("busy", v),
        is_recording=lambda: state["recording"],
        log=lambda msg: calls["log"].append(msg),
        notify=lambda t, b: calls["notify"].append((t, b)),
        session_factory=lambda c, **kw: session,
        arm_escape=lambda active: calls["arm"].append(active))
    return coord, session, state, calls


def wait_until(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return False


class FakePanel:
    using_overlay = True
    built = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.updates = []
        self.started = 0
        self.closed = 0
        FakePanel.built.append(self)

    def update(self, entries, status=None, awaiting=None):
        self.updates.append((list(entries), status, awaiting))

    def start(self):
        self.started += 1

    def close(self):
        self.closed += 1


@pytest.fixture()
def panel(monkeypatch):
    FakePanel.built = []
    monkeypatch.setattr("fluidvoice.overlay.CommandPanel", FakePanel)
    return FakePanel


# ---------------------------------------------------------------------------
# begin: turn 1
# ---------------------------------------------------------------------------

class TestBegin:
    def test_begin_refused_while_busy(self):
        coord, session, state, _ = make()
        state["busy"] = True
        coord.begin("list files")
        time.sleep(0.1)
        assert session.started == []  # no LLM session was ever built

    def test_new_session_phrase_clears_context_without_llm(self):
        coord, session, state, calls = make()
        store = type("S", (), {"cleared": [],
                               "clear": lambda self, app:
                               self.cleared.append(app)})()
        coord.context = store
        coord.begin("  New Session  ", app="firefox")
        assert store.cleared == ["firefox"]
        assert session.started == []
        assert any("context cleared" in (t + b).lower()
                   for t, b in calls["notify"])

    def test_begin_proposal_lands_pending(self, panel):
        gate = threading.Event()
        coord, session, state, calls = make()
        proposal = FakeProposal("echo hello", "greet")

        class GatedSession(FakeSession):
            def __init__(self, cfg, **kw):
                super().__init__(cfg, **kw)
                self.pending = proposal  # the scripted proposal

            def start(self, instruction):
                gate.wait(timeout=5.0)  # hold turn 1 for the busy assert
                return super().start(instruction)

        coord.session_factory = lambda c, **kw: GatedSession(c, **kw)
        coord.begin("list files")
        assert wait_until(lambda: state["busy"])  # claimed while turn 1 runs
        gate.set()
        assert wait_until(lambda: coord.pending)
        assert state["busy"] is False  # waiting for the user, not busy
        assert calls["arm"] == [True]  # Escape grab armed
        assert coord.timer is not None  # confirm watchdog armed
        entries, status, awaiting = panel.built[-1].updates[-1]
        assert entries[0] == {"kind": "user", "text": "list files"}
        assert entries[-1]["kind"] == "proposal"
        assert "Esc" in awaiting
        assert any("run this command?" in (t + b)
                   for t, b in calls["notify"])
        coord.cancel_pending()  # hygiene: never leave the confirm watchdog

    def test_begin_none_proposal_releases_busy(self):
        coord, session, state, calls = make()
        session.pending = None  # the model had nothing to run
        session.summary = None  # force the default nothing-to-run message
        coord.begin("list files")
        assert wait_until(lambda: any("nothing to run" in (t + b)
                                      for t, b in calls["notify"]))
        assert not state["busy"] and not coord.pending

    def test_begin_error_releases_busy(self):
        coord, _, state, calls = make()
        import fluidvoice.command as command_mod

        class ExplodingSession(FakeSession):
            def start(self, instruction):
                raise command_mod.CommandError("no ai")

        coord.session_factory = lambda c, **kw: ExplodingSession(c, **kw)
        coord.begin("list files")
        assert wait_until(lambda: any("Command mode failed" in (t + b)
                                      for t, b in calls["notify"]))
        assert not state["busy"] and not coord.pending  # never stranded


# ---------------------------------------------------------------------------
# rerun: History Commands view
# ---------------------------------------------------------------------------

class TestRerun:
    def test_rerun_presents_pending_without_llm(self, panel):
        coord, session, state, _ = make()
        session.pending = FakeProposal("echo reran me", "checking")
        out = coord.rerun("echo reran me", "checking")
        assert out["ok"] is True and out["pending"] is True
        assert coord.pending and not state["busy"]
        assert session.presets == [("echo reran me", "checking")]
        assert session.started == []  # NO LLM call to propose
        entries = panel.built[-1].updates[-1][0]
        assert entries[-1]["kind"] == "proposal"
        coord.cancel_pending()  # hygiene: never leave the confirm watchdog

    def test_rerun_guards(self):
        coord, _, state, _ = make()
        coord.pending = True
        assert coord.rerun("x")["error"] == "daemon busy"
        coord.pending = False
        state["busy"] = True
        assert coord.rerun("x")["error"] == "daemon busy"
        state["busy"] = False
        state["recording"] = True
        assert coord.rerun("x")["error"] == "daemon busy"
        state["recording"] = False

    def test_rerun_refuses_unready_ai(self):
        coord, _, _, _ = make()
        coord.cfg["ai"]["enabled"] = False
        out = coord.rerun("echo x")
        assert out["ok"] is False and out["error"]
        assert not coord.pending


# ---------------------------------------------------------------------------
# confirm / strong confirm / cancel / timeout
# ---------------------------------------------------------------------------

class TestConfirm:
    def _pending(self, panel, destructive=False):
        coord, session, state, calls = make()
        session.pending = FakeProposal("echo hi", destructive=destructive)
        session.outcome = FakeOutcome("echo hi")
        session.next_proposal = None
        coord.begin("do it")
        assert wait_until(lambda: coord.pending)
        return coord, session, state, calls

    def test_single_press_executes_and_finishes(self, panel):
        coord, session, state, _ = self._pending(panel)
        coord.confirm_pending()
        assert wait_until(lambda: coord.session is None and not state["busy"])
        assert [o.command for o in session.executed] == ["echo hi"]
        assert coord.pending is False
        # summary entry landed, panel close scheduled, grab disarmed
        assert panel.built[-1].updates[-1][0][-1]["kind"] == "summary"
        assert coord.timer is None or coord.timer.finished.is_set()
        coord._tasks.cancel("command-panel-close")  # hygiene: no 8 s leftover

    def test_busy_press_is_ignored(self, panel):
        coord, session, state, _ = self._pending(panel)
        state["busy"] = True
        coord.confirm_pending()
        time.sleep(0.1)
        assert session.executed == []  # nothing ran
        state["busy"] = False
        coord.cancel_pending()  # hygiene: never leave the confirm watchdog

    def test_destructive_needs_two_presses(self, panel):
        coord, session, state, _ = self._pending(panel, destructive=True)
        coord.confirm_pending()  # first press: arms only
        assert session.executed == []
        assert coord.pending is True
        assert coord.destructive_armed is True
        _, _, awaiting = panel.built[-1].updates[-1]
        assert "AGAIN to CONFIRM" in awaiting
        coord.confirm_pending()  # second press: executes
        assert wait_until(lambda: session.executed)
        assert wait_until(lambda: coord.session is None)
        coord._tasks.cancel("command-panel-close")  # hygiene: no 8 s leftover

    def test_escape_between_presses_executes_nothing(self, panel):
        coord, session, state, _ = self._pending(panel, destructive=True)
        coord.confirm_pending()  # armed
        coord.cancel_pending()
        assert session.executed == []
        assert coord.pending is False
        assert coord.destructive_armed is False
        assert session.cancelled and session.finished

    def test_timeout_cancels(self, panel):
        coord, session, _, calls = self._pending(panel)
        coord.on_confirm_timeout()
        assert coord.pending is False
        assert session.cancelled
        assert any("timed out" in (t + b) for t, b in calls["notify"])

    def test_followup_proposal_stays_pending(self, panel):
        coord, session, state, _ = self._pending(panel)
        session.next_proposal = FakeProposal("echo more")
        coord.confirm_pending()
        assert wait_until(lambda: coord.pending and not state["busy"])
        assert [o.command for o in session.executed] == ["echo hi"]
        assert coord.session is session  # same conversation continues
        entries = panel.built[-1].updates[-1][0]
        assert entries[-1]["kind"] == "proposal"
        coord.cancel_pending()  # hygiene: never leave the confirm watchdog


# ---------------------------------------------------------------------------
# teardown
# ---------------------------------------------------------------------------

class TestTeardown:
    def test_headless_panel_falls_back_to_none(self, monkeypatch):
        class Headless:
            using_overlay = False

            def __init__(self, **kw):
                self.closed = 0

            def close(self):
                self.closed += 1

        monkeypatch.setattr("fluidvoice.overlay.CommandPanel", Headless)
        coord, session, _, _ = make()
        session.pending = FakeProposal("echo hi")
        coord.begin("x")
        assert wait_until(lambda: coord.pending)
        assert coord.display is None  # headless: no panel, no crash
        coord.cancel_pending()  # hygiene: never leave the confirm watchdog

    def test_cancel_during_present_never_rearms_watchdog(self):
        """Q2 leak-gate catch: Escape landing between the pending handoff
        and the watchdog arm used to leave the 120 s `command-confirm`
        timer pending after cancel_pending's teardown — a late
        _restart_confirm_watchdog (the background present thread) must
        now find `pending` False and arm nothing."""
        coord, session, _, _ = make()
        session.pending = FakeProposal("echo hi")
        with coord._lock:  # the exact state begin()'s worker hands over
            coord.session = session
            coord.pending = True
        coord.cancel_pending()  # Escape wins the race
        coord._restart_confirm_watchdog()  # late arm attempt
        assert coord.timer is None
        assert coord._tasks.handle("command-confirm") is None
        report = coord._tasks.shutdown(timeout=5)
        assert "command-confirm" not in report["cancelled"]
