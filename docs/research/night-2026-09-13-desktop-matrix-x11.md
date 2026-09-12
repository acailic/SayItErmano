# Night run: GNOME X11 desktop matrix — 2026-09-13

- Machine: maintainer desktop (Pop!_OS 24.04 LTS, GNOME Shell 46.0, X11
  session on `DISPLAY=:1`, NVIDIA RTX 4060, kernel 7.1.5-76070105).
- Source: worktree `FluidVoiceLinux-night-q7-x11-matrix` @
  `09e1b9ffe0c2221710218f49a1faa6b08fe83591` (branch
  `agent/night/20260913-q7-x11-matrix`, from `linux`).
- Window/session rules honored: the production `sayit-ermano` daemon ran
  untouched the whole night (never restarted, never reconfigured; one
  read-only `status` probe and synthetic-hotkey probes, see §Prod).
  All dictation exercised through ISOLATED daemons spawned from the
  worktree (own `SAYITERMANO_CONFIG`/`SAYITERMANO_SOCKET`/XDG root, F9/F12
  hotkeys — no conflict with the production Right_Control grab), per
  `tests/integration/conftest.py`'s pattern.
- Synthetic voice: `tests/integration/fixtures/parakeet_v2_0.wav` (7.4 s
  real speech, golden text below) played into a PipeWire null-sink
  (`fv-matrix-mic`) whose monitor the isolated daemon records from
  (`recording.command="parecord"`, `recording.device` = monitor). This
  gives REAL end-to-end takes (hotkey → parecord → faster-whisper →
  context read → insertion) with a KNOWN transcript, without a human
  voice at 01:00.
  - tiny/CPU/int8 transcript of the fixture (calibration, stable across
    the night except one word): "Well, I don't wish to see it anymore,
    observe Phoebe, turning away her eyes. It is certainly very like the
    old portrait." (120 chars; one take produced "very likely").
  - PipeWire quirk found while wiring this: **native `pw-record` from a
    null-sink monitor captures at ~7% level; the pulse-compat `parecord`
    path captures at ~67%** — use `recording.command="parecord"` for
    monitor sources.
- Apps available tonight: gedit 46 (GTK), gnome-terminal 3.52 (VTE),
  Firefox (X11 native), Chromium (snap, X11), Discord (Electron chat),
  LibreOffice 24.2. NOT installed: VS Code, Slack, Telegram, any XFCE
  app. Human windows were open (Brave, Discord, ZCode, Steam game
  running) — all dictation targeted my own instances; one 5-char stray
  ("TOK2.", cleaned immediately) and one 118-char take that landed in
  the focused Steam game window (transcript-only impact; see §F notes)
  were the only excursions, both from focus-placement lessons below.

## Method notes (reproducibility)

- Focus under GNOME click-to-focus ignores `wmctrl -ia` for *input*
  focus: every cell clicked a verified point inside the target window
  and asserted `xdotool getwindowfocus` before taking. Pop Shell
  auto-tiling moves windows between checks — clicks must be re-derived
  from geometry each time.
- Byte-exact readback: GTK apps via the AT-SPI Text interface
  (`Atspi.Text.get_text(node, 0, n)`, unbound form — see F3); terminals
  via the same (VTE exposes `role=terminal` text); browsers via a
  self-reporting page (JS mirrors the textarea into the window title,
  read with `wmctrl -l`). Clipboard-based readback is unreliable right
  after a paste-mode take (the restore races the copy).
- Daemon logs (per-variant): `/tmp/fv-night/logs/matrix/daemon-{c1,c3,paste}.log`,
  configs `config-*.toml`, isolated histories `history-*.jsonl` (same dir).
  These /tmp copies are the durable pointers referenced below.
- Isolated daemon variants: `c1` (context off, default insertion),
  `c2` (context on, provider auto → x11 identity), `c3` (provider
  atspi), `paste` (context off, `paste_threshold_chars=40` → the 120-char
  transcript forces the paste path).

## Matrix 4 (desktop-matrix.md, X11 sessions) — outcomes

Legend: ✓ pass, ✗ fail (reproduced defect, ledger entry filed), ◐
partial/not-achievable tonight (reason given), — app absent.

| cell | app | steps | result | evidence |
|---|---|---|---|---|
| context regression C1 | gedit | C1 | ✓ | take via F9, `recording (app=Gedit)`, typed 120 chars in 0.9–1.6 s, clipboard marker restored; no context log line (context off = byte-identical pre-P2) |
| context regression C2 | gedit | C2 | ✓ | provider auto → x11 identity; take-start hint + insertion-time identity both `Gedit`; identity-only (no role/preceding) exactly as documented |
| context regression C3 | gedit | C3 | ✗ (F1/F2/F3 below) | atspi read returns `missing` on this desktop (28 a11y apps > ReadLimits.apps=16); even with limits raised the walk picks the WRONG window (first ACTIVE-flagged = an unfocused Electron window) — context line never appears in the daemon log |
| gedit (REQ-PARITY) | gedit | C1–C3 + check 1–3 | ✓ for C1/C2/paste/long/dash; A3/A4/A6 mechanics verified out-of-band (see F3) | check 1 empty-field take lands at caret once; check 2 mid-sentence: continuation math verified (`apply_continuation("Well, test.", "mid sentence")` → `" Well, test."`; after `".\n"` → capitalized, no space); check 3: 450-char insert-text → paste, lands ONCE, clipboard restored; `--dash-check line one` → paste path (leading-dash guard), lands once |
| gnome-terminal (REQ-PARITY) | gnome-terminal | C1–C3 + check 3 | ✓ typed cells; ✗ paste cell (F4) | typed take: byte-exact via AT-SPI VTE read: `"$ Well, …old portrait."` — full transcript at prompt, ends `.` → NO autocomplete space (rule's punctuation branch); `insert-text "word ending token"` → `"word ending token "` one trailing space (word-char branch); cancel (F12) during recording discards cleanly |
| Firefox (EXTENDED) | Firefox | check 1–3 | ✓ typed; ✗ paste (F5) | typed: `insert-text` lands at caret (`V:SEEDtyped probe seven words`); paste-mode take: strategy `paste`, "verified", clipboard restored — but the textarea NEVER received the text (title still `V:SEED`) |
| Chromium (EXTENDED) | snap Chromium | check 1–3 | ✓ typed; ✗ paste (F6) | typed lands; paste-mode take inserted the PREVIOUS CLIPBOARD (my marker) instead of the dictation — classic paste-completes-after-restore race |
| VS Code (EXTENDED) | — | — | absent | not installed; Discord serves as the Electron-app data point |
| chat: Discord (EXTENDED) | Discord | check 1–3 (+A15 variant) | ✓ typed; ✗ paste (F5-class) | typed `insert-text` lands in the message draft (read back via ctrl+a/c, deleted after; Enter NEVER pressed); paste-mode take reported paste/verified, draft unchanged (silent loss); clipboard restored |
| LibreOffice Writer (EXTENDED) | LO 24.2 | check 1–3 | ◐ | take started (`app=LibreOffice`), strategy fell back paste→typed (LO never read the selection in the verify window — the documented slow-focus risk, now reproduced at the strategy level); byte-level landing NOT verifiable: LO never exposed a real document window under automation and its a11y tree was absent; second take (A15 spillover) reported paste-verified into an LO-classed window |
| XFCE row | — | — | absent | no XFCE session/apps on this machine |
| A15 focus change mid-take | gedit→terminal | A15 | ✓ | take STARTED in gedit (`recording (app=Gedit)`), focus switched to the terminal before stop (focus-at-stop verified `=terminal window`), insertion landed in the TERMINAL with terminal quirks, zero wrong-window typing, clipboard restored |
| absent insertion tools | gedit | tools stripped | ✓ | with xdotool+xclip off PATH: honest `InsertionFailure(TOOL_MISSING)`, capability detail names both tools + `sudo apt install xdotool xclip`, "text is saved in History", clipboard untouched; no silent success |
| compositor shortcuts (safe subset) | — | Super | ✓ | Super opens Activities, Escape returns; daemon grab held (`hotkey_grabbed: true`), no recording triggered, no stray insertion |
| clipboard-manager hygiene | copyq (live), clipboard-indicator (GNOME ext) | cross-cutting | ✓ copyq; ◐ indicator | live SelectionHold with hygiene markers: copyq history top-of-stack UNCHANGED, secret never stored (manual run of the integration test's exact sequence — the tier itself skipped it, see integration report); clipboard-indicator keeps history in gnome-shell memory only (no disk cache exists to inspect) |
| A13 privacy sweep | all isolated daemons | A13 | ✓ | history.jsonl rows carry only transcript + wm_class app field (Gedit/Gnome-terminal/firefox/Chromium/LibreOffice); zero preceding/selection text, zero atspi app ids |
| A11 degrade (a11y bus killed) | gedit | A11 | ✓ | at-spi2-registryd killed mid-c3-run: dbus respawned it in <1 s; daemon unaffected, next take identical (typed, 120 chars, restored) |
| A14 insertion latency | all | A14 | ✓ | stop→inserted totals 0.9–2.7 s (includes CPU transcription); insertion itself sub-second; no perceptible gap |
| A8/A10 spoken-send | terminal/non-terminal | A8–A10 | ◐ not run | requires an utterance ending in the spoken-send phrase; no TTS available at night to synthesize one. Covered by unit tests; live cell stays open for a human-voice run |

## Matrix C (wayland-smoke-matrix.md, X11 control row)

- C1 ✓ (isolated daemon, context off — byte-identical pre-P2 behavior,
  no context log line, insertion identical).
- C2 ✓ (provider auto → `x11` identity provider; identity-only, WM_CLASS
  quirks exactly as before).
- C3 ✗ blocked on this desktop by F1–F3 (not an X11-specific defect —
  the same atspi provider is the Wayland REQ-PARITY path; see findings).
  Mechanism-level verification: with the walk bypassed (locate the gedit
  app directly), the focused `role=text` object IS found on X11 and its
  caret/character-count read correctly (caret 12 after typing "mid
  sentence"); the text-interface read itself needs the F3 fix.

## Findings (ledger candidates; no product code changed tonight)

- **F1 (High, P2/context)**: `ReadLimits.apps = 16`
  (fluidvoice/context/base.py) silently truncates the desktop walk. This
  desktop runs **28 a11y-registered apps** (background helpers included),
  so the focused app (index 27) is never visited: the atspi context read
  returns `missing` with NO log line — P2 continuation/GAAV and
  insertion-time identity silently never work on busy desktops. Repro:
  `read_focus(Atspi, 120, ReadLimits(apps=16))` → missing=True; with
  `apps=64` → returns a window (the wrong one — see F2). Fix direction:
  raise the cap and/or order candidates by recency/active-window hint.
- **F2 (High, correctness)**: `_find_active_window` returns the FIRST
  window carrying STATE_ACTIVE in desktop order. Multiple apps can hold
  ACTIVE-flagged windows while unfocused (reproduced: an Electron window
  "Codex|ChatGPT" keeps ACTIVE while gedit is focused; gedit also ACTIVE
  later in the list). With F1's cap raised, the read attached to the
  WRONG app (identity=Codex, stale=True) — wrong-window identity feeds
  profiles/spoken-send routing. Fix direction: prefer the window that
  also carries FOCUSED descendants, or cross-check with the take-start
  wm_class.
- **F3 (High, GIR path)**: the atspi provider's text reads silently
  return None under GIR Atspi (python-atspi not installed in the venv):
  `Atspi.Accessible.get_text()` is a deprecated 1-arg interface getter,
  so the adapter's `_call(holder, "getText", "get_text", args=(start,
  end))` raises TypeError and is swallowed. Verified working form:
  `Atspi.Text.get_text(node, start, end)` (unbound interface call) →
  `'mid sentence'`. caret_offset works both ways. Fix direction: try the
  unbound interface call in the adapter. (A2's `context: atspi (role=
  text…)` log line therefore never appears on GIR-only installs even
  when F1/F2 were bypassed.)
- **F4 (High, paste mode in terminals)**: verified-paste into
  gnome-terminal under a clipboard manager reports "paste not verified"
  while the paste text ACTUALLY landed; the auto-fallback then TYPES the
  text again → **duplicated transcript in the terminal** (reproduced
  twice: a dictation take and a 450-char insert-text; VTE read shows the
  payload twice). Clipboard restore still correct. gedit does not
  duplicate (contrast cell).
- **F5 (High, paste mode in browsers/Electron)**: false-positive
  verification — Firefox and Discord report strategy `paste`
  ("verified"), restore the clipboard, and the field NEVER receives the
  text (silent loss; transcript only in history). The verify step counts
  any post-keystroke selection read (e.g. a TARGETS probe or
  clipboard-manager proxy read), then releases ownership; the app's
  actual content read races the release.
- **F6 (High, paste mode in Chromium)**: the paste/restore race lands
  the WRONG text: Chromium's read resolves AFTER the restore, so the
  PREVIOUS clipboard content (my marker) was inserted instead of the
  dictation. Data-corruption class for paste mode.
- **F7 (Low, observability)**: paste-fallback notices ("Paste did not
  land - typing instead") route to ui.notify only; with notifications
  disabled they vanish — the daemon log shows a bare `typed (…)` line
  with no trace of the failed paste attempt (made F4 harder to see).
- **F8 (Low, tests)**: `test_hygiene_markers_suppress_copyq_history`
  skipped as "copyq not running" during the integration tier although
  copyq IS running: its `copyq read 0` probe fails inside the pytest
  environment (isolated XDG env) while succeeding in a user shell —
  skip reason misleading; the check passed when run manually with the
  session env (see §hygiene above).
- Soak-environment note (not a product defect): a PipeWire null-sink
  suspends when idle; a recorder attaching to its monitor before the
  playback stream resumes it captures literal zeros (flaky ~1-in-10).
  A persistent feed (ffmpeg `-stream_loop -1` → pw-cat) makes virtual-
  mic testing deterministic — recorded here for future night runs.

## Production-daemon probe (read-only)

- Synthetic `Control_R` (XTEST) pressed twice with gedit focused: the
  production daemon (hotkey_grabbed=true per one read-only status probe)
  never entered recording, no history row, nothing typed. Cause not
  investigated further (out of tonight's scope; possibly XTEST vs its
  grab). The human's real-dictation path is exercised daily and its
  history shows healthy takes.
- The production config was never modified; no systemctl interaction;
  no journal entries were readable for the unit (none exist).

## What this closes / what remains

- CLOSED tonight (X11 half of Q7's desktop-matrix item): Matrix 4 rows
  for gedit/gnome-terminal/Firefox/Chromium/Discord (typed paths all
  pass; paste paths reproduce four real defects), C1/C2 of Matrix C,
  cross-cutting cells (focus change, absent tools, compositor shortcut,
  copyq hygiene, privacy sweep, a11y-bus degrade, latency).
- STILL OPEN for Q7: Matrix 1 (GNOME Wayland REQ-PARITY cells — needs a
  Wayland session), Matrix 2 (sway), Matrix 3 (KDE), VS Code (absent),
  Slack/Telegram (absent), XFCE (absent), LibreOffice byte-exact cell
  (needs a stable LO window), spoken-send live cells (needs human voice
  or TTS), and the C3 atspi row anywhere until F1–F3 are fixed.
- `requests/wayland-matrix-execution.md` stays OPEN (its Wayland/sway
  evidence is untouched; only the X11 control row advanced).
