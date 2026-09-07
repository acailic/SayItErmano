# Plan: AI refusal/derail guardrail (D5)

Source: docs/research/2026-09-05-fluidvoice-reviews.md insight 9 + design doc
D5 — upstream's closed model once pasted "I'm sorry, I can't assist with
that." into a user's document (GetVoibe review); a refusing or derailed LLM
must NEVER type into the user's doc. Port addition (upstream has no
guard). Local-first: pure post-hoc string detection, no extra calls.

## Verified facts (agent worktree @ 8a941b9)

- `Daemon._polish` (daemon.py:137) returns `(text, ai_used)`, catches
  `AIError` → raw-text fallback; success path returns the polisher output
  unvalidated. `self.polisher` is the injectable seam (tests use it).
- `Daemon._rewrite` (daemon.py:170) catches `RewriteError` → notify +
  `return None`; success path inserts `rewritten` unvalidated.
- Config: `DEFAULTS["ai"]`, `SETTING_RANGES`/`SETTING_ENUMS` (no ai rows
  needed for a bool), `ALLOWED_SETTINGS["ai"]`, `_SAVE_WHITELIST["ai"]`,
  mask_secrets. Settings AI page: `_build_ai` rows registry; `_switch`
  helper exists.
- Doctor: `run()` prints section lists; `_ai_lines(cfg)` exists (check
  exact name during build; AI section prints enabled/model state).
- History rows carry `ai: bool` — guard fallback yields `ai_used=False`,
  so the row honestly records that AI output was NOT used.
- Tests: `tests/test_daemon.py` has stub-polisher Daemon construction
  patterns; `tests/test_config_settings.py` covers key whitelists.

## Design decisions (binding)

1. **Detection = pure function** `fluidvoice/processing/refusal.py::
   is_refusal(text) -> bool`. Normalize (lowercase, collapse whitespace,
   strip leading markdown/bullets/quotes), then:
   - **sorry-family** (the false-positive trap: "I'm sorry I'm late" is a
     legitimate dictation): matches ONLY when a sorry-lead
     (`i'm sorry`/`i am sorry`/`sorry`/`unfortunately`/`i apologize`)
     appears within the first 80 chars AND a refusal-verb combo
     (`can't|cannot|can not|unable to|won't|will not` within 40 chars of
     `assist|help with|comply with|fulfill|provide that|support that`)
     appears within the first 240 chars.
   - **unambiguous leads** (match alone, first 40 chars):
     `as an ai`, `i must decline`, `i will not assist`, `i cannot assist`,
     `i can't assist`, `i cannot help with`, `i can't help with`,
     `i cannot comply`, `i can't comply`, `i'm unable to assist`,
     `i am unable to assist`, `i cannot fulfill`, `i can't fulfill`,
     `that request goes against`, `against my guidelines`,
     `i'm not able to assist`, `i am not able to assist`.
   - Only the reply's FIRST 240 chars are examined (refusals lead).
   - English only in v1 — a non-English refusal falls through to the
     existing behavior (documented limitation, STATUS note).
2. **Wiring**: `_polish` — after a successful polisher call, if
   `cfg["ai"].get("refusal_guard", True)` and `is_refusal(polished)`:
   log `AI polish refused (guardrail); using raw transcription`, notify
   "AI polish refused — typed the raw transcript", return `(text, False)`.
   `_rewrite` — refusal output raises the existing `RewriteError` path
   ("AI refused the rewrite") → notify + None, nothing typed.
3. **Config**: new `[ai] refusal_guard` (bool, default true — the guard
   is the product promise; opting out is explicit). `ALLOWED_SETTINGS`,
   `_SAVE_WHITELIST`, Settings AI page switch "Refusal guard", doctor AI
   line suffix `refusal guard on/off`.
4. **Trade-off (documented)**: a dictation that VERBATIM starts like a
   refusal ("I can't assist with that" as quoted speech) falls back to the
   raw transcript + a notification — never data loss, always explained;
   without the guard the failure mode is junk typed silently.

## Phase 1 — detector + config + wiring

Files: `fluidvoice/processing/refusal.py` (new), `fluidvoice/config.py`,
`fluidvoice/daemon.py`, `fluidvoice/doctor.py`,
`fluidvoice/gtkui/settings_window.py`, tests.

1. `is_refusal` per decision 1; module docstring cites the GetVoibe case.
2. Config key per decision 3 (DEFAULTS comment in file style, TEMPLATE
   comment line).
3. `_polish` + `_rewrite` wiring per decision 2.
4. Doctor line; Settings switch.
5. Tests (`tests/test_refusal_guard.py` new):
   - matrix: the verbatim GetVoibe string, markdown-wrapped refusals
     (`**I'm sorry**, but I cannot assist`), as-an-AI, decline variants,
     sorry+verb at 200 chars in → True; "I'm sorry I'm late",
     "Sorry, I can't make it today", "I can't make it to the meeting",
     normal polished prose, 5 000-char doc starting "I'm sorry to hear
     that" → False.
   - Daemon stub-polisher: refusal → `(raw, False)` + log + notify;
     refusal with guard off → polished text typed (opt-out honored);
     normal → unchanged; injected polisher returning "" (existing
     behavior preserved).
   - rewrite: stub rewriter returning a refusal → notify + None.
   - config: default true, socket set-config accepts bool, save persists.
   - doctor line asserts `refusal guard` present.

Gate: full suite green.

## Phase 2 — docs

README AI-polish subsection note (one paragraph: the guard, the fallback,
the opt-out key); STATUS divergence row (port addition: refusal guard,
upstream pasted refusals into docs — research insight 9); ROADMAP leapfrog
row notes D5 shipped.

Gate: full suite green (docs only).

## Phase 3 — live smoke (Xvfb-isolated, e2e recipe)

Daemon with `[ai]` pointed at a tiny scripted OpenAI-compatible chat
server (extend nothing: reuse a python http.server one-shot stub in /tmp)
that returns a refusal for every request; dictate via socket toggle +
virtual mic; expect: raw transcript typed, notification present, history
row `ai: false`; flip `refusal_guard = false` → refusal text typed
(proving the opt-out end-to-end); teardown restores nothing (sandboxed).

## Out of scope

Non-English refusal patterns, semantic derail detection ("summarized
instead of cleaned"), confidence scoring, LLM-based self-check calls,
command-mode replies (parse errors already surface loudly), telemetry.
