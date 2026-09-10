Plan the implementation of Command Mode - the last big unported macOS feature: voice-driven command execution with AI, confirmation-gated, as a multi-turn agent loop.

STATUS: SHIPPED

<!-- shipped in 49ef209 -->

What it is upstream (Sources/Fluid/Services/CommandModeService.swift in altic-dev/Fluid-oss, fetched at /tmp/fluidui/mac/CommandModeService.swift): the user dictates an instruction; an LLM (OpenAI-compatible, same AIClient transport we already have) analyzes it and proposes shell commands (with a purpose string); the proposal is a PendingCommand that the user must CONFIRM before execution (confirmAndExecute / cancelPendingCommand); executed commands run with a working directory, output is fed back into the conversation, and the loop continues for a bounded number of turns (maxTurns); conversation history persists per chat; Escape cancels.

Linux v1 scope (repo context: fluidvoice/daemon.py has modes dictate/rewrite already - rewrite is the pattern to follow: hotkey.rewrite_key -> start_rewrite -> _process(mode="rewrite")):
- new `hotkey.command_key` config + whitelist + settings exposure; a `start_command()` daemon path that records dictation and processes it as mode="command"
- new `fluidvoice/command.py` (like rewrite.py): the agent loop over AIClient - system prompt asking for strict JSON {command, purpose, done}; execute ONLY after confirmation; bounded turns (config `command.max_turns`, default 4); cwd = config `command.working_dir` (default $HOME); shell via subprocess with timeout
- confirmation UX on Linux: the pending command goes into the pill overlay (overlay.py already supports states) as a new "awaiting confirmation" state showing the command text; hotkey-press = confirm and run, Escape = cancel (hotkey.py already has the recording-scoped Escape grab); result + follow-up turns surface via notifications and history entries (mode "command"); no new GTK window in v1
- safety: never execute without the explicit confirm; log every executed command to history; strip the model's markdown fences before JSON parsing; on parse failure show the raw proposal and cancel
- AI must be enabled and configured - otherwise command mode refuses with a clear notification (like rewrite does today)

Where: docs/superpowers/specs/2026-09-02-native-settings-app-design.md shows how this repo plans/specs; fluidvoice/daemon.py (_process, start_rewrite, test_dictation), fluidvoice/rewrite.py, fluidvoice/ai/client.py (polish/chat transports, AIError), fluidvoice/overlay.py (FluidOverlay states, set_state/show), fluidvoice/hotkey.py (cancel-key grab while active), fluidvoice/history.py are the integration points. Suite: `.venv/bin/python -m pytest -q tests --ignore=tests/integration`.

Done means: a phased, file-level plan under `specs/` that a builder can implement without asking questions - each phase leaves the default suite green; unit tests for the command-loop JSON protocol (proposal parsing, fence stripping, turn bound, refusal without AI), the daemon wiring (mode routing, confirm/cancel paths incl. the no-audio and Escape paths), and the overlay awaiting-confirmation state; an integration-style test with a stub AIClient covering propose -> confirm -> execute (echo) -> result-history entry, and propose -> Escape-cancel -> nothing executed.

Out of scope: chat session persistence/store (single run only, history entries suffice), the notch-expanded command UI (no notch on Linux), analytics, upstream's ProcessDiscovery/tool schemas beyond the single shell-command tool, Wayland.

