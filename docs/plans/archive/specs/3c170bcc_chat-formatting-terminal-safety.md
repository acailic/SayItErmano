# Plan: Chat-app slash/mention squeeze + terminal safety (spoken-send blocklist, autocomplete spacing)

Session `3c170bcc` · repo `/home/nistrator/Documents/github/FluidVoiceLinux` (branch `linux`) ·
planned against HEAD `f6afa6e` (daemon.py / config.py / insertion.py / processing are **clean** in the
worktree — the parakeet work is merged; dirty files are icons + `recorder.py` + `faster_whisper_backend.py`,
all orthogonal to this plan).

Request brief: `requests/chat-formatting-terminal-safety.md` (verbatim copy of the task prompt).

Every phase MUST leave `.venv/bin/python -m pytest -q tests --ignore=tests/integration` green.

---

## 0. Upstream verification (Fluid-oss, local clone `/home/nistrator/Documents/github/FluidVoice`, HEAD `66ca6825dd0c`, 2026-09-01)

All file:line references below are from that clone and map 1:1 to
`github.com/altic-dev/Fluid-oss` at that revision. The Swift source is the authority; where the
prompt's shorthand differs, the plan adopts upstream and flags it (same method as the punctuation
port, `docs/BEHAVIOR-SPEC.md` §2 "Critical fidelity note").

### 0.1 Slash-command literal squeeze — EXISTS upstream, ungated by app

`Sources/Fluid/Services/ASRService+DictationLiteralFormatting.swift` (463 lines):

- **Regex** (`:91-94`):
  `(?<![\p{L}\p{N}_])/\s+([A-Za-z][A-Za-z0-9_-]{1,39})(?![A-Za-z0-9_-])`
  — `/` not preceded by letter/digit/underscore, then **one or more** whitespace (`\s+`, not
  "exactly one"), then a token: letter first, 2–40 chars of `[A-Za-z0-9_-]`, not followed by
  another token char. Replaced by `"/" + token.lowercased()` (`:283`:
  `result.replaceCharacters(in: match.range, with: "/\(token.lowercased())")`) — **the token is
  lowercased**.
- **Token validation** (`isValidSlashCommandToken`, `:289-297`): token must be ASCII
  `[A-Za-z0-9_-]` (regex already enforces), start ASCII-alpha, and **not** be in
  `slashCommandRejectedTokens` (`:101-109`, verbatim, 53 tokens):
  `a, an, and, as, at, back, backslash, be, been, being, bin, by, comma, desktop, documents, dot,
  downloads, etc, for, forward, from, home, in, is, library, local, mark, of, on, or, period,
  private, question, quote, quotes, semicolon, slash, slashes, source, sources, src, the, tmp, to,
  user, users, usr, var, volumes, was, were, with, without`
- **Gating**: `applySlashCommandFormatting` (`:170-188`) is gated ONLY on the master setting
  `literalDictationFormattingEnabled` (`SettingsStore.swift:4124-4129`, **default `false`**) —
  **no per-app gate**. (A second, *spoken* form exists — `slashCommandSpokenRegex :96-99`
  `"forward slash X"/"slash X"` with lead-in words `:111-114` — NOT in this task's scope; tracked
  as follow-up.)
- Known upstream quirk (accepted, we match): prose like `"I / think we should"` → `"I /think we
  should"` ("think" is a valid token). `"km / h"` and `"24 / 7"` are safe (token must be ≥2 chars,
  letter-first).

### 0.2 Mention formatting — upstream has SPOKEN forms only; no literal `@ John` rule

Same file:

- **Explicit** (`explicitMentionRegex`, `:121-124`):
  `(?<![\p{L}\p{N}_@])(?i:(?:at\s+(?:sign|the\s+rate)|tag|mention))\s+([A-Za-z][A-Za-z0-9_.-]*(?:\s+[A-Z][A-Za-z0-9_.-]*){0,2})(?![A-Za-z0-9_.-])`
  → replaced by `"@" + name` with **internal spaces preserved** (`:343`). **Ungated.**
- **Relaxed** (`relaxedMentionRegex`, `:126-129`, `"at John"`) — gated on
  slack/discord/teams (`isRelaxedMentionApp :399-405`) + lead-in words (`:148-151`). Not ported.
- **Name validation** (`isValidMentionName :349-371`): 1–3 tokens, all chars
  `[A-Za-z0-9_.-]` (`isASCIIMentionTokenCharacter :455-458`), no token in
  `mentionRejectedTokens` (`:141-146`, 25 tokens):
  `a, an, airport, breakfast, brunch, class, dinner, home, hotel, house, lunch, meeting, night,
  noon, office, place, restaurant, school, shop, store, the, today, tomorrow, work, yesterday`
- **Possessive guard** (`isPossessiveMentionMatch :374-380`): skip the match if the char right
  after it is `'` or `’`.
- **There is NO literal `@ John` squeeze anywhere upstream.** The prompt requires one
  (`@ John Smith` → `@John Smith`). **Port decision:** implement a literal-`@` pass that mirrors
  upstream's explicit-mention *name grammar* (same charsets, 1–3 tokens, rejected list, possessive
  guard, internal spaces preserved) but keyed on the literal `@` sigil instead of the spoken
  phrases. It runs **unconditionally** (upstream's literal-slash and explicit-mention passes are
  the ungated ones; the only app gate is on the relaxed spoken form, which we don't port).

### 0.3 Chain position — literal formatting runs AFTER AI cleanup, BEFORE GAAV

`Sources/Fluid/ContentView.swift`, dictation stop route:

1. `:2554` `applySpokenPunctuationFormatting`
2. `:2556-2560` `SpokenSendParser.parse` (phrase strip, pre-AI)
3. `:2590+` AI post-processing
4. `:2658-2661` `applyDictationLiteralFormatting` — comment `:2656-2657`: *"Normalize literal
   command and mention syntax after AI cleanup and before final user preferences."* (slash pass,
   then mention pass — `:153-167`)
5. `:2662` `applyGAAVFormatting`
6. `:2665` continuous-dictation spacing
7. `:2668` `applyTerminalLiteralAutocompleteSpacing`

**Port decision:** our `post_process` runs pre-AI, so the squeeze call does NOT go in
`post_process`; it goes at the TOP of `DictationPipeline._after_ai_formatting`
(`fluidvoice/daemon.py:147`), i.e. after polish, **before** GAAV — the position upstream uses and
the reason upstream gives (AI cleanup would re-split `/fix`). `fluidvoice/processing/__init__.py`
gains the import/re-export only. Dictate route only (rewrite/command keep today's behavior — out
of scope).

### 0.4 Terminal autocomplete spacing — upstream STRIPS trailing spaces in chat apps; it never appends

`applyTerminalLiteralAutocompleteSpacing` (`:236-261`): runs only when the text **ends in
horizontal whitespace**; strips the trailing whitespace when (a) the whole text is a standalone
slash command (`standaloneSlashCommandRegex :136-138`, `^/[A-Za-z][A-Za-z0-9_-]{1,39}$`) in a
codex/chatgpt/claude/cursor/windsurf window (`:408-416`), or (b) the text ends with an `@mention`
token (`terminalMentionTokenRegex :131-134`) in a slack/discord/teams window (`:399-405`).
Otherwise the text is returned unchanged. **There is no append-a-space rule upstream** — the only
upstream trailing-space *append* is continuous-dictation chaining
(`ASRService.swift:5134-5163`, off by default, app-agnostic).

**Port decision:** implement the prompt's Linux rule as a **documented Linux-specific
adaptation** (it is the roadmap item "terminal autocomplete spacing"): when the insertion
target's WM_CLASS matches `terminal_apps` and the insertion will be **typed** (not paste) and
ends in a word character, append exactly one trailing space. Upstream's chat-app strip rule stays
unported and is tracked ⏳ in UPSTREAM-TRACKING.

### 0.5 Spoken-send terminal blocklist — strip yes, Enter no, text still inserted

- `isSpokenSendBlockedApp` (`ContentView.swift:1928-1937`): `identity = "\(appName)
  \(bundleId)".lowercased()`; blocked when it contains any of `terminal, iterm, warp, ghostty,
  kitty, alacritty`. App info is the **recording-start** capture.
- Use (`:2757-2766`): `spokenSendAllowed` requires `... && !isSpokenSendBlockedApp(appInfo)`.
- When blocked (`:2786-2798`): the phrase was still stripped earlier (`:2556`), the text **is
  still inserted**, the post-insertion key is NOT sent, the pill indicator goes to *failed* and
  the overlay shows "Text inserted — send skipped" for 650 ms. **Nothing else is substituted**
  (no newline, no other key).

**Port decision:** in `_after_ai_formatting`, when `parse_spoken_send` says *send* and the
recording-start WM_CLASS (`app_hint`) matches `terminal_apps`: keep the stripped text, set
`_pending_send_key = None`, set a skipped flag so the pill badge shows `⏎ skipped (terminal)`
after insertion (reuses the existing badge surface — no new UI), and log one line.

### 0.6 Default-on divergence (accepted, flag in spec)

Upstream `literalDictationFormattingEnabled` defaults **false** (`SettingsStore.swift:4124-4126`).
The prompt mandates `processing.slash_mention_squeeze = true`, consistent with this port's
convention (fillers/punctuation default on). Documented in BEHAVIOR-SPEC.

---

## 1. Design summary

| Piece | Where | Shape |
|---|---|---|
| Squeeze functions | `fluidvoice/processing/slash.py` (new) | `squeeze_slash_commands(text)`, `squeeze_mentions(text)`, `squeeze_slash_mentions(text, cfg=None)` |
| Wiring | `daemon.py:_after_ai_formatting(text, app_hint=None)` | squeeze first, then existing GAAV → spoken-send; `run()` passes `app_hint` |
| Terminal match | `insertion.py` | `is_terminal_app(wm_class, cfg)` (case-insensitive substring over `general.terminal_apps`) |
| Trailing space | `insertion.py` | `terminal_trailing_space(text)`; hooked into `insert_text` typed path |
| Send blocklist | `daemon.py:_after_ai_formatting` | suppress `_pending_send_key`, badge skipped |
| Config | `config.py` | `processing.slash_mention_squeeze=true`, `insertion.terminal_autocomplete_space=true`, `general.terminal_apps=[…15]` |
| Doctor | `doctor.py` | `_formatting_lines(cfg)` — one line per new key |
| Docs | README, BEHAVIOR-SPEC §2, UPSTREAM-TRACKING, ROADMAP | see Phase 6 |

New config keys (defaults in `DEFAULTS`, template, save whitelist, settings validation):

```python
# general
"terminal_apps": ["gnome-terminal", "kgx", "konsole", "xterm", "alacritty",
                  "kitty", "wezterm", "ghostty", "foot", "tilix", "terminator",
                  "guake", "yakuake", "st-256color", "warp"],
# processing
"slash_mention_squeeze": True,
# insertion
"terminal_autocomplete_space": True,
```

`terminal_apps` = the prompt's 14 entries **plus `warp`** (upstream blocks warp,
`ContentView.swift:1933`; Warp ships a Linux client). `iterm` omitted (macOS-only). Bare
`terminal` (upstream's first entry) is deliberately not a default — the concrete Linux list
covers it; users can add it.

---

## 2. Phases

### Phase 1 — config surface

**`fluidvoice/config.py`**
1. `DEFAULTS`: add the three keys above (`general.terminal_apps`, `processing.slash_mention_squeeze`,
   `insertion.terminal_autocomplete_space`).
2. `TEMPLATE`: add commented entries — under `[general]`:
   `terminal_apps = [...]  # case-insensitive WM_CLASS substrings; spoken-send never presses Enter here`;
   under `[processing]`: `slash_mention_squeeze = true  # "/ fix the deploy" -> "/fix the deploy"`;
   under `[insertion]`: `terminal_autocomplete_space = true  # one trailing space in terminals so autocomplete commits`.
3. `_SAVE_WHITELIST`: `general += ["terminal_apps"]`, `processing += ["slash_mention_squeeze"]`,
   `insertion += ["terminal_autocomplete_space"]`.
4. `SETTING_BOOLS` += the two booleans; `ALLOWED_SETTINGS` mirrors the whitelist.
5. New `_coerce_terminal_apps(value)` modeled on `_coerce_mic_priority`: list of str, strip each,
   drop empties, case-insensitive dedupe keeping first, entry ≤64 chars, ≤32 entries else reject;
   wire into `coerce_setting` (dedicated branch, like `mic_priority`).

**`tests/test_config_settings.py`** — new class `TestNewFormattingKeys`:
- defaults: all three present; `terminal_apps` non-empty, contains `"kitty"` and `"warp"`.
- `apply_settings` accepts `slash_mention_squeeze=False` / `terminal_autocomplete_space=False`
  and rejects non-bools; accepts a cleaned `terminal_apps` list (strips/dedupes), rejects
  non-str entries / >64-char entry / 33 entries.
- `save_config` round-trip: a config with the keys set survives save→load (whitelist).

Gate: `.venv/bin/python -m pytest -q tests --ignore=tests/integration`.

### Phase 2 — `fluidvoice/processing/slash.py` (pure functions, not yet wired)

```python
"""Slash-command / mention literal squeeze.

Port of Fluid-oss ASRService+DictationLiteralFormatting (literal forms):
Sources/Fluid/Services/ASRService+DictationLiteralFormatting.swift:91-94,
:101-109, :141-146, :283, :343, :349-371, :374-380. The literal-@ pass is a
port addition (upstream formats spoken mentions only); its name grammar,
rejected-token list and possessive guard mirror the explicit-mention pass.
"""

import re

SLASH_REJECTED_TOKENS = frozenset("""a an and as at back backslash be been being
bin by comma desktop documents dot downloads etc for forward from home in is
library local mark of on or period private question quote quotes semicolon
slash slashes source sources src the tmp to user users usr var volumes was were
with without""".split())

MENTION_REJECTED_TOKENS = frozenset("""a an airport breakfast brunch class
dinner home hotel house lunch meeting night noon office place restaurant
school shop store the today tomorrow work yesterday""".split())

# upstream slashCommandLiteralRegex (Python \w ≈ [\p{L}\p{N}_])
_SLASH_RE = re.compile(r"(?<!\w)/\s+([A-Za-z][A-Za-z0-9_-]{1,39})(?![A-Za-z0-9_-])")
# literal-@ analog of explicitMentionRegex :121-124 (name grammar identical)
_MENTION_RE = re.compile(
    r"(?<![\w@])@\s+([A-Za-z][A-Za-z0-9_.-]*(?:\s+[A-Za-z][A-Za-z0-9_.-]*){0,2})"
    r"(?![A-Za-z0-9_.-])")

def squeeze_slash_commands(text: str) -> str: ...
def squeeze_mentions(text: str) -> str: ...
def squeeze_slash_mentions(text: str, cfg: dict | None = None) -> str:
    """slash pass then mention pass (upstream :153-167 order); no-op when
    processing.slash_mention_squeeze is false."""
```

Implementation notes:
- Slash: early-out when `"/" not in text` (upstream `:173-175`). Per match, if
  `m.group(1).lower() in SLASH_REJECTED_TOKENS` skip, else replace whole match with
  `"/" + m.group(1).lower()`. Works via `re.sub` with a function.
- Mention: early-out when `"@" not in text`. For each match: capture `name = m.group(1)`;
  skip if the char immediately after the match is `'` or `’`; skip if any whitespace-split
  token of `name` is in `MENTION_REJECTED_TOKENS`; else replace whole match with `"@" + name`
  (internal spacing preserved — regex groups contain no outer whitespace).
- Both: `\s+` (not single space) per upstream; replaces are non-overlapping and left-to-right.

**`fluidvoice/processing/__init__.py`**: add `from .slash import squeeze_slash_mentions`
(re-export; docstring line updated — this is the file's "one call" per the brief; the actual
invocation lives in the daemon per §0.3).

**`tests/test_processing.py`** — new class `TestSlashMentionSqueeze` (table-driven):

Positives:
- `"/ fix the deploy"` → `"/fix the deploy"`
- `"@ John Smith"` → `"@John Smith"` (rest of name kept)
- `"please / fix it now"` → `"please /fix it now"` (mid-text, preceded by space)
- `"/   fix"` → `"/fix"` (`\s+`, multi-space)
- `"/ Fix the deploy"` → `"/fix the deploy"` (token lowercased)
- `"@ Jane Roe Smith"` → `"@Jane Roe Smith"` (3 tokens)
- `"open /gerrit review"` → unchanged (already joined)

Negatives (prompt-mandated):
- `"check https://example.com/path"` unchanged (no whitespace after `/`)
- `"mail me at a@b.co"` unchanged (`@` preceded by `\w`)
- `"user@ example.com"` unchanged (same lookbehind)
- `"and/or x"`, `"he/she said"`, `"24 / 7"`, `"km / h"` unchanged
- `"hello /"` and `"hello @"` unchanged (lone trailing sigil)
- `"/ the deploy"` unchanged (`the` rejected), `"/ tmp/xyz"` unchanged (`tmp` rejected)
- `"@ home now"` unchanged (`home` rejected → whole match skipped)
- `"@ John's card"` unchanged (possessive guard)
- `"at sign John"` unchanged (spoken forms not ported)

Known-quirk parity assertion (documents upstream behavior, `:0.1`):
- `"I / think we should"` → `"I /think we should"` (upstream-matching false positive)

Gating: `squeeze_slash_mentions(text, {"processing": {"slash_mention_squeeze": False}})` is a
no-op; default (None cfg / True) squeezes.

Gate: pytest command green.

### Phase 3 — wire the squeeze into the dictation pipeline

**`fluidvoice/daemon.py`**
1. `_after_ai_formatting(self, text, app_hint=None)`: at the top, before the GAAV block:
   `if p.get("slash_mention_squeeze", True): text = squeeze_slash_mentions(text)`
   (import at top of file: `from .processing.slash import squeeze_slash_mentions`).
2. `run()`: change `polished = self._after_ai_formatting(polished)` (daemon.py:234) to
   pass `app_hint=app_hint`.

**`tests/test_daemon.py`** (TestPipeline additions):
- `StubBackend("/ fix the deploy")` → inserter receives `"/fix the deploy"`.
- with `cfg["ai"]["enabled"]=True` + polisher that echoes, squeeze still applied (post-AI
  position proof).
- rewrite mode (`mode="rewrite"`) is unaffected (instruction text not squeezed).

Gate: pytest green.

### Phase 4 — terminal class match + trailing space in `insertion.py`

**`fluidvoice/insertion.py`**
1. `def is_terminal_app(wm_class: str | None, cfg: dict) -> bool` —
   `any(p.lower() in (wm_class or "").lower() for p in cfg.get("general", {}).get("terminal_apps", []))`.
2. `def terminal_trailing_space(text: str) -> str` — return `text + " "` iff
   `re.search(r"\w$", text)` (idempotent; empty/punctuated/space-ending text unchanged).
3. In `insert_text`: after the paste decision, on the typed path only:
   ```python
   if cfg["insertion"].get("terminal_autocomplete_space", True):
       wm = active_window_class() if wm_class is None else wm_class
       if wm and is_terminal_app(wm, cfg):
           text = terminal_trailing_space(text)
   ```
   with a new optional parameter `insert_text(text, cfg, wm_class=None)` (None → live lookup;
   tests inject). Paste path and `-`-prefixed texts never gain the space. History/clipboard keep
   the unspaced text (one-space divergence from upstream's finalText, documented in
   BEHAVIOR-SPEC).

**`tests/test_insertion.py`** — new class `TestTerminalAutocompleteSpace`:
- `is_terminal_app`: `"gnome-terminal-server"` matches; `"Kitty"/"GHOSTTY"` case-insensitive;
   `"org.wezfurlong.wezterm"` substring; `"firefox"` no; `None` no; custom list honored.
- `insert_text("git checkout", cfg, wm_class="kitty")` → `"typed"` and the captured xdotool
  argv ends with `"git checkout "` (trailing space in the typed string — assert via the
  existing `runner` fixture).
- `"done."` → no space (ends in punctuation); `"already "` → unchanged (idempotent);
  2000-char text → `"paste"` strategy, no space; `terminal_autocomplete_space=False` → no space;
  `wm_class="firefox"` → no space; `wm_class=None` with monkeypatched
  `insertion.active_window_class` → resolved live.

Gate: pytest green.

### Phase 5 — spoken-send terminal blocklist in the daemon

**`fluidvoice/daemon.py`**
1. `__init__`: `self._pending_send_skipped_terminal = False` (next to `_pending_send_key`).
2. `_after_ai_formatting` spoken-send branch: when `result.should_send`:
   ```python
   if insertion.is_terminal_app(app_hint, self.cfg):
       self._pending_send_key = None
       self._pending_send_skipped_terminal = True
       self.log("spoken-send: Enter suppressed (terminal app)")
   else:
       self._pending_send_key = self.cfg["recording"].get("spoken_send_key", "enter")
   ```
3. `run()`, after `strategy = self._insert(polished)` (daemon.py:235): add
   ```python
   elif self._pending_send_skipped_terminal:
       self._pending_send_skipped_terminal = False
       self._set_pill_badge("⏎ skipped (terminal)")
   ```
   (badge reuses the existing pill surface; `_set_pill_badge` is a no-op without a display —
   upstream shows "Text inserted — send skipped", `ContentView.swift:2792-2796`).

**`tests/test_daemon.py`** (or `test_extra_formats.py` TestSpokenSendPipeline neighbors):
- terminal: `spoken_send_enabled=True`, `StubBackend("/ fix the deploy send it")`,
  `pipe.run(wav, "kitty")` → inserter got `"/fix the deploy"` (phrase stripped, squeeze
  applied), `key_presser` **never called**, strategy has no `+enter`.
- non-terminal: same with `app_hint="firefox"` → `key_presser` called once with `"enter"`.
- `terminal_apps=[]` in cfg → Enter pressed even for `"kitty"` (blocklist is list-driven).
- spoken-send disabled → nothing changes anywhere.

Gate: pytest green.

### Phase 6 — doctor + docs

**`fluidvoice/doctor.py`**: add `_formatting_lines(cfg)` after `_parakeet_lines` and a section in
`run()`:

```
chat/terminal formatting:
  slash/mention squeeze: on            (processing.slash_mention_squeeze)
  terminal autocomplete space: on      (insertion.terminal_autocomplete_space)
  terminal_apps (15): gnome-terminal, kgx, …   (spoken-send Enter suppressed here)
```
(one line per key; `off` variants when disabled; count + names for the list).

**`tests/test_infra.py`**: new class `TestDoctorFormattingLines` — default cfg → three lines,
`on`/count correct; disabled cfg → `off` lines; empty list → `terminal_apps (0)`.

**Docs**
1. `README.md` Configuration block: add the three keys (one row/comment each) mirroring Phase 1
   template text.
2. `docs/BEHAVIOR-SPEC.md` §2 fidelity note: replace the "Not yet ported" sentence —
   slash/mention **literal** squeeze ✅ (port of `:91-94`/`:101-109`; literal-`@` pass is a port
   addition mirroring `:121-124` name grammar; spoken `slash`/`at sign`/`tag`/`mention` forms and
   the relaxed slack/discord/teams `at John` form remain ⏳); add a short §2.1 "Terminal safety":
   the append-space Linux rule (upstream `:236-261` strips instead — divergence documented),
   the spoken-send blocklist (strip-but-no-Enter, `ContentView.swift:1928-1937`, `:2786-2798`),
   default-on divergence (`SettingsStore.swift:4124` default false).
3. `docs/UPSTREAM-TRACKING.md`: "Spoken-send" row → drop "terminal-blocklist not ported ⏳"
   (now ✅, configurable `terminal_apps`); add a row "Slash-command/mention literal formatting"
   → ✅ literal forms / spoken forms ⏳; changelog row "Spoken-send commands, quiet-countdown
   completion, terminal blocklist" → terminal blocklist ✅.
4. `docs/ROADMAP.md` "Later": tick the slash/mention + terminal-autocomplete item with a
   one-line DONE note (literal forms; spoken forms tracked upstream).

Gate: full pytest green + `grep` sanity that no doc still claims the items are ⏳.

---

## 3. Coordination notes

- `requests/insertion-hardening.md` (separate session) also touches `insertion.py`; keep every
  change here additive (new functions + one hook in `insert_text`) to minimize conflicts.
- New keys are socket-settable via `ALLOWED_SETTINGS` automatically; surfacing them in the GTK
  settings UI is deliberately NOT in scope (no gtkui files in the brief).
- Do not reorder the existing GAAV/spoken-send steps; the squeeze is inserted at the top of
  `_after_ai_formatting` only (upstream order literal→GAAV; our GAAV→strip order is a
  pre-existing divergence, untouched).
- The `/ send it` edge (phrase == squeeze target) types `/send` + Enter outside terminals —
  upstream's pre-AI strip would leave a bare `/`; accepted, noted in BEHAVIOR-SPEC §2.1.

## 4. Definition of done

- All six phases landed, each with `.venv/bin/python -m pytest -q tests --ignore=tests/integration`
  green at its commit.
- Squeeze table tests cover every prompt case (positives, URL/email/prose negatives, lone sigils)
  plus the parity quirk; terminal matching, blocklist branch (Enter suppressed in terminal,
  pressed elsewhere), and the three config keys have unit tests; doctor lines tested.
- Spec carries the upstream quotes with file:line (§0 above) — the builder copies §0 into
  BEHAVIOR-SPEC §2/§2.1 during Phase 6.
- Out of scope (unchanged): Wayland, per-app prompt sets, GAAV behavior, punctuation/filler
  behavior, chat-store/command-mode tool schema, spoken `slash`/`at sign` phrasing forms.
