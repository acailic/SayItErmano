# Command Mode — voice-driven terminal agent (Linux port plan)

Session: `3c04b728` · Repo: FluidVoiceLinux, branch `linux` · Baseline HEAD:
`3803787`, working tree clean except untracked `requests/` (leave it alone).
Default suite at planning time: **331 passed** in ~13 s.

Gate (run after EVERY phase, must stay green):

```
.venv/bin/python -m pytest -q tests --ignore=tests/integration
```

---

## 1. What we are building

Port of macOS **Command Mode** (`/tmp/fluidui/mac/CommandModeService.swift`,
the upstream reference — read it, don't port it literally). The user presses a
hotkey, dictates an instruction; the LLM (same OpenAI-compatible `AIClient`
transport as polish/rewrite) proposes **one shell command at a time**; the
command is shown in the pill overlay in a new **awaiting-confirmation** state;
pressing the command hotkey again **confirms and executes**, `Escape`
**cancels**; the command's output is fed back to the model and the loop
continues, bounded by `command.max_turns` (default 4).

Deliberate v1 divergences from upstream (recorded in the plan, keep them):

| Upstream (macOS) | Linux v1 |
|---|---|
| native OpenAI `tools` / tool_calls protocol | **strict-JSON protocol** — system prompt demands a single JSON object `{"command", "purpose", "done"}`; parsed client-side after fence-stripping |
| `role:"tool"` messages with `tool_call_id` | result fed back as a **`user` message** containing result JSON (plain OpenAI-compatible servers reject bare `role:"tool"`) |
| auto-executes non-destructive commands; confirm only destructive ones | **EVERY command requires explicit confirmation** — nothing ever runs without the hotkey press |
| maxTurns 20, chat store persistence, notch UI, streaming, analytics | max_turns default 4, single run (history entries only), pill overlay + notifications, none of the rest |
| zsh via TerminalService | `bash -c` (falls back to `sh -c`) via `subprocess` with timeout, cwd from config |

Rewrite mode is the structural template to follow end-to-end:
`hotkey.rewrite_key` → `Daemon.start_rewrite()` → `_process(mode="rewrite")` →
`DictationPipeline.run` branch → `fluidvoice/rewrite.py` module with injectable
seams. Command mode gets the same shape with one extra state: the daemon holds
a **pending proposal** between turns (idle / recording / busy / pending).

### Safety invariants (every phase must preserve these)

1. A shell command executes ONLY from `CommandSession.confirm()`, which the
   daemon calls ONLY from the hotkey-confirm path. No auto-execution anywhere.
2. Every executed command is appended to history (`mode: "command"`), no matter
   the exit code. Cancellations and parse failures never execute anything.
3. Model output is never trusted: `<think>` stripped, markdown fences stripped,
   JSON parsed; on parse failure the **raw proposal text is shown to the user**
   (notification) and the run is cancelled.
4. AI must be enabled AND configured (`base_url` + `model`) or command mode
   refuses with a clear notification — before recording starts, and again in
   the session (defense in depth).
5. Execution is bounded: subprocess timeout (`command.timeout_seconds`), turn
   bound (`command.max_turns`), confirmation timeout (`command.confirm_timeout_s`)
   so a stray pill/Escape-grab can never strand.

---

## 2. Design — the pieces

### 2.1 `fluidvoice/command.py` (new module, mirrors rewrite.py)

```python
class CommandError(RuntimeError): ...          # carries user-readable text

@dataclass
class PendingCommand:
    command: str
    purpose: str | None = None

@dataclass
class CommandOutcome:                          # execution result (never raised)
    command: str
    success: bool
    exit_code: int
    output: str                                # stdout+stderr combined
    error: str | None = None
    duration_ms: int = 0

@dataclass
class ParsedReply:
    kind: str                                  # "proposal" | "done"
    proposal: PendingCommand | None = None
    summary: str | None = None
```

Pure functions (unit-test targets):

- `command_mode_ready(cfg) -> str | None` — `None` when ready, else the
  readiness issue. Reuses rewrite's exact wording: `"command mode needs [ai]
  enabled with a model"` / `"AI enabled but base_url/model not configured"`.
- `strip_code_fences(text) -> str` — strip whitespace, then a leading
  ` ```json ` / ` ``` ` line and a trailing ` ``` ` line (regex, tolerate
  language tags and stray spaces).
- `parse_reply(content) -> ParsedReply` — `strip_thinking` (from
  `fluidvoice.ai.client`) → `strip_code_fences` → `json.loads`; if that fails,
  one tolerant retry on the slice between the first `{` and last `}`. Then:
  `done` truthy → `ParsedReply("done", summary=... or "Done.")`; `command` a
  non-empty string → `ParsedReply("proposal", PendingCommand(command, purpose))`;
  anything else → `raise CommandError(f"could not parse the model's proposal: {raw}")`
  with the raw text embedded (the daemon shows it verbatim).
- `working_dir(cfg) -> Path` — `expanduser(cfg["command"].get("working_dir") or "~")`;
  if not a directory, fall back to `Path.home()` (log a warning, never crash).
- `run_shell(command, cwd, timeout) -> CommandOutcome` —
  `subprocess.run([shell, "-c", command], cwd=..., capture_output=True,
  text=True, timeout=...)` where `shell = "/bin/bash"` if it exists else
  `"/bin/sh"`. `TimeoutExpired` / `OSError` become failure outcomes
  (`exit_code=-1`, `error="timed out after Ns"` / the OS message), never
  exceptions. Combined output = stdout + stderr.

The session (agent loop):

```python
class CommandSession:
    def __init__(self, cfg, *, client=None, runner=None,
                 history_appender=None, log=None):
        ...
    # introspection for the daemon/tests
    pending: PendingCommand | None
    finished: bool            # done / exhausted / cancelled / errored
    exhausted: bool           # hit max_turns
    cancelled: bool
    summary: str | None       # final assistant summary
    executed: list[CommandOutcome]
    turns: int                # LLM calls made

    def start(self, instruction: str) -> PendingCommand | None
    def confirm(self) -> PendingCommand | None
    def cancel(self) -> None
```

- `start(instruction)`:
  1. `ready = command_mode_ready(cfg)`; if not ready → `raise CommandError(ready)`.
  2. `self.client = client or AIClient(cfg)` (injected stubs never build one).
  3. Append `{"role": "user", "content": instruction}` to
     `self.messages` (initialized with the system prompt), run one turn via
     `_advance()`, return the proposal (or `None` when finished).
- `confirm()`:
  1. `if self.finished: raise CommandError("session is over")`;
     `if self.pending is None: raise CommandError("no pending command")`.
  2. `outcome = self._execute(self.pending)` — via `self.runner`
     (default `run_shell`) with `cwd=working_dir(cfg)`,
     `timeout=cfg["command"]["timeout_seconds"]`. **This is the only
     execution site in the codebase.** Append to `self.executed`; write ONE
     history entry (below).
  3. Feed the result back as a user message and `_advance()` for the next
     proposal (or `None` when finished).
- `cancel()`: `self.pending = None; self.cancelled = self.finished = True`.
  No history, nothing executed.
- `_advance()` (shared turn logic):
  1. If `self.turns >= int(cfg["command"].get("max_turns", 4))`:
     `self.exhausted = self.finished = True`;
     `self.summary = "Reached maximum steps limit. Please review the progress and continue if needed."`
     (upstream wording); return `None`.
  2. `self.turns += 1`;
     `content = self.client.chat_messages(self.messages, temperature=0.1)`
     (`AIError` → `raise CommandError(str(e)) from e`).
  3. Append the raw assistant reply to `self.messages`
     (`{"role": "assistant", "content": content}`).
  4. `reply = parse_reply(content)` — parse errors propagate as
     `CommandError` (marks the session errored: set `finished=True` first).
  5. Proposal → `self.pending = reply.proposal`; return it.
     Done → `self.summary = reply.summary; self.finished = True`;
     return `None`.
- Result feedback message (built in `confirm`):
  `{"role": "user", "content": "Command result (JSON): " + json.dumps({...})}`
  with keys `command`, `exit_code`, `success`, `output` (clipped: first 3000 +
  `"\n…\n"` + last 1000 chars), `error`, `duration_ms`.
- History entry per executed command (written by `confirm` via
  `self.history_appender`, default `None` → real writer that no-ops when
  `cfg["history"].get("save")` is false, else `history_mod.append(entry)`
  — import as `from . import history as history_mod` so the tests' path
  monkeypatch (see `quiet_ui`) applies):

```python
{"ts": time.time(), "mode": "command", "raw": <instruction>,
 "text": f"$ {command}", "command": command, "purpose": purpose,
 "exit_code": outcome.exit_code, "success": outcome.success,
 "output": outcome.output[:2000], "duration_ms": outcome.duration_ms,
 "backend": "shell"}
```

  (Final summaries, cancellations and turn-limit stops surface via
  notification only — history records executed commands, nothing else.)

System prompt (ship exactly this, in `command.py` as `SYSTEM_PROMPT`):

```
You are a careful Linux terminal agent. The user speaks an instruction; you
accomplish it by proposing ONE shell command at a time. Every proposal is
shown to the user and only runs after they confirm it.

Respond with EXACTLY ONE JSON object and nothing else - no markdown fences,
no prose before or after:

  {"command": "<one shell command>", "purpose": "<short reason>", "done": false}

When the task is complete (or nothing needs to run), respond with:

  {"command": "", "purpose": "", "done": true, "summary": "<what happened, 1-3 sentences>"}

Rules:
- One command per reply. Chain with && or ; only when it is genuinely one step.
- Check before acting: list or test -e before deleting or overwriting; read
  before editing; check --version before installing.
- Verify after acting, then finish with done=true and a short summary.
- Non-interactive commands only: never propose password prompts, editors,
  top, or anything that waits for input.
- Quote paths with spaces; prefer absolute paths.
- Commands run in the user's shell in the working directory; after each one
  you receive its stdout+stderr, exit code and duration as JSON.
```

### 2.2 Config (`fluidvoice/config.py`)

- `DEFAULTS["hotkey"]["command_key"] = ""` — same "optional, unbound by
  default" contract as `rewrite_key`.
- New section in `DEFAULTS`:

```python
"command": {
    "max_turns": 4,          # agent loop bound (upstream: 20)
    "working_dir": "",       # "" -> $HOME
    "timeout_seconds": 60.0, # per-command subprocess timeout
    "confirm_timeout_s": 120.0,  # auto-cancel a pending confirmation
},
```

- Mirror every place `rewrite_key` appears, plus the new section:
  `_SAVE_WHITELIST["hotkey"]` += `"command_key"`;
  `_SAVE_WHITELIST["command"] = ["max_turns", "working_dir", "timeout_seconds",
  "confirm_timeout_s"]`;
  `SETTING_RANGES` += `("hotkey", "command_key"): ("str", 64)`,
  `("command", "max_turns"): ("int", (1, 20))`,
  `("command", "working_dir"): ("str", 4096)`,
  `("command", "timeout_seconds"): ("float", (1, 3600))`,
  `("command", "confirm_timeout_s"): ("float", (5, 600))`;
  `ALLOWED_SETTINGS["hotkey"]` += `"command_key"`;
  `ALLOWED_SETTINGS["command"] = {"max_turns", "working_dir",
  "timeout_seconds", "confirm_timeout_s"}`.
- No additions to `RESTART_REQUIRED` / `ENGINE_KEYS` (all `command.*` keys are
  read at use time; `hotkey.*` already triggers the live re-grab via
  `apply_config`'s `k.startswith("hotkey.")`).
- `TEMPLATE`: leave unchanged (`rewrite_key` is not in it either; settings UI
  and DEFAULTS are the surface). Note this in the commit message, not the file.

### 2.3 Overlay (`fluidvoice/overlay.py`) — awaiting-confirmation state

- New pure helper:

```python
def confirmation_pill_text(command: str, purpose: str | None = None) -> str:
    lines = [f"$ {command}"]
    if purpose:
        lines.append(purpose)
    lines.append("hotkey = run · Esc = cancel")
    return "\n".join(lines)
```

- `FluidOverlay.set_state`: accept `"recording" | "processing" | "confirm"`.
  Move the state assignment **before** the `if self._d is None: return` guard
  (headless instances then record state for tests/notifications), validate the
  name (`ValueError` otherwise), update the docstring. No renderer changes
  needed: in `confirm` state the bars simply stay static (no PCM, no shimmer)
  and the pill text carries the content; `_run`'s 15 s `PROCESSING_CAP` only
  applies to `"processing"` (already conditioned on `state == "processing"`),
  so a confirm pill waits indefinitely for `close()` — the daemon's
  `confirm_timeout_s` timer is what bounds it.

### 2.4 Pipeline routing (`fluidvoice/daemon.py`, `DictationPipeline`)

In `run()`, after `post_process`, branch like rewrite:

```python
if mode == "command":
    return self._command(text, raw, duration, wav)
```

`_command` does NOT insert, polish, or write history (the session logs executed
commands later). It returns
`{"mode": "command", "text": text, "raw": raw, "duration_s": round(duration, 2)}`,
logs `command instruction (N chars): …`, and that's all. `run()`'s existing
`finally: wav.unlink` still applies.

### 2.5 Daemon wiring (`fluidvoice/daemon.py`)

State (init in `__init__`): `self._command_mode = False` (recording flag,
mirrors `_rewrite_mode`), `self._command_session = None`,
`self._command_pending = False`, `self._command_display = None`,
`self._command_timer = None`, `self._command_hotkey = None`. New ctor kwarg
`command_session_factory=None` (default: build `command.CommandSession`;
tests inject a closure that returns a session with a stub `AIClient` /
runner / history appender — same pattern as `backend_factory`).

**Hotkey registration** — in `_start_hotkey`, after the rewrite block, add the
same shape:

```python
command_key = (hk.get("command_key") or "").strip()
if command_key:
    try:
        self._command_hotkey = HotkeyListener(
            key=command_key, modifiers=[], mode="toggle",
            on_toggle=self._on_command_hotkey,
            on_cancel=self.cancel_pending_command,
            cancel_key=hk.get("cancel_key", "Escape"))
        self._command_hotkey.start()
        for line in self._command_hotkey.summary:
            log(line)
    except HotkeyError as e:
        self._command_hotkey = None
        log(f"WARN command hotkey unavailable: {e}")
        error = error or str(e)
```

- The command listener's cancel key (`Escape`) is grabbed **only while a
  proposal is pending**, by calling `self._command_hotkey.set_recording(True/False)`
  (the method is the generic "arm the cancel grab while active" switch —
  that's exactly the recording-scoped Escape grab). While *recording* the
  instruction, the main listener's Escape grab handles cancel, exactly like a
  rewrite recording today; the two grabs are never armed at the same time.
- Add `"_command_hotkey"` to the attrs loop in `_restart_hotkey` and stop it
  in `shutdown()`.

**Recording start** — `start_command()`, mirroring `start_rewrite`:

```python
def start_command(self) -> None:
    from . import command as command_mod
    with self._lock:
        if self.recording or self.busy or self._command_pending:
            return
    ready = command_mod.command_mode_ready(self.cfg)
    if ready:
        log(ready)
        ui.notify("FluidVoice", f"Command mode unavailable: {ready}",
                  enabled=self.cfg["notifications"]["enabled"])
        return
    with self._lock:
        self._command_mode = True
        log("command mode")
        self._start_recording_locked()
```

**Hotkey router**:

```python
def _on_command_hotkey(self) -> None:
    if self._command_pending:
        self._confirm_pending_command()
    else:
        self.start_command()      # existing guards make this a no-op mid-recording
```

(While command-recording, the command hotkey is a no-op — stop the recording
with the main dictation hotkey, same as rewrite mode. Document in README.)

**Stop/mode plumbing** — in `_stop_recording_locked`:

- On the **no-audio early-return path** (`wav is None or too small`), reset
  `self._rewrite_mode = False` and `self._command_mode = False` **before**
  `return` — today the rewrite flag survives this path and poisons the next
  plain dictation as a rewrite; command mode must not inherit the bug (fix
  both flags in one edit, add the tests below).
- Mode selection becomes
  `mode = "rewrite" if self._rewrite_mode else "command" if self._command_mode else "dictate"`,
  then reset BOTH flags to False right after reading them (the existing code
  already resets `_rewrite_mode` there; add `_command_mode`).

**Recording cancel** — `cancel()`: inside the lock after stopping, also
`self._rewrite_mode = False; self._command_mode = False` (same latent bug as
above: an Escape during a rewrite/command recording must not leak the mode
into the next dictation).

**Turn 1** — at the END of `_process` (after the `try/finally` that clears
`busy`, so there is no busy-flag race):

```python
if mode == "command" and out.get("mode") == "command":
    self._begin_command(str(out.get("text", "")))
```

```python
def _begin_command(self, instruction: str) -> None:
    from . import command as command_mod
    def _work():
        factory = self._command_session_factory or command_mod.CommandSession
        session = factory(self.cfg)
        try:
            proposal = session.start(instruction)
        except command_mod.CommandError as e:
            self.log(f"command mode failed: {e}")
            ui.notify("FluidVoice", f"Command mode failed: {e}",
                      enabled=self.cfg["notifications"]["enabled"])  # parse failures: raw proposal is in `e`
            return
        if proposal is None:
            ui.notify("FluidVoice",
                      session.summary or "Command mode: nothing to run.",
                      enabled=self.cfg["notifications"]["enabled"])
            return
        with self._lock:                 # atomic handoff to the pending state
            self._command_session = session
            self._command_pending = True
            self.busy = False            # waiting for the user, not busy
        self._present_proposal(session, proposal)
    with self._lock:
        if self.busy or self._command_pending:
            return
        self.busy = True
    threading.Thread(target=_work, name="fluidvoice-command", daemon=True).start()
```

**Pending UX** — `_present_proposal(session, proposal)` (call with no lock held):

1. Pill: `from .overlay import FluidOverlay, confirmation_pill_text`;
   build `FluidOverlay(raw_path=None, bottom_offset=int(rcfg.get("preview_bottom_offset", 64)), size="large")`
   (`size="large"` — the command + purpose + hint needs the 4-line preset).
   If `ov.using_overlay`: `ov.show(confirmation_pill_text(proposal.command, proposal.purpose))`,
   `ov.set_state("confirm")`, `ov.start()`, store in `self._command_display`;
   else `ov.close()` (headless: the notification below carries everything).
   Wrap in try/except → log a WARN and continue (never block confirmation on
   the pill).
2. Arm the Escape grab: `if self._command_hotkey: self._command_hotkey.set_recording(True)`.
3. Notification:
   `ui.notify("FluidVoice — run this command?", f"{purpose}\n$ {command}\nPress the command hotkey to run · Esc to cancel", ...)`.
4. Confirm watchdog:
   `self._command_timer = threading.Timer(float(cfg["command"].get("confirm_timeout_s", 120.0)), self._on_confirm_timeout)`,
   daemon=True, start.

**Confirm** — `_confirm_pending_command()`:

```python
with self._lock:
    if not self._command_pending or self.busy or self.recording:
        return
    self._command_pending = False
    self.busy = True                     # atomic with the flag clear
session = self._command_session          # never None while pending
self._teardown_pending_ux()              # cancel timer, close pill, disarm Escape grab
def _work():
    from . import command as command_mod
    try:
        proposal = session.confirm()
    except command_mod.CommandError as e:
        self.log(f"command mode failed: {e}")
        ui.notify("FluidVoice", f"Command mode failed: {e}", ...)
        self._end_command_session()
        return
    outcome = session.executed[-1] if session.executed else None
    if outcome is not None:              # result surfaces via notification + history
        brief = (outcome.output or outcome.error or "").strip()[:200]
        ui.notify("FluidVoice",
                  f"$ {outcome.command} → exit {outcome.exit_code}"
                  + (f"\n{brief}" if brief else ""), ...)
    if proposal is None:
        ui.notify("FluidVoice",
                  (session.summary or "Command finished.")
                  + (" (step limit reached)" if session.exhausted else ""), ...)
        self._end_command_session()
        return
    with self._lock:
        self._command_pending = True
        self.busy = False
    self._present_proposal(session, proposal)
with-try/finally: on any unexpected exception → log + notify + _end_command_session
threading.Thread(target=_work, name="fluidvoice-command", daemon=True).start()
```

**Cancel (Escape) / timeout / teardown**:

```python
def cancel_pending_command(self) -> None:          # hotkey Escape + tests
    with self._lock:
        if not self._command_pending:
            return
        self._command_pending = False
    session, self._command_session = self._command_session, None
    self._teardown_pending_ux()
    if session is not None:
        session.cancel()
    ui.notify("FluidVoice", "Command cancelled", ...)

def _on_confirm_timeout(self) -> None:
    if self._command_pending:
        self.cancel_pending_command()
        ui.notify("FluidVoice", "Command mode: confirmation timed out", ...)

def _teardown_pending_ux(self) -> None:
    if self._command_timer:
        self._command_timer.cancel(); self._command_timer = None
    if self._command_hotkey:
        try: self._command_hotkey.set_recording(False)
        except Exception: pass
    display, self._command_display = self._command_display, None
    if display is not None:
        try: display.close()
        except Exception: pass

def _end_command_session(self) -> None:
    with self._lock:
        self._command_pending = False
        self.busy = False
    self._command_session = None
    self._teardown_pending_ux()
```

Shutdown: `shutdown()` already cancels recordings; also call
`cancel_pending_command()` before stopping hotkeys so the grab and pill go
away cleanly.

**Tray** (`_build_tray_menu`): no change required (v1); optionally the Cancel
item already covers recording. Skip.

### 2.6 Settings UI (`fluidvoice/gtkui/settings_window.py`)

- `_build_dictation` (~line 643, hotkey group): after the `rewrite_key` row add
  `hk.add(self._entry("hotkey", "command_key", "Command key (optional, needs AI)", capture=True))`.
- `_build_ai` (~line 469, AI page): new group after the existing ones:

```python
cmd = Adw.PreferencesGroup(title="Command mode",
                           description="Voice → terminal agent. Every command needs confirmation.")
cmd.add(self._spin("command", "max_turns", "Max agent turns", 1, 20, 1))
cmd.add(self._entry("command", "working_dir", "Working directory (empty = home)"))
cmd.add(self._spin("command", "timeout_seconds", "Command timeout (s)", 1, 3600, 5, digits=1))
cmd.add(self._spin("command", "confirm_timeout_s", "Confirmation timeout (s)", 5, 600, 5, digits=1))
page.add(cmd)
```

(`_entry`/`_spin` are generic `(section, key)` row builders — no plumbing
needed beyond the config whitelist from Phase 1.)

---

## 3. Phases

### Phase 1 — Config keys (foundation)

**Edit `fluidvoice/config.py`** exactly as §2.2.

**Tests — `tests/test_config_settings.py`** (new class `TestCommandSettings`,
reuse existing fixtures):

- `test_defaults` — `load_config()` has `hotkey.command_key == ""` and the
  `command` section defaults (`max_turns` 4, `working_dir` "", `timeout_seconds`
  60.0, `confirm_timeout_s` 120.0).
- `test_apply_accepts_command_keys` — `apply_settings` round-trips
  `hotkey.command_key="F9"`, `command.max_turns=6`, `command.working_dir="/tmp"`,
  `command.timeout_seconds=30`, `command.confirm_timeout_s=60`; changed list
  has all five `section.key` strings.
- `test_apply_rejects_bad_values` — `max_turns=0`, `max_turns=21`,
  `max_turns="four"`, `timeout_seconds=0`, `working_dir="x"*5000` all rejected
  (in `rejected`, cfg untouched).
- `test_save_whitelist_writes_command_section` — `save_config` emits
  `[command]` and `command_key` (mirror the existing whitelist test's shape).

**Gate:** 331 → ~335 passed.

### Phase 2 — `fluidvoice/command.py` + protocol/loop tests

Implement §2.1 in full. **Tests — new `tests/test_command.py`**, importing the
daemon-test fixtures exactly like `tests/test_extra_formats.py` does:
`from tests.test_daemon import StubBackend, StubRecorder, make_wav, quiet_ui, cfg`.

Shared stub (top of file):

```python
class StubAIClient:
    def __init__(self, replies): self.replies = list(replies); self.calls = []
    def chat_messages(self, messages, temperature=None):
        self.calls.append(messages)
        return self.replies.pop(0)          # AssertionError("unexpected extra LLM call") when empty
```

- `TestFences`: `strip_code_fences("```json\n{…}\n```")` → the inner JSON;
  bare JSON unchanged; ` ``` ` without language tag; fenced with surrounding
  blank lines.
- `TestParseReply`: proposal dict → `ParsedReply("proposal", …)` with
  command/purpose; `{"done": true, "summary": "all good"}` → done+summary;
  `done` without summary → summary `"Done."`; prose-wrapped JSON (tolerant
  brace slice); garbage → `CommandError` whose `str()` contains the raw text.
- `TestReadiness` (refusal without AI): `cfg` default (ai disabled) →
  `command_mode_ready` returns the "needs [ai] enabled" issue and
  `CommandSession(cfg).start("x")` raises it; ai enabled but no model → the
  "not configured" issue; enabled + `base_url`/`model` set → `None`.
- `TestSessionLoop` (use `cfg` with `ai.enabled=True`, `base_url`/`model` set,
  real `run_shell` unless a `runner` stub is stated):
  1. `test_propose_confirm_execute_done` (integration core): replies
     `['{"command": "echo hello", "purpose": "say hi", "done": false}',
       '{"command": "", "purpose": "", "done": true, "summary": "said hello"}']`;
     `start` → pending command `"echo hello"`; `confirm()` → returns `None`,
     `executed[0].exit_code == 0`, `"hello" in executed[0].output`,
     `session.summary == "said hello"`, `session.finished`;
     history appender (injected list) got exactly one entry with
     `mode == "command"`, `command == "echo hello"`, `"hello" in entry["output"]`,
     `entry["text"] == "$ echo hello"`.
  2. `test_cancel_executes_nothing` (integration core): one proposal reply;
     `start` → pending; `cancel()` → `cancelled and finished`, `executed == []`,
     history appender never called; `confirm()` after cancel raises
     `CommandError`.
  3. `test_turn_bound`: `cfg["command"]["max_turns"] = 2`; three proposal
     replies; `start` → prop; `confirm` → prop; `confirm` → `None` with
     `session.exhausted`, `"maximum steps" in session.summary.lower()`;
     `len(client.calls) == 2`; runner stub called exactly 2×.
  4. `test_failure_feeds_back`: replies `['{"command": "exit 3", "purpose": "fail", "done": false}',
     '{"done": true, "summary": "failed as expected"}']`; after `confirm`,
     `executed[0].success is False`, and the LAST message sent to the client
     contains `"exit_code": 3` and `"success": false`.
  5. `test_parse_failure_raises_with_raw`: reply
     `"I will just run ls for you"` (no JSON) → `start` raises, `str(e)` contains
     that raw text; `executed == []`; `session.finished`.
  6. `test_transport_error_wrapped`: StubAIClient raising `AIError("HTTP 500")`
     → `CommandError` with "HTTP 500".
  7. `test_messages_shape`: after one propose+confirm, `client.calls[-1]`
     starts with system prompt (contains "terminal agent" and "JSON"), then
     user instruction, assistant reply, user result message (roles asserted).
- `TestRunShell`: `echo hello` → exit 0 + output; `echo err 1>&2` → stderr in
  output; `exit 3` → exit_code 3, success False; `sleep 5` with timeout 0.3 →
  success False, "timed out" in error, exit_code -1.
- `TestWorkingDir`: default → `Path.home()`; set to `tmp_path` → `tmp_path`;
  nonexistent path → falls back to `Path.home()`.

**Gate:** ~335 → ~360 passed.

### Phase 3 — Overlay awaiting-confirmation state

Implement §2.3. **Tests — `tests/test_overlay.py`** (new class
`TestConfirmState`, file is already headless/Pillow-only):

- `confirmation_pill_text("ls -la", "list files")` starts with `"$ ls -la"`,
  contains the purpose and `"Esc"`.
- `FluidOverlay()` headless: `using_overlay is False`;
  `set_state("confirm")` → `_state == "confirm"`; `set_state("nonsense")`
  raises `ValueError`; `close()` does not crash (fallback path).

**Gate:** suite green (+3).

### Phase 4 — Pipeline routing + daemon wiring (the heart)

Implement §2.4 and §2.5. **Tests — append to `tests/test_command.py`**
(class `TestPipelineCommandMode` + `TestDaemonCommandMode`), reusing
`quiet_ui` for every daemon test (it isolates the history file AND captures
notifications — assert UX through `quiet_ui["notify"]`):

`TestPipelineCommandMode`:
- `test_command_mode_returns_instruction_without_side_effects` —
  `DictationPipeline(cfg, StubBackend("um list my files"), inserter=recorder,
  history_writer=recorder)`; `run(wav, None, mode="command")` → dict with
  `mode == "command"`, non-empty `text`/`raw`; inserter and history writer
  never called.

`TestDaemonCommandMode` (Daemon with `use_hotkey=False, use_sounds=False`,
`StubRecorder`; set `cfg["ai"]` enabled+configured where a real readiness
check runs; `cfg["hotkey"]["command_key"]` unused here since listeners are
faked):
- `test_start_command_refuses_without_ai` — ai disabled → `start_command()`
  leaves `recording` False; notification contains "Command mode unavailable".
- `test_start_command_records` — ai ready → `recording` True,
  `_command_mode` True, recorder started.
- `test_mode_routing` — CapturingPipeline (copy the pattern from
  `tests/test_extra_formats.py::TestDaemonRewriteMode`) → `start_command()`,
  `toggle()`, wait for `busy` False → captured `mode == "command"` and
  `d.last_result["mode"] == "command"`; `_command_mode` reset to False.
- `test_no_audio_resets_flags` — StubRecorder subclass whose `stop()` returns
  `None` → after the toggle, `_command_mode` is False and a FOLLOW-UP plain
  `toggle()` runs the pipeline with `mode == "dictate"` (add the equivalent
  `_rewrite_mode` assertion — this is the pre-existing bug being fixed).
- `test_escape_during_recording_resets_flags` — `start_command()` then
  `d.cancel()` → `recording` False, `_command_mode` False, `_rewrite_mode`
  False; next plain toggle processes as `"dictate"`.
- `test_propose_shows_confirmation_pill` — monkeypatch
  `fluidvoice.overlay.FluidOverlay` with a `StubDisplay` recording
  `show/set_state/start/close` (`using_overlay = True`); inject
  `command_session_factory` returning a real `CommandSession` with
  `StubAIClient([proposal])` and a recording runner; fake
  `d._command_hotkey` (records `set_recording`); `d._begin_command("list files")`;
  poll until `d._command_pending` → assertions: pill `show` text contains the
  command, `set_state` called with `"confirm"`, hotkey armed
  (`set_recording(True)`), notification mentions "Esc", `busy` False,
  `_command_timer` armed.
- `test_confirm_executes_and_logs_history` (**the integration-style test**,
  real `run_shell`): factory → real `CommandSession` with
  `StubAIClient([proposal `echo hello`, done summary])`; `_begin_command` →
  wait pending → `d._on_command_hotkey()` → wait until not busy and
  `_command_session is None` → history file (via `quiet_ui`'s path) has one
  `mode == "command"` entry containing `"echo hello"` and `"hello"` in
  `output`; notifications include the result line ("exit 0") and the summary;
  pill `close` called; hotkey disarmed.
- `test_escape_cancel_executes_nothing` (**the second integration-style
  test**): same setup, but after pending call `d.cancel_pending_command()` →
  session `cancelled`, runner stub never called, history file has ZERO
  `mode == "command"` entries, notification "Command cancelled", pill closed,
  hotkey disarmed, `_on_command_hotkey()` afterwards is a harmless no-op.
- `test_confirm_timeout_cancels` — after pending, call `d._on_confirm_timeout()`
  directly (deterministic) → pending cleared + "confirmation timed out"
  notification.
- `test_on_command_hotkey_routes` — pending → confirm path (spy on
  `_confirm_pending_command`); not pending → `start_command()`; busy → ignored.
- `test_restart_and_shutdown_cover_command_hotkey` — mirror
  `TestApplyConfig.test_hotkey_restart_stops_old_and_restarts`: a
  `FakeListener` in `d._command_hotkey` is stopped by
  `apply_config(["hotkey.command_key"])` and by `shutdown()`.

**Gate:** ~363 → ~378 passed.

### Phase 5 — Settings UI + docs, final gate, commit

1. `fluidvoice/gtkui/settings_window.py` per §2.6.
   **`tests/test_gtkui.py`**: extend the settings-populate test (the one
   asserting row count/families, ~line 122) to also assert a row titled
   "Command key…" and the "Command mode" group rows ("Max agent turns",
   "Working directory…") exist. Runs under the module's existing offscreen
   skip guard.
2. Docs ledger (keep each edit small):
   - `README.md` ~line 136 feature table: add a row
     `| Command mode (voice → terminal) | ✅ (v1) | dedicated hotkey, pill confirmation, JSON agent loop |`
     and one short paragraph where rewrite mode is described: hotkey
     `command_key`, stop recording with the main dictation key, confirm with
     the command hotkey, Escape cancels, `[command]` config keys, "every
     command requires confirmation".
   - `docs/UPSTREAM-TRACKING.md` line 70: flip ⏳ → ✅ v1 with the note
     "strict-JSON single-tool protocol (no native tool_calls), every command
     confirmed, pill overlay instead of notch; chat store/tool schema later".
   - `docs/STATUS.md` line 135: check the box (reword to what shipped);
     add a test-table row for command mode; refresh the header test count
     with the real post-Phase-4 number.
3. Final gate + `git status --short` sanity (only the files above; `requests/`
   stays untracked). One commit:

```
feat: command mode — voice-driven terminal agent with pill confirmation

hotkey.command_key + [command] config; fluidvoice/command.py strict-JSON
agent loop over AIClient (bounded turns, fence-stripped parsing, every
command confirm-gated); pill awaiting-confirmation state; daemon wiring
with Escape cancel, confirm timeout, per-command history entries; settings
rows + docs. Also fixes mode flags surviving a cancelled/no-audio take
(affecting rewrite mode too).
```

---

## 4. Definition of done (maps to the task's criteria)

- **Phased, file-level plan under `specs/`** ✔ this document
  (`specs/3c04b728_command-mode.md`).
- **Each phase leaves the default suite green** ✔ gate after every phase;
  331 passing at planning time, expect ~380 at the end.
- **Unit tests for the command-loop JSON protocol** ✔ Phase 2 `TestFences` /
  `TestParseReply` (proposal parsing, fence stripping), `TestSessionLoop`
  (turn bound), `TestReadiness` (refusal without AI), plus failure-feedback
  and raw-proposal-on-parse-failure.
- **Unit tests for daemon wiring** ✔ Phase 4: mode routing, confirm path,
  cancel path, no-audio path, Escape-during-recording path, confirm timeout,
  hotkey router + lifecycle.
- **Overlay awaiting-confirmation state test** ✔ Phase 3.
- **Integration-style tests with a stub AIClient** ✔ Phase 4:
  propose → confirm → execute (`echo`) → result-history entry; and
  propose → Escape-cancel → nothing executed.
- **Safety** ✔ §1 invariants; the only subprocess execution site is
  `CommandSession.confirm()`'s runner call.

## 5. Out of scope (do not build)

Chat/session persistence between runs, the notch-expanded command UI (no
notch on Linux) and any new GTK window for command mode, analytics,
upstream's ProcessDiscovery/multi-tool schemas (single shell-command tool
only), native OpenAI tool_calls, Wayland, a `fluidvoice command` CLI/socket
action, streaming pill updates during LLM turns, per-decision "destructive
command" classification (v1 confirms everything — strictly safer).

## 6. Notes for the builder

- Follow `fluidvoice/rewrite.py` + `tests/test_extra_formats.py` as the
  structural template; copy their test idioms (`quiet_ui`, `StubRecorder`,
  `CapturingPipeline`, busy-wait deadline loops).
- The busy/pending handoff MUST happen under `self._lock` (§2.5) — a
  hotkey-press racing the turn-1 thread's `finally` would otherwise drop a
  confirm. The plan's `_begin_command` sets `busy = False` under the lock at
  the same moment `_command_pending = True`.
- `set_recording()` on a hotkey listener means "arm/disarm the cancel-key
  grab while active" — reusing it for the pending state is intended; do not
  rename it in this change.
- `ui.notify` monkeypatching in `quiet_ui` patches the shared `fluidvoice.ui`
  module object, so notifications from `command.py`/`daemon.py` are captured
  as long as both do `from . import ui` and call `ui.notify(...)` at call
  time. `command.py` must import history the same way
  (`from . import history as history_mod`).
- Do not touch `requests/`, `adws/`, `docs/superpowers/specs/`, `.venv/`.
  `docs/superpowers/specs/2026-09-02-native-settings-app-design.md` is the
  format reference only.
