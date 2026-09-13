# Command Mode v2 — multi-tool schema, destructive strong-confirm, follow-up context, History Commands view

Session: `d13f4d53` · Repo: FluidVoiceLinux, branch `linux` · Baseline HEAD:
`55c2062`, working tree clean. Default suite at planning time: **1166 passed**
in ~46 s.

Gate (run after EVERY phase, must stay green):

```
.venv/bin/python -m pytest -q tests --ignore=tests/integration
```

Sequencing prerequisite satisfied: `requests/history-integrity.md` landed as
commit `634dbca` (suite XDG/data isolation + `--scrub-tests`); the conftest
now pins every `paths.py` resolution into a session tmp root
(`tests/conftest.py:52-100`) with a `_real_data_untouched` tripwire
(`tests/conftest.py:100-115`), so the new history-writing tests in this plan
are safe by default.

Upstream reference: local clone at `~/Documents/github/FluidVoice`
(remote `altic-dev/FluidVoice`, clone HEAD `66ca682`; the tracking baseline
pins `b395a7a` in `docs/UPSTREAM-TRACKING.md`). All Swift citations below
were verified against that checkout during planning. v1 background:
`specs/3c04b728_command-mode.md`, `requests/command-mode.md`,
`requests/command-mode-v2.md`.

---

## 1. What we are building (from requests/command-mode-v2.md)

Deepen command mode, the port's clearest differentiator, with four pieces:

1. **Tool schema parity** — the strict-JSON protocol gains upstream's tool
   shape: a `tool_calls` array (upstream sends a native OpenAI `tools` array,
   `TerminalService.toolDefinition`) with per-tool argument validation, plus a
   **destructive-command classification list** ported verbatim from upstream
   and extensible via config. Destructive commands get a **strong
   confirmation** (two presses, distinct amber pill); non-destructive keep
   today's light confirm.
2. **Conversation store** — follow-up context per focused app: the last N=5
   command results are replayed to the model when the next voice command runs
   in the same app within `command.context_window_s` (default 300 s, 0
   disables). In-memory only. A spoken "new session" phrase clears it.
3. **History window Commands view** — a second page in the GTK History window
   listing command rows (command, purpose, exit code, duration, collapsible
   output) with **Copy** and **Re-run**; re-run re-posts through the same
   confirmation flow as a fresh voice command — never silent.
4. **doctor** — a command-mode line: enabled, tool count, destructive pattern
   count, context window.

Hard invariants carried over from v1 (every phase preserves them):

- A shell command executes ONLY from `CommandSession.confirm()`
  (`fluidvoice/command.py:236`), which the daemon calls ONLY from the
  hotkey-confirm path. Re-run adds a second entry into the *same* pending
  state, never a bypass.
- Every executed command gets one history row (`mode: "command"`); cancelled
  or unconfirmed commands execute nothing and write nothing.
- Model output is never trusted: `<think>` stripped, fences stripped, JSON
  parsed with the tolerant brace-slice retry; parse failure shows the raw
  text and cancels the run.
- Execution stays bounded: subprocess timeout, `max_turns`, confirm timeout.

---

## 2. Upstream semantics (verified, with citations)

### 2.1 Tool schema

Upstream sends a **native OpenAI function-calling `tools` array containing
exactly one tool** (`tools: [TerminalService.toolDefinition]`,
`CommandModeService.swift:868`). The definition
(`TerminalService.swift:20-61`, verbatim shape):

- `type: "function"`, `function.name = "execute_terminal_command"`
- `function.parameters`: object with
  - `command` (string, **required**) — "The shell command to execute…"
  - `workingDirectory` (string, optional) — "Defaults to user's home
    directory."
  - `purpose` (string, **required**) — brief reason; semantically one of
    `checking` / `executing` / `verifying` (drives step typing).
- description carries the agentic workflow: check prerequisites → execute →
  verify.

Wire and parsing: `LLMClient` sends `body["tools"] = config.tools` and
`tool_choice: "auto"` (`LLMClient.swift:329-333`). On the response it decodes
`message.tool_calls[].function.arguments` (a JSON *string*) into dicts,
silently drops entries that fail to decode (`compactMap`,
`LLMClient.swift:847-865`) and synthesizes `"call_<uuid8>"` for a missing
`id` (`:863`) — only for the service to then consume **`toolCalls.first`
alone** (`CommandModeService.swift:953-954`): a call is honored **only when
`tc.name == "execute_terminal_command"`**; other names fall through to text
handling. Arguments are read with `getString("command") ?? ""` (empty
tolerated — it would run `zsh -c ""`), `getOptionalString("workingDirectory")`
(nil for missing *or empty*, `LLMClient.swift:105-108`), and
`getString("purpose")` (nil-tolerant despite the schema's `required`)
(`CommandModeService.swift:953-970`). `workingDirectory` empty/absent → home
dir in `TerminalService.execute` (`TerminalService.swift:81-87`); hard 30 s
timeout (`:70`, sleep-then-terminate `:104-112`), `/bin/zsh -c` (`:79-80`),
PATH prefixed with `/opt/homebrew/bin:/usr/local/bin:` (`:89-94`). No
allowlist/regex/shell-parse validation of `command` exists — the only gate
is the destructive check (§2.2). When past tool calls are replayed to the
API, only `command` + `workingDirectory` are re-encoded — `purpose` is
dropped (`CommandModeService.swift:799-821`); the executed-result feedback
is `EnhancedCommandResult` JSON `{success, command, output, error,
exitCode, executionTimeMs, purpose}`.

`FunctionCallingProvider.swift` is **dead code for command mode** (referenced
nowhere else in `Sources/`) — do not port semantics from it.

Upstream **does** ship a system prompt (`CommandModeService.swift:704-783`,
sent as the first `system` message `:785-787`): "autonomous, thoughtful
macOS terminal agent" framing, PRE-FLIGHT CHECKS (ls / test -e / which /
--version before acting, `:709-714`), the `purpose` convention
checking/executing/verifying (`:716-721`), POST-ACTION VERIFICATION
(`:723-728`), SAFETY RULES ("For destructive ops (rm, mv, overwrite): ALWAYS
check target exists first", `:741-744`), worked examples (`:747-763`), macOS
`osascript` app control (`:766-780`), and "zsh shell" + summary-with-✓/✗
(`:781-782`). What it does **not** contain is a JSON-in-text protocol — the
tool-call shape is carried natively.

### 2.2 Destructive-command classification

`CommandModeService.swift:562-598` — `isDestructiveCommand(_:)`, lowercases
the command, then:

```swift
let destructivePrefixes = [            // :566-577  (cmd.hasPrefix)
    "rm ", "rm\t", "rmdir ", "rm -",          // delete
    "mv ", "mv\t",                            // move/rename
    "sudo ",                                  // elevated privileges
    "kill ", "pkill ", "killall ",            // terminate processes
    "chmod ", "chown ", "chgrp ",             // change permissions/ownership
    "dd ",                                    // disk operations
    "mkfs", "format",                         // filesystem formatting
    "> ",                                     // overwrite file
    "truncate ",                              // truncate file
    "shred ",                                 // secure delete
]
let destructivePatterns = [            // :585-591  (cmd.contains)
    "| rm ", "| sudo ", "| dd ",
    "; rm ", "; sudo ",
    "&& rm ", "&& sudo ",
    "xargs rm", "xargs -I",
]
// :594-597  additionally: cmd.contains("rm -")  → true
```

19 prefixes + 9 contains-patterns + the anywhere-`"rm -"` rule (28 built-in
rules). Matching is **literal, case-insensitive, prefix-or-substring** — not
regex.

Upstream UX split (`CommandModeService.swift:439-456`): when
`commandModeConfirmBeforeExecute` is ON, **destructive** commands become a
`PendingCommand` (confirmation needed); **non-destructive commands
auto-execute**. There is no second, stronger step for destructive beyond the
ordinary confirm.

### 2.3 Confirm flow and agent loop

- Proposal → `pendingCommand` set, `isProcessing = false`, step cleared
  (`:440-456`). `confirmAndExecute()` (`:357-364`) executes the pending
  command; `cancelPendingCommand()` (`:366-375`) drops it and appends a
  "Command cancelled." failure message.
- Loop `processNextTurn()` (`:376`…): `maxTurns = 20` (`:24`); on exhaustion
  the assistant message is exactly "Reached maximum steps limit. Please
  review the progress and continue if needed." (`:380-381` — v1 already
  ports this string). After execution the JSON result is appended as a
  `role:"tool"` message and the loop continues (`:620-633`). A plain text
  reply ends the run when it contains "complete/done/success/finished"
  (`:458-462`).
- Purpose strings drive step typing: `determineStepType` maps `checking` →
  `.checking`, else `.executing` (`:418-421` region).

### 2.4 Chat/conversation store

`ChatHistoryStore.swift`: `ChatMessage` (role user/assistant/tool, content,
toolCall{id, command, workingDirectory, purpose}, stepType, timestamp,
`:13-53`), `ChatSession` (id, title from first user message ≤50 chars,
createdAt/updatedAt, messages, `:56-90`). Store is `UserDefaults`-persisted,
cap `maxChats = 30` trimmed by `updatedAt` (`:95`, `:261-266`), always has a
current session (`:100-110`); **the whole conversation is replayed** to the
model every call (`CommandModeService.swift:790+`). `createNewChat()`
(`CommandModeService.swift:134-154`) saves the current chat, starts an empty
one, clears pending + turn count; `clearHistory()` (`:115-123`) clears in
place. **No timeout and no per-app scoping exist upstream.**

### 2.5 What v2 ports vs. diverges

| Upstream semantic | v2 decision | Citation |
|---|---|---|
| `tools` array with one function, per-arg validation | **Port** the schema shape into the strict-JSON protocol: reply carries `tool_calls: [{id, name, arguments}]`; per-tool arg validation mirrors upstream exactly | TerminalService.swift:20-61, CommandModeService.swift:953-961 |
| Only `execute_terminal_command` honored | Port: unknown tool name → `CommandError` (raw text shown). Upstream silently falls through to text; we fail loudly | CommandModeService.swift:953 |
| `command` arg tolerated empty (`?? ""`) | Divergence (tightening): empty/non-string command → parse error | CommandModeService.swift:955 |
| `workingDirectory` per call, "" → home | Port: per-call `workingDirectory`, "" → `working_dir(cfg)`; non-directory → fallback + log (v1's `working_dir` behavior) | TerminalService.swift:88-93 |
| `purpose` required in schema, nil-tolerant in code | Port both sides: prompt marks it required, parser tolerates absence (v1 parity) | TerminalService.swift:52-56, CommandModeService.swift:958 |
| Destructive list (28 rules, literal ci prefix/contains) | **Port verbatim** as built-ins + user-extensible `command.destructive_patterns` | CommandModeService.swift:562-598 |
| Destructive → confirm, non-destructive → auto-execute | Divergence (kept from v1): **everything** is confirmed; destructive additionally needs the strong two-press confirm | CommandModeService.swift:439-456 |
| Whole conversation replayed; persisted 30-chat store | Divergence (per request): in-memory, per-app, last-5, 300 s window, spoken clear; nothing persisted beyond history rows | ChatHistoryStore.swift:93-110, 261-266 |
| Multi tool_calls parsed (`LLMClient`), only `first` used by the service | Divergence (per request): a reply may propose a **set**; all calls are presented sequentially, each individually confirmed before the next executes; undecodable calls are *rejected loudly* (raw text shown), not silently dropped | LLMClient.swift:847-865, CommandModeService.swift:953-954 |
| System prompt with agent framing, pre-flight/verify rules, macOS `osascript` examples; protocol carried by native tools | Divergence (kept from v1): our system prompt additionally defines the strict-JSON `tool_calls` reply protocol; pre-flight/verify/purpose rules mirror upstream's (`:709-728`), `osascript`/zsh lines replaced by Linux/bash equivalents | CommandModeService.swift:704-787 vs `command.py` SYSTEM_PROMPT |
| maxTurns 20, 30 s subprocess timeout | Kept at v1 values (`max_turns` 4, `timeout_seconds` 60) — already diverged, unchanged | CommandModeService.swift:24, TerminalService.swift:86 |

---

## 3. Design

### 3.1 Tool registry and reply protocol v2 (`fluidvoice/command.py`)

New module-level registry mirroring upstream's definition (one tool today,
the shape admits more later):

```python
TOOL_REGISTRY: dict[str, dict] = {
    "execute_terminal_command": {
        "description": "...upstream text adapted: 'the user's Linux computer',
            bash -c, cwd, timeout...",
        "parameters": {
            "command": {"type": "string", "required": True},
            "workingDirectory": {"type": "string", "required": False},
            "purpose": {"type": "string", "required": False},
        },
    },
}
```

`SYSTEM_PROMPT` (`command.py:31`) is rewritten to the new protocol (keep the
operational rules — check-before-act, verify-after, non-interactive, quoting;
they mirror the upstream tool description's agentic workflow):

```
Respond with EXACTLY ONE JSON object and nothing else - no fences, no prose:

  {"tool_calls": [
      {"id": "call_1", "name": "execute_terminal_command",
       "arguments": {"command": "<one shell command>",
                     "workingDirectory": "", "purpose": "<short reason>"}}
   ], "done": false}

You may include several tool_calls in one reply when the task is genuinely a
small fixed set of steps; propose ONE command per call. When the task is
complete (or nothing needs to run):

  {"tool_calls": [], "done": true, "summary": "<what happened, 1-3 sentences>"}
```

`parse_reply` (`command.py:116`) → new `ParsedReply`:

- `kind: "proposal"` carries `calls: list[ToolCall]`; `kind: "done"` carries
  `summary` (unchanged).
- `ToolCall` dataclass: `id: str`, `name: str`, `command: str`,
  `working_directory: str | None`, `purpose: str | None`, and
  `destructive: bool` (computed at parse via §3.2).
- Validation per call (mirrors §2.1): `name` must be in `TOOL_REGISTRY`
  (else `CommandError` with the raw text); `command` must be a non-empty
  string (missing/empty/non-string → `CommandError`); `workingDirectory`
  optional string (non-string → dropped + tolerated? **No**: non-string →
  `CommandError` — strict); `purpose` optional string; `id` optional
  (generated `call_<n>` when absent). `arguments` must be an object.
- The v1 single-command shape `{"command": ...}` is **removed**; tests are
  updated in the same phase. Fence-strip + `<think>`-strip + brace-slice
  retry all stay as-is.

`CommandSession` changes:

- `PendingCommand` gains `working_directory: str | None = None` and
  `destructive: bool = False`.
- `start()` stores the parsed set on the session
  (`self._queued: list[ToolCall]`) and presents the first call.
- `confirm()` executes the pending call with
  `cwd = Path(proposal.working_directory) if set else working_dir(cfg)`
  (nonexistent dir → `working_dir(cfg)` + log, reusing the v1 fallback), then
  **presents the next queued call immediately without an LLM round-trip**
  (multi-tool set = one voice run). When the queue empties, one user message
  `"Command results (JSON): " + json.dumps([result, ...])` (the set's
  outcomes, each clipped by `_clip_output`) is appended and `_advance()`
  runs — the verify loop from v1 is preserved. A set of one behaves exactly
  like v1. Each result object carries `purpose` (upstream's
  `EnhancedCommandResult` includes it) alongside command/exit_code/success/
  output/error/duration_ms.
- `cancel()` drops the whole queue (Escape mid-set cancels everything
  remaining — nothing queued survives unconfirmed).
- `max_turns` still counts LLM calls (`_advance`), not commands.
- `_write_history` (`command.py:300`) adds `"destructive": bool` and
  `"cwd": str | None` to the row — additive; every consumer already uses
  `.get` (verified: `history.search/tail/read_all` pass rows through;
  `count_test_entries` matches on `mode`/`command` only).

### 3.2 Destructive classification (built-in port + user patterns)

In `command.py`, port upstream verbatim (comments citing
`CommandModeService.swift:566-597`):

```python
DESTRUCTIVE_PREFIXES = ["rm ", "rm\t", "rmdir ", "rm -", "mv ", "mv\t",
                        "sudo ", "kill ", "pkill ", "killall ",
                        "chmod ", "chown ", "chgrp ", "dd ", "mkfs",
                        "format", "> ", "truncate ", "shred "]
DESTRUCTIVE_PATTERNS = ["| rm ", "| sudo ", "| dd ", "; rm ", "; sudo ",
                        "&& rm ", "&& sudo ", "xargs rm", "xargs -I"]

def is_destructive_command(command: str,
                           extra_patterns: list[str] | None = None) -> bool:
    cmd = command.lower()
    if any(cmd.startswith(p) for p in DESTRUCTIVE_PREFIXES):
        return True
    if any(p in cmd for p in DESTRUCTIVE_PATTERNS):
        return True
    if "rm -" in cmd:                      # upstream anywhere-rule
        return True
    for pat in (extra_patterns or []):
        if pat and pat.lower() in cmd:     # user list: ci substring, same
            return True                    # convention as terminal_apps
    return False
```

The session computes `extra_patterns` from
`cfg["command"].get("destructive_patterns", [])` at parse time; re-run
recomputes it at present time (config edits apply to re-runs).

### 3.3 Strong confirmation (daemon state machine)

`fluidvoice/daemon.py`, extending the v1 pending lifecycle
(`_present_proposal` :1618, `_confirm_pending_command` :1646,
`cancel_pending_command` :1714, `_on_confirm_timeout` :1727,
`_teardown_pending_ux` :1733):

- New daemon attr `self._command_destructive_armed = False` (init next to
  `_command_pending`, daemon.py:316-322).
- `_present_proposal`: when `proposal.destructive`, the panel entry is
  `{"kind": "proposal", "text": command, "sub": purpose, "destructive":
  True}`, the awaiting hint is `"⚠ destructive — press command key AGAIN to
  run · Esc cancels"`, and the notification body is prefixed
  `"⚠ DESTRUCTIVE\n"`. `armed` is reset to `False` here.
- `_confirm_pending_command` (the ONLY entry to `session.confirm()`):
  if `pending.destructive and not self._command_destructive_armed`:
  set `armed = True`, refresh the pill awaiting hint to
  `"press command key AGAIN to CONFIRM"`, re-notify, **restart the confirm
  timer**, and return — nothing executes. The next press takes the normal
  path. Non-destructive: single press, as today.
- `armed` resets to `False` on: execution, `cancel_pending_command`,
  `_on_confirm_timeout`, `_teardown_pending_ux`, and `_end_command_session`.
- Unit-provable: with a destructive pending, one `_on_command_hotkey()` must
  leave the (injected) runner untouched; two presses execute; Escape between
  the presses executes nothing.

Overlay (`fluidvoice/overlay.py`): `CommandPanelRenderer.render` (:666-757)
renders the proposal row's `$` marker, text and `sub` in amber
`DONE_LOW = (255, 186, 66)` (:75) prefixed with `⚠` when
`e.get("destructive")` — the "distinct pill color" for destructive
proposals; the awaiting hint string (daemon-authored) carries the two-press
instruction. No new panel states; the entries contract just gains the
optional flag.

### 3.4 Conversation context store (in-memory, per app, windowed)

New class in `command.py` (keeps the "Where" list from the request):

```python
class CommandContextStore:
    """Last-N executed command results per focused app, replayed as context
    to the next voice command within a time window. In-memory only."""
    def __init__(self, max_entries: int = 5, clock=time.monotonic): ...
    def record(self, app: str, outcome: CommandOutcome) -> None: ...
    def snapshot(self, app: str, window_s: float, now=None) -> list[dict] | None: ...
    def clear(self, app: str | None = None) -> None: ...
```

- `record` appends `{command, purpose, exit_code, success, output (≤500
  chars), ts (clock())}` under `app`, caps the deque at `max_entries` (5).
- `snapshot` returns `None` when `window_s <= 0`, the app has no entries, or
  `now - last_ts > window_s` (rolling expiry measured from the most recent
  record); expired entries are dropped (prune). Output is the clipped form.
- `CommandSession.__init__` gains `context_store=None` and `app=None`;
  `start()` injects one synthetic user message before the instruction when a
  snapshot exists:
  `"Context - recent commands you ran in this app (JSON, newest last): [...]"`
  and the system prompt gains one line explaining it. `confirm()` calls
  `self.context_store.record(self.app, outcome)` after each execution
  (session-owned so tests can inject; the daemon passes its store).
- Daemon: `self._command_context = command_mod.CommandContextStore()`
  (module default next to `_command_session_factory`); `_begin_command`
  signature becomes `_begin_command(self, instruction, app=None)` — call
  site `daemon.py:1540` passes the captured `self._app_hint` (set at
  recording start, `daemon.py:1237`); the session factory call passes
  `context_store=self._command_context, app=app`.
- **"new session" phrase**: in `_begin_command`, before creating a session:
  `NEW_SESSION_PHRASES = {"new session", "new command session"}` (module
  constant in `command.py`); if `instruction.strip().lower()` matches, clear
  the store for that app (`clear(app)`), notify "Command context cleared",
  and return without an LLM call. Config "clears" it naturally: the store
  reads `context_window_s` on every snapshot, so setting `0` (or any edit)
  applies to the next run with no migration.
- Nothing is persisted: daemon restart starts every app cold (request
  mandate); history rows remain the only durable record.

### 3.5 History window Commands view + Re-run

`fluidvoice/gtkui/main_window.py` (HistoryWindow :231, single ListBox :303,
`_load_history` :337 via `client.history` :136 in `gtkui/client.py`):

- Wrap the existing list area in an `Adw.ViewStack` with two pages —
  **Transcripts** (everything that exists today, unchanged) and
  **Commands** — plus an `Adw.ViewSwitcher` under the search entry (the
  search box drives both pages; the Commands page filters
  `e.get("mode") == "command"` client-side in `_load_history`, since
  `history_mod.search` has no mode filter and the request forbids history
  schema/query changes beyond additive row fields).
- `CommandRow(Gtk.ListBoxRow)`: monospace `$ command` line, purpose as dim
  sub-line, meta chips (exit code `✓ 0` / `✗ n`, duration ms, timestamp), a
  **Output** toggle button opening a `Gtk.Revealer` with a selectable
  monospace `Gtk.Label` of `entry.get("output")`, and action buttons
  **Copy** (clipboard, copy of `_on_copy_row` :382 — copies the `command`
  field) and **Re-run**.
- Re-run path (never silent): `CommandRow` → `self.c.command_rerun(command)`
  → `gtkui/client.py` new method
  `command_rerun(self, command, purpose=None)` → socket action
  `"command-rerun"` → `Daemon.handle_request` (`daemon.py:1073`) new branch:

  ```python
  if action == "command-rerun":
      return self._rerun_command(str(req.get("command", "")),
                                 str(req.get("purpose") or "") or None)
  ```

  `_rerun_command(command, purpose)` (placed with the other command-mode
  methods): guards `busy/recording/_command_pending` → `{"ok": False,
  "error": "daemon busy"}`; readiness check (`command_mode_ready`);
  classification recomputed from current config; builds a `CommandSession`
  via `_command_session_factory` and sets a preset pending proposal (new
  `CommandSession.preset(command, purpose)` classmethod-ish helper: sets
  `pending` + `instruction = f"re-run: {command}"` with **no LLM call**),
  then the standard `self._command_pending = True` +
  `_present_proposal(...)` handoff — the user confirms exactly like a voice
  proposal, strong-confirm included when destructive. History row is written
  by the normal confirm path (a fresh row; no dedupe).
- The Commands view reuses the existing refresh/load plumbing; `count_lbl`
  per page.

### 3.6 doctor

`fluidvoice/doctor.py`: new `_command_mode_lines(cfg) -> list[str]` (pure
cfg, pattern of `_formatting_lines` :86), printed after the
"chat/terminal formatting" block in `run()` (:~404):

```
command mode:
  ai: ready (model <name>) | not configured (needs [ai] enabled + base_url/model)
  tools: 1 (execute_terminal_command)
  destructive patterns: 28 built-in + 2 user (command.destructive_patterns)
  context window: 300 s (last 5 results per app) | disabled (context_window_s = 0)
```

### 3.7 config

`fluidvoice/config.py` — the request names the keys
`command_mode.destructive_patterns` / `command_mode.context_window_s`; the
repo's established section for command mode is `[command]`
(`config.py:150-155`, TEMPLATE, keys list :386, constraints :467-470), so
they land there as **`command.destructive_patterns`** and
**`command.context_window_s`** (one section per feature; recorded here so
nobody hunts for a `[command_mode]` section):

- `DEFAULTS["command"]["destructive_patterns"] = []` and
  `["context_window_s"] = 300.0` (:150-155).
- TEMPLATE `[command]` block gains commented lines (pattern of
  `mic_priority` :216 area): what counts as destructive (ci substring),
  examples (`"git push", "shutdown"`), and the context window
  (`0` disables; `300` default; what it does).
- Keys list (`:386`) + `_CONSTRAINTS`: `("command", "context_window_s"):
  ("float", (0, 86400))`; `destructive_patterns` via a new
  `_coerce_destructive_patterns` modeled on `_coerce_terminal_apps`
  (:681): list[str], strip, drop empties, dedupe case-insensitively
  (keep first), ≤32 entries, each ≤128 chars, else reject whole list.
- `apply_settings` routes the new key through the coerce (list-typed keys
  precedent: `general.terminal_apps`).

---

## 4. Phased plan

Every phase ends with the gate green. Files are touched in the listed order
within a phase; tests are written alongside the code they cover.

### Phase 1 — Tool registry + multi-tool protocol (command core)

Files: `fluidvoice/command.py`, `tests/test_command.py`.

1. Add `TOOL_REGISTRY`, `ToolCall` dataclass, rewrite `SYSTEM_PROMPT` to the
   `tool_calls` protocol (§3.1).
2. Rewrite `parse_reply` → set-of-calls with per-tool validation (§3.1);
   keep fence/think/brace-slice handling; `ParsedReply.calls`.
3. `CommandSession`: queued-set semantics, per-call `workingDirectory`,
   batched result feedback, cancel drops the queue (§3.1). Remove the v1
   single-command reply shape.
4. Tests (`tests/test_command.py` — note: the request says
   `tests/test_command_mode.py`; the actual file is `test_command.py`,
   extend it):
   - parse: 2-call reply parses to `calls[0..1]` with validated args;
     `workingDirectory` honored; missing `command` → `CommandError`;
     unknown tool name → `CommandError` naming the tool; non-object
     `arguments` → `CommandError`; `id` generated when absent; done shape
     unchanged; fenced/prose-wrapped multi-call replies tolerated
     (existing helpers).
   - session: `start` presents call 1 of 2; `confirm` executes call 1 and
     presents call 2 **with no second LLM call yet** (StubAIClient counts
     calls); second `confirm` executes call 2, then exactly ONE advance
     happens with a results message containing both commands' JSON;
     per-call cwd override reaches the runner (tmp_path); nonexistent
     `workingDirectory` falls back to `working_dir(cfg)`;
     Escape-style `cancel()` between set members executes nothing further;
     single-call reply == v1 behavior (messages shape test updated);
     failure feedback still carries exit codes; max_turns still bounds LLM
     turns (2 commands in one set + 1 follow-up within `max_turns=2`).
   - Update every existing v1 test that feeds `{"command": ...}` replies to
     the new shape (`TestParseReply`, `TestSessionLoop`, the daemon
     `_pending` fixtures' canned replies).

### Phase 2 — Destructive classification + strong confirm

Files: `fluidvoice/command.py`, `fluidvoice/config.py` (this key only),
`fluidvoice/daemon.py`, `fluidvoice/overlay.py`, `tests/test_command.py`,
`tests/test_config_settings.py`.

1. `DESTRUCTIVE_PREFIXES` / `DESTRUCTIVE_PATTERNS` / `is_destructive_command`
   (§3.2) with the upstream citation comments; `ToolCall.destructive` +
   `PendingCommand.destructive` computed at parse.
2. Config: `command.destructive_patterns` (DEFAULTS, TEMPLATE comments,
   keys list, `_coerce_destructive_patterns`, constraint wiring) (§3.7).
3. History row gains `destructive` (+ `cwd`) in `_write_history`.
4. Daemon: `_command_destructive_armed` state machine in
   `_present_proposal` / `_confirm_pending_command` / cancel / timeout /
   teardown (§3.3); distinct awaiting hint + notification prefix.
5. Overlay: amber + `⚠` for `destructive` proposal entries (§3.3).
6. Tests:
   - classification table (parametrized): every one of the 19 prefixes and
     9 patterns + `"rm -"` anywhere (e.g. `"echo hi && rm -rf /"`,
     `"find . | xargs rm"`, `"MKFS..."` case-insensitivity); negatives
     (`"echo rm -rf"`, `"ls -la"`, `"grep sudo file"`, `"vim"`); user
     pattern matched as ci substring; empty user list no-op.
   - daemon: destructive proposal — ONE `_on_command_hotkey()` does not run
     the injected runner and re-presents the armed hint; a SECOND press
     executes; Escape after the first press cancels with nothing executed;
     confirm timeout during the armed stage cancels; non-destructive
     proposal still executes on the first press; history row carries
     `destructive: true`.
   - config: coerce accepts/dedupes/rejects per §3.7 bounds.
   - overlay: renderer unit (pure) — destructive entry produces the ⚠/amber
     draw path (follow `test_overlay.py`'s renderer-level tests).

### Phase 3 — Conversation context store

Files: `fluidvoice/command.py`, `fluidvoice/config.py`, `fluidvoice/daemon.py`,
`tests/test_command.py`.

1. `CommandContextStore` (§3.4) with injectable clock.
2. `CommandSession(context_store=..., app=...)`: context message injection in
   `start()`, `record()` in `confirm()`; system-prompt context line.
3. Config: `command.context_window_s` (DEFAULTS 300.0, TEMPLATE comment,
   keys list, float constraint 0-86400).
4. Daemon: store instance, `app` plumbing (`_begin_command(instruction,
   app)`; call site :1540 passes `self._app_hint`), `NEW_SESSION_PHRASES`
   handling + notify.
5. Tests:
   - store: record/snapshot round-trip; N=5 cap (6th drops oldest); expiry
     (`now - last > window` → None and pruned); `window_s=0` → None; clear
     (app-scoped and all); output clipped to 500.
   - session: follow-up `start()` in the same app sees a context message
     containing the prior command + output; different app does not;
     after expiry/clear it does not; `confirm()` recorded the outcome.
   - daemon: `NEW_SESSION_PHRASES` instruction clears the app's store,
     notifies, and makes no LLM call (StubAIClient assertions); normal
     instruction flows through with context present (stub sees it in
     messages).

### Phase 4 — History Commands view + Re-run

Files: `fluidvoice/gtkui/main_window.py`, `fluidvoice/gtkui/client.py`,
`fluidvoice/daemon.py`, `tests/test_gtkui.py`, `tests/test_command.py`.

1. ViewStack + ViewSwitcher; Commands page; `CommandRow` with collapsible
   output, Copy, Re-run (§3.5). Keep Transcripts byte-identical in behavior.
2. `Client.command_rerun` (§3.5); daemon `"command-rerun"` action +
   `_rerun_command` + `CommandSession.preset` (§3.5).
3. Tests:
   - gtkui (offscreen, StubClient extended with `command_rerun` recording):
     commands page lists command rows with `$` text, purpose, exit/duration;
     non-command rows excluded; search filters them; Output toggle reveals
     the output text; Copy puts the command on the clipboard (existing
     clipboard test pattern); Re-run calls `command_rerun` with the row's
     command and toasts; daemon-down Re-run toasts the error.
   - daemon: `"command-rerun"` presents a pending proposal through the
     standard pill/notify path (CapturingPanel fixture) and does NOT execute
     until `_on_command_hotkey()`; destructive re-run needs the two-press
     flow; busy/recording/pending → `{"ok": False, ...}`; unready AI →
     error; executed re-run writes a fresh history row.

### Phase 5 — doctor + docs

Files: `fluidvoice/doctor.py`, `tests/test_infra.py`, `docs/UPSTREAM-TRACKING.md`,
`docs/STATUS.md`, `docs/ROADMAP.md`, `README.md` (command-mode section).

1. `_command_mode_lines(cfg)` + `run()` insertion (§3.6).
2. Tests (`tests/test_infra.py`, pattern of the `_formatting_lines` tests):
   ready/not-configured ai line; tool count from `TOOL_REGISTRY`;
   built-in+user pattern counts (28 + n); context window line for 300 and 0.
3. Docs:
   - `docs/UPSTREAM-TRACKING.md` capability matrix: command-mode row →
     "✅ v2 — upstream tool schema (tools array, per-arg validation) and the
     destructive list ported verbatim (`TerminalService.swift:20-61`,
     `CommandModeService.swift:562-598`); confirm-every-run + two-press
     destructive confirm deliberate (see STATUS divergences); chat store
     replaced by an in-memory per-app windowed context (no persistence)".
   - `docs/STATUS.md`: "Intentional divergences" table gains rows: (a)
     confirm-every-run vs upstream auto-execute non-destructive
     (`CommandModeService.swift:439-456`); (b) two-press strong confirm is a
     port addition (no upstream equivalent); (c) in-memory app-scoped
     windowed context vs persisted 30-chat global store
     (`ChatHistoryStore.swift:93-110`, `:261-266`); (d) `tool_calls` in
     strict JSON vs native tool_calls (`CommandModeService.swift:868`);
     (e) multi-call sets all confirmed sequentially vs upstream consuming
     `toolCalls.first` only (`:953`); (f) empty-command rejection vs
     upstream `?? ""` (`:955`). "Later" section: replace the v1-shipped
     bullet with the v2 one (multi-tool schema + destructive list +
     context window + Commands view DONE; native tool_calls / persistent
     chat sessions remain upstream-only).
   - `docs/ROADMAP.md` "Later": tick the command-mode item (:71-72) with a
     DONE summary line.
   - `README.md`: command-mode section — destructive strong-confirm, the
     context window, `destructive_patterns` example, History Commands view
     re-run (one short paragraph + config snippet).
4. Final gate + manual smoke (X11 machine): dictate a destructive-sounding
   command → amber pill + two-press; "new session" → cleared notification;
   History → Commands → Copy/Re-run → pill confirm.

---

## 5. Done means (acceptance)

- Gate green after every phase; final run includes the new tests.
- A multi-tool transcript dispatches to the named tool with validated args
  (Phase 1 tests) and an unknown/invalid call fails loudly with the raw
  text shown.
- A command matching any destructive pattern cannot execute without the
  strong-confirm step — unit-proven state machine (Phase 2 daemon tests:
  one press ≠ execution; two presses = execution; escape between = nothing).
- A follow-up command sees prior output within the window and not after
  expiry, clear, or a different app (Phase 3 tests).
- History Commands view lists, copies, and re-runs through confirmation
  (Phase 4 tests; re-run never executes without the hotkey press).
- doctor prints the command-mode line with tool count, pattern counts, and
  the context window (Phase 5 tests).
- Upstream parity table updated with file:line citations; deliberate
  divergences recorded in `docs/STATUS.md` (Phase 5).

## 6. Out of scope (restated)

Autonomous chaining (one voice run = one command set, always confirmed);
persistent chat sessions across daemon restarts; agent/tool plugins beyond
the schema (the registry carries exactly upstream's one tool); AT-SPI;
anything that executes without an explicit confirm event; settings-UI
editors for the new keys (config file only, per the request's "new keys +
template comments"); native `tool_calls` wire format; history schema changes
beyond additive, backward-tolerant fields.

## 7. Risks / notes for the builder

- The v1→v2 reply-shape change means every canned reply in
  `tests/test_command.py` (including the daemon `_pending` fixture at the
  bottom) must be rewritten in Phase 1 — do them all in one pass, don't
  leave the suite red across phases.
- `command_mode_ready` runs both at `start_command` (daemon.py:1202) and in
  `start()` — the re-run action must go through the same check.
- Keep `CommandSession.confirm()` the single execution site; the re-run
  action only ever *creates a pending proposal* — resist any shortcut that
  executes directly.
- The confirm timer restart on the destructive arm must reuse the existing
  `_command_timer` teardown pattern (`daemon.py:1727-1733`) or you will
  stack timers.
- Overlay changes are renderer-level only; do not touch the X11 plumbing.
  Headless boxes skip GTK tests automatically (`test_gtkui.py:14-21`), but
  the suite must still pass where they run.
- Config keys live in `[command]`, not a new `[command_mode]` section —
  see §3.7 for the recorded interpretation of the request's key names.
- `git status` was clean at `55c2062`; commit per phase with the house
  style (`feat: command mode v2 - <piece>`).
