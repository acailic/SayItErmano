# Configuration reference

Part of the [documentation index](../README.md). Everything lives in
`~/.config/sayit-ermano/config.toml`, written with 0600 permissions.
`sayit-ermano config init` writes a fully commented template with
every key at its default — rendered from the same settings registry as
the reference below, so delete any line to fall back to the built-in
default.

Quick start — the three keys most people touch (every key is in the
table below):

```toml
[hotkey]
key = "Right_Control"   # any keysym: F9, space, Pause, right_alt...
mode = "toggle"         # or "hold" (push-to-talk), "both"

[model]
name = "small"          # tiny/base/small/medium/large-v3/large-v3-turbo
```

## All settings

Defaults are the built-in defaults (`config init` starts from these).
**Notes**: *engine reload* — the change applies by reloading the speech
engine; *daemon restart* — takes effect after a daemon restart;
*secret* — the value is masked in every config read that crosses a
boundary. This block is generated from the code registry and cannot
drift: `python scripts/gen_config_reference.py` regenerates it.

<!-- BEGIN generated settings reference — regenerate: python scripts/gen_config_reference.py -->

### `[general]`

| Key | Default | Notes | Effect |
| --- | --- | --- | --- |
| `language` | `"auto"` |  | Whisper language code ("auto" detects, or "en", "de", ...) |
| `language_cycle` | `[]` |  | Ordered language codes the cycle hotkey steps through, e.g. ["auto", "en", "sl"] - may include "auto"; empty = the cycle key is off. The cycle override is RUNTIME daemon state and never persisted. |
| `language_whitelist` | `[]` |  | Wrong-language guard: when language = "auto" detects a language NOT in this list, the take is re-decoded once with the first entry, e.g. ["sl", "en"]. Empty = off. |
| `copy_to_clipboard` | `false` |  | Also copy every transcription to the clipboard |
| `tray_enabled` | `true` |  | Panel/tray icon while the daemon runs |
| `terminal_apps` | `["gnome-terminal", "kgx", "konsole", "xterm", "alacritty", "kitty", "wezterm", "ghostty", "foot", "tilix", "terminator", "guake", "yakuake", "st-256color", "warp"]` |  | Case-insensitive WM_CLASS substrings identifying terminals. In these apps spoken-send never presses Enter (a half-typed shell line would EXECUTE) and typed insertions gain one trailing space so autocomplete commits. |
| `pause_when_locked` | `true` |  | Ignore hotkeys and cancel an active dictation while the session is locked/suspended (logind lock watch; the tray notes "paused (locked)") |

### `[hotkey]`

| Key | Default | Notes | Effect |
| --- | --- | --- | --- |
| `key` | `"Right_Control"` |  | X11 keysym name of the dictation hotkey, e.g. "Right_Control", "F9", "space", "Pause". Modifier-only keys (Right_Control / Right_Alt / Right_Shift / Super_R) only work with mode = "toggle". |
| `modifiers` | `[]` |  | Extra modifiers to require, e.g. ["ctrl", "shift"] |
| `mode` | `"toggle"` |  | "toggle": tap to start, tap again to stop & transcribe. "hold": push-to-talk (non-modifier key only). Other keys typed during the hold pass through to the focused app natively (the keyboard is freed for the hold's duration). "both": a quick tap toggles, a held key talks (250 ms disambiguation window). |
| `cancel_key` | `"Escape"` |  | macOS parity: this key cancels an in-progress dictation (discards, nothing typed). Grabbed ONLY while recording; "none" disables. |
| `rewrite_key` | `""` |  | Optional keysym for Rewrite mode (needs [ai]) |
| `command_key` | `""` |  | Optional keysym for Command mode (needs [ai]) |
| `paste_key` | `""` |  | Optional keysym: re-type the last transcription |
| `language_key` | `""` |  | Optional keysym that cycles the runtime language override through general.language_cycle (the cycle state itself is never persisted) |
| `extra_shortcuts` | `[]` |  | Up to 2 EXTRA dictation shortcuts (3 total with the primary), each optionally bound to a named prompt profile (ai/profiles), e.g. extra_shortcuts = [{key = "F8", profile = "Terse notes"}] |
| `wayland_evdev` | `false` |  | Wayland (no global grabs): optional physical push-to-talk read straight from /dev/input (PRIVILEGED: needs the input group and python-evdev). Off by default; the DE-shortcut assist is the primary wayland hotkey. |
| `wayland_evdev_device` | `""` |  | Device-name substring matching /dev/input devices, e.g. "Keyboard" |
| `wayland_evdev_key` | `"KEY_RIGHTCTRL"` |  | ecodes KEY_* name held for evdev push-to-talk |

### `[recording]`

| Key | Default | Notes | Effect |
| --- | --- | --- | --- |
| `command` | `"auto"` |  | auto \| pw-record \| parecord |
| `device` | `""` |  | Optional PipeWire node target (pw-record --target) / PulseAudio source; "" = system default (the mic dropdown's "Auto" option) |
| `mic_priority` | `[]` |  | Ordered microphone priority patterns - case-insensitive substrings of the source name, first match wins, e.g. ["bluez", "usb-cam"] (Bluetooth headset first, then a USB webcam). When the configured `device` above disappears, FluidVoice switches to the first available match and notifies you; switching never happens mid-dictation. |
| `max_seconds` | `300` |  | Maximum recording duration in seconds |
| `skip_silent` | `false` |  | Skip recordings <= 4s that are pure silence |
| `first_pcm_timeout` | `2.0` |  | Stop early when the microphone sends no audio at all (muted/wrong device); 0 = off |
| `stall_timeout_s` | `8.0` |  | Cancel the take when the capture stream freezes mid-dictation for this many seconds (device glitch/route change) - 0 = off |
| `sample_rate` | `16000` |  | Fixed hardware capture rate - the whisper models want 16 kHz mono; not UI-settable |
| `spoken_send_enabled` | `false` |  | Spoken-send: a trailing phrase strips and presses Enter after typing |
| `spoken_send_phrase` | `"send it"` |  | Trailing phrase that strips and presses Enter |
| `spoken_send_key` | `"enter"` |  | Key pressed by spoken-send: enter \| shift+enter \| ctrl+enter |
| `spoken_send_countdown_s` | `1.2` |  | Quiet countdown after the phrase: with the phrase at the end and 0.5 s of silence, a countdown of this many seconds finishes the take by itself (speak again to cancel); 0 = off. Needs spoken_send_enabled. |
| `preview_enabled` | `true` |  | Live transcription preview while recording |
| `preview_mode` | `"auto"` |  | auto (pill, falls back) \| overlay \| notify |
| `preview_interval` | `1.2` |  | Seconds between partial passes |
| `preview_min_audio` | `1.0` |  | Seconds before the first partial |
| `preview_bottom_offset` | `64` |  | Pill px above the screen bottom edge |
| `preview_overlay_size` | `"medium"` |  | pill \| small \| medium \| large (macOS sizes) |
| `preview_segmented` | `true` |  | Segmented streaming preview: fixed windows (50% hop), one decode per tick instead of re-decoding the whole take |
| `preview_segment_s` | `2.0` |  | Segment window length in seconds (larger windows track the final text more closely - measured F1 vs the final decode 0.26/0.49/0.57 at 2/3/4 s on hard audio - at higher per-window decode cost) |
| `preview_conf_gate` | `true` |  | Hide preview text the model scored below its low-confidence band (wrong-word suppression on hard audio; the pill's level bars still show activity) |
| `preview_vad_silence_s` | `2.0` |  | Trailing-silence VAD auto-stops the take after this many seconds of quiet; 0 disables the auto-stop (the hotkey stops every take) |
| `overlay_chips` | `true` |  | Hover action chips above the pill (X11) |
| `pause_media` | `true` |  | Pause MPRIS players while dictating (resume after) |
| `push_to_talk_button` | `""` |  | Mouse push-to-talk: hold this button to dictate (always hold-style, independent of hotkey.mode). "button8"/"b8"/"8" - buttons 6-255 only; 1-5 (click/scroll) are refused, they would break the desktop. Thumb buttons are usually 8/9 (6/7 on some mice). Empty = off. |
| `push_to_talk_modifiers` | `[]` |  | Extra modifiers to require for the button, e.g. ["ctrl"] |

### `[model]`

| Key | Default | Notes | Effect |
| --- | --- | --- | --- |
| `backend` | `"auto"` | engine reload | auto \| faster-whisper \| whisper-torch \| whisper.cpp \| parakeet |
| `name` | `"auto"` | engine reload | auto -> "small" when CUDA is available, "base" otherwise; or one of tiny, base, small, medium, large-v3, large-v3-turbo; with backend="parakeet": parakeet catalog names |
| `device` | `"auto"` | engine reload | auto \| cuda \| cpu |
| `compute` | `"auto"` | engine reload | auto \| float16 \| int8 |
| `whispercpp_model` | `""` | engine reload | ggml/gguf model for the whisper.cpp backend: a catalog name (ggml-base.bin, ggml-small.en.bin, ...) or a path to a file |
| `eager_warmup` | `true` | daemon restart | Load the model at daemon start (preview-ready). Changing this needs a daemon restart. |
| `idle_unload_s` | `0` |  | Unload the speech model after this many idle seconds to free RAM/VRAM (0 = keep it loaded forever; range 30..86400 when set). The next dictation after an unload pays the model load time again. |
| `languages` | `{ }` |  | Per-model language overrides, e.g. languages = { small = "de", "ggml-base.en.bin" = "en" }. "auto" = always detect for that model; a missing key follows general.language (read per-dictation, applies live). |
| `remote_url` | `""` | engine reload | Remote STT server (OpenAI-compatible /v1/audio/transcriptions) - local-first: nothing leaves this machine while remote_url is empty. Point it at a LAN GPU box (vLLM/whisper.cpp server/NIM/DGX Spark) or any compatible cloud, e.g. "http://192.168.1.50:8000". |
| `remote_model` | `"whisper-large-v3"` | engine reload | Model name sent in the remote form |
| `remote_api_key` | `""` | engine reload, secret | Optional bearer token for the remote server (masked, never logged) |
| `remote_timeout_s` | `30` | engine reload | Per-request timeout (5..600) |
| `hotwords` | `[]` | engine reload | Custom vocabulary biasing (upstream #916 - words to ADD, unlike the dictionary's replacements): fed to the decoder as hints, e.g. ["SayItErmano", "PipeWire"] - <=20 focused words: long lists over-bias the decoder. Changing this key reloads the speech engine. |

### `[processing]`

| Key | Default | Notes | Effect |
| --- | --- | --- | --- |
| `remove_filler_words` | `true` |  | Remove filler words (um, uh, hmm...) before punctuation formatting |
| `filler_words` | `["um", "uh", "er", "ah", "eh", "umm", "uhh", "err", "ahh", "ehh", "hmm", "hm", "mm", "mmm", "erm", "urm", "ugh"]` |  | Filler words removed before punctuation formatting |
| `punctuation_enabled` | `true` |  | Spoken punctuation commands enabled |
| `punctuation_prefix` | `"literal"` |  | Spoken commands require this prefix word: "literal comma" -> "," |
| `formatting_action_triggers` | `{ }` |  | Extra spoken formatting-action trigger aliases (action name -> aliases, on top of the built-ins), e.g. formatting_action_triggers = {new_line = ["nova vrstica"]} |
| `dictionary` | `[]` |  | Custom dictionary: phrases replaced on insert, e.g. [[{ triggers = ["miro board"], replacement = "Miro board" }]] |
| `gaav_enabled` | `false` |  | GAAV: casual/search-field formatting of the final text |
| `gaav_lowercase_first` | `true` |  | Lowercase the first letter (GAAV) |
| `gaav_remove_trailing_period` | `true` |  | Remove the trailing period (GAAV) |
| `slash_mention_squeeze` | `true` |  | Chat-app literal squeeze: "/ fix the deploy" -> "/fix the deploy", "@ John Smith" -> "@John Smith" (runs after AI cleanup, before GAAV) |

### `[ai]`

| Key | Default | Notes | Effect |
| --- | --- | --- | --- |
| `enabled` | `false` |  | Optional AI polish of the raw transcript (FluidVoice's headline feature). Any OpenAI-compatible /v1/chat/completions endpoint works: OpenAI, Groq, Ollama, LM Studio, ... |
| `base_url` | `"http://localhost:11434/v1"` |  | OpenAI-compatible chat endpoint, e.g. http://localhost:11434/v1 (Ollama) or http://localhost:1234/v1 (LM Studio) |
| `model` | `""` |  | Chat model name, e.g. qwen3:8b |
| `api_key` | `""` | secret | API key (preferred: leave empty and export the env var below). Socket-settable it is NOT: edit the file or use the env var - a save carries the stored key over. |
| `api_key_env` | `"SAYITERMANO_API_KEY"` |  | Environment variable holding the API key |
| `temperature` | `0.2` |  | Sampling temperature |
| `timeout_seconds` | `120` |  | Request timeout |
| `max_retries` | `3` |  | Retries per request |
| `per_app_prompts` | `[]` |  | Upstream per-app prompt sets: [{"apps": ["zed"], "instructions": "..."}] (first match wins) |
| `base_prompt` | `""` |  | Custom base prompt for AI polish (empty = the built-in dictation prompt; Settings -> AI can save named presets of it) |
| `refusal_guard` | `true` |  | Refusal guardrail: a polish reply that reads as a model refusal ("I'm sorry, I can't assist...") never reaches the doc - the raw transcript is typed instead, with a notification. false = trust the model blindly. |

### `[insertion]`

| Key | Default | Notes | Effect |
| --- | --- | --- | --- |
| `mode` | `"typed"` |  | typed: simulate keystrokes (xdotool type). paste: clipboard + Ctrl+V (restores your clipboard afterwards). auto: typed, falling back to paste for very long texts |
| `type_delay_ms` | `8` |  | Delay between simulated keystrokes |
| `paste_threshold_chars` | `1200` |  | Longer texts use clipboard paste |
| `terminal_autocomplete_space` | `true` |  | One trailing space after typed insertions in terminal apps (general.terminal_apps) so the shell's autocomplete commits the last token |
| `verify_paste` | `true` |  | Verify the paste landed (selection read by the target) before restoring the clipboard; false = legacy fixed-delay restore |
| `terminal_paste_key` | `"ctrl+shift+v"` |  | Keystroke used to paste in terminal apps (general.terminal_apps); X11 terminals pass ctrl+v through to the app, they need ctrl+shift+v |
| `wayland_tool` | `"auto"` |  | Wayland typing tool: auto \| wtype \| ydotool (wtype needs wlroots/KDE; ydotool works everywhere but needs ydotoold + /dev/uinput access). Ignored on X11 sessions. |

### `[context]`

| Key | Default | Notes | Effect |
| --- | --- | --- | --- |
| `enabled` | `false` |  | Insertion-time focused-field context (P2 seam): app identity, accessible role, selection and bounded preceding text, read ONCE right before typing and never stored (nothing reaches history/config/logs). Off by default - prototype-grade; complete the Wayland smoke matrix (docs/dev/wayland-smoke-matrix.md) before relying on it. |
| `provider` | `"auto"` |  | Context backend: auto (wayland -> atspi, x11 -> x11) \| x11 (WM_CLASS identity only) \| atspi (identity + role + selection + preceding text; works on x11 too) \| none. Missing dependencies degrade to no context, never crash. |
| `max_preceding_chars` | `120` |  | Hard cap on the focused field text preceding the caret read for sentence continuation (0..500; 0 disables the continuation read). Enforced again inside FocusContext, whatever the provider returns. |

### `[profiles]`

| Key | Default | Notes | Effect |
| --- | --- | --- | --- |
| `rules` | `[]` |  | Canonical per-app behavior profiles, first match wins (case-insensitive substrings of the app identity, "*" matches everything): rules = [{ match = ["gnome-terminal", "kgx"], terminal = true, spoken_send = "off" }]. Fields: terminal, prompt_profile (a named AI prompt preset), instructions (inline prompt text), insertion_mode (typed\|paste\|auto), formatting_mode (gaav), spoken_send (on\|off); empty = inherit the global setting. Legacy general.terminal_apps and ai.per_app_prompts stay read as fallback until v1.0 and migrate into rules on the first settings save. |
| `migrated_from_legacy` | `false` |  | Set once the first settings save migrated legacy per-app prompts / terminal lists into profiles.rules (migration marker; not socket-settable). |

### `[sounds]`

| Key | Default | Notes | Effect |
| --- | --- | --- | --- |
| `enabled` | `true` |  | Play start/stop sounds |
| `volume` | `1.0` |  | 0.0 - 1.0 |

### `[notifications]`

| Key | Default | Notes | Effect |
| --- | --- | --- | --- |
| `enabled` | `true` |  | Desktop notifications |

### `[updates]`

| Key | Default | Notes | Effect |
| --- | --- | --- | --- |
| `check` | `true` |  | Check GitHub releases for a newer version (once per daemon start + daily). The updater NEVER installs anything - it notifies and prints the upgrade command. Set check = false to disable every probe (SAYITERMANO_SKIP_UPDATE_CHECK=1 does the same per-run). |
| `notify` | `true` |  | Desktop notification when a newer release is first seen |

### `[history]`

| Key | Default | Notes | Effect |
| --- | --- | --- | --- |
| `save` | `true` |  | Save transcriptions to history |
| `save_audio` | `false` |  | Store the recording with each entry |
| `audio_budget_gb` | `4.0` |  | Retained-audio budget in GB |

### `[command]`

| Key | Default | Notes | Effect |
| --- | --- | --- | --- |
| `max_turns` | `4` |  | Command-mode agent loop bound (upstream: 20) |
| `working_dir` | `""` |  | Working directory for commands (empty = $HOME) |
| `timeout_seconds` | `60.0` |  | Per-command subprocess timeout |
| `confirm_timeout_s` | `120.0` |  | Auto-cancel a pending confirmation after this many seconds |
| `destructive_patterns` | `[]` |  | Additions to the built-in destructive-command list (rm, mv, sudo, kill, chmod, chown, dd, mkfs, truncate, shred, pipes into rm/sudo, ...): commands MATCHING any of these substrings case-insensitively need the strong confirmation (the command hotkey twice). e.g. destructive_patterns = ["git push", "shutdown"] |
| `context_window_s` | `300.0` |  | Follow-up context: seconds the last 5 executed command results stay available to the next voice command in the SAME focused app (0 disables). Say "new session" to clear it immediately; nothing is persisted, a daemon restart starts cold. |
<!-- END generated settings reference -->

## Notes

**Why is my first dictation slow after a break?** With `idle_unload_s`
set (Settings → Models → Memory), the speech model is released after the
idle window to free RAM — and VRAM on GPU machines — instead of staying
pinned in memory forever. The first dictation afterwards pays the model
load again (a few seconds; the load overlaps your speech), while the
following ones are instant. Set it to `0` — the default — to keep the
model always loaded, and check the live state any time with
`sayit-ermano doctor` (it prints `model: loaded/unloaded (idle Xm;
policy Ns)`).

## AI polish

```toml
[ai]
enabled  = true
base_url = "http://localhost:11434/v1"   # Ollama; or OpenAI/Groq/LM Studio
model    = "qwen3:8b"                    # pick a general-purpose chat model
# api_key_env = "SAYITERMANO_API_KEY"    # cloud providers: export the variable
```

**Refusal guardrail** (`ai.refusal_guard`, default on): a polish reply
that reads as a model refusal — "I'm sorry, I can't assist with that." —
is never typed into your document; the raw transcript is used instead
and a notification explains why. Opt out with `refusal_guard = false` if
you want the model's reply verbatim (English patterns only in v1).
