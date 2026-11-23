# Correction surfaces: a discoverability survey (product-excellence bet 3)

Date: 2026-11-23. Scope: product-excellence plan bet 3 ("fast correction
that improves tomorrow", [plan](../plans/2026-09-11-product-excellence-and-monetization-plan.md#improvement-bets-to-validate-first)):
history, dictionary suggestions, vocabulary hints and per-app prompt
profiles exist — this note measures whether a new user can FIND and
CONTROL them, then fixes the three cheapest gaps. No system was
redesigned.

Method: every claim below was read from the tree at `0191f72` (file:line
citations are that commit; a few shift after the fixes landed in the same
PR — the survey section keeps the pre-fix numbers on purpose). "Clicks"
count pointer clicks from an already-running daemon with its tray icon
visible; scroll depth is reported separately as rows above the target on
an 840×700 Settings window (the default, `fluidvoice/gtkui/settings_window.py:59-60`).

## 1. Where each surface lives today

| System | Surface | Citation | Reach from the tray | Docs |
| --- | --- | --- | --- | --- |
| History | Tray → **History** | `fluidvoice/daemon.py:334-335` | 2 clicks (right-click, item) → History window | quickstart mentions it as a safety net only ([quickstart.md](../guides/quickstart.md), "After setup"); README "The native app" ([README](../../README.md)) |
| History | Tray → **Copy Last Transcript** | `fluidvoice/daemon.py:329-330` | 2 clicks | none (not in README, quickstart or FAQ) |
| History | Per-entry Insert at cursor / Copy / **Edit** / Delete | `fluidvoice/gtkui/main_window.py:174-194` | 2 + 1 = 3 clicks; icon-only buttons with tooltips | none |
| History | Inline editor (Save rewrites the entry) | `fluidvoice/gtkui/main_window.py:210-250` → `history.update_text` (`fluidvoice/history.py:459-484`) | as above | none |
| History | Settings → History page (save toggle, audio, Clear, "Browse history") | `fluidvoice/gtkui/settings_pages/history.py:11-57` | tray → Settings… (1) → sidebar History (5th row, 1) → Browse (1) = 3 | generated reference only ([configuration.md](../guides/configuration.md) `[history]`) |
| History | Paste-last hotkey row | `fluidvoice/gtkui/settings_pages/dictation.py:304-311` | tray → Settings… → Dictation → 5th row of Hotkeys | none |
| Dictionary suggestions | Settings → Dictation → **Suggested words** group — hidden until ≥ 1 pending | group `fluidvoice/gtkui/settings_pages/dictation.py:669-678`, hidden at `:676`; visibility `fluidvoice/gtkui/settings_pages/ai.py:820` | 2 clicks + scroll past ~33 rows (8 groups); **invisible** until the same correction was made twice | STATUS.md describes the mechanism; no user guide, no UI hint at the signal source |
| Dictionary suggestions | Accept / Dismiss buttons | `fluidvoice/gtkui/settings_pages/ai.py:822-845` → `fluidvoice/gtkui/client.py:190-214` | as above | none |
| Dictionary suggestions | `sayit-ermano doctor` line "dictionary suggestions: N pending" | `fluidvoice/doctor.py:469-478` | terminal | none |
| Vocabulary hints | Settings → Models → **Vocabulary boosting**, one "Hotwords" entry row | `fluidvoice/gtkui/settings_pages/models.py:858-872` | 2 clicks + scroll to the 6th of 8 sections (`models.py:24-31`) | generated reference only (`configuration.md`, `model.hotwords`); README never says "hotwords"/"vocabulary" |
| Per-app prompts | Settings → AI → **Per-app prompts** group | `fluidvoice/gtkui/settings_pages/ai.py:200-210`, editor `:725-784` | 2 clicks + scroll past 5 groups incl. a multi-line base-prompt editor | generated reference (`ai.per_app_prompts`); README parity table row only |
| Per-app prompts | Prompt profiles (presets) the editor and the extra-shortcut combos refer to | `fluidvoice/gtkui/settings_pages/ai.py:144-164`; consumers `dictation.py:321-347` | same page, 3 groups above / another page | none |

## 2. How a new user would actually find each one

**History** is the best-served of the four: the tray has it (2 clicks),
`sayit-ermano app` opens it by default (`fluidvoice/gtkui/application.py:63-73`),
and the quickstart says every dictation lands there. What is NOT
discoverable is the *correction* half: nothing in the UI or the guides
says that an entry can be edited in place and re-inserted. The Edit
button is an unlabeled pencil (`main_window.py:185-189`) and the empty
state copy is about dictating, not repairing (`main_window.py:464-469`).
Count: a user who never hovers a row never learns that repair exists.

**Dictionary suggestions** score worst. The group is `set_visible(False)`
until a suggestion exists (`dictation.py:676`, `ai.py:820`), it sits
below ~33 rows on the longest Settings page, and NOTHING announces that
something is pending: no tray item, no badge, no line in the History
window where the signal is produced. The only indicator outside that
group is the `doctor` line. The loop the feature presumes — dictate,
open History, press a pencil, fix the same word twice, then go to
Settings → Dictation and scroll to the bottom — is undocumented and
unguided. Honest click count: *undefined* — discovery happens by
accident, not by path.

**Vocabulary hints** are reachable (2 clicks + scroll) but unfindable by
intent: a user whose name keeps coming out wrong opens Dictation, where
"Custom dictionary" lives, and the dictionary group says "phrases
replaced on insert" with no word about the Models page. The two
mechanisms solve adjacent problems (bias the decoder before vs replace
after) and neither surface mentions the other. The UI text also does not
say which backends honor hotwords (faster-whisper and openai-whisper do:
`fluidvoice/backends/faster_whisper_backend.py:49-50,118`,
`fluidvoice/backends/torch_whisper.py:40-42,68`; whisper.cpp, Parakeet
and remote STT do not: `fluidvoice/backends/whisper_cpp.py:27`,
`fluidvoice/backends/parakeet_onnx.py:218`,
`fluidvoice/backends/remote_stt.py:63`).

**Per-app prompts** sit last-but-one on the AI page, after Provider
profiles, AI polish, Local polish, Prompt profiles and the Base prompt
editor. Two extra traps: (a) the group is shown even when polish is off
and nothing says the rules do nothing until `ai.enabled` or
`ai.local_polish` is on (`fluidvoice/pipeline.py:246-248`); (b) the app
pattern the rule needs (e.g. `zed`) is only discoverable from the
History row's app tooltip (`main_window.py:155-158`), which no doc
points at.

## 3. What the docs cover

- [quickstart.md](../guides/quickstart.md) — the first-use path; History
  appears once, as "text is never lost" (`:75-76`). Nothing about
  correcting, the dictionary, suggestions, hotwords or per-app rules.
- [README](../../README.md) — "The native app" lists the Settings pages
  and names "per-app prompts" inside the AI bullet (`:151-156`); the
  post-processing line names the custom dictionary (`:90`). Hotwords and
  suggestions are absent; the parity table has a per-app row (`:249`).
- [configuration.md](../guides/configuration.md) — generated from the
  settings registry: `model.hotwords`, `processing.dictionary`,
  `ai.per_app_prompts`, `profiles.rules`, `[history]`. One row per key,
  no click path, no "which one do I want?".
- [STATUS.md](../STATUS.md) — the mechanism and the upstream
  divergences (D1–D7) for auto-learning (`:66-67`, `:171-178`), hotwords
  (`:41`), the history cap (`:153`). A status report, not a guide.
- [dev/FAQ.md](../dev/FAQ.md) — nothing on any of the four.

Gap summary: every system has *a* home and exactly one of them (History)
is explained where a new user looks first.

## 4. Ranked gaps (discoverability gain ÷ effort)

| # | Gap | Gain | Effort | Score |
| --- | --- | --- | --- | --- |
| 1 | Pending suggestions are invisible: no surface outside a hidden, below-the-fold group | high (the only *invisible* system; the feature is a dead end for anyone who does not stumble on it) | low: the tray menu is rebuilt per open and already hides an empty "AI profile" submenu (`daemon.py:341-346`); needs a page deep-link | **best** |
| 2 | No "Corrections" entry point in the docs linking all four | medium-high (the guides are the first place a new user looks; today they say nothing about correction) | low (docs only) | **2nd** |
| 3 | Adjacent surfaces do not point at each other (dictionary ⇄ hotwords; prompt profiles ⇄ shortcut combos); the Custom dictionary text never mentions the suggestions below it; the History Edit tooltip never says edits teach the dictionary | medium (turns 2 clicks + scroll into 1 click from the place the user already is) | low-medium: link rows + three strings (translations for sr-Latn/sr/bs) | **3rd** |
| 4 | History window: no hint that rows are editable / re-insertable beyond icon tooltips | medium | medium (copy + empty-state design) | deferred |
| 5 | Per-app prompts: shown while polish is off, with no "needs AI polish on" note; the app pattern source (History tooltip) undocumented | medium | low, but the honest fix needs product wording on the canonical-vs-legacy story (see §6) | deferred |
| 6 | Hotwords honored by only some backends; the group text names the two that do but not the three that do not | low-medium | low | deferred |

## 5. The three fixes (this PR)

1. **Tray announces pending suggestions and deep-links to them** —
   `Dictionary suggestions (N)…` appears in the tray menu only while
   suggestions are pending (same pattern as the AI-profile submenu) and
   opens Settings *on the Dictation page*. This required teaching
   `app --open` every Settings page name (previously only
   `history|settings`), so every tray/CLI entry can now land on the
   right page. Tests: `tests/test_tray.py::TestDictionarySuggestionsMenu`,
   `tests/gtkui/test_open_targets.py` (unit), and a display-tier check
   in `tests/test_settings_sections.py`.
2. **Settings cross-links + in-place hints** — Custom dictionary ⇄
   Vocabulary boosting link rows; Prompt profiles ⇄ extra-shortcut
   combos link rows; the Custom dictionary description says History
   corrections become suggestions below; the History Edit tooltip says
   so too. Tests: `tests/test_settings_sections.py::TestCorrectionLinks`,
   `tests/test_gtkui.py` (tooltip).
3. **A "Corrections" guide subsection** — `docs/guides/quickstart.md`
   gains "When the text is wrong" linking all four (where, clicks, what
   each does); README's native-app paragraph points at it; this note is
   indexed from `docs/research/README.md`.

## 6. Found while surveying, deliberately not fixed here (redesign territory)

- **Per-app prompt edits are shadowed after the legacy migration.**
  `save_config` copies `ai.per_app_prompts` into the canonical
  `profiles.rules` on the first save (`fluidvoice/config.py:1356-1365`),
  and resolution consults the canonical rules FIRST
  (`fluidvoice/profiles.py:129-150`, `fluidvoice/pipeline.py:258-262`).
  The Settings editor still edits `ai.per_app_prompts`. Repro (verified
  2026-11-23): save a rule `{apps: ["zed"], instructions: "OLD"}`; change
  it in Settings → AI → Per-app prompts to `"NEW"`; save;
  `prompt_instructions_for(cfg, "org.gnome.zed.Zed")` still returns
  `OLD`. The control is live until the first save, then silently dead for
  migrated rules. Fix = the editor must write `profiles.rules` (or the
  migration must be skipped while the legacy editor is the UI) — a
  design decision for the per-app system, out of this bet's scope.
- **Hotword backend support** is uneven (§2); the group description
  could list the backends that ignore the field.
- The **extra-shortcut profile combos** render `(none)` plus names only;
  a user with no profiles has no inline way to create one (the link row
  added in fix 2 mitigates; an inline "Add profile…" would be the
  redesign).
