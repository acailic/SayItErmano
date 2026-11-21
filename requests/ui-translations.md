# UI translations: gettext framework, Serbian (Latin + Cyrillic) first

STATUS: SHIPPED

Shipped on `feature/ui-translations` — framework (fluidvoice/i18n.py +
`_()`/`_n()` wrapping of every user-facing string), complete sr-Latn /
sr / bs catalogs (501 strings), live language switching (Settings
selector + daemon hot-apply, no restart), extraction + build tooling
gated by `just translations` and `just gate`, README/site section.
Native-speaker review of the three catalogs is the remaining follow-up.

Origin: GitHub issue acailic/SayItErmano#23.

## Why

Dictation is already multilingual, but the app UI is English-only. US
competitors treat non-English UIs as an afterthought; a native
Serbian/Bosnian UI is a cheap, defensible wedge in a market the author
knows firsthand, and it compounds with multilingual dictation demos.

## What shipped

1. `fluidvoice/i18n.py` — gettext wiring, domain `sayit-ermano`,
   catalogs under `fluidvoice/locale/<code>/LC_MESSAGES/` (compiled,
   committed). Resolution order: `SAYITERMANO_UI_LANGUAGE` env >
   `[general] ui_language` ("system" default) > system locale
   (LANGUAGE/LC_ALL/LC_MESSAGES/LANG; plain `sr` = Cyrillic, Latin must
   be named: `sr-Latn`, `sr@latin`). `set_language()` swaps the catalog
   at runtime and notifies change listeners.
2. `_()` / `_n()` on every user-facing string in Settings (all pages),
   History, onboarding (+ hints), the daemon tray menu/tooltip and
   notifications. Dynamic strings use `_("…{name}…").format(...)` so
   translations can reorder placeholders; plurals use `_n` with the
   Serbian/Bosnian one/few/many rule.
3. New config key `[general] ui_language` (system|en|sr-Latn|sr|bs).
   Settings → General → Language combo (native endonyms). Live switch:
   the GTK app rebuilds open windows on a language change; the daemon
   hot-applies `ui_language` (tray menu rebuilds per open,
   notifications translate at send time). No restart anywhere.
4. Tooling: `scripts/extract_translations.py` (AST extraction →
   `po/sayit-ermano.pot` + per-language merge; `--check` fails on
   stale/orphaned/untranslated — the CI extraction report),
   `scripts/build_translations.py` (stdlib msgfmt incl. plural forms;
   committed `.mo`), `po/apply_translations.py` (sr-Latn hand-written;
   sr = script conversion of sr-Latn; bs = ijekavica + lexical
   adaptation). `just translations` + `just gate` enforcement.
5. README "Speaks your language" section + Unreleased What's-new; site
   section under the native-app area with
   `docs/screenshots/language-sr.png` (rendered offline from the real
   catalog by `scripts/gen-language-media.py`).

## Tests

`tests/test_i18n.py` — catalog loading/fallbacks, placeholder format
safety, the three-form plural rule (table + real catalog), runtime
switching + listeners, system-locale mapping, env precedence, config
seam, daemon `apply_config` hot-apply, translated tray menu, and
(in repo checkouts) completeness / zero-orphans / .mo freshness.

## Out of scope / follow-ups

- Native-speaker review of the three catalogs (acceptance criterion
  "reviewed by a native speaker" — the catalogs were machine-assisted
  translations, reviewed by the author, not yet by a native reviewer).
- More languages: the framework is language-agnostic; each addition is
  one `po/<lang>.po` + a `LANGUAGES` entry.
- libadwaita/GTK stock-string localization (needs `bindtextdomain` on
  the Gtk locale dir; deferred until a stock string is reported
  untranslated).
- Extraction in CI runs through the repo's `just gate` recipe (unit
  tier); a dedicated workflow job is provided as a patch artifact (the
  runner token in the automation environment cannot push workflow
  files).
