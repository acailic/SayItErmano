# Diagnostics redaction audit (excellence bet 5)

Last audited: 2026-11-23 · scope: every path that turns app state into
human-readable diagnostics — the `doctor` CLI, daemon log lines (stderr →
journal), desktop-notification/error toasts, crash/exception text, and
crash-adjacent artifacts (subprocess logs). No support bundle exists
today (none to audit; noted as a row).

Threat model: **dictated content** (transcripts, rewrite results,
command-mode instructions) and text derived from it reaching any
persistent or shareable surface a user would not expect to contain
their speech. Secrets (API keys) were already covered by the Q11
contracts in `tests/test_privacy_contracts.py`; this audit adds the
transcript class.

Mechanism (all fixed rows): `fluidvoice/redact.py` prints a
content-free descriptor — `<label> (<N> chars, sha256:<8 hex>)` — that
correlates lines about the same take without revealing what was said.
The content itself appears only when `SAYITERMANO_LOG_TRANSCRIPTS=1` is
explicitly exported, and that opt-in announces itself with a loud
one-time warning per process. Regression contracts:
`tests/test_privacy_contracts.py::TestTranscriptsStayOutOfDiagnostics`.

## Audit table

| # | Output path | What it printed at audit time | Leak risk | Fix |
|---|---|---|---|---|
| 1 | Daemon log, dictation result — `fluidvoice/pipeline.py` (`typed (...)` line) | `typed (strategy, N chars, …s): <first 120 chars of the final text>` — **every take's content prefix in the journal** | HIGH — the primary leak | FIXED: descriptor via `redact.describe` (pipeline.py:598-600); content only behind the opt-in |
| 2 | Daemon log, rewrite result — `pipeline.py` `_rewrite` | `rewrote (strategy, N chars): <first 120 chars>` | HIGH | FIXED: descriptor (pipeline.py:337-338) |
| 3 | Daemon log, command instruction — `pipeline.py` `_command` | `command instruction (N chars): <first 120 chars of the dictated instruction>` | HIGH | FIXED: descriptor (pipeline.py:352-354) |
| 4 | Daemon log, dead-capture suppression — `pipeline.py` `run()` | `suppressed hallucinated text '<40 chars>'` | MEDIUM — hallucinated text over silence is model bias output, not user speech, but it is transcript-class text in the journal | FIXED: descriptor (pipeline.py:544-548) |
| 5 | Daemon log, repetition suppression — `pipeline.py` `run()` | `suppressed repetition output (N chars): '<60 chars>'` | MEDIUM — repetition loops over real (bad-mic) speech can contain speech fragments | FIXED: descriptor (pipeline.py:560-562) |
| 6 | AIError text → daemon log (`AI polish failed (…)`, pipeline.py `_polish`) and the `Rewrite failed` toast (pipeline.py `_rewrite` → `notify`) | `HTTP <code>: <first 300 chars of the provider error body>` — a 400-class validation error can echo request content (i.e. the transcript) back | MEDIUM — indirect, provider-dependent | FIXED: body becomes a descriptor, HTTP code kept (ai/client.py:191-206); content behind the same opt-in |
| 7 | `doctor` CLI (`fluidvoice/doctor.py`) | config values, paths, counts, model names; history lines print entry count/size/oldest date (doctor.py:481-501); no transcript text anywhere | N/A (verified) — prints like `spoken-send: phrase '<config value>'` (doctor.py:115) are user *config*, not dictated content | N/A; guarded by the Q11 secrets test plus this audit |
| 8 | Daemon log, take bookkeeping — `daemon.py` | lengths only: `copied last transcript (N chars)` (daemon.py:375), `pasted last transcription (N chars)` (daemon.py:1432), `rewrite mode (context: N chars)` (daemon.py:1209) | N/A (verified) | N/A |
| 9 | Insertion-context log — `pipeline.py` `_read_insertion_context` | provider name, accessible role, staleness flag with an explicit "lengths/flags only, never text" invariant (pipeline.py:453-456) | N/A (verified) | N/A |
| 10 | Recording/capture log — `capture.py` | app hint, device names, watchdog state; no transcript (the transcript does not exist yet at capture time) | N/A (verified) | N/A |
| 11 | Error toasts — `ui.notify` callers | fixed strings or exception text without content: `Transcription failed: {e}`, `Could not type text: {e}` (pipeline.py); the one content-bearing case was the Rewrite toast via row 6 | N/A after row 6 | covered by row 6 |
| 12 | Crash/exception text (daemon) | tracebacks of startup/shutdown paths (e.g. `cli.py:167-181` daemon start); exceptions carry paths/reasons, not transcripts. A corrupted config crashes at startup with a `TOMLDecodeError` *location* only — see `docs/guides/recovery.md` | N/A (verified) | N/A |
| 13 | whisper.cpp stderr — `backends/whisper_cpp.py` | subprocess runs with `-nt -np` (whisper_cpp.py:71): no transcript on stdout/stderr; on failure `stderr[:500]` (whisper_cpp.py:76) is whisper.cpp's own error text | N/A (verified — flags suppress decoding output) | N/A |
| 14 | Managed llama-server log — `local_llm.py` | `~/.cache/sayit-ermano/llama-server.log` (local_llm.py:176-183) holds llama.cpp startup/diagnostic lines; prompts are not logged at default verbosity. `_log_tail` (local_llm.py:237-244, 3 lines) is folded into `LocalPolishError` only when the child fails **during startup**, before any dictation reached it | LOW — residual: a future llama.cpp default could change; log is user-local (0600-ish cache dir) and rotated at 1 MiB | N/A (documented); revisit if llama.cpp changes |
| 15 | Control-socket `status` — `control_routes.py` | counts and states (`today` word/second totals, history.py:733-743); the menu's "copy last transcript" exposes only `enabled: bool(text)` (daemon.py:289-330) | N/A (verified) | N/A |
| 16 | History view/export (`sayit-ermano history`, GTK History page, `history --export`) | full transcript text | N/A **by design** — the user's own record, user-invoked, on-screen; retention and export are covered by the History settings and the Q11 retention contracts | N/A |
| 17 | MCP server (`sayit-ermano mcp`) | serves history/transcripts to the connected local agent | N/A **by design** — explicit user opt-in (docs/guides/scripting-and-mcp.md), local stdio | N/A |
| 18 | Overlay pill / streaming preview | live transcript on screen while dictating | N/A **by design** — ephemeral on-screen UI, never logged (preview partials are guarded from the final-decode log paths) | N/A |
| 19 | Support bundle | does not exist | N/A | if one is ever added, it must reuse `redact.describe` from day one |

## What a user should know

- Default: the daemon journal, `doctor`, notifications and error text
  contain **no dictated content** — only lengths and 8-hex sha256
  prefixes (enough to say "these two lines are about the same take",
  nothing more).
- Debugging a mis-transcription: run the daemon with
  `SAYITERMANO_LOG_TRANSCRIPTS=1` (it warns loudly, once per process),
  reproduce, then unset it. E.g. for the systemd user unit:
  `systemctl --user edit sayit-ermano` →
  `[Service] Environment=SAYITERMANO_LOG_TRANSCRIPTS=1` → reproduce →
  remove the override.
- Files that still legitimately hold content (history.jsonl, exported
  ZIPs, the clipboard during paste-mode insertion) are covered in
  [docs/guides/recovery.md](../guides/recovery.md) — that is data, not
  diagnostics.
