"""CommandCoordinator: the daemon's command-mode conversation owner (P1.2).

The full command-conversation state machine - proposal, (strong)
confirmation, timeout, the conversation panel and its history feed -
extracted from ``Daemon`` (fluidvoice/daemon.py) along the plan's
decomposition. ``CommandSession.confirm()`` stays the single execution
site; this coordinator only orchestrates WHO is pending, WHAT the user
sees, and WHEN the watchdog expires:

* ``begin``       turn 1 after a spoken instruction: ask the model for the
                  first proposal on a background thread (never blocking
                  the user), then hand off atomically to the pending state
* ``rerun``       the History Commands view's re-run: a stored command
                  becomes a PENDING proposal without any LLM call
* ``confirm_pending``  the hotkey press - the ONLY path into
                  CommandSession.confirm(); destructive proposals need the
                  STRONG confirmation (first press arms, second executes)
* ``cancel_pending``   Escape / lock / shutdown: nothing ever executes
* ``on_confirm_timeout``  the confirm watchdog's callback

Explicit dependencies, no singletons, no daemon import: the cfg dict, the
shared daemon state ``lock``, the daemon's ``RuntimeTasks``, the daemon's
``busy``/``recording`` state through injected callables, and callbacks
OUT (``notify``, ``arm_escape`` for the command hotkey's Escape grab).
The daemon constructs this in ``__init__`` and stays the control router.
"""
from __future__ import annotations

import threading
from typing import Callable

from .runtime_tasks import RuntimeTasks

__all__ = ["CommandCoordinator"]

Logger = Callable[[str], None]
Notifier = Callable[[str, str], None]


class CommandCoordinator:
    """Owns the pending-proposal conversation state and its UX."""

    def __init__(self, cfg: dict, tasks: RuntimeTasks, lock: threading.Lock,
                 *, is_busy: Callable[[], bool],
                 set_busy: Callable[[bool], None],
                 is_recording: Callable[[], bool],
                 log: Logger,
                 notify: Notifier,
                 session_factory: Callable | None = None,
                 arm_escape: Callable[[bool], None] = lambda active: None):
        self.cfg = cfg
        self._tasks = tasks
        self._lock = lock
        self._is_busy = is_busy
        self._set_busy = set_busy
        self._is_recording = is_recording
        self._log = log
        self._notify = notify
        self.session_factory = session_factory
        self._arm = arm_escape
        # -- conversation state (guarded by the shared lock) ---------------
        self.pending = False            # a proposal awaits confirmation
        self.session = None             # the live CommandSession while pending
        self.destructive_armed = False  # 1st press of strong confirm
        self.entries: list[dict] = []   # panel conversation feed
        self.display = None             # the live CommandPanel (overlay)
        self.timer: threading.Timer | None = None  # confirm watchdog
        self.context = None             # lazily built CommandContextStore

    # -- turn 1: propose -------------------------------------------------------

    def begin(self, instruction: str, app: str | None = None) -> None:
        """Turn 1: ask the model for the first proposal (background thread;
        the user is not blocked - not even by the LLM latency). `app` scopes
        the follow-up context store (last results in the SAME focused app)."""
        from . import command as command_mod
        if instruction.strip().lower() in command_mod.NEW_SESSION_PHRASES:
            if self.context is not None:
                self.context.clear(app)
            self._log("command context cleared (spoken 'new session')")
            self._notify("SayItErmano", "Command context cleared")
            return
        self.entries = [{"kind": "user", "text": instruction}]
        self._panel(self.entries, status="Working...", awaiting=None)

        def _work():
            factory = self.session_factory or command_mod.CommandSession
            if self.context is None:
                self.context = command_mod.CommandContextStore()
            session = factory(self.cfg, context_store=self.context, app=app)
            try:
                proposal = session.start(instruction)
            except command_mod.CommandError as e:
                with self._lock:
                    self._set_busy(False)
                self._log(f"command mode failed: {e}")
                self._notify("SayItErmano", f"Command mode failed: {e}")
                return
            if proposal is None:
                with self._lock:
                    self._set_busy(False)
                self._notify("SayItErmano",
                             session.summary or "Command mode: nothing to run.")
                return
            with self._lock:            # atomic handoff to the pending state
                self.session = session
                self.pending = True
                self._set_busy(False)   # waiting for the user, not busy
            self._present_proposal(session, proposal)

        def _guarded():
            try:
                _work()
            except Exception as e:  # noqa: BLE001 - never strand `busy`
                self._log(f"command mode failed: {e}")
                self._notify("SayItErmano", f"Command mode failed: {e}")
                self._end_session()

        with self._lock:
            if self._is_busy() or self.pending:
                return
            self._set_busy(True)
        self._tasks.spawn("command", _guarded)

    def rerun(self, command: str, purpose: str | None = None) -> dict:
        """History Commands view 'Re-run' (v2): re-post the exact stored
        command as a PENDING proposal - the user confirms with the hotkey
        exactly like a fresh voice proposal (strong confirm included when
        destructive). NOTHING executes here: this only ever creates a
        pending proposal; CommandSession.confirm() stays the single
        execution site. No LLM call is needed to propose."""
        from . import command as command_mod
        with self._lock:
            if self._is_recording() or self._is_busy() or self.pending:
                return {"ok": False, "error": "daemon busy"}
        ready = command_mod.command_mode_ready(self.cfg)
        if ready:
            return {"ok": False, "error": ready}
        if self.context is None:
            self.context = command_mod.CommandContextStore()
        factory = self.session_factory or command_mod.CommandSession
        session = factory(self.cfg, context_store=self.context)
        try:
            proposal = session.preset(command, purpose)
        except command_mod.CommandError as e:
            return {"ok": False, "error": str(e)}
        self.entries = [{"kind": "user",
                         "text": session.instruction or ""}]
        with self._lock:                # atomic handoff to the pending state
            self.session = session
            self.pending = True
            self._set_busy(False)       # waiting for the user, not busy
        self._present_proposal(session, proposal)
        return {"ok": True, "pending": True, "command": proposal.command}

    # -- the conversation panel (best-effort) ----------------------------------

    def _panel(self, entries: list[dict], status: str | None,
               awaiting: str | None):
        """Live conversation panel. Reuses the running panel when present;
        falls back to None headlessly."""
        try:
            from .overlay import CommandPanel
            panel = self.display
            if panel is not None and panel.using_overlay:
                panel.update(entries, status=status, awaiting=awaiting)
                return panel
            rcfg = self.cfg["recording"]
            panel = CommandPanel(
                bottom_offset=int(rcfg.get("preview_bottom_offset", 64)))
            if not panel.using_overlay:
                panel.close()
                return None
            panel.update(entries, status=status, awaiting=awaiting)
            panel.start()
            self.display = panel
            return panel
        except Exception as e:  # noqa: BLE001 - never block the agent loop
            self._log(f"WARN command panel unavailable: {e}")
            return None

    def _present_proposal(self, session, proposal) -> None:
        """Awaiting-confirmation UX: conversation panel, armed Escape grab,
        notification, confirm watchdog. Call with no lock held. A
        destructive proposal arms the STRONG confirmation (two presses,
        amber pill warning) - the first press only arms."""
        self.destructive_armed = False
        try:
            entry = {"kind": "proposal", "text": proposal.command,
                     "sub": proposal.purpose}
            awaiting = "run: command key · Esc"
            if proposal.destructive:
                entry["destructive"] = True
                awaiting = ("⚠ destructive — press command key AGAIN to run"
                            " · Esc")
            self.entries = (self.entries + [entry])[-8:]
            self._panel(self.entries, status=None, awaiting=awaiting)
        except Exception as e:  # noqa: BLE001 - never block confirmation
            self._log(f"WARN command pill unavailable: {e}")
        self._arm(True)  # arm the command hotkey's Escape grab
        purpose = proposal.purpose or ""
        body = (f"{purpose}\n" if purpose else "") \
            + f"$ {proposal.command}\n" \
            + "Press the command hotkey to run · Esc to cancel"
        if proposal.destructive:
            body = "⚠ DESTRUCTIVE\n" + body
        self._notify("SayItErmano — run this command?", body)
        self._restart_confirm_watchdog()

    def _restart_confirm_watchdog(self) -> None:
        timer = self._tasks.prepare_timer(
            "command-confirm", self.on_confirm_timeout,
            float(self.cfg["command"].get("confirm_timeout_s", 120.0)))
        self.timer = timer
        if timer is not None:
            timer.start()

    # -- confirm / cancel / timeout ---------------------------------------------

    def confirm_pending(self) -> None:
        """Hotkey-confirmed: execute (the only path into
        CommandSession.confirm), then either present the next proposal or
        finish. Destructive proposals need the STRONG confirmation: the
        first press only arms (fresh hint + restarted watchdog); the second
        takes this normal path. Non-destructive: single press, as always."""
        arm = False
        with self._lock:
            if not self.pending or self._is_busy() or self._is_recording():
                return
            session = self.session
            proposal = session.pending if session is not None else None
            if proposal is not None and proposal.destructive \
                    and not self.destructive_armed:
                self.destructive_armed = True
                arm = True
            else:
                self.destructive_armed = False
                self.pending = False
                self._set_busy(True)    # atomic with the flag clear
        if arm:
            self._arm_destructive(proposal)
            return
        session = self.session      # never None while pending
        # the conversation panel survives the pending-UX teardown
        panel, self.display = self.display, None
        self._teardown_pending_ux()
        self.display = panel

        def _work():
            from . import command as command_mod
            try:
                proposal = session.confirm()
            except command_mod.CommandError as e:
                self._log(f"command mode failed: {e}")
                self._notify("SayItErmano", f"Command mode failed: {e}")
                self._end_session()
                return
            outcome = session.executed[-1] if session.executed else None
            if outcome is not None:    # result via notification + history
                brief = (outcome.output or outcome.error or "").strip()[:200]
                self._notify("SayItErmano",
                             f"$ {outcome.command} → exit {outcome.exit_code}"
                             + (f"\n{brief}" if brief else ""))
                self.entries = (self.entries + [
                    {"kind": "ok" if outcome.success else "fail",
                     "text": f"$ {outcome.command} · "
                             f"exit {outcome.exit_code}"}])[-8:]
            if proposal is None:
                self.entries = (self.entries + [
                    {"kind": "summary",
                     "text": session.summary or "Command finished."}])[-8:]
                self._panel(self.entries, status=None, awaiting=None)
                self._notify("SayItErmano",
                             (session.summary or "Command finished.")
                             + (" (step limit reached)" if session.exhausted
                                else ""))
                self._tasks.schedule("command-panel-close",
                                     self._close_panel, 8.0)
                self._end_session(close_panel=False)
                return
            self._panel(self.entries, status="Working...", awaiting=None)
            with self._lock:
                self.pending = True
                self._set_busy(False)
            self._present_proposal(session, proposal)

        def _guarded():
            try:
                _work()
            except Exception as e:  # noqa: BLE001 - never strand `busy`
                self._log(f"command mode failed: {e}")
                self._notify("SayItErmano", f"Command mode failed: {e}")
                self._end_session()

        self._tasks.spawn("command", _guarded)

    def _arm_destructive(self, proposal) -> None:
        """First press on a destructive proposal: NOTHING executes. Refresh
        the pill to the again-to-CONFIRM hint, re-notify and restart the
        confirm watchdog (the old timer is cancelled - never stacked)."""
        if self.timer:
            self.timer.cancel()
            self.timer = None
        self._panel(
            self.entries, status=None,
            awaiting="⚠ press command key AGAIN to CONFIRM · Esc cancels")
        purpose = proposal.purpose or ""
        body = (f"{purpose}\n" if purpose else "") \
            + f"$ {proposal.command}\n" \
            + "⚠ destructive: press the command hotkey AGAIN to CONFIRM " \
              "· Esc to cancel"
        self._notify("SayItErmano — ⚠ destructive", body)
        self._restart_confirm_watchdog()

    def cancel_pending(self) -> None:
        """Escape on a pending proposal (or a test): nothing executes."""
        with self._lock:
            if not self.pending:
                return
            self.pending = False
        session, self.session = self.session, None
        self._teardown_pending_ux()
        if session is not None:
            session.cancel()
        self._notify("SayItErmano", "Command cancelled")

    def on_confirm_timeout(self) -> None:
        if self.pending:
            self.cancel_pending()
            self._notify("SayItErmano",
                         "Command mode: confirmation timed out")

    # -- teardown --------------------------------------------------------------

    def _teardown_pending_ux(self) -> None:
        self.destructive_armed = False
        if self.timer:
            self.timer.cancel()
            self.timer = None
        self._arm(False)  # disarm the command hotkey's Escape grab
        display, self.display = self.display, None
        if display is not None:
            try:
                display.close()
            except Exception:
                pass

    def _close_panel(self) -> None:
        if self.session is not None:
            return  # a new command session reused the panel - leave it up
        panel, self.display = self.display, None
        if panel is not None:
            try:
                panel.close()
            except Exception:
                pass

    def _end_session(self, close_panel: bool = True) -> None:
        with self._lock:
            self.pending = False
            self.destructive_armed = False
            self._set_busy(False)
        self.session = None
        if close_panel:
            self._teardown_pending_ux()
