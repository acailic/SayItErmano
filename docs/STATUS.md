# SayItErmano — Status Ledger

Last updated: 2026-09-05 · v0.5.0 · **1114 automated tests** (1077 offline + 37 integration)
· verified against upstream `altic-dev/FluidVoice` by a 5-agent audit
(prompts/AI, punctuation rules, daemon pipeline, models, security).

Companion docs: [BEHAVIOR-SPEC.md](BEHAVIOR-SPEC.md) (what upstream does,
with file:line evidence) · [ROADMAP.md](ROADMAP.md) (the forward plan) ·
[COMPARISON.md](COMPARISON.md) (vs. other Linux dictation tools) ·
[UPSTREAM-TRACKING.md](UPSTREAM-TRACKING.md) (macOS-vs-Linux capability
matrix + upstream changelog with its refresh loop).

---

## ✅ Done and verified

### Core dictation loop (live-tested on Pop!_OS X11)
- Global hotkey via XGrabKey: **toggle** mode with any keysym (modifier-only
  keys like Right Ctrl included, lock-mask variants handled) and **hold**
  (push-to-talk) for non-modifier keys with **native key passthrough**: the
  hold releases the XGrabKey activation (which by X11 semantics grabs the
  whole keyboard for the held key's press-to-release duration), so keys
  typed while holding reach the focused app as real events — no injection;
  release is detected by auto-repeat-proof query_keymap polling, a passive
  Escape grab covers cancel-during-hold, and the hotkey re-arms afterwards
  (an XTEST-replay variant was prototyped and abandoned: live Xorg 21.1
  silently drops XTEST fakes that match the current key state, so replayed
  presses never reach the app); optional second cancel key.
- Recording through PipeWire (`pw-record`) / PulseAudio (`parecord`), 16 kHz
  mono s16 WAV, configurable device; SIGINT→SIGTERM→SIGKILL stop escalation;
  stderr drained to avoid pipe blocking.
- Transcription on **faster-whisper with CUDA** (auto-falls back to CPU int8);
  torch-whisper and whisper.cpp backends; auto-selection priority; models
  tiny→large-v3-turbo (auto: small on GPU / base on CPU), background
  download + hot-swap; upstream `whisper-*` names accepted.
- Text insertion: `xdotool type` (clipboard-free) or clipboard paste with
  restore; auto-paste for long texts; leading-dash guard; clipboard fallback;
  **insertion hardening**: for the duration of a paste SayItErmano owns the
  CLIPBOARD selection (python-xlib) and serves it with clipboard-manager
  hygiene markers, the paste keystroke is verified by observing the target
  read the selection (a 0.25 s quiesce first lets the eager managers reveal
  their windows; 0.6 s cap), and only then is the previous clipboard
  restored — read-back checked, one retry, warning notification on mismatch;
  an unverified paste raises and auto-mode falls back to typed insertion
  with a notification; X11 terminals paste with `ctrl+shift+v`
  (`insertion.terminal_paste_key`, matched via the shared
  `general.terminal_apps` key — `ctrl+v` is passed through to the app in
  terminals; live matrix: gnome-terminal, kitty, alacritty, bare and under
  tmux); `insertion.verify_paste = false` restores the legacy fixed-delay
  behavior; `fluidvoice doctor` reports both keys.
  Insertion-hardening residuals (live-observed, GNOME 46 X11, CopyQ 7.1.0):
  - CopyQ suppression verified live: with `application/x-copyq-secret` /
    `application/x-copyq-hidden` advertised and served, `copyq read 0` is
    unchanged after a marker-tagged hold (the item is silently discarded);
    `x-kde-passwordManagerHint = secret` is also served for
    Klipper/GPaste/KeePassXC semantics, but this CopyQ build does NOT honor
    it (probe-verified: the monitor never fetches the atom — a
    build-without-KGuiAddons quirk).
  - The mutter/gnome-shell selection proxy (used by the enabled
    clipboard-indicator GNOME extension) reads flashed text eagerly at
    ownership change regardless of any marker — a shell-extension history
    capture remains possible; no X11-side fix short of not pasting.
  - GPaste and Klipper are untested (not running on this machine).
  - ghostty is not installed locally — `ctrl+shift+v` there is expected but
    untested; it is covered by the `general.terminal_apps` list.
  - Verify-timeout edge: an app whose own window already read the clipboard
    during the quiesce (its id is in the exclusion set) and whose paste
    lands without a fresh selection read would time out (0.6 s) and re-type
    — the documented trade-off of the read-based verify signal.
- Watchdogs: max-duration auto-stop (300 s), **first-PCM timeout** (2 s —
  muted/wrong mic fails fast), silence gate (opt-in, upstream thresholds),
  sub-1s zero padding, stale `/tmp` sweep at startup, auto-stop race guard.
- Stop/start SFX (the original GPLv3 upstream sounds), desktop notifications,
  history JSONL (5000-entry cap, efficient tail, optional audio retention
  with GB budget), `paste-last`, optional copy-to-clipboard.
- **Live streaming preview** (upstream's headline UX): SEGMENTED engine
  (2026-09-05, spec a3f7c21e) — fixed 2 s windows at 50% hop, exactly one
  decode per tick (constant cost regardless of take length — the
  re-transcribe-whole-buffer failure upstream hit as bug #833 cannot
  happen), committed text is stable while the fresh tail re-renders with
  word-overlap dedupe, preview works on ALL four backends
  (faster-whisper/whisper-torch pass rolling initial_prompt; whisper.cpp
  and parakeet get preview for the first time), and an energy+zero-crossing
  VAD auto-stops the take after 2.0 s of trailing silence once real speech
  was committed (0 disables; all-silence takes keep the first-PCM
  watchdog path). Shown in a Mac-style pill overlay (bottom-center stadium:
  live-audio waveform, streaming text, processing shimmer;
  override-redirect, no focus stealing) or a replaceable notification; the
  model is pre-warmed at daemon start. Per-take `preview stats:` log line
  (decodes/commits/mean decode ms/lag) makes the cadence measurable;
  Recorder.start()'s first-word probe contract is pinned by regression
  test (upstream #751 class). `recording.preview_segmented=false` reverts
  to the legacy whole-buffer engine.
- **Rewrite/Write mode** (`hotkey.rewrite_key`): captures the selection,
  dictates the instruction, runs the verbatim upstream edit prompts
  (context block, follow-up history, temperature 0.7), types the result.
- **Spoken-send**: trailing "send it" strips and presses Enter afterwards
  ("literal send it" escape honored); configurable phrase/key-combo.
  **Quiet countdown** (B7 parity, `recording.spoken_send_countdown_s`,
  default 1.2, 0 = off): 0.5 s of silence with the phrase at the end of
  the rolling preview arms a countdown — the pill shows "⏎ sending…",
  speech resumes cancel it, expiry finishes the take by itself (no hotkey
  press) and Enter is pressed by the existing spoken-send path; the plain
  VAD auto-stop (2 s) stays as the no-phrase path and backstop.
- **GAAV mode**: optional lowercase-first + trailing-period strip for
  search-box/casual dictation.
- **Mic priority list + input-device monitoring** (`recording.mic_priority`):
  a 3 s `pactl list short sources` diff poll notices connects/disconnects;
  when the configured microphone disappears and a priority pattern matches
  (case-insensitive substring, first pattern wins — e.g. `bluez` for a
  Bluetooth headset), SayItErmano switches and notifies. Switching never
  happens mid-dictation (the take finishes on the still-open stream, the
  fallback lands ≤ 3 s after it), `device = ""` (auto) is never overridden,
  and a working device is never preemptively upgraded. Same reselect runs
  once at daemon start (restart while the mic is disconnected → fallback
  instead of a first_pcm_timeout failure). The tray Microphone submenu is
  ordered by the same priority list; the Settings → Dictation page edits it
  (add/move/remove rows).

### Text processing (upstream-faithful, audit-verified)
- Filler removal — upstream split/trim semantics, default word list identical.
- Custom dictionary — case-insensitive, longest-first, boundaries only on
  word-char edges.
- Dictionary auto-learning from History edits (upstream v1.6.3 port) —
  inline repair stamps the pre-edit text as `edited_from` (first edit
  wins) and `processing/dict_learn.py` diffs it against the final text:
  token-level difflib with upstream's shape checks (≤3 words/side, ≤40
  chars/side, ≤70 combined, ≥2 letters/side, purely alphabetic trimmed
  tokens, filler-free, trigger-already-saved suppression —
  Tracker:215-241/676-705); a pair is suggested after 2 occurrences
  (upstream `requiredOccurrences`; counts derive from the history itself
  — one entry = one correction event). Suggest-only Accept/Dismiss rows
  in Settings → Dictation "Suggested words" (nothing enters the
  dictionary without a click — the macOS model; the Windows port
  silently auto-adds); Accept merges through the validated save path
  without duplicate triggers, Dismiss permanent; decisions at
  `~/.config/sayit-ermano/dictionary-suggestions.json`; doctor prints
  the pending count. Divergences in the table below.
- Spoken punctuation (`literal comma`, …) — **matches upstream's LIVE rule
  table**: all 108 aliases (incl. parentheses/curly/angle/quote variants,
  `plus`, `equal`, `equals`), `double quote` toggling, longest-alias-first
  matching (`dot dot dot` beats `dot`), upstream spacing semantics per symbol,
  and the real cleanup passes (comma sandwiched between symbols; comma before
  `%` after a digit; original-text trailing period before formatting actions).
  *Fidelity note: upstream's dot/slash/at-sign "context gates" are dead code —
  the shipping app applies rules unconditionally; so do we.*

### AI polish (audit-verified byte-identical prompts)
- All five upstream prompts copied **byte-identical** (verified with Swift
  multiline semantics).
- One-user-message folding with `${transcript}` placeholder support.
- Request params faithful: temperature omitted for reasoning/claude-5-family
  models, `reasoning_effort` (gpt-5*/o1/o3/o4/gpt-oss) and `enable_thinking`
  (nemotron/deepseek-reasoner), OpenAI **Responses API** support,
  think-tag stripping with the opening-tag guard, empty-response error.
- Works with any OpenAI-compatible endpoint (OpenAI/Groq/Ollama/LM Studio/
  llama.cpp); live-tested against local Ollama.
- Error behavior: AI failure falls back to raw transcript + notification.
- Custom base prompt: `ai.base_prompt` (empty = the built-in dictation
  prompt) feeds both the AI client and the per-app compose; Settings → AI
  edits it and manages named presets in a sidecar `prompt-profiles.json`
  (0600 atomic writes; loading copies text into the editor — config.toml
  stays the single source of truth; a malformed file degrades to an empty
  list with one warning, never a crash).

### Native GTK app (`fluidvoice app` / `fluidvoice settings`)
- GTK 4 + libadwaita, single instance with remote window raising
  (`--open history|settings`, `--onboard`); follows the system theme.
- **History window** (macOS main-window counterpart): live status header
  (state/backend/GPU/model + warmup), search, copy/delete, inline audio
  replay (GtkMediaFile, xdg-open fallback), clear-all, daemon-down banner.
- **Settings window**: every validated key across General / Models /
  AI Polish (+ per-app prompt rules) / Dictation (hotkeys with press-to-
  capture, mic picker, preview, spoken send, GAAV, insertion) / History /
  About; dirty tracking, Ctrl+S, close-with-changes confirm; saves go over
  the control socket and hot-apply (hotkeys re-grab, recorder/tray/model
  rebuild); file-only mode when the daemon is down.
- **whisper.cpp GGUF manager** (Settings → Models): curated catalog of the
  7 `ggerganov/whisper.cpp` ggml models (base…large-v3, multilingual +
  English-only) with streaming one-click download (progress subtitle,
  worker thread + GLib polling, `.part` + atomic rename, no half-written
  files); "Use" switches the backend via validated `set-config`
  (`model.whispercpp_model` accepts a catalog name **or** a path, with
  clear missing/unknown errors); `fluidvoice doctor` reports the binary,
  the resolved model and what's downloaded.
- **Parakeet (ONNX) manager** (Settings → Models): the two curated
  Parakeet TDT exports (v2/v3) with the same download/Use flow (checksum-
  verified atomic model dir); selecting it sets
  `model.backend = "parakeet"` + `model.name` (an engine key — hot-swaps
  the loaded model like `select-model`); doctor reports onnxruntime,
  providers and per-model download state.
- **Prompt profiles + base prompt** (Settings → AI): multi-line base-prompt
  editor (empty = built-in, one-click "Insert built-in" seed) and a
  profile bar above it — Save/Rename/Delete of named presets in
  `prompt-profiles.json` (delete is confirmation-gated).
- **Per-model language + disk usage** (Settings → Models): one language
  picker per downloaded model (inherit / auto / code; `model.languages`),
  and a disk-usage group listing every cached model under
  `~/.cache/sayit-ermano/models` with per-entry sizes, the total, and a
  Delete button that goes through the socket-only `model-delete` action
  (the active model is disabled with a tooltip; the GTK app never deletes
  files directly, not even in daemon-offline mode).
- Replaces the retired web UI (spec: docs/superpowers/specs/
  2026-09-02-native-settings-app-design.md) - no TCP listener remains;
  the localhost CSRF/DNS-rebinding surface is gone by construction.
  Validation lives in config.apply_settings (one source of truth), config
  is written 0600 atomically, secrets masked in get-config.

### Infrastructure
- CLI: `daemon / toggle / cancel / status / paste-last / transcribe (multi-format
  + --json/--out) / history
  / config / settings / doctor`; unix-socket control protocol.
- systemd user unit (DISPLAY/XAUTHORITY aware, tied to graphical session);
  installer generates it with real paths and enables it.
- Python 3.11+, GPLv3, published as the `linux` branch of the fork
  `acailic/SayItErmano`.
- Hotkey-grab self-healing (P1, `docs/research/2026-09-04-product-proposals.md`):
  every listener grab carries a per-request python-xlib `onerror` (a truthy
  return suppresses the printing default handler — BadAccess never raises
  through `grab_key`), so a refused combo (stale deb autostart, WM rebind,
  any second grab holder) becomes per-combo data, not stderr noise; the
  poll loop re-attempts missing combos every ~10 ms tick (zero X traffic
  when healthy, WARN-capped), startup logs WARN + desktop notification
  when refused, and health is surfaced in `status` (`hotkey_grabbed`),
  the tray tooltip (` - hotkey blocked!`, live-refreshed on flip) and
  `doctor` (ok / BLOCKED / disabled / unknown). Live-verified 2026-09-04:
  deliberate conflicting holder of all 8 F9 lock-mask combos → daemon
  WARNed + `hotkey_grabbed:false`, and within one tick of the holder
  closing its connection the grab was re-taken, `status` flipped true and
  a synthetic F9 press toggled recording — no restart
  (`tests/integration/test_live_x11.py::TestHotkeyGrabRecovery`).
- **Mouse-button push-to-talk** (`recording.push_to_talk_button`, e.g.
  `"button8"`; buttons 6–255, click/scroll buttons 1–5 refused by
  validation, optional `push_to_talk_modifiers`): a spare mouse button
  held = dictation, released = stop & transcribe; CLICKS during the hold
  reach the window under the pointer as real events. Mechanism
  (hotkey.MousePTTListener, the pointer twin of the keyboard hold):
  XGrabButton passive grabs on all 8 lock-mask combos
  (owner_events=False, GrabModeAsync, refusals as data + ~10 ms retry —
  the keyboard pattern); the press activation is released with
  ungrab_pointer so clicks pass through natively, and the passive grab
  SURVIVES it (buttons have no auto-repeat, so no re-arm dance unlike
  keys); the release is detected from XI2 RawButtonRelease events on all
  master pointers, parsed from python-xlib's GenericEvent bytes; a
  passive Escape grab covers cancel-during-hold. Live-verified on Xorg
  21.1: mousedown 8 → recording; a native button-1 click reached the
  receiver window mid-hold; mouseup 8 → stop & transcribe; Escape
  mid-hold → cancel; a conflicting holder blocked the arm (status
  `mouse_ptt_grabbed:false` + WARN) and releasing it re-armed within a
  tick. Doctor reports the resolution + live arm state; `status` exposes
  `mouse_ptt_grabbed`.
- **Lock suppression** (`general.pause_when_locked`, default true;
  fluidvoice/lockmon.py): while the session is locked or suspended the
  daemon ignores every hotkey entry (keyboard, mouse PTT, tray click,
  socket `toggle`/rewrite/command starts), cancels an active dictation
  through the existing cancel path (watchdog off, discard, notify),
  cancels a pending command proposal, and the tray tooltip notes
  `paused (locked)`; `cancel` and the rest of the socket surface stay
  available. Sources (all additive, transitions deduped): logind session
  Lock/Unlock signals, LockedHint PropertiesChanged (GNOME's path — no
  screensaver D-Bus name is owned there, verified live), Manager
  PrepareForSleep (suspend counts as locked),
  org.freedesktop/org.gnome ScreenSaver ActiveChanged where a DE owns
  the names, plus a 5 s LockedHint reconcile poll. Session resolution:
  validated `$XDG_SESSION_ID` → `GetSessionByPID(own pid)` → (the fix
  for daemons under the systemd USER unit, which live in user.slice
  with no session scope) `ListSessions` picking the same-UID active
  graphical user session (active > online > FIFO, x11/wayland only —
  `NoSessionForPID` is swallowed quietly); manager signals wire first
  (PrepareForSleep + SessionRemoved/SessionNew), so no graphical
  session at all = sleep-only mode (suspend still gates, start() True)
  and a logout/login swap re-resolves the watched session. Without
  D-Bus/logind the watch is off with one WARN. `status` exposes
  `lock_watch` (mode/session/via) and doctor reports the watched
  session. Live-verified on the daily-driver user-unit daemon
  (2026-09-05, `systemctl --user` start): the old
  `NoSessionForPID`/headless WARN pair is gone —
  `lock watch: session _34 via ListSessions (active, x11, uid 1000)`,
  doctor `lock watch: ok (watching session _34 via ListSessions)`, and
  the real `loginctl lock-session`/`unlock-session` cycle logged
  `screen locked - hotkeys paused` / `screen unlocked - hotkeys
  resumed` with `lock_watch.locked` following; the transition state
  machine is unit-pinned (mocked-bus ListSessions fallback included)
  and the lock flow has a documented manual check (below).

---

## ⚠️ Intentional divergences (documented decisions)

| Divergence | Why |
|---|---|
| AI refusal guardrail (`ai.refusal_guard`, `fluidvoice/processing/refusal.py`): a polish/rewrite reply that reads as an LLM refusal is never typed — the raw transcript is used instead, with a notification; rewrite refusals surface as ordinary rewrite failures | upstream has no guard and its closed model once pasted "I'm sorry, I can't assist with that." into a document (research insight 9); port addition. English patterns only in v1; a dictation that verbatim opens like a refusal falls back to raw text (never data loss, always explained) |
| Remote OpenAI-compatible STT backend (`model.remote_url`, `fluidvoice/backends/remote_stt.py`): while a URL is configured, each dictation POSTs the recorded WAV to `<url>/v1/audio/transcriptions` (any vLLM/whisper.cpp-server/NIM/DGX-Spark/cloud endpoint) and that backend wins over every local choice; Settings → Models → Remote edits it | upstream declined the community's opt-in PR in favor of a native protocol (research insight 12) — LAN GPU boxes are a real upstream user ask, so this is a deliberate differentiator; local-first: an empty URL means the backend never constructs and no network happens (test-enforced), no live preview/VAD for remote takes in v1 |
| 429/5xx HTTP responses are retried (upstream never retries HTTP errors) | resilience for rate-limited local/remote endpoints |
| Thinking-only model answers fall back to the raw transcript (upstream types the raw content) | never type `<think>` junk |
| AI timeout 120 s (upstream: 30 s streaming / 120 s non-streaming) | we are non-streaming; big local models are slow |
| `max_seconds` cap (upstream: none) | runaway-recording safety; configurable |
| Hold mode passes typed keys through natively but they do NOT end the dictation (upstream clean-tap: other keys interrupt the trigger); the held hotkey's auto-repeat pairs also reach the app | deliberate "keep typing while holding"; X11 has no per-event passthrough under an active grab — releasing the grab entirely is the only clean mechanism (live-verified) |
| No telemetry at all (upstream has opt-in analytics) | privacy-first choice |
| D1: case-only corrections are dictionary candidates (upstream rejects them, `AutomaticDictionaryCorrectionTracker.swift:111`/`:216-227`, test "fluidvoice"→"FluidVoice"→nil) | the canonical documented use of this dictionary is exactly `["miro board"] → "Miro board"` and the engine matches triggers case-insensitively, so a learned case entry is fully functional; threshold-2 + suggest-only + permanent dismiss bound the risk |
| D2: learning signal = History inline repair (`history.update_text` `edited_from`), not a live accessibility observer on the edited field (upstream `TypingService.swift:525-532` kAXValueChangedNotification) | no AT-SPI text-field observation on Linux yet (roadmap keeps it later); v1 scope: no re-dictation, no external editors |
| D3: suggestions surface as a persistent Settings list ("Suggested words"), not a typing-time 5 s overlay (upstream `AutomaticDictionaryCorrectionOverlay.swift`) | no D2 signal at typing time; a passive list needs no interruption ⇒ none of upstream's cooldowns/session-ignores; Settings is where the dictionary lives |
| D4: dismissal is permanent, not upstream's 7-day dismissed-pair cooldown with max-3 dismissals (Tracker:236-237) | the brief mandates "never resuggested"; simpler and stricter |
| D5: counts persist with no 7-day occurrence window (Tracker:234-235) and derive from the history itself, not a stored state file | the 5000-entry history cap bounds them; the store records decisions only (dismissed/accepted) |
| D6: token-level difflib over the whole edit, not upstream's anchored in-range character diff expanded to token boundaries | our edit boundary is one whole History entry (a single edit event), so anchoring is trivially satisfied |
| D7: no config toggle (upstream `automaticDictionaryLearningEnabled`, default on) | upstream's toggle gates an interruptive overlay; a passive list that only records what the user already typed needs no gate — `history.save = false` disables the signal at the source |
| D1 corollary: suggest-only, unlike the Windows port which silently auto-adds (windows-v0.0.8: "FluidVoice adds it to your custom dictionary, with a card to undo") | silent dictionary growth degrades trust (research §5) — nothing enters without an explicit Accept |
| C1: EVERY command needs the hotkey confirm; upstream auto-executes non-destructive commands when its confirm setting is on (`CommandModeService.swift:439-456`: destructive → `PendingCommand`, non-destructive → run) | voice → shell is the highest-blast-radius path in the port; one uniform gate beats two mental models, and the request's safety brief mandates confirm-first |
| C2: destructive commands need a TWO-press strong confirm (armed state, amber ⚠ pill, refreshed hint, restarted watchdog); upstream has no stronger step beyond the ordinary confirm | port addition (no upstream equivalent): a mis-heard `rm -rf` under a single stray keypress is the worst failure mode command mode has |
| C3: follow-up context is in-memory, per focused app, last-5 results within a `command.context_window_s` (300 s) window, cleared by the spoken phrase "new session"; upstream persists a 30-chat global store in UserDefaults and replays the whole conversation (`ChatHistoryStore.swift:93-110`, `:261-266`, `CommandModeService.swift:790+`) | per the v2 request: voice runs stay cheap to reason about, a daemon restart starts cold, nothing about shell usage is persisted beyond the history rows that already exist |
| C4: the tool schema travels in our strict-JSON text protocol (`{"tool_calls": [...]}` replies); upstream sends a native OpenAI `tools` array with `tool_choice: "auto"` (`CommandModeService.swift:868`, `LLMClient.swift:329-333`) | keeps the single transport every other mode uses (works with any chat-completions endpoint incl. local Ollama without tool-calling support); the schema SHAPE is ported, the wire format is not |
| C5: a reply may propose a SET of tool calls, all presented sequentially and each individually confirmed before the next executes; upstream parses multi-call arrays but consumes `toolCalls.first` alone (`CommandModeService.swift:953-954`) and silently drops undecodable calls (`LLMClient.swift:847-865`) — we reject undecodable calls loudly (raw text shown, run cancelled) | one voice run = one confirmed command set (no autonomous chaining), and a half-parsed proposal must never look like success |
| C6: an empty/non-string `command` argument is a parse error; upstream tolerates it (`getString("command") ?? ""` would run `zsh -c ""`, `CommandModeService.swift:955`) | executing an empty shell is always a protocol failure; failing loudly at parse time is strictly safer |
| Mouse PTT release detection is XI2 raw-event-driven, not button-state polling | core XQueryPointer's CARD16 mask only carries buttons 1–5 — the canonical thumb buttons (8/9) are invisible to it; XI2 RawButtonRelease is grab-independent and non-consuming (needs XI ≥ 2.1, negotiated as 2.2 directly because python-xlib hardcodes 2.0 and live Xorg then withholds release events — setup refuses to start below 2.1 rather than never fire) |
| Mouse PTT buttons 1–5 refused outright (config validation) | a primary/wheel button PTT would swallow every click/scroll while armed — breaking the desktop; the doctor/WARN surfaces explain it |
| Suspend is treated as locked (PrepareForSleep flips the same gate) | a suspended screen with a live dictation is exactly the bug pause_when_locked fixes |
| GNOME lock detection goes through the logind LockedHint property, not the screensaver D-Bus name | on this GNOME neither org.freedesktop.ScreenSaver nor org.gnome.ScreenSaver is ever owned — ActiveChanged alone would miss every lock; the screensaver sources remain as fallbacks where a DE owns the names |
| Lock latency = signal + ≤ 5 s reconcile | signals are instant for logind-locking DEs and GNOME; a pathological DE could lag to the poll — the recording-under-locked-screen bug is still fixed |
| A pointer vanishing mid-hold (USB unplug) ends the take via the max_seconds watchdog, not instantly | no raw release ever fires; the listener stays healthy and re-arms for the next press |

---

## 🚧 Left (see ROADMAP.md for details and upstream references)

### Near term — daily-driver polish (v0.2)
- [x] **Rewrite/Write mode** — DONE (the Done-section entry above): the
      `hotkey.rewrite_key` capture → verbatim upstream edit prompts →
      retype loop, X11 selection capture + the Wayland tool-based path,
      confidence badge. This bullet predates the implementation.
- [x] **Hold-mode key passthrough** — DONE: keys typed during a push-to-talk
      hold reach the focused app as REAL events (the XGrabKey activation is
      released for the hold's duration; release detected via auto-repeat-proof
      query_keymap polling; passive Escape grab keeps cancel-during-hold;
      hotkey re-armed after). An XTEST ungrab→inject→re-grab replay design
      was prototyped and rejected: live Xorg 21.1 drops XTEST fakes that
      match the current key state, so replayed presses are deduped away.
      Remaining divergence (deliberate): typed keys do not end the dictation
      (upstream clean-tap interrupts), and the held hotkey's auto-repeats
      reach the app.
- [x] **Per-model language selection** — DONE: one flat `model.languages`
      dict (`{model_key: code}` across all three catalogs) instead of
      upstream's separate whisper/cohere/nemotron per-store pickers;
      missing key / `""` inherits `general.language`, `"auto"` forces
      detection for that model (upstream "automatic preserved"). Resolution
      lives in one `backends.effective_language(cfg, backend)` helper wired
      into all four language call sites (pipeline, live preview,
      test-dictation, `transcribe` CLI); applies live (not an engine key).
      Parakeet v2 (English-only) records but cannot enforce a code; the
      Settings → Models picker skips it. Divergence from upstream: flat
      dict vs per-store pickers.
- [x] **Runtime language cycle + wrong-language guard** — DONE: upstream
      promised runtime language switching in #506 (unshipped since
      2026-04) and closed #100 with "Parakeet doesn't allow language
      selection"; this ships the leapfrog. `hotkey.language_key` steps
      `general.language_cycle` (ordered codes, may include `auto`) through
      a RUNTIME daemon override — never persisted, sticky across takes
      until cycled away or the daemon restarts; precedence per take:
      cycle > `model.languages[model_key]` > `general.language`. Every
      press announces (pill badge / notification) and the tray tooltip +
      `status`/doctor report the effective source; `sayit-ermano
      language` is the socket/wayland path. The guard: when the effective
      language is `auto` and `general.language_whitelist` is set, a
      detected language outside the list re-decodes ONCE with
      `whitelist[0]` (faster-whisper + whisper-torch surface the detected
      language; whisper.cpp does not under auto — silent skip, a noted
      limitation; parakeet English-only — not applicable, same rationale
      as upstream's #100 closure).

### Wayland parity (v0.3)
- [x] **Shipped (2026-09)** — the daemon is genuinely useful on a Wayland
      session; X11 behavior is byte-identical (the port is strictly
      additive; every wayland branch is gated ONLY on the session probe:
      `XDG_SESSION_TYPE` > `WAYLAND_DISPLAY` > `DISPLAY` > unknown-as-x11,
      `fluidvoice/session.py`).
      - **Session + capability matrix**: daemon startup logs one
        `session: wayland (gnome) - capabilities: …` line; `status`
        carries additive `session`/`capabilities` keys; `doctor` prints
        the per-capability matrix with per-tool found/missing and flips
        its exit code only when insertion is `unavailable`; Settings →
        Wayland shows the same resolution.
      - **Insertion**: `wtype` (auto resolution skips it on GNOME — no
        zwp virtual-keyboard protocol) or `ydotool` (any compositor;
        needs `ydotoold` + `/dev/uinput`; spec→code table with loud
        errors for unmapped keys), `insertion.wayland_tool` =
        `auto|wtype|ydotool`. Typed insertion is the default (no
        clipboard flash), matching xdotool semantics; paste mode uses
        wl-clipboard with snapshot + restore of the original mime type.
        Degradation ladder: tool+typed → tool+wl-paste → wl-copy +
        "paste manually" notice (`clipboard-fallback`) → InsertError.
      - **Hotkey**: no global grabs exist — the daemon writes a bindable
        `~/.local/share/sayit-ermano/bin/sayit-ermano-toggle` script and
        prints per-DE bind steps (GNOME/KDE/COSMIC/generic) in doctor and
        Settings → Wayland (copy button + open-DE-panel button on
        GNOME/KDE). Optional **evdev push-to-talk**
        (`hotkey.wayland_evdev`, default off): hold a physical key read
        from `/dev/input` — a PRIVILEGED path (input group +
        `pip install 'sayit-ermano[wayland]'`), never fatal when absent.
      - **Overlay**: the notification preview IS the wayland preview
        (FluidOverlay's existing notify fallback); the X11 pill is not
        possible on GNOME-Wayland in v1 and a wlroots layer-shell pill
        is future work (out of scope).
      - **Rewrite selection capture** works via tool-ctrl+c + wl-paste/
        wl-copy restore; spoken-send/paste-last keys route through the
        resolved tool; `copy_to_clipboard`/`clipboard_fallback` use
        wl-copy on wayland sessions.
      - **Divergences (deliberate)**: (1) paste verification degrades to
        a fixed settle (`WAYLAND_PASTE_SETTLE_S = 0.45`) — cross-client
        selection-read observation, the core of the X11 verified paste,
        is impossible on Wayland; (2) no clipboard-manager hygiene
        markers while flashing the clipboard — wayland clipboard managers
        will see the dictation; (3) no app hints (no WM_CLASS equivalent;
        AT-SPI future work), so `terminal_apps` quirks (ctrl+shift+v,
        autocomplete space, spoken-send Enter blocklist) are inert.
      - **Deviation from the task text (documented)**: a literal KDE
        shortcut-file import was rejected — Plasma 6 custom-command
        shortcuts live in `kglobalshortcutsrc` under kglobalacceld with
        no supported import format; writing it blind is fragile. The
        bindable script + open-panel button + per-DE instructions
        deliver the same outcome honestly.
- [ ] **Live smoke checklist** (run per compositor, priority GNOME-Wayland
      then sway; note results here as they land — unit coverage is
      complete in `tests/test_wayland_capabilities.py`, these are the
      live-verification items):
      1. Baseline, no tools installed: daemon starts in the foreground
         AND under the installed systemd user unit (the unit bakes
         `Environment=DISPLAY` — confirm `XDG_SESSION_TYPE`/
         `WAYLAND_DISPLAY` reached `systemctl --user show-environment`;
         the probe tolerates a stale DISPLAY, the unit comment says so).
         Tray/socket/`status` alive; a dictation transcribes and lands in
         history; the "no insertion tool" notification appears.
      2. sway + wtype: typed insertion into a terminal and an editor;
         spoken-send Enter; paste-last; paste mode via wl-clipboard —
         verify the pre-paste clipboard is restored after both paths;
         leading-dash text takes the paste path.
      3. GNOME + ydotool (ydotoold running, uinput perms): same set; note
         whether key-duration tuning is needed (fix the central
         `_ydotool_*` builders only).
      4. Overlay: recording shows the notification preview; no X11-pill
         attempt noise in the log.
      5. Doctor on the live session: matrix + per-tool found/missing
         correct; exit 0 with insertion resolved, non-zero without.
      6. Settings → Wayland: renders, Copy yields a working script; bind
         it in the DE; toggle dictation via the shortcut.
      7. evdev push-to-talk (if input-group access): hold-to-talk works;
         note the device-name match.
      8. X11 regression, same build: full manual pass (hotkey grab, pill
         preview, verified paste, spoken-send, rewrite) — zero deltas.

### Models (v0.4)
- [x] **Parakeet TDT v2/v3 via ONNX** — DONE: curated sherpa-onnx tarball
c      catalog (v2 English / v3 multilingual, int8) with sha256-verified
      multi-file download (tarball + per-file checksums, atomic model dir,
      streamed extraction — never extractall), pure-numpy log-mel
      featurizer + greedy TDT decode over ONNX Runtime (CUDA execution
      provider picked up automatically when the installed wheel has it);
      Settings → Models "Parakeet (ONNX)" group with download progress +
      Use, doctor resolution report, `parakeet` pip extra.
      *Divergence (deliberate): `backend = "auto"` still prefers the
      whisper family — upstream runs Parakeet as its default; Parakeet is
      explicit-selection-only here.* Default model is v2 (upstream defaults
      to v3).
- [ ] Parakeet Realtime / Nemotron 3.5 streaming (NeMo/Riva) — unlocks real
      streaming preview.
- [x] whisper.cpp GGUF auto-download + model manager — DONE: curated GGUF
      catalog with one-click streaming download (progress, atomic rename),
      name-or-path `model.whispercpp_model`, "Use" hot-swaps the backend,
      doctor resolution report.

### Later
- [x] Command mode **v2 shipped**: upstream tool schema in the strict-JSON
      `tool_calls` protocol (per-arg validation, one-command-per-call sets,
      all confirmed sequentially), the destructive-command list ported
      verbatim + `command.destructive_patterns` user additions behind a
      two-press strong confirm, per-app follow-up context (last 5 results,
      300 s window, spoken "new session" clear), History Commands view with
      collapsible output + Copy + confirm-gated Re-run, doctor line. (Native
      `tool_calls` wire format and persistent chat sessions across daemon
      restarts stay upstream-only — see the C1-C6 divergences.)
- [ ] GAAV + continuous-dictation formatting (needs caret text via AT-SPI).
- [x] Slash-command/mention literal formatting + terminal autocomplete
      spacing — DONE: literal squeeze (`processing.slash_mention_squeeze`)
      AND the spoken forms (`slash fix`/`at sign`/`tag`, bc1ced3);
      terminal trailing space `insertion.terminal_autocomplete_space`
      keyed on the shared `general.terminal_apps` list.
- [x] **Insertion hardening** — DONE: paste verify-then-restore (selection
      ownership + read observation before the clipboard restore, unverified
      pastes fall back to typed insertion with a notification),
      clipboard-manager hygiene markers (CopyQ 7.1.0 live-verified; the
      GNOME-shell-extension residual and the full live evidence are recorded
      in the Done section above), terminal `ctrl+shift+v` paste key
      (`insertion.terminal_paste_key`, shared `general.terminal_apps` key),
      `insertion.verify_paste` toggle + doctor lines. The AT-SPI insertion
      fallback stays open (grouped with the AT-SPI work above).
- [x] Input-device monitoring / Bluetooth auto-switch — DONE: mic priority
      list (`recording.mic_priority`, tray + settings editor) + pactl source
      monitoring (3 s diff poll) with vanished-device auto-switch (bluez
      pattern example in README); never mid-take, auto never overridden.
      MPRIS media pause shipped earlier.
- [x] Updater + packaging (deb / AUR / pipx / one-shot installer) — DONE:
      update check + `sayit-ermano update` assisted upgrade (v0.6.0),
      deb releases, AUR and pipx install paths (README). Still open from
      this line: a nix flake; the local scriptable API landed as the
      **unix-socket routes** (`transcribe` through the warm model +
      `history` query — TCP/HTTP stays a locked non-goal).

### Non-goals
- Cohere Transcribe (CoreML-only artifacts, no Linux runtime).
- Bundling a closed-source "Fluid Intelligence" (use any local LLM server).
- macOS support. Telemetry.

---

## Test & verification status

| Area | Verification |
|---|---|
| Processing engines (punctuation/fillers/dictionary) | 60+ unit tests incl. upstream-fidelity cases |
| AI client (params/endpoints/think-strip/retries) | unit tests with mocked transport + live Ollama session |
| Daemon state machine & pipeline | stub-based tests (toggle/cancel/busy/watchdogs/races) |
| Command mode (JSON protocol, agent loop, run_shell, daemon confirm/cancel/timeout) | stub-AIClient unit + integration-style daemon tests (pill overlay, Escape, history file) |
| Socket config actions (get/set-config, select-model) + apply_settings | unit (fake backend factory) + real-daemon socket integration |
| Mic monitoring (pactl poll/diff/priority matching, daemon auto-switch, tray ordering) | unit (fake pactl runner, stub recorder daemon) |
| Recorder / insertion / history / backends | stub or subprocess-mock tests |
| Hotkey grab self-healing (error routing, retry state machine, warn cap, status/tooltip/notify/doctor surfaces) | fake-Display unit tests (no X server) + live X11 conflicting-holder recovery (blocked → WARN → release → re-take → F9 fires) |
| Mouse push-to-talk (button parsing, XI gate, grab routing, hold-cycle state machine, daemon wiring, doctor lines) | fake-X unit tests (no X server) + live X11: arm/hold with native click passthrough/release-transcribe/Escape-cancel/blocked-arm recovery (`test_live_x11.py::TestMousePTTLive`, desktop-marked) |
| Lock suppression (lockmon dedup/sources/session resolution chain incl. the ListSessions fallback for user-slice daemons, sleep-only mode, re-resolve on session close, daemon gate: toggle ignored, recording cancelled, pending command cancelled, log-once, pause_when_locked flip, lock_watch status + doctor line) | unit state machine (handlers driven directly, no bus) + mocked-bus fallback/re-resolve/run tests + live monitor start/reconcile against the real logind session (both session-scoped and user-unit launches) |
| Manual lock check (the live lock flow cannot be exercised by CI — locking the session locks the operator's desktop) | with a running daemon and `push_to_talk_button = "button8"`: 1) start a dictation, 2) lock the session (Super+L or `loginctl lock-session`) → log shows `screen locked - hotkeys paused`, the recording is cancelled ("Cancelled" notification), tray tooltip reads `… - paused (locked)`; 3) press the dictation hotkey while locked → nothing happens; 4) unlock → `screen unlocked - hotkeys resumed`, dictation works again |
| End-to-end speech | JFK sample through GPU transcription (pytest `-m slow`) |
| Live hardware loop | mic→GPU transcription via speaker playback; hotkey grab on X11; acoustic JFK transcription verified verbatim |
