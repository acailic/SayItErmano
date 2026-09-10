# Domain glossary

One tight definition per term that actually appears in code, UI, config or
docs — plus the module where it lives. Companion docs:
[STATUS.md](STATUS.md) (shipped behavior) · [ROADMAP.md](ROADMAP.md)
(future work) · [adr/](adr/) (decisions).

**take** — One dictation utterance, from hotkey press (or socket
`toggle`/`rewrite`/`command` start) to stop: capture → transcribe →
post-process → optional polish → insert → history row. Watchdogs and
state are per-take (max-duration, first-PCM timeout, stall timer, VAD
auto-stop); the daemon guarantees exactly one active take (`busy`).
Lives in `fluidvoice/pipeline.py` (`DictationPipeline`) with lifecycle
in `fluidvoice/daemon.py`.

**preview** — Live streaming transcription shown while the take is still
recording. The SEGMENTED engine transcribes fixed 2 s windows at 50 %
hop with one decode per tick (constant cost — upstream bug #833's
re-transcribe-whole-buffer failure cannot happen), keeps committed text
stable and re-renders only the fresh tail. Rendered by the Mac-style pill
overlay (`fluidvoice/overlay.py`) or a replaceable notification.
Lives in `fluidvoice/preview.py`.

**transcript** — The raw text (and, where the backend exposes it, segment
metadata: language, timing, mean log-prob) returned by decoding a take's
audio, before post-processing and polish. Feeds both the preview display
and the final insert path. Backends return it; `DictationPipeline`
threads it through `post_process` and optional AI polish.
Lives in `fluidvoice/backends/` (per adapter) and `fluidvoice/pipeline.py`.

**polish** — Optional AI cleanup of a transcript (punctuation, casing,
formatting) through the configured OpenAI-compatible endpoint
(`ai.base_prompt` etc.), distinct from the deterministic processing
engines (fillers, dictionary, spoken punctuation) which always run.
AI failure falls back to the raw transcript; the refusal/prompt-leak/
over-correction guards never let model junk reach the insert.
Lives in `fluidvoice/pipeline.py` (`_polish`) + `fluidvoice/ai/`.

**insertion** — Delivering final text into the focused application:
typed keystrokes (xdotool / wtype / ydotool) or clipboard paste with
verify-then-restore and clipboard-manager hygiene markers; terminal
quirks (`ctrl+shift+v`, autocomplete space) keyed on
`general.terminal_apps`. Wayland branches are taken only via the session
probe. Lives in `fluidvoice/insertion.py`.

**history entry** — One JSONL row in `~/.local/share/sayit-ermano/
history.jsonl` recording a take (text, mode, timestamps, confidence
band, command fields for command mode, optional retained-audio path,
`edited_from` when inline-repaired). Capped at 5000 entries; reads use a
tail window; all mutations are flock-serialized transactions
(ADR-0002). Lives in `fluidvoice/history.py` (`HistoryStore`).

**command proposal** — In command mode, one shell command the LLM wants
to run, shown in the pill in an awaiting-confirmation state. Nothing
ever executes without the confirm press (`CommandSession.confirm()` is
the only execution site, per its contract docstring);
destructive-pattern matches demand a two-press strong confirm. A reply
may carry a set of proposals, each individually confirmed. Lives in
`fluidvoice/command.py`.

**speech backend** — A pluggable STT engine behind one `transcribe()`
shape: faster-whisper, whisper-torch, whisper.cpp, parakeet (ONNX) and
remote (OpenAI-compatible endpoint; outbound only, never a listener).
`model.backend = "auto"` picks by availability; the remote URL, while
set, wins over every local choice. Lives in `fluidvoice/backends/`.

**context provider** — The P2 seam behind one insertion-time read:
`read_focus_context()` returns a `FocusContext` (app identity,
accessible role, selection, bounded preceding text, stale/missing
flags). X11 adapter (WM_CLASS identity only) and AT-SPI adapter (lazy
import, bounded tree walk, identity fallback marked stale); the
factory hands the pipeline a one-shot callable, never a handle.
Read ONCE immediately before insertion; nothing persisted anywhere
(off by default until the smoke matrices pass). Lives in
`fluidvoice/context/`.

**behavior profile** — Per-app dictation policy in `[profiles] rules`:
`match` patterns → `terminal`, `prompt_profile`/`instructions`,
`insertion_mode`, `formatting_mode` (gaav), `spoken_send`
(on/off). Canonical rules win over the legacy `terminal_apps` /
`per_app_prompts` keys, which remain read as fallback until v1.0 and
migrate into rules on the first settings save. Lives in
`fluidvoice/profiles.py`.

**PTT (push-to-talk)** — Hold-style activation: press starts the take,
release stops and transcribes. Keyboard holds release the XGrabKey
activation so other keys pass through natively; mouse PTT
(`recording.push_to_talk_button`) uses XGrabButton + XI2 raw release
events; Wayland has an optional privileged evdev listener. Lives in
`fluidvoice/hotkey.py` and `fluidvoice/evdev_ptt.py`.

**VAD** — Voice-activity detection used to auto-stop a take: the
segmented preview engine counts trailing silence (energy RMS +
zero-crossing-rate frames) and finishes the take after ~2 s of silence
once real speech was committed. Config: `recording.preview_vad_silence_s`
(0 disables). Lives in `fluidvoice/preview.py`.

**stall** — A frozen capture stream mid-take (pipe writer died, device
hiccup). The stall watchdog (`recording.stall_timeout_s`, default 8 s,
0 off; upstream #852) cancels the take with a clear error instead of
recording air until max-duration. Lives in `fluidvoice/daemon.py`.

**retained audio** — The take's WAV kept beside the history when
`history.save_audio` is on, named collision-proof under the audio dir
and rolled back if the history append fails; a GB budget
(`history.audio_budget_gb`) prunes old files. Lives in
`fluidvoice/history.py` (`_retain_audio`).

**control socket** — The daemon's only external interface: a
user-owned Unix socket (mode 0600) speaking one-JSON-object-per-line
requests (status, toggle, set-config, transcribe, history query, …),
served by a fixed worker pool so a long transcription never blocks a
status read. No TCP listener exists or will (ADR-0001). Lives in
`fluidvoice/control.py` + `fluidvoice/control_server.py`.

**rewrite mode** — A take whose dictated text is an edit instruction for
the current selection: the selection is captured (clipboard snapshot +
restore), the instruction is polished with the upstream edit prompts,
and the result is typed over the selection. Activated by
`hotkey.rewrite_key`. Lives in `fluidvoice/rewrite.py`.
