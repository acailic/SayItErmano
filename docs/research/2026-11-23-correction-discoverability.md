# Correction discoverability survey (excellence bet 3)

Date: 2026-11-23
Bet: [product-excellence plan — improvement bets, bet 3 "Fast correction
that improves tomorrow"](../plans/2026-09-11-product-excellence-and-monetization-plan.md#improvement-bets-to-validate-first):
history, dictionary suggestions, vocabulary hints and per-app prompt
profiles all EXIST — the question is whether a new user can FIND and
control them. This survey maps every UI surface honestly (clicks
counted from the default starting point: the tray icon / panel menu),
then picks the top-3 discoverability fixes by gain ÷ effort.
No system is redesigned here — signposts only.

Click-count convention: from the running daemon's tray icon, GNOME with
an appindicator host, fresh install (empty history, no profiles). "Click"
= one menu open or one menu/sidebar/row activation; scrolls counted
separately because two of these surfaces live at the bottom of long
pages.

## Where the four systems live today

### 1. History (transcript list + inline correction)

| Surface | Where | Citation |
| --- | --- | --- |
| Tray menu → "History" | 2 clicks; opens the History window | `fluidvoice/daemon.py:334-335` → `_open_settings("/history")` (`fluidvoice/daemon.py:628-631`) → `app --open history` |
| GTK app default window | launching the app (desktop file / `sayitermano app`) opens History first | `fluidvoice/gtkui/application.py:36,70-73` |
| Settings → History page | retention/clear + "Browse history" button into the window | `fluidvoice/gtkui/settings_pages/history.py:11-57` (browse row: 45-54) |
| CLI | `sayitermano history` | `docs/guides/quickstart.md:75-76` |
| Docs | README screenshot + feature table | `README.md:135`, `README.md:250` |

New-user path: **2 clicks** from the tray (best of the four). Editing a
row inline is self-evident once there (row ⋯ menu → edit,
`fluidvoice/gtkui/main_window.py:813-824`). The gap is not History
itself but what the edit FEEDS (see #2).

### 2. Dictionary suggestions (auto-learned from history edits)

| Surface | Where | Citation |
| --- | --- | --- |
| Settings → Dictation page → "Suggested words" group | the ONLY surface; at the very bottom of the page, **hidden entirely until a suggestion is pending** | `fluidvoice/gtkui/settings_pages/dictation.py:669-678` (`set_visible(False)` at 676; revealed only on a non-empty load, `fluidvoice/gtkui/settings_pages/ai.py:786-794`) |
| The correction moment itself | editing text in History toasts only "Saved" — nothing says edits teach anything | `fluidvoice/gtkui/main_window.py:813-824` |
| doctor report | pending-suggestion count | `fluidvoice/doctor.py:472-478` |
| Docs | `dictionary` key documented; the auto-learning semantics only in the spec/plan files | `docs/guides/configuration.md:123`, `docs/plans/archive/specs/a8b2afa6_dict-auto-learn.md`, `fluidvoice/history.py:475-476` |

New-user path: **3 clicks + scrolling through 5 sections** (right-click
tray → Settings… → Dictation sidebar row → the group sits below
Hotkeys/Languages/Mic/Preview/Polish, `fluidvoice/gtkui/settings_pages/dictation.py:22-29`).
Worse: from the History window — where the user JUST made the
correction — there is no pointer at all; you must already know the group
exists. A user who never opens Settings → Dictation bottom never learns
the feature exists.

### 3. Vocabulary hints (decoder hotword biasing)

| Surface | Where | Citation |
| --- | --- | --- |
| Settings → Models page → "Vocabulary boosting" group | the ONLY UI surface; 6th of 8 sections on that page | `fluidvoice/gtkui/settings_pages/models.py:858-872` (section order: `fluidvoice/gtkui/settings_pages/models.py:24-31`) |
| Docs | `hotwords` key | `docs/guides/configuration.md:112` |
| (engine support matrix) | faster-whisper native, whisper via initial-prompt, whisper.cpp/parakeet: none | `fluidvoice/backends/faster_whisper_backend.py:49-50,118`, `fluidvoice/backends/torch_whisper.py:40-42,68`, `fluidvoice/backends/whisper_cpp.py:27`, `fluidvoice/backends/parakeet_onnx.py:218` |

New-user path: **3 clicks + a deep scroll** past catalogs/state/memory/
engine/remote. The conceptual trap: a user who wants the recognizer to
stop mangling "SayItErmano" looks under Dictation — where the Custom
dictionary lives — and finds nothing pointing at the Models page. The
two word-teaching mechanisms (replace-on-insert vs decoder biasing) are
split across pages with zero cross-references in either direction.

### 4. Per-app prompt profiles (named presets + per-app rules)

| Surface | Where | Citation |
| --- | --- | --- |
| Settings → AI → "Prompt profiles" group | named presets, radio + ⋯ menu + Add | `fluidvoice/gtkui/settings_pages/ai.py:142-164` |
| Settings → AI → "Base prompt" / "Save to profile" | the editor the presets wrap | `fluidvoice/gtkui/settings_pages/ai.py:166-198` |
| Settings → AI → "Per-app prompts" group | per-app instruction rules | `fluidvoice/gtkui/settings_pages/ai.py:200-210` |
| Settings → Dictation → Hotkeys → "Shortcut N prompt profile" | binding an extra shortcut to a preset | `fluidvoice/gtkui/settings_pages/dictation.py:322-344` |
| Tray menu → "AI profile" submenu | **name collision: this is PROVIDER profiles** (endpoint presets), not prompt profiles | `fluidvoice/daemon.py:307-323,341-346`, `fluidvoice/ai/provider_profiles.py` |
| Canonical `[profiles]` rules | config-file only (terminal/insertion/spoken-send per app); the Settings UI edits the legacy `ai.per_app_prompts` view and migrates on save | `fluidvoice/profiles.py:1-31,176-197`, `docs/guides/configuration.md:174` |
| Docs | three scattered table rows | `docs/guides/configuration.md:58,141,174`; `README.md:249` |

New-user path: **3-4 clicks** to the AI page groups; but the workflow
("create a preset → bind it to an extra shortcut / an app rule") spans
two different settings pages with no signposts, and the tray's "AI
profile" actively points at the wrong system. The hotkey binding
(`dictation.py:340` "Shortcut {n} prompt profile") never names where
profiles are managed.

### Quickstart coverage

`docs/guides/quickstart.md` — the doc a new user actually reads —
mentions none of dictionary suggestions, vocabulary hints, or per-app
prompts, and History only in passing (`docs/guides/quickstart.md:75-76`).
There is no "fixing mistakes" narrative anywhere in `docs/`.

## Survey table (clicks from the tray, fresh install)

| System | Best UI path | Clicks | Hidden until | Docs entry point |
| --- | --- | --- | --- | --- |
| History | tray → History | 2 | — | README screenshot |
| Dictionary suggestions | tray → Settings… → Dictation → page bottom | 3 + deep scroll | yes (group invisible until pending) | none user-facing |
| Vocabulary hints | tray → Settings… → Models → 6th group | 3 + deep scroll | no, but mis-placed conceptually | configuration.md only |
| Per-app prompt profiles | tray → Settings… → AI → 4th/6th group | 3-4 | no | configuration.md only |

## Top-3 fixes (gain ÷ effort)

1. **Settings cross-link rows.** The four controls live 3+ clicks and
   two page-switches apart with zero signposts; `_show_page()` — the
   programmatic page navigator — already existed unused by any UI
   (`fluidvoice/gtkui/settings_window.py:228-233`). Add a
   "related setting" link row to each correction-adjacent group:
   History page → Suggested words (Dictation); Custom dictionary →
   Vocabulary boosting (Models) and back; Hotkeys' shortcut-profile
   combos → Prompt profiles (AI); Prompt profiles → the shortcut
   bindings (Dictation). Gain: every wrong door now has a
   signpost to the right one. Effort: one small row factory + 5 rows.
2. **Teach-at-the-correction-moment toast.** The History edit toast is
   just "Saved" (`fluidvoice/gtkui/main_window.py:821`); the user is at
   the exact moment the feature feeds on, with no hint it exists. Say
   where it pays off: "Saved — edits teach the dictionary (Settings →
   Dictation)". Gain: the invisible-est surface gets surfaced at its
   only natural moment. Effort: one string.
3. **"Corrections" docs section.** A quickstart subsection linking all
   four with their click-paths (and a configuration.md pointer), so the
   narrative "dictation gets better tomorrow" exists in the docs index.
   Gain: the entry-point doc finally covers the loop. Effort: writing.

### Considered and deferred (for the record)

- **Tray deep-links / "Corrections" tray submenu** — needs new
  `--open settings:<page>` plumbing through GApplication command-line
  forwarding (`fluidvoice/gtkui/application.py:46-61`) + daemon spawn
  args; medium effort, and the tray already reaches Settings in 2
  clicks, which fix 1 then shortens. Revisit if the cross-links don't
  move the needle.
- **Renaming the tray "AI profile" submenu to "AI provider"** — would
  kill the collision with prompt profiles but churns three translated
  catalogs and the tray tests for a label nuance; the cross-links
  already disambiguate from the prompt-profile side.

## Chosen-fix implementation notes

- Fix 1 rows navigate via `_show_page(name)`; names are the
  PreferencesPage `name=` properties (`dictation`, `models`, `ai`,
  `history`), stable and pinned by `tests/test_settings_sections.py`.
- Fix 2 replaces the edit-success toast string only — no behavior
  change on failure paths.
- Fix 3 adds `docs/guides/quickstart.md#fixing-mistakes-corrections`
  and cross-references from `docs/guides/configuration.md`; links are
  covered by `scripts/check_docs_links.py` (gate step).
