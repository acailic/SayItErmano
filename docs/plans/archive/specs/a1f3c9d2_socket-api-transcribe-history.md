# Plan: Scriptable unix-socket API (C1): transcribe + history routes

Source: design doc C1 — upstream ships a loopback HTTP API (PR #715) for
on-device agents; the locked no-TCP scope beats it with routes on the
EXISTING control socket (`fluidvoice/control.py`, JSON lines). Most of the
surface already exists (toggle/cancel/status/paste-last/language/set-config/
select-model/mics/test-dictation/insert-text/command-rerun); this adds the
two missing scriptable routes and documents the surface.

## Design decisions (binding)

1. `transcribe {path, process?}` — file → text through the daemon's WARM
   backend (no second model load, GPU reused). Rules: path must exist and
   be a file ≤ 200 MB (v1 is not chunked — honest rejection, not an OOM);
   refuses while `recording` or `busy` ({"ok": false, "error": "busy"} —
   GPU contention mid-take is the one state that must not interleave);
   `ensure_wav` conversion like the CLI (whisper.cpp forces it);
   language via the daemon's live resolution (`_language_detail()[0]`,
   runtime cycle override included); `process` (default false) runs the
   standard post-process chain; response `{ok, text, language,
   duration_s, path}`; converted temp dirs removed. Errors are
   key-free one-liners.
2. `history {limit?, since_ts?}` — `history.tail()` + optional
   `since_ts` filter, `limit` clamped 1..200 (default 10); response
   `{ok, entries, count}`; entries are the stored JSONL rows verbatim.
3. No new config keys, no TCP, no auth (the socket is filesystem-scoped
   to the user's runtime dir — same trust boundary as every existing
   action).
4. Docs: README "Scripting the daemon" note (python control.request +
   socat one-liner), ROADMAP C1 shipped, STATUS Left line updated,
   UPSTREAM-TRACKING loopback-API row (if present) marked equivalent.

## Phases

1. Implement + unit tests (handle_request direct calls with a stub
   backend + tmp wav: text roundtrip, process flag, missing-file and
   busy errors, size cap via monkeypatched stat, history limit/since
   with an isolated history file). Gate: full suite green.
2. Docs. Gate: full suite green.
3. Live smoke: isolated daemon (Xvfb recipe), `control.request(
   "transcribe", path=<fixture wav>)` returns real text via the warm
   faster-whisper backend; `history` returns the smoke rows; busy
   refusal while a take records.
