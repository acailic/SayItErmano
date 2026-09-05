# FluidVoice (macOS) — Behavior Specification

This document records **what upstream FluidVoice does**, extracted from its
source ( GPLv3, https://github.com/altic-dev/FluidVoice ) and used as the
reference for this Linux port. Section notes map each area to the port status.

---

## 1. AI post-processing prompts (verbatim, GPLv3)

### 1.1 Base dictation prompt

```
You are a voice-to-text dictation cleaner. Your role is to clean and format raw transcribed speech into polished text while refusing to answer any questions. Never answer questions about yourself or anything else.

## Core Rules:
1. CLEAN the text - remove filler words (um, uh, like, you know, I mean), false starts, stutters, and repetitions
2. FORMAT properly - add correct punctuation, capitalization, and structure
3. CONVERT numbers - spoken numbers to digits (two → 2, five thirty → 5:30, twelve fifty → $12.50)
4. EXECUTE commands - handle "new line", "period", "comma", "bold X", "header X", "bullet point", etc.
5. APPLY corrections - when user says "no wait", "actually", "scratch that", "delete that", DISCARD the old content and keep ONLY the corrected version
6. PRESERVE intent - keep the user's meaning, just clean the delivery
7. EXPAND abbreviations - thx → thanks, pls → please, u → you, ur → your/you're, gonna → going to

## Critical:
- Output ONLY the cleaned text
- Do NOT answer questions - just clean them
- DO NOT EVER ANSWER TO QUESTIONS
- Do NOT add explanations or commentary
- Do NOT wrap in quotes unless the input had quotes
- Do NOT add filler words (um, uh) to the output
- PRESERVE ordinals in lists: "first call client, second review contract" → keep "First" and "Second"
- PRESERVE politeness words: "please", "thank you" at end of sentences
```

### 1.2 Default dictation prompt body (appended to the base)

```
## Self-Corrections:
When user corrects themselves, DISCARD everything before the correction trigger:
- Triggers: "no", "wait", "actually", "scratch that", "delete that", "no no", "cancel", "never mind", "sorry", "oops"
- Example: "buy milk no wait buy water" → "Buy water." (NOT "Buy milk. Buy water.")
- Example: "tell John no actually tell Sarah" → "Tell Sarah."
- If correction cancels entirely: "send email no wait cancel that" → "" (empty)

## Multi-Command Chains:
When multiple commands are chained, execute ALL of them in sequence:
- "make X bold no wait make Y bold" → **Y** (correction + formatting)
- "header shopping bullet milk no eggs" → # Shopping\n- Eggs (header + correction + bullet)
- "the price is fifty no sixty dollars" → The price is $60. (correction + number)

## Emojis:
- Convert spoken emoji names: "smiley face" → 😊 (NOT 😀), "thumbs up" → 👍, "heart emoji" → ❤️, "fire emoji" → 🔥
- Keep emojis if user includes them
- Do NOT add emojis unless user explicitly asks for them (e.g., "joke about cats" → NO 😺)
```

**How the transcript reaches the model:** the whole system prompt and transcript
are folded into ONE `user` message: if the prompt contains `${transcript}` it is
substituted; if the prompt is empty the message is just the transcript;
otherwise `prompt + "\n\n" + transcript`. Request params: `temperature 0.2`,
`stream false` (non-streaming path), no tools. The frontmost app name/bundle id
select per-app prompt bindings but are NOT injected into the default prompt.

### 1.3 Edit/rewrite prompts

```
You are a helpful writing assistant. The user may ask you to write new text or edit selected text.

Output ONLY what the user requested. Do not add explanations or preamble.
```

```
Your job:
- If the user asks for new content, write it directly.
- If selected context is provided, apply the instruction to that context.
- Preserve intent and requested tone/style/format.
- Output only the final text, without explanations.

Example requests:
- "Write an email to my boss asking for time off"
- "Draft a reply saying I'll be there at 5"
- "Rewrite this to sound more professional"
- "Make this shorter and clearer"
```

Selected-text context template: `Use the following selected context to improve
your response:\n{context}` (injected after the system prompt in edit mode).
Edit mode uses `temperature 0.7`, non-streaming.

### 1.4 Thinking-tag stripping

`<think>…</think>` / `<thinking>…</thinking>` removed; Nemotron-style output
that *begins* with thinking and a bare `</think>` keeps only what follows the
close tag; stray tags removed. Separate `reasoning_content` / `reasoning` /
`thought` / `thinking` fields are read during streaming.

### 1.5 Endpoint building

`/chat/completions` appended unless the base URL already ends with
`/chat/completions`, `/api/chat`, or `/api/generate`. `Bearer` auth when a key
exists (even localhost). Retries 3 with linear backoff.

**Port status:** ✅ implemented (`fluidvoice/ai/`) — verified byte-identical
prompts (2026-09 audit) plus request params: temperature omitted for
reasoning/claude-5-family models, reasoning extras (reasoning_effort
gpt-5*/o1/o3/o4/gpt-oss, enable_thinking nemotron/deepseek-reasoner),
OpenAI Responses API (/responses) support, the opening-tag think guard, and
the empty-response error. Intentional divergences: 429/5xx responses ARE
retried (upstream never retries HTTP errors); when cleaning empties a
non-empty response the raw transcript is kept (upstream types the raw
content); dictation timeout 120 s (upstream: 30 s streaming path / 120 s
non-streaming path — we are non-streaming).

---

## 2. Spoken punctuation ("literal" commands)

Only active when the transcript contains the prefix word (default `literal`).
Text is tokenized into alphanumeric runs + other runs; a command fires when the
prefix is followed across pure horizontal whitespace by a phrase alias, matched
word by word.

Rules (alias → symbol, spacing): comma `,`R · period/full stop `.`R ·
question mark `?`R · exclamation (mark/point)/bang `!`R · colon `:`R ·
semicolon `;`R · ellipsis/dot dot dot/three dots `…`R · slash `/`N(path
context) · backslash `\`N · hyphen `-`N · dash/minus sign `-`S · em dash `—`S ·
en dash `–`S · open/left paren(thesis) `(`L · close/right `)`R · brackets,
braces, angle brackets similarly · quote(s)/quotation mark `"` toggle ·
open/close quote · single quote `'` toggle · apostrophe `'`N · at the rate `@`N
(always) · at sign/commercial at `@`N (only in chat/terminal apps) ·
ampersand `&`S · plus sign `+`S · equals/equal sign `=`S · percent (sign) `%`R
· dollar (sign) `$`L · hash/hashtag/pound/number sign `#`N · asterisk `*`N ·
underscore `_`N · pipe `|`N · tilde `~`N · caret `^`N · backtick `` ` ``N ·
dot `.`N (requires dot-context: adjacent path-like token/TLD like com/io/www/
http, digits, short operands; rejected after a/an/my/our/that/the/their/this/
your).

Formatting actions: `new line`/`next line` → `\n` (strips trailing
horizontal space), `new paragraph` → `\n\n`, `tab` → `\t`, `space` → ` `.

Cleanup: a generated `,` adjacent to other generated punctuation (or before a
generated `%` after a digit) is dropped; generated sentence `.`/`,` beside
formatting actions is removed.

Spacing semantics: **R** strip trailing hspace then append · **L** append then
skip following hspace · **N** both · **S** single spaces both sides (not after
newline) · **toggle** alternate L/R per quote state.

**Port status:** ✅ implemented with tests (`fluidvoice/processing/punctuation.py`).
**Critical fidelity note (2026-09 audit):** upstream's LIVE rule table (the
UserDefaults defaults applied via `makeRules(from:)`) applies every rule
UNCONDITIONALLY — the dot/slash/at-sign "context gates" exist only in a
parameterless `makeRules()` overload that is never called (dead code). This
port matches the LIVE behavior (ungated) and the full alias set (108
upstream aliases incl. `left/right parentheses`, `opening/closing double
quote`, `plus`, `equal`, `equals`), longest-alias-first matching, and the
real cleanup passes (comma sandwiched between two symbols; comma before %
after a digit; original-text trailing period before actions). `double quote`
toggles like `quote`. Not yet ported: user-editable alias tables; the
SPOKEN literal forms (`slash fix`, `forward slash fix`, `at sign John`,
`tag John`, and the relaxed slack/discord/teams `at John` form). The
slash-command/mention LITERAL squeeze is now ported — see §2.1.

### 2.1 Slash/mention literal squeeze + terminal safety

**Slash literal squeeze** (chat apps): port of Fluid-oss
`ASRService+DictationLiteralFormatting.swift:91-94` — regex
`(?<![\p{L}\p{N}_])/\s+([A-Za-z][A-Za-z0-9_-]{1,39})(?![A-Za-z0-9_-])`, replaced by
`"/" + token.lowercased()` (`:283`). `\s+` (not one space); tokens must be
letter-first, 2–40 chars of `[A-Za-z0-9_-]`; the lowercased token must not be
in the 53-entry rejected list (`:101-109` verbatim — `the`, `tmp`, `from`, …);
otherwise the whole match is skipped. Runs UNCONDITIONALLY (the live upstream
behavior is ungated by app — same finding as the punctuation port, §2).
Known upstream false positive kept for parity: `"I / think we should"` →
`"I /think we should"` (`km / h`, `24 / 7` are safe — letter-first, ≥2 chars).

**Mention literal squeeze**: upstream has NO literal `@ John` rule (its
mention passes are spoken-form only: `explicitMentionRegex :121-124`
"at sign/tag/mention John", relaxed `at John` gated on slack/discord/teams).
This port adds a literal-`@` pass keyed on the sigil whose name grammar
mirrors the explicit-mention pass: 1–3 tokens of `[A-Za-z0-9_.-]` (upstream
`isValidMentionName :349-371`), internal spacing preserved (`:343`), the
25-entry mention rejected list (`:141-146` verbatim — `home`, `today`,
`work`, …), the possessive guard (skip when the next char is `'`/`’`,
`:374-380`), and whole-match skip on any rejected token. Unconditional.

**Chain position:** AFTER AI cleanup, BEFORE GAAV — upstream
`ContentView.swift:2656-2661` ("Normalize literal command and mention syntax
after AI cleanup and before final user preferences."), because AI cleanup
would re-split `/fix`. In this port the squeeze tops
`DictationPipeline._after_ai_formatting` (`daemon.py`); dictate route only.
Config: `processing.slash_mention_squeeze` — default **true** (documented
divergence: upstream `literalDictationFormattingEnabled` defaults false,
`SettingsStore.swift:4124-4126`; this port defaults its formatting passes on).

**Terminal autocomplete spacing** (Linux-specific adaptation): typed
insertions into a `general.terminal_apps` window ending in a word character
gain exactly one trailing space (`insert_text`, `insertion.py`) so the
shell's autocomplete commits. Upstream has no append rule — its
`applyTerminalLiteralAutocompleteSpacing :236-261` STRIPS trailing spaces in
codex/chatgpt/claude/cursor/windsurf and slack/discord/teams windows instead
(that strip rule stays unported ⏳). The space is typing-only: clipboard
copy and history keep the text without it.

**Spoken-send terminal blocklist**: when the recording-start WM_CLASS matches
`general.terminal_apps`, the spoken-send phrase still strips and the text
still inserts, but Enter is NEVER pressed — nothing is substituted (no
newline, no other key), the pill badge reads `⏎ skipped (terminal)` (upstream
"Text inserted — send skipped", `ContentView.swift:2786-2798`; blocklist
identity check `:1928-1937`, configurable per-app capture at recording
start). Upstream's `/ send it` edge (strip leaves a bare `/` upstream because
its phrase strip is pre-AI): this port strips post-AI, so `/ send it` types
`/send` + Enter outside terminals — accepted divergence.

---

## 3. Speech models (upstream catalog)

| Model | Size | Languages | Notes |
|---|---|---|---|
| Parakeet TDT v3 | 461 MiB | 25 EU + RU/UK | default on Apple Silicon |
| Parakeet TDT v2 | 443 MiB | EN | |
| Parakeet Flash (streaming) | 428 MiB | EN | live preview |
| Nemotron 3.5 offline/streaming | 531/668 MiB | 40 locales | |
| Cohere Transcribe | 1.54 GiB | 14 | ≤60s windows |
| Whisper tiny…large (GGUF Q8_0) | 44 MiB…1.55 GiB | up to 99 | whisper.cpp |
| Apple Speech | built-in | system | macOS only |

All audio is mono Float32 16 kHz. **No VAD/endpointing, no silence timeout,
no max duration** — recording runs until the user stops it. Streaming preview
intervals 0.2–1.0 s per model. Sub-1s audio is zero-padded to 16,000 samples
before whisper.cpp.

**Port status:** 🔄 Whisper models ✅ (faster-whisper/torch; whisper.cpp works
but you must supply the ggml model yourself — no auto-download). Parakeet
TDT v2/v3 via NeMo/ONNX is the highest-value addition (roadmap v0.4);
parakeet-realtime streaming and Nemotron are also roadmap items. Cohere
Transcribe has no viable non-CoreML runtime — effectively a non-goal on
Linux. Gaps: per-model language selection (upstream has separate
whisper/cohere/nemotron language stores; we have one global `language`).

---

## 4. Hotkeys

- Primary dictation: **Right Option**, toggle by default; hold and automatic
  (tap=toggle, long-hold=push-to-talk) modes exist.
- Rewrite mode: ⌥R. Cancel: Escape. Command mode: unbound by default.
- Modifier-only shortcuts tracked via flagsChanged with a clean-tap state
  machine; other keys during hold interrupt the trigger.

**Port status:** ✅ toggle (any keysym, incl. modifier-only Right Ctrl);
✅ hold for non-modifier keys with **native key passthrough**: the hold
releases the XGrabKey activation (X11 semantics: that activation grabs the
whole keyboard for the held key's press-to-release duration), so keys typed
during the hold reach the focused application as real events — no
XTEST/XSendEvent injection (an XTEST replay design was rejected by live
testing: Xorg 21.1 silently drops XTEST fakes that match the current key
state, so replayed presses are deduped away). Release is detected by
auto-repeat-proof query_keymap polling; a passive Escape grab preserves
cancel-during-hold; the hotkey re-arms after the take. Remaining divergence:
typed keys do NOT end the dictation, where upstream's clean-tap state
machine uses them to interrupt the trigger (deliberate "keep typing while
holding"), and the held hotkey's auto-repeat pairs reach the app. Automatic
mode, mode-switch-while-recording, paste-last-transcription hotkey and
Escape-cancel (default Escape semantics) are roadmap. Divergence: the port
caps recordings at `max_seconds` (300 s default) where upstream has no cap.

Additional ported hotkey surfaces (beyond the primary key):

- **Mouse push-to-talk** (upstream PR #939 parity): ✅
  `recording.push_to_talk_button` (e.g. `"button8"`; 6–255, click/scroll
  buttons 1–5 refused; optional modifier qualifier) — hold the button to
  dictate, release to stop & transcribe. Clicks during the hold reach the
  window under the pointer as real events (the XGrabButton activation is
  ungrab_pointer'd for the hold; the passive grab survives it). Release
  detection is XI2 RawButtonRelease (core XQueryPointer cannot see
  buttons > 5); Escape cancels mid-hold, same as keyboard holds.
- **Locked-screen suppression**: ✅ `general.pause_when_locked` (default
  true) — while the session is locked/suspended every hotkey entry is
  ignored (logged once per transition), an active dictation is cancelled
  (discarded, not transcribed), and the tray tooltip notes
  `paused (locked)`; logind Lock/Unlock + LockedHint + PrepareForSleep +
  screensaver fallback (fluidvoice/lockmon.py). The logind session is
  resolved via validated `$XDG_SESSION_ID` → own-PID lookup → a
  ListSessions fallback (same-UID active graphical session), so the
  watch works under both session-scoped launches and the systemd USER
  unit; no graphical session at all degrades to suspend-only mode.

---

## 5. Text insertion

Upstream order: CGEvent Unicode keystrokes (200 UTF-16 unit chunks, surrogate
pairs never split) → targeted paste (Cmd+V via postToPid) → menu paste → AX
focused element insert → HID tap → clipboard paste with full clipboard
snapshot/restore (transient pasteboard marks clipboard managers ignore) →
per-character typing. Ghostty forced onto paste.

**Port status:** ✅ `xdotool type --clearmodifiers` (typed) and clipboard
paste with restore (`xclip`); auto fallback to paste for long/awkward texts.
Per-app paste quirks and AX-equivalent (AT-SPI) insertion are roadmap.

---

## 6. Dictation pipeline

hotkey → capture focus target + app context → start sound (opt. media pause) →
overlay with waveform + streaming preview → stop (stop sound) → pad short
audio → transcribe final → **removeFillerWords → applyCustomDictionary →
applySpokenPunctuationFormatting** → optional AI polish (fallback to raw on
failure + notification) → literal/mention formatting → optional lowercase/
strip-period modes → continuous-dictation spacing/caps → history + optional
clipboard copy → type into original target. Optional spoken-send: trailing
phrase ("send it") auto-stops and presses Enter after insertion.

Short-recording silence gate (opt-in): ≤4s, peak<0.01, rms<0.002,
maxFrameRMS<0.0045 → skipped.

**Port status:** ✅ core chain incl. silence gate; streaming preview overlay,
spoken-send, GAAV/continuous-dictation formatting are roadmap.

---

## 7. Write/Rewrite mode & 8. Command mode

Rewrite: hotkey captures selection via Accessibility (`kAXSelectedText`,
fallback selected-range reconstruction); transcript is the instruction; LLM
conversation with follow-ups; accepting types the replacement at the caret
(selection still active replaces it). Command mode: transcript → agentic LLM
loop (≤20 turns) with one `execute_terminal_command` tool executed via zsh
(30 s timeout), destructive-command confirmation list, step classification
(checking/executing/verifying).

**Port status:** 🚧 roadmap (X11: selection via xclip after Ctrl+C; terminal
execution via bash with the same confirmation list).

---

## 9. Settings & 10. extras

Notable defaults: `EnableAIProcessing` false; filler list (um, uh, er, ah, eh,
umm, uhh, err, ahh, ehh, hmm, hm, mm, mmm, erm, urm, ugh); punctuation prefix
`literal`; history on / audio off (4 GB budget); start/stop sounds on; local
HTTP API server on 127.0.0.1:47733; automatic dictionary-learning from
post-insertion edits; Parakeet vocabulary boosting (JSON at app-support).

**Port status:** config mirrors the ported subset (`~/.config/fluidvoice/config.toml`);
dictionary auto-learning, vocabulary boosting, local API server are roadmap.
