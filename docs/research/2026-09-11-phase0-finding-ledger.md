# Phase 0 — prioritized finding ledger

- Date: 2026-09-11
- Status: PHASE 0 artifact (plan item 3; the seed for WP5's top-five
  synthesis) of
  [2026-09-11-product-excellence-and-monetization-plan.md](2026-09-11-product-excellence-and-monetization-plan.md)
- Code baseline: `f74c924` on `linux` (agent worktree `agent/p0-ledger`)
- Companion artifacts: [journey map](2026-09-11-phase0-journey-map.md)
  (the failure points below carry its step IDs), reconciled
  [ROADMAP](../ROADMAP.md).

Conventions:

- **Status** = `REPRODUCED` (defect/gap proven by live observation or by
  direct code+history evidence), `UNVERIFIED-RISK` (mechanism is
  code-evidenced or documented, but not observed live), `IDEA`
  (improvement hypothesis, no defect claimed).
- **Severity** judged on user impact at the plan's gates (first-use
  funnel, dependable insertion, honest promises). Safety/data-loss
  outranks score regardless of frequency.
- **Effort**: S ≤ hours, M ≤ days, L > days (investigation+fix+test).
- Every finding links its acceptance check — the evidence that would
  close it. None of this is fixed here (phase 0 is investigation only).

Area keys follow the plan's checklist: **INSTALL** first use and
installation · **CAPTURE** capture and lifecycle · **SPEECH** speech
quality and speed · **INSERT** insertion and recovery · **CORRECT**
correction and daily workflows · **UI** UI and accessibility ·
**PRIVACY** privacy and security · **ARCH** architecture and
maintenance · **TEST** testing and distribution · **POSITION**
positioning and support.

---

## Findings (ordered by area, then severity)

### F-01 · INSTALL · high · UNVERIFIED-RISK

Onboarding "Hotkeys" row claims a global hotkey that cannot exist on
Wayland.

- User impact: a first-run Wayland user is told `dictate
  Right_Control` works; it does nothing (no global grabs on Wayland).
  The one shot at teaching the correct bind (DE custom shortcut to the
  toggle script) is missed; the row is *wrong*, not just incomplete.
- Source: `fluidvoice/gtkui/onboarding.py:160-166` (prints raw config,
  no session probe) vs. `fluidvoice/daemon.py:644-650` (the daemon's
  wayland branch says the opposite) and `fluidvoice/session.py:198`
  (toggle script + per-DE steps exist and are shown by doctor +
  Settings → Wayland only).
- Proposed fix: probe the session in onboarding; on Wayland render the
  bind steps + copy button (reuse `session.de_shortcut_instructions`),
  and show X11 grab health from `status` on X11.
- Effort: S–M. Acceptance: on a GNOME-Wayland live session the row
  shows bind instructions and no false "Right_Control works" claim; on
  X11 it reflects `hotkey_grabbed`.

### F-02 · INSTALL · high · UNVERIFIED-RISK

First-use model download is invisible and can hang the first take or
the onboarding tryout.

- User impact: fresh install → daemon starts → eager warmup begins a
  ~140–460 MB download in a background thread with **no progress
  surface anywhere** (no status field, no notification; only a journal
  line on completion — `fluidvoice/engine_manager.py:86-119`). If the
  user dictates (or runs the onboarding tryout) before it finishes,
  `FasterWhisperBackend._load()` (`fluidvoice/backends/
  faster_whisper_backend.py:86-107`) blocks the take's processing —
  the pill shows "processing" for the entire remaining download, with
  no progress, no cancel, and new toggles ignored (F-10). On a slow
  link that reads as a hang on the product's very first use.
  Onboarding's engine row shows OK before any model exists on disk
  (`onboarding.py:150-158` resolves the *name* only).
- Source: journey map step 2 / step 3 / step 6; plan checklist
  "slow/interrupted model downloads" (never tested).
- Proposed fix: (a) surface warmup/download state in `status`, doctor
  and onboarding's engine row (name + downloaded?/downloading %);
  (b) on first take with a cold model, notify "first use: downloading
  model (~X MB, ~Y s left)" and keep the take path cancel-safe; (c)
  retry/resume guidance on download failure (huggingface_hub resumes
  partial blobs; a killed daemon leaves a clean retry path — verify).
- Effort: M. Acceptance: on a throttled clean install, the engine row
  is honest, the first take during download shows progress and can be
  abandoned without restart, and a mid-download daemon kill resumes on
  the next start.

### F-03 · CAPTURE · medium · UNVERIFIED-RISK

Unlocked lazy `_load()` can double-construct the model (concurrent
download/load) on first take.

- User impact: wasted bandwidth/memory, possibly two full model loads
  racing on small-RAM CPU-only machines; worst case a confusing first
  take. Mechanism: `engine_manager.py:110-119` (warm thread) and a
  first take's `transcribe → _load()` (`faster_whisper_backend.py:86-91,
  108-112`) both check `self._model is None` with no lock — both pass,
  both construct `WhisperModel`. HF-hub per-blob locks make corruption
  unlikely; double allocation is still real.
- Source: code trace, journey map step 6.
- Proposed fix: a module-level `threading.Lock` in `_load()` (or route
  first decode through `ensure_backend`'s existing
  `backend_load_lock`).
- Effort: S. Acceptance: unit test proving two concurrent
  `_load()`/`warmup()` callers construct the model once (fake
  WhisperModel with a sleep); existing suite green.

### F-04 · TEST/PRIVACY · medium-high · UNVERIFIED-RISK

One-shot installer performs no artifact verification before installing.

- User impact: a compromised release, a MITM'd download, or a hijacked
  `raw.githubusercontent.com` delivers an arbitrary root-owned
  (system mode) or user-space payload; the only check is `size > 10 MB`
  (`scripts/install-one-shot.sh:85-87`), plus the `curl | bash` pattern
  itself. Also blocks "trustworthy distribution" claims for a paid
  release.
- Source: `scripts/install-one-shot.sh:66-87`; deb README documents
  hash-locking *dependencies* but not the artifact the user downloads.
- Proposed fix: publish `sha256` + minisign/cosign signature per
  release asset; installer fetches and verifies both, with a plain
  checksum line for manual verification; document in README.
- Effort: M (release plumbing; GPG signing ritual). Acceptance: a
  tampered asset makes the installer abort with a clear message; a
  clean install verifies and reports the signer.

### F-05 · INSTALL · high · UNVERIFIED-RISK

Skipping the optional system-package step strands the first insertion
and then lies about it.

- User impact (user-space install that declined sudo, or pipx on a
  clean box): daemon runs, hotkey works, recording works, transcription
  works — then `xdotool` missing → `InsertError` → notification
  "Could not type text: … (copied to clipboard instead)" → but with
  `xclip` also missing, `copy_to_clipboard` **silently no-ops**
  (`fluidvoice/insertion.py:546-596`). The text is nowhere; the message
  is false; the history row records `strategy: clipboard-fallback`
  (F-18). Recovery exists but is invisible: the take IS in History
  (copy/insert-at-cursor). Doctor flags the missing tools — the user
  was never routed to it.
- Source: `scripts/install-one-shot.sh:90-107` (skip path),
  `pipeline.py` `_insert` notify text, `insertion.py:574-596` no-op
  branch; deb path is covered by `Depends:` (`packaging/build-deb.sh:146`).
- Proposed fix: (a) runtime precheck — if `xdotool`/`xclip` (X11) or
  no wayland tool is missing, one actionable notification at daemon
  start ("insertion impossible: install xdotool xclip — `doctor` for
  details") and a `status`/onboarding row; (b) make the failure
  notification tell the truth when the clipboard fallback also failed
  ("text saved to History" instead of "copied").
- Effort: S–M. Acceptance: on a box without xdotool/xclip the daemon
  announces the problem at start (not at first insertion), the failed
  insert notification names History as the recovery path, and doctor's
  missing-tool lines match reality.

### F-06 · INSTALL · medium · UNVERIFIED-RISK

Shipped systemd user units carry no session environment; correctness
depends on the DE importing it.

- User impact: on sessions where the display manager does not run
  `systemctl --user import-environment` (TTY-started sway, some WMs),
  the daemon sees neither `DISPLAY` nor `WAYLAND_DISPLAY` → session
  probe falls to unknown→x11 (`fluidvoice/session.py:52-66`) → hotkey
  listener cannot reach X (HotkeyError → notification), insertion
  picks the wrong ladder. Symptom: "works when launched from a
  terminal, dead at login."
- Source: `packaging/build-deb.sh:120-137` and
  `scripts/install-one-shot.sh:192-210` (no `Environment=` lines; the
  dev `scripts/install.sh` unit *does* bake DISPLAY — inconsistent);
  STATUS.md documents the probe order but not this dependency.
- Proposed fix: unit `ExecStartPre`/wrapper that imports
  `DISPLAY/XAUTHORITY/WAYLAND_DISPLAY/XDG_SESSION_TYPE` from the active
  logind session (or document `import-environment` as a requirement +
  doctor check "daemon environment matches session").
- Effort: M. Acceptance: daemon started by the user unit on a
  TTY-launched sway session reports `session: wayland` correctly
  (matrix A precondition).

### F-07 · INSTALL · low · IDEA

Dev installer runs `doctor` immediately after `enable --now` — races
daemon readiness and prints "daemon not running" noise
(`scripts/install.sh:47`).

- Fix: short readiness wait or drop the line. Effort: S. Acceptance:
  clean `install.sh` output ends with doctor showing the socket alive.

### F-08 · INSTALL · low · UNVERIFIED-RISK

One-shot installer is Debian-family-only but dies without pointing
elsewhere: on non-dpkg systems the guard aborts with "only amd64
packages are published" (`install-one-shot.sh:44-46`) and never
mentions pipx; GitHub-release API parsing via grep/sed is brittle
(`:66-78`).

- Fix: detect non-dpkg and print the pipx path; tolerate API shape
  drift. Effort: S. Acceptance: on Fedora, the script prints the pipx
  instructions instead of a bare error.

### F-09 · CAPTURE · medium · UNVERIFIED-RISK

Toggle during processing is ignored with no user feedback.

- User impact: press-to-dictate during the busy window (stop→done-beat)
  does nothing except a journal line ("still processing previous
  dictation; ignoring toggle", `fluidvoice/daemon.py:1180-1182`). Users
  interpret it as "hotkey stopped working" — the top-trust failure for
  a hotkey-driven tool. With AI polish on (timeout 120 s) the window is
  long.
- Fix: surface busy state (pill badge "still working…", tray tooltip,
  one suppressed-press notification); consider queueing the press.
- Effort: S–M. Acceptance: scripted press-during-processing produces a
  visible cue within 300 ms in ≥ 95 % of attempts (live matrix case).

### F-10 · CAPTURE · medium · UNVERIFIED-RISK

No cancel path while processing (post-stop).

- User impact: Escape cancels only while recording
  (`capture.py:203-210` gates on `recording`); during transcription,
  model download (F-02) or a slow AI polish, the user cannot abandon a
  take — they must wait out the pipeline (up to minutes) while `busy`
  blocks everything. Wrong-language slips and accidental long takes
  become unrecoverable dead air.
- Fix: accept cancel during `busy` — cooperative abort between pipeline
  stages (check a flag before insert; keep history optional), plus a
  hard bound on the processing window (e.g. cancel-after-N s).
- Effort: M (threading discipline in the process thread; RuntimeTasks
  already gives the supervision scaffolding). Acceptance: a scripted
  Escape within 1 s of stop during a mocked 10 s polish aborts cleanly,
  nothing is typed, no state corruption, next take works immediately.

### F-11 · CAPTURE · low · UNVERIFIED-RISK

USB mic unplug mid-take ends via the 8 s stall watchdog (or 300 s max)
rather than instantly; notification wording for the stall path is
"clear error" per code but unverified live (`capture.py:281-313`).
Acceptance: live unplug test shows a takeover notification ≤ 10 s and
the next take auto-switches (mic monitor). Effort: S (test only).

### F-12 · CAPTURE · low · UNVERIFIED-RISK

Suspend/lock mid-take: lockmon cancels the active dictation and pauses
hotkeys (shipped, live-verified per STATUS.md) — but
resume-from-suspend with a take in flight (sleep counts as locked) is
only unit-covered; the daemon-under-user-unit path after resume is
UNVERIFIED on live hardware. Acceptance: one scripted
suspend/resume cycle with an active take on the daily driver. Effort: S
(test only).

### F-13 · SPEECH · medium · REPRODUCED (the gap), effects UNVERIFIED

VAD/confidence/hallucination guards have no real-speech evidence.

- User impact: false auto-stops (trailing-silence VAD is an RMS+ZCR
  heuristic, `preview.py:124-150`), over-suppression by the preview
  confidence gate (a8759d6), or wrong-language/whitelist re-decodes
  (`pipeline.py:163-228`) can each eat or garble real dictation — or be
  perfectly fine; today nobody can say which. The bundled corpus is
  tones/silence/chirps (`fluidvoice/evalharness/synth.py`), so every
  "guard false positive/negative" metric the plan's gates require is
  unmeasurable as shipped.
- Source: `docs/eval/README.md` (CC0 synthetic corpus), the 2026-09-11
  learning-session analysis (1bcefc0) used real speech but only as a
  one-off measurement, not a committed corpus.
- Status split: the *measurement gap* is REPRODUCED (documented,
  deliberate CC0 policy); user-visible guard behavior is UNVERIFIED.
- Proposed fix: the plan's phase-1 corpus (150–300 licensed utterances,
  held-out set) + harness adapters; then set language-specific
  thresholds.
- Effort: L (recruitment/licensing + harness work). Acceptance: eval
  report on the corpus shows guard FP/FN rates with CIs for the
  recommended model/language before the next speech-pipeline release.

### F-14 · SPEECH · medium · REPRODUCED

Evaluation corpus is synthetic-only (measurement gap) — see F-13; kept
separate because it is the *instrument* finding: even pure STT WER/CER
accuracy of shipped backends has never been measured on recorded
speech, so the plan's "trustworthy speech" gate has no baseline. Any
quality claim in README/positioning must carry this caveat until phase
1 runs. Fix = the corpus (F-13's work); acceptance = baseline report
attached to a release SHA per the release policy.

### F-15 · INSERT · medium · REPRODUCED (live-observed, documented)

X11 clipboard-manager capture residuals during paste-mode insertion.

- User impact: on GNOME 46 + clipboard-indicator extension, flashed
  dictation text is readable by the shell extension regardless of
  hygiene markers; this CopyQ build ignores
  `x-kde-passwordManagerHint` (only the `x-copyq-*` markers work);
  GPaste/Klipper untested (STATUS.md "Insertion-hardening residuals").
  A dictation of a password-adjacent secret can land in a clipboard
  history UI. Typed insertion (default, ≤ 1200 chars) avoids all of it.
- Proposed fix: document loudly (doctor/Settings: "paste mode exposes
  text to clipboard managers on X11-GNOME"); prefer typed below the
  threshold (already default); revisit wlroots/GNOME pill copy for a
  "secure paste" story. Effort: S (docs) / L (real fix).
- Acceptance: docs + doctor line state the exposure; matrix includes a
  clipboard-manager sweep case on GNOME/sway.

### F-16 · INSERT · medium · UNVERIFIED-RISK

Wayland paste is unverifiable-by-design (fixed 0.45 s settle, no
hygiene markers, `insertion.py:59, 294-330`) and every Wayland
insertion branch is live-untested (matrices "none yet"). No defect
claim — a parity-evidence gap: README presents Wayland as shipped
(v0.3) while the gating matrices are explicitly NOT DONE
(`docs/dev/wayland-smoke-matrix.md`). Acceptance: matrices A/B/C
executed and recorded; then either parity or a documented reduce-scope
promise. Effort: M (test access) — the blocker is a real Wayland
session on the dev machine, not code.

### F-17 · INSERT · medium · UNVERIFIED-RISK

GNOME-Wayland default insertion requires ydotool + `ydotoold` +
`/dev/uinput` permissions — a privileged, error-prone setup that
onboarding never mentions (F-01 sibling); auto-resolution skips wtype
on GNOME (`session.resolve_wayland_tool`). User impact: fresh
GNOME-Wayland installs get "no wayland insertion tool available"
notifications whose text does name the fix, but only after first use.
Fix: precheck + doctor-driven guidance at onboarding. Effort: S–M.
Acceptance: onboarding on live GNOME-Wayland names the ydotool step
before the first take.

### F-18 · CORRECT · low · UNVERIFIED-RISK

History `strategy: clipboard-fallback` is recorded even when the
clipboard write silently no-opped (F-05's diagnostic half) — misleading
support evidence. Fix: `copy_to_clipboard` returns success;
`clipboard_fallback` propagates it; history/notify tell the truth.
Effort: S. Acceptance: unit test — missing xclip ⇒ strategy says
"failed (History)" and the notification points at History.

### F-19 · CORRECT/UI · high · REPRODUCED (gap by design)

Onboarding tryout validates mic+model only; the tryout → real-insertion
transition is untested and untaught.

- User impact: the single funnel the plan's bet #1 targets ("a complete
  first-use path") currently ends one step early: a green tryout proves
  recording+transcription, then the user is dropped into the world with
  no verification that the hotkey, focus, and insertion tooling work in
  *their* editor — precisely the step where F-05/F-16/F-17 bite. No
  user has been observed making this transition (phase-1 observation
  will confirm drop-off).
- Source: `onboarding.py:179-207` (tryout → `test_dictation`,
  `daemon.py:1288-1324` — no insertion involved); journey map step 3/6.
- Proposed fix: a guided final onboarding step — "now open any app,
  press <hotkey>, say a phrase, and watch it appear" with a self-check
  (last history row's `strategy` within N seconds = success; failure ⇒
  route to doctor + per-cause guidance). Measure abandonment (plan
  gate: 8/10 unaided).
- Effort: M. Acceptance: on 10 fresh-install volunteers, ≥ 8 complete
  setup→real-app-insertion unaided with the new step (the plan's
  first-use gate), each failure attributed to a known finding.

### F-20 · CORRECT · low · IDEA

Dictionary-suggestion and correction workflows are suggest-only and
Settings-buried (deliberate D1–D7 divergences); discoverability is
untested. Acceptance: phase-1 user tests measure whether corrections
decrease over a two-week pilot (plan bet #3). Effort: investigation
only.

### F-21 · UI · medium · IDEA

Accessibility/keyboard-only/screen-reader audit of the GTK app has
never been done (no evidence either way; plan checklist "UI and
accessibility"). Adw widgets give a decent floor; labels/focus order/
reduced-motion are unverified. Effort: M (audit) + S–M (fixes).
Acceptance: an a11y pass report (orca + keyboard-only + scaling) with
concrete fixes filed per finding.

### F-22 · UI · low · UNVERIFIED-RISK

Notifications are the only feedback channel for several failure paths
(recorder error, insertion failure, guard suppression). With
notifications disabled or a bare WM without a notification daemon,
feedback vanishes entirely (log-only). Fix: mirror important failures
into the tray tooltip/status + History banner. Effort: S–M.
Acceptance: with `notify-send` absent, a forced insertion failure is
still visible in tray/status within seconds.

### F-23 · PRIVACY · low · REPRODUCED (verified posture)

Privacy posture is strong by default (local STT, AI off by default
`config.py:845`, no telemetry, `history.save_audio` default **false**
`config.py:970`, context seam never persists surrounding text,
selection bounded 500 chars) — but is nowhere *stated* as a product
promise, and the paste-mode clipboard exposure (F-15) is the one leak
class. Fix: a plain-language privacy page (README + Settings → About)
covering audio retention defaults, AI off-by-default, remote-STT
opt-in, MCP trust boundary, clipboard flash caveat. Effort: S.
Acceptance: page exists, reviewed for accuracy against code; linked
from onboarding.

### F-24 · PRIVACY · medium · UNVERIFIED-RISK

Remote STT (`model.remote_url`) sends every recorded WAV off-machine
while configured, winning over all local backends; disclosure exists in
Settings/doctor but there is no per-take visual marker in the
pill/history that a take was cloud-processed, and no redaction
considerations for the remote path documented. Effort: S–M.
Acceptance: takes transcribed remotely are visibly marked in preview +
history; doctor prints the endpoint; README section covers data flow.

### F-25 · ARCH · low · IDEA

No concrete architecture hazard found in phase 0 beyond what P1.2
already decomposed (daemon.py remains a 1.4 k-line composition root —
acceptable per the plan's "no file-length refactors" rule). Watch item
only: the insertion-time context seam added a second identity source
(take-start `app_hint` vs focus-at-insert) — behavior today is
documented; keep it pinned by tests when flipping `context.enabled`.

### F-26 · TEST · high · REPRODUCED (gap, documented)

Live Wayland + context smoke matrices NOT DONE ("Runs: none yet",
`docs/dev/wayland-smoke-matrix.md`) — they gate Wayland parity claims,
the `context.enabled` default flip, and matrix C (X11 regression) for
the P2 consumers. Everything Wayland/context is therefore UNVERIFIED
live despite being shipped-behind-a-flag; subsumes F-16's evidence gap.
Blocker: no Wayland compositor on the dev machine (noted 2026-09-08).
Effort: M once a session exists (the doc *is* the runbook).
Acceptance: matrices A/B executed + recorded with date/SHA/machine, C
green; ROADMAP's parity gate cleared or scope honestly reduced.

### F-27 · TEST · medium · UNVERIFIED-RISK

Dependency lock is pin-only until a release-time network run adds
wheel hashes; `build-deb.sh` then auto-switches to `--require-hashes`
(`packaging/deb/README.md`). The committed lock today is pin-only — a
skipped manual step silently ships a weaker build, and nothing in the
gate *fails* on pin-only (it prints a note). Fix: make release-prepare
fail (or require an explicit `--allow-pin-only`) when the lock carries
no hashes. Effort: S. Acceptance: release-prepare on a pin-only lock
aborts with instructions.

### F-28 · TEST · medium · UNVERIFIED-RISK

Upgrade/rollback/uninstall of shipped packages never exercised end to
end: deb pre/postinst restart logic ("daemon restarts itself
automatically"), user-space shadowing (unit-vs-autostart races are
handled in the installer, `install-one-shot.sh:165-175`), user+system
double-install (doctor WARN exists), and pre-rename legacy cleanup are
code-reviewed but untested as journeys. The plan's "release
reproducibility under the contract" gate needs one scripted
old→new→rollback pass per install method. Effort: M. Acceptance:
documented VM pass: v_prev → v_next (deb and user-space) keeps
config/history, hotkey survives, rollback works, uninstall is clean.

### F-29 · TEST · low · REPRODUCED (positive)

pipx path has a verification script (`scripts/verify-pipx.sh`) and the
deb build is container-pinned with an exact-pin lock — solid baselines;
keep them in the release checklist. No action.

### F-30 · POSITION · medium · UNVERIFIED-RISK

Promises vs evidence: README markets Wayland support and a v0.8.1
feature set while the gating matrices (F-26), real-speech quality
numbers (F-14), and first-use funnel evidence (F-19) are absent. For a
paid release this is the honesty risk the plan calls out
("Are promises supported by the current compatibility evidence?").
Fix: a supported-matrix page that states what is live-verified vs
unit-verified vs untested; sync README claims to it. Effort: S (docs)
after F-26. Acceptance: every README claim maps to a matrix row or
bears an explicit "not yet verified live" note.

---

## Coverage note (plan checklist → this ledger)

Audit areas seeded: INSTALL (F-01/02/04–08), CAPTURE (F-03/09–12),
SPEECH (F-13/14), INSERT (F-15–17), CORRECT (F-18–20), UI (F-21/22),
PRIVACY (F-23/24), ARCH (F-25), TEST (F-26–29), POSITION (F-30).
Deliberately empty in phase 0: live user observation (phase 1), the
speech benchmark (phase 1 corpus), command/MCP security re-audit
(covered by the 5-agent upstream audit + P0 sweep; re-open on change).

---

## Top 5 findings (ranked: frequency × severity × reach × evidence confidence)

Ranked for the coordinator's WP5 synthesis. Safety/data-loss first
per the plan's ranking rule — none of the current findings is an
active data-loss defect (the closest, F-05, strands-but-keeps text in
History), so ranking follows the product gates.

1. **F-26 — Live Wayland + context matrices NOT DONE** (high, reach:
   every Wayland user + the context default flip + every parity claim;
   confidence: documented gate, zero runs). Blocks honest Wayland
   positioning (F-30), the P2 payoff, and any "dependable insertion
   ≥ 99 %" measurement on half the desktops. Cheapest high-value item
   once a Wayland session exists — the runbook is already written.

2. **F-02 — Invisible first-use model download that can hang the first
   take/tryout** (high, reach: every fresh install on a non-instant
   network — i.e., nearly all; confidence: code-evidenced, effects
   plausible). The first-use funnel is the plan's bet #1 and its 8/10
   gate; pair with F-19 when fixing. Sibling F-03 is the S-effort
   correctness half.

3. **F-14/F-13 — No real-speech measurement instrument** (medium
   severity for users today, blocker for every quality gate and
   positioning claim; confidence: reproduced/documented gap). Without
   the corpus, WER/CER, guard FP/FN, VAD false-stops and the
   "trustworthy speech" gate are all unfalsifiable — this gates phase
   2 prioritization itself.

4. **F-05 (+F-18) — Missing-tool install path strands the first
   insertion and reports falsely** (high when hit — the product's core
   promise fails on first use with a wrong message; reach: user-space
   installs that skip sudo, any pipx on a clean box; confidence:
   code-evidenced mechanism, live confirmation pending). Small effort,
   outsized trust payoff; includes F-08/F-17 as siblings.

5. **F-19 — Tryout → real-insertion transition untested/untaught**
   (high, reach: every new user; confidence: reproduced design gap).
   The exact step the plan's bet #1 names; measuring its drop-off is
   the phase-1 user-test's first question, and the guided final step
   is the likely fix once F-02/F-05 stop eating users first.

Deferred-but-noted for WP5: F-09/F-10 (busy-window feedback + cancel —
daily-trust friction once the funnel works), F-04 (artifact signing —
prerequisite for any paid release), F-30 (promise honesty — follows
F-26/F-14 mechanically).
