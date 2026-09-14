# Command mode (voice → terminal agent)

Part of the [documentation index](../README.md). Configuration
reference: [configuration.md](configuration.md).

Set `command_key` under `[hotkey]` to a spare keysym (e.g. `F10`); like the
rewrite key it needs `[ai]` enabled with a base URL and model. Press it and
dictate an instruction ("list the biggest files in my downloads folder");
stop the recording with the main dictation key. The model answers in a
strict-JSON tool-call protocol (the upstream `execute_terminal_command`
schema) and proposes shell commands — shown in the pill overlay in an
awaiting-confirmation state — and you press the command hotkey again to run
each one; `Escape` cancels. **Every** command requires that explicit
confirmation before anything executes; output is fed back to the model and
the loop continues (bounded by `[command] max_turns`).

Commands matching the built-in **destructive list** (ported from upstream:
`rm`, `mv`, `sudo`, `kill`, `chmod`, `dd`, redirections, `xargs rm`, …) or
your own patterns get a stronger gate: an amber ⚠ pill and a **two-press**
confirm — the first press only arms, the second runs.

Follow-up context: within `command.context_window_s` (default 300 s, `0`
disables) the last five executed results in the *same* focused app are
replayed to the model so "now the biggest one" works; say **"new session"**
to clear it. Nothing is persisted — a daemon restart starts cold.

Executed commands land in History, which has a **Commands** page showing
command, purpose, exit code, duration and collapsible output, with Copy and
**Re-run** (re-run only re-posts a proposal — it still needs the hotkey
confirm, never silent):

```toml
[command]
context_window_s = 300.0
# extra strings treated as destructive (case-insensitive substrings):
destructive_patterns = ["git push", "shutdown"]
```
