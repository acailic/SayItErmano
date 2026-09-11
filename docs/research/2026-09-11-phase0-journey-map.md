# Phase 0 — install-to-first-insertion journey & take lifecycle map

- Date: 2026-09-11
- Status: PHASE 0 artifact (plan item 3 of
  [2026-09-11-product-excellence-and-monetization-plan.md](2026-09-11-product-excellence-and-monetization-plan.md))
- Code baseline: `f74c924` on `linux` (agent worktree `agent/p0-ledger`)
- Method: static code trace with file:line references. Nothing here was run
  against a live session; steps that can only be settled live are marked
  **UNVERIFIED**. Companion artifact: the
  [finding ledger](2026-09-11-phase0-finding-ledger.md) seeds from every
  failure point below.

Line numbers refer to `f74c924`; they drift with any future change.

---

## Map 1 — Install-to-first-insertion journey

The happy path: clean Ubuntu-family machine → one-shot installer (or deb /
pipx) → daemon autostarts → first-run onboarding opens → mic + engine +
hotkey understood → hotkey pressed → speech → text lands in the user's own
editor. Steps are numbered; each carries the code path, the failure modes,
what the user sees, and whether recovery exists.

### Step 1 — Obtain and install the package

Three supported routes ([README](../../README.md) install section):

| Route | Entry point | Notes |
|---|---|---|
| One-shot curl installer | `scripts/install-one-shot.sh:26-27` | Default **user-space, no sudo**; downloads the latest release deb from the GitHub API (`:66-78`), extracts the bundled venv into `~/.local/share/sayit-ermano/venv` (`:148-155`), writes `~/.local/bin/sayit-ermano` (`:157-163`), a systemd user unit (`:192-210`) and — only when the unit is absent/disabled — an XDG autostart entry (`:165-175`). |
| System deb | `packaging/build-deb.sh` | Ubuntu 24.04 / amd64 / Python 3.12 contract ([ADR-0004](../adr/ADR-0004-ubuntu-deb-contract.md), [deb README](../../packaging/deb/README.md)). `Depends:` covers `xdotool, xclip, libnotify-bin, python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1, pipewire-audio-utils` (`build-deb.sh:146`), so a deb install brings every external tool. |
| pipx (any distro) | `scripts/verify-pipx.sh` | Python 3.11+; system GTK GIRs + tools still required by hand. |
| AUR | `packaging/aur/` | Instructions only, not published by the project. |

Failure modes (user-space default route):

- **No artifact verification.** The downloaded deb is checked only for
  `size > 10 MB` (`install-one-shot.sh:85-87`); no checksum/signature.
  Any MITM or compromised release installs silently. `curl | bash`
  compounds it. → ledger F-04.
- **Non-Debian machine:** the `dpkg --print-architecture` guard
  (`:44-46`) kills the script with "only amd64 packages are published"
  before the pipx alternative is ever mentioned. → F-08.
- **Optional sudo step declined:** missing `python3-gi`,
  `gir1.2-gtk-4.0`, `gir1.2-adw-1`, `xdotool`, `xclip`, `libnotify-bin`,
  `pw-record` (`:90-107`) are skipped with a printed apt line. Install
  "succeeds"; the app is broken later (steps 6/9). → F-05.
- **`~/.local/bin` not on PATH:** one-line warning at the end
  (`:212-213`); the launcher entry still works, but `doctor`/CLI do not.
  Recovery: add PATH.
- **GitHub unreachable / rate-limited:** `curl -fsSL` fails → `die` with
  a clear message. Recoverable (retry).

What the user sees: cyan `==>` progress lines; a final NOTE card
("press Right Ctrl, speak…"). No verification of anything at the end of
the script (the deb path's `apt` does dependency checks; the user-space
path does not run `doctor`).

### Step 2 — Daemon first start

- The systemd user unit is `PartOf/After/WantedBy=graphical-session.target`
  (`install-one-shot.sh:192-210`, deb unit `build-deb.sh:120-137`). The
  unit sets **no** `DISPLAY`/`WAYLAND_DISPLAY`/`XDG_SESSION_TYPE`
  environment — it relies on the display manager importing the session
  environment into the user manager (GNOME does; a TTY-started sway or
  minimal WM may not). If the environment is missing, the session probe
  (`fluidvoice/session.py:60-66` — `XDG_SESSION_TYPE` >
  `WAYLAND_DISPLAY` > `DISPLAY` > unknown→x11) classifies the session
  wrong and every capability resolution follows the wrong branch.
  **UNVERIFIED** (needs a live non-GNOME session). → F-06.
- `Daemon.run()` (`fluidvoice/daemon.py:195-232`): session probe + one
  `session: … - capabilities: …` log line (`:234-243`) → stale `/tmp`
  sweep (`:628`) → `startup_load` (`engine_manager.py:86-109`: the
  backend *constructor* synchronously — fast; the model *download/load*
  in a background warm thread `:110-119`) → hotkey listeners
  (`daemon.py:641-751`) → tray, mic monitor, lock monitor, update
  checker → **first-run onboarding spawn** (`:1207-1220`) → control
  socket (`:215-218`).
- Failure modes:
  - Backend constructor raises (e.g. faster-whisper not importable):
    `WARN speech backend not ready yet (…); will retry on first use`,
    daemon continues. The first take then fails with a notification
    unless the model stack is fixed. Recovery: `doctor`, reinstall.
  - **Model download in progress:** `eager_warmup` (default true)
    starts downloading the auto-selected model (small ~460 MB on GPU /
    base ~140 MB on CPU per `backends.resolve_model_name`) in the
    background. There is **no progress surface** for this download
    anywhere (no status field, no notification when done; only the
    `speech model loaded (preview ready)` log line,
    `engine_manager.py:112-118`). → F-02.

### Step 3 — First-run onboarding window

- Trigger: no `.onboarded` marker and empty history → daemon spawns
  `python -m fluidvoice app --onboard` (`daemon.py:1207-1220`,
  `_spawn_app` `:551-562` — note `stdout/stderr → DEVNULL`; if the GTK
  app fails to start (missing GIRs, no display) the failure is **silent
  beyond the journal**).
- The window (`fluidvoice/gtkui/onboarding.py`) checks, in order:

  | Row | Code | What it actually verifies | Gaps |
  |---|---|---|---|
  | Microphone | `:137-148` | Lists inputs via the daemon; OK when any default source exists | A listed-but-dead/muted mic passes; the tryout is the real test |
  | Speech engine | `:150-158` | Resolves the configured *name* (`auto` → concrete) | **Download state is never checked** — row shows OK with zero models on disk; message points to Settings → Models |
  | Hotkeys | `:160-166` | Prints the *config* values (`dictate Right_Control · cancel Escape`) | **No session awareness**: on Wayland there are no global grabs — the row is wrong there; no press-to-capture test; no grab-conflict check |
  | AI polish | `:167-173` | enabled / configured / optional | fine |
  | Updates | `:88-94` | static sentence | fine |

  → ledger F-01 (Wayland hotkey row), F-02 (engine row), F-19 (tryout gap).
- Tryout: "Record 3 seconds" (`:179-207`) → daemon `test_dictation`
  (`daemon.py:1288-1324`): recorder start → sleep 3 s → stop → silence
  check → `ensure_backend()` → transcribe → text shown in the window.
  **Not exercised:** the hotkey, the take lifecycle, insertion into a
  real app, insertion tools, context. A successful tryout therefore does
  NOT predict a successful real dictation-insertion (the plan's explicit
  gap). If the model is still downloading (step 2), `ensure_backend` +
  `transcribe → _load()` **blocks the tryout for the whole download**
  with the button stuck on "recording… speak now" — no timeout, no
  progress. → F-02, F-19.
- "Start dictating" writes the `.onboarded` marker and opens History
  (`:209-219`); "Open Settings" leaves onboarding reachable only via
  the launcher (the marker is written by the daemon on first spawn
  anyway, `daemon.py:1215`).

### Step 4 — Microphone selected

- Default `recording.device = ""` (auto). Recorder = `pw-record`, else
  `parecord` (`recorder.py:19-28`); neither present → every take fails
  at once with "no recorder found: install pipewire…" (notification,
  `capture.py:115-121`). Doctor flags this (`doctor.py:689-692`).
- Mic-priority auto-fallback: 3 s `pactl` diff poll notices
  connect/disconnect and switches to the first matching pattern
  (`daemon.py:353-372, 463-500`); at daemon start the same reselect runs
  once (`:473`). Recovery from an unplugged configured mic is automatic
  **iff** a pattern matches; otherwise the take hits the 2 s first-PCM
  watchdog (below).
- Failure modes: muted mic / wrong device → live-but-silent source →
  first-PCM timeout (2 s, `capture.py:245-280`) cancels with a clear
  notification; digital silence produces the "No audio from the mic"
  notice at transcription time (`pipeline.py`, empty-transcript branch).
  Bluetooth HFP switch-over mid-take: take finishes on the open stream;
  the fallback lands ≤ 3 s later. **UNVERIFIED** live (Bluetooth matrix
  not run).

### Step 5 — Hotkey bound

- X11: `HotkeyListener` (`hotkey.py:388` setup, `:417-447` poll loop)
  grabs 8 lock-mask combos per key with per-combo `onerror` + retry
  every ~10 ms tick; a refused grab (conflict) WARNs, notifies, and
  surfaces in `status`/tray/doctor (shipped request
  `hotkey-grab-selfheal`, live-verified 2026-09-04 per STATUS.md).
  Default key `Right_Control` (`config.py:618`).
- Wayland: no global grabs exist. The daemon writes
  `~/.local/share/sayit-ermano/bin/sayit-ermano-toggle`
  (`session.py:198`) and logs/prints per-DE bind steps; doctor repeats
  them (`doctor.py:666-673`). **The user must perform a manual DE
  settings action that the app cannot verify** — nothing checks that the
  shortcut was actually bound. Optional evdev push-to-talk is privileged
  (input group). **UNVERIFIED** live per-session (matrices pending).
- Failure recovery: X11 conflicts self-heal (live-verified); Wayland
  wrong-binding → `sayit-ermano toggle` from a terminal always works
  (control socket), which is also the documented fallback.

### Step 6 — First dictation → first insertion

Take lifecycle (detail in Map 2): hotkey → `Daemon.toggle`
(`daemon.py:1171-1185`) → `CaptureCoordinator.start_locked`
(`capture.py:105-156`) → … → stop → `_process` (`daemon.py:1381-1424`)
→ `DictationPipeline.run` (`pipeline.py:494+`) → `insert_text`
(`insertion.py:442-486`).

First-take-specific failure modes:

- **Model still downloading (step 2 race):** the take records fine, but
  `_transcribe → backend.transcribe → _load()`
  (`faster_whisper_backend.py:86-107, 108-112`) blocks on the download.
  Because `_load()` has **no lock**, the warm thread's download and the
  take's `_load` can construct `WhisperModel` twice concurrently
  (double download / double memory). The user sees the processing pill
  for minutes with no progress and **no cancel** (busy ignores toggles,
  `daemon.py:1180-1182`; Escape only works while recording,
  `capture.py:203-210`). → F-02/F-03/F-10.
- **Insertion tools missing** (user-space install that skipped sudo,
  step 1): `xdotool` absent → `insert_typed` raises "required tool not
  found" → notification "Could not type text: … (copied to clipboard
  instead)" → `clipboard_fallback` → `copy_to_clipboard`
  (`insertion.py:546-596`) **silently no-ops without xclip** — the
  notification is false, the text is nowhere, and the history row claims
  `strategy: clipboard-fallback`. Recovery exists but is invisible:
  the take IS in History (copy / insert-at-cursor). Doctor would have
  flagged the missing tool (`doctor.py:694-696`). → F-05/F-18.
- **Wayland, no insertion tool:** degradation ladder ends in
  `wl-copy` + "paste manually" notice, else `InsertError`
  (`insertion.py:488-544`). Doctor's exit code flips only when insertion
  is `unavailable` (`doctor.py:668-670`). **UNVERIFIED** live (GNOME
  ydotool/uinput setup path never exercised in a matrix run).

### Journey verdict (static)

The happy path on Ubuntu/GNOME-X11 with a deb install is well covered:
deps are declared, self-healing hotkey, onboarding, doctor, history.
The gaps cluster at (a) the un-downloaded model at first use, (b) the
user-space install that skipped system packages, (c) Wayland session
handling (hotkey row, bind verification, insertion tools), and (d) the
tryout validating less than the real journey needs. All four are ledger
entries; (a)+(d) are top-5.

---

## Map 2 — Take lifecycle (hotkey press → history append)

The core dictation loop, step by step. Each step: code path, failure
modes, what the user sees, recovery.

### T1 — Hotkey press → toggle

- X11: poll-loop key event → `Daemon.toggle` (`daemon.py:1171-1185`).
  Locked session → press ignored (`:1172-1175`, lockmon). Already
  recording → `stop_locked`. Busy (previous take processing) → **log
  line only**, no notification (`:1180-1182`). Not recording →
  `CaptureCoordinator.start_locked`.
- Wayland: DE shortcut → `sayit-ermano toggle` CLI → control socket →
  same `toggle`. Failure: socket dead → CLI error text (clear).
- Hold/both modes and mouse PTT have their own listener paths
  (`hotkey.py:521-599`, `:764+`), live-verified per STATUS.md.

### T2 — Recorder start (`capture.py:105-156`)

- `insertion.active_window_class()` snapshot (`insertion.py:86-109`; X11
  only — drives per-app prompts/punctuation, NOT insertion targeting).
- `Recorder.start` (`recorder.py:58-110`): spawn `pw-record`/`parecord`
  writing headerless 16 kHz s16 PCM to `/tmp/sayitermano-*.raw`; 0.35 s
  probe fails fast if the process dies immediately (bad device →
  RecorderError → notification "Recording failed: …", take never
  opens); stderr drained by a thread (no 64 KB pipe deadlock).
- Media pause (`pause_media` default true), start sound, tray state,
  overlay/pill construction (T3).
- Watchdogs armed: max-duration 300 s timer; **first-PCM 2 s** (a
  live-but-silent source cancels the take with a notification,
  `capture.py:245-280`); **stall 8 s** (frozen stream cancels with a
  clear error, `:281-313`).
- Failure recovery: all watchdog ends leave the daemon consistent
  (timers identity-checked and cancelled on every end path; N1/P0.4
  fixes). An immediate-recorder-death keeps recording=False so the next
  press starts clean.

### T3 — Preview while speaking (`capture.py:314-428`, `preview.py`)

- Segmented engine (default): fixed 2 s windows, 50 % hop, one decode
  per tick (`SegmentedPreviewEngine`, `preview.py:167+`, `_tick`
  `:353+`), word-overlap dedupe (`join_tail` `:152`), confidence gate
  (`:283`, a8759d6), display = X11 pill overlay or notification
  fallback (`overlay.py`; on Wayland the notification IS the preview).
- Trailing-silence VAD (`trailing_silence_s`, `preview.py:124-150`,
  energy RMS + zero-crossing heuristic) fires `vad_auto_stop`
  (`capture.py:445-456`) after 2.0 s of silence once speech committed.
  Failure mode: **false auto-stop under steady noise / breath / music**
  is unmeasured — no real-speech corpus exists (ledger F-13/F-14).
  Recovery: disable via `preview_vad_silence_s = 0`.
- Spoken-send countdown (0.5 s silence + phrase arms a 1.2 s countdown;
  speech resumes cancel it) rides the same engine
  (`capture.py:458-519`).
- Preview is best-effort everywhere: any exception → `WARN preview
  unavailable` and the take proceeds without it (`capture.py:425-427`).

### T4 — Stop (hotkey tap / VAD / max-duration) → WAV finalized

- `stop_locked` (`capture.py:158-186`): cancel timers, stop preview
  (finishing beat), stop sound, `Recorder.stop`
  (`recorder.py:115-137`): SIGINT → 0.25 s grace → SIGTERM → 1 s →
  SIGKILL; the raw PCM gets a WAV header written locally
  (`raw_to_wav_file`) — no third process involved.
- Empty/short capture (< 200 bytes) → "no audio captured" log, take
  ends, nothing typed. Recovery: none needed (press again).
- Media resumes; `_on_take_complete` (`daemon.py:1196-1205`) spawns the
  `process` thread (`_process` `:1381-1424`); `busy = true` until the
  pipeline finishes (see T9 for the busy-window UX).

### T5 — Transcribe (`pipeline.py:163-228`, `_transcribe`)

- `ensure_backend` (already loaded in the normal path; loads after an
  idle unload while the user was speaking — `_on_take_start` spawns the
  background reload, `daemon.py:1190-1194`).
- `pad_wav` to ≥ 1 s (whisper.cpp requirement); language resolution
  (per-model override > runtime cycle > config; `engine_manager.py:369+`).
- **Wrong-language whitelist guard**: auto-detected language outside
  `general.language_whitelist` → one re-decode with the first whitelist
  entry (`pipeline.py:199-227`).
- **Hallucination guard**: forced-language decode with repetition /
  no-speech tells or confidence band 0 → one auto re-decode, kept only
  if clean and confident (`:171-197`); a pure repetition loop is
  suppressed with the "No usable speech caught — check mic and language"
  notification instead of typing garbage (`:526-536`).
- Failure: any transcribe exception → notification "Transcription
  failed: …", take dropped, wav unlinked. Recovery: retry by voice; the
  audio is gone unless `history.save_audio` (default **false**,
  `config.py:970`).

### T6 — Post-process (`processing/`, `pipeline.py:520-528`)

- `post_process`: filler removal, custom dictionary (longest-first,
  boundaries), spoken punctuation (upstream 108-alias rule table),
  per-app prompt hints from the take-start `app_hint`.
- No failure modes beyond rule edge cases (unit-covered upstream-fidelity
  tests); wrong-language punctuation is a quality risk, not a crash.

### T7 — Optional AI polish (`pipeline.py:230-282`)

- Off by default (`ai.enabled = false`, `config.py:845`). On: any
  OpenAI-compatible endpoint; guard chain refusal → prompt-leak →
  over-correction, each falling back to the raw transcript with a
  notification; transport error → raw transcript + notification.
- Failure mode: a **slow endpoint** blocks here up to the 120 s timeout
  with no progress/cancel surface (T9). Local-first note: with AI off,
  nothing ever leaves the machine.

### T8 — Post-AI formatting + insertion-time context

- Slash/mention squeeze, GAAV, spoken-send phrase parse
  (`pipeline.py:352-400`).
- **P2 context seam** (`context.enabled`, default **false**,
  `config.py:910`): one bounded focused-field read immediately before
  insertion (`_read_insertion_context` `pipeline.py:414-436`; X11
  WM_CLASS adapter + AT-SPI adapter, `context/`), consumed for sentence
  continuation, GAAV-by-role, terminal-safety re-check
  (`_apply_focus_formatting` `:438-478`, `_recheck_send_key`).
  Privacy: selection/preceding text bounded to 500 chars, never
  persisted (pinned by tests). With the seam off or a failed read:
  behavior is byte-identical pre-P2. **Live matrices NOT DONE** — the
  default stays off until they pass (ledger F-26).

### T9 — Insert (`insertion.py:442-544`)

- X11: terminal detection (canonical profile > legacy
  `general.terminal_apps`), mode auto → paste when > 1200 chars or
  leading `-`; verified paste = own the CLIPBOARD selection with
  hygiene markers, observe the target read it (0.6 s cap), restore the
  previous clipboard (read-back checked, one retry); unverified paste
  raises and auto falls back to typed insertion with a notice.
  Terminals paste with `ctrl+shift+v`; typed insertions gain one
  trailing autocomplete space.
- Wayland: wtype/ydotool typed → wl-clipboard paste (fixed 0.45 s
  settle — **verification impossible by design**, no hygiene markers)
  → wl-copy + "paste manually" → InsertError ladder.
- Terminal detection uses the **focus at insertion time** (live
  WM_CLASS when the context seam is off), so a mid-take focus change
  keeps terminal quirks correct; the *prompt/punctuation* hint remains
  take-start identity (minor, by design).
- Failure recovery: any InsertError → notification + clipboard
  fallback; history row records the attempted strategy — **except** the
  silent-no-op case when xclip is missing too (F-05/F-18).
- **UNVERIFIED live**: paste/clipboard-manager interactions beyond the
  2026-09 GNOME 46 + CopyQ observations in STATUS.md (GPaste, Klipper,
  ghostty untested); all Wayland matrices pending.

### T10 — Spoken-send key, history append, done beat

- Pending send key (phrase present, terminal-suppression re-checked at
  insertion) → `press_key` after the insert (`pipeline.py:546-566`);
  badge on the pill.
- History append: transactional JSONL under a sidecar flock, atomic
  replace + fsync, 5000-entry cap, optional retained audio with
  rollback (`pipeline.py:480-492` → `history.py`, ADR-0002). Failure
  (disk full/corrupt row): the exception propagates to the process
  thread's supervised handler (logged); **the typed text already
  landed**, only the row is lost. Low severity.
- Pill finish beat ("✓"/"✓ AI", amber tint on low confidence) or
  close, `busy` clears, `touch_activity` resets the idle-unload clock
  (`daemon.py:1411-1424`).

### Lifecycle verdict (static)

The take loop is defensively built: watchdogs everywhere, best-effort
preview, guard chains, verified paste, transactional history. The
uncovered exposure is (1) the **busy window** — between stop and
done-beat the user cannot cancel or start another take and gets no
stage feedback (downloads, slow AI), (2) **no real-speech measurement**
of VAD/guard false positives (synthetic corpus only), and (3) every
Wayland branch — solid by design, unproven live.

---

## What this map deliberately did NOT do

- Run any of it on a live session (dev machine has no Wayland
  compositor; production daemon must not be touched from an agent
  worktree — AGENTS.md housekeeping).
- Measure stop-to-insertion latency, WER, or guard rates (needs the
  corpus + benchmark work of phase 1).
- Audit the command/rewrite/file-transcription/MCP paths beyond their
  take-lifecycle intersections (they have their own request briefs and
  test suites).
