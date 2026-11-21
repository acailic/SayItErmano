# UI translations: gettext framework, Serbian (Latin + Cyrillic) first

Branch: `feature/ui-translations` (pushed; commits 741d746 → c533b77)
Task: acailic/SayItErmano#23

## What shipped

- **Framework** — `fluidvoice/i18n.py`: gettext wiring (domain
  `sayit-ermano`, catalogs under `fluidvoice/locale/<lang>/LC_MESSAGES/`,
  compiled + committed so wheels/debs need no build-time gettext),
  runtime language switching with change listeners, ngettext plurals
  (Serbian/Bosnian three-form rule), locale normalization
  (`sr`, `sr_RS`, `sr-Latn`, `sr@latin`, `bs_BA.UTF-8`…). Resolution:
  `SAYITERMANO_UI_LANGUAGE` env > `[general] ui_language` > system
  locale. No restart anywhere: the GTK app rebuilds open windows on a
  language change; the daemon hot-applies `ui_language` (tray menu
  rebuilds per open, notifications translate at send time).
- **All user-facing strings wrapped** — Settings (every page), History,
  onboarding (+ failure hints), tray menu/tooltip, daemon notifications,
  language-cycle announcement: 503 strings through `_()`/`_n()`, dynamic
  text as `_("…{name}…").format(...)` (f-strings inside `_()` are
  rejected by the extractor).
- **Catalogs complete** — sr-Latn (hand-written), sr Cyrillic
  (transliteration of sr-Latn — lossless by orthography), bs (ijekavica
  + lexical adaptation with per-string overrides): 503/503 each, zero
  orphans.
- **Selector + config** — Settings → General → Language (System default
  + native endonyms), `[general] ui_language` (system|en|sr-Latn|sr|bs);
  config reference regenerated.
- **Tooling + gates** — `scripts/extract_translations.py` (AST
  extraction, per-language completeness report, `--check` fails on
  stale/orphaned/untranslated), `scripts/build_translations.py`
  (stdlib-only msgfmt incl. plural forms, `--check` freshness),
  `po/apply_translations.py` (reproducible derivation), `just
  translations` + both checks joined `just gate`.
- **Tests** — `tests/test_i18n.py`: 60 tests (lookup/fallbacks,
  placeholder format-safety, plural-rule table + real catalog forms,
  live switching + listeners, locale mapping, env precedence, config
  seam, daemon hot-apply, translated tray menu, catalog
  completeness/orphans/mo-freshness). Suite pins
  `SAYITERMANO_UI_LANGUAGE=en` in conftest for deterministic strings.
- **Docs/site** — README "Speaks your language" section + Unreleased
  what's-new; landing-site section with `language-sr.png` (rendered
  offline from the real catalog by `scripts/gen-language-media.py`);
  `docs/dev/translations.md` contributor guide; CHANGELOG entry;
  `requests/ui-translations.md` brief (STATUS: SHIPPED).

## Intentionally left out / follow-ups

- **Native-speaker review** of the three catalogs — acceptance
  criterion "reviewed by a native speaker" is NOT yet met: the catalogs
  were authored machine-assisted and self-reviewed; flagged in the
  brief as the remaining follow-up.
- **CI workflow change** — delivered as a patch
  (`docs/dev/ci-patch-i18n-extraction.md`) because the runner token
  cannot push `.github/workflows/` files. The extraction report already
  runs in CI indirectly: `just gate` runs both checks, and
  `tests/test_i18n.py` (unit tier) pins completeness/orphans/freshness.
- **GTK/libadwaita stock-string localization** (bindtextdomain for the
  toolkit's own strings) — deferred; no stock string reported
  untranslated.
- **More languages** — framework is language-agnostic; each new
  language is one po file + a `LANGUAGES`/enum entry.
- **`um, uh, hmm…` filler examples in the Dictation page's "Example"
  row** are illustrative Slovenian-like phrases from upstream — kept
  verbatim (they demonstrate the spoken-trigger feature, not the UI
  language).

## Verification performed

- `just`-gate equivalents (no `just` binary here): `ruff check .` ✓,
  `check_docs_links.py` ✓, `gen_config_reference.py --check` ✓,
  `validate_requests.py` ✓, `extract_translations.py --check` ✓,
  `build_translations.py --check` ✓
- Unit tier (`scripts/test_tier.py unit`, canonical gate flags):
  **3670 passed**, 35 failed — the failing 35 are byte-identical to
  the pre-change baseline (worktree at linux@43a0013): missing system
  binaries on this runner (`xclip`/`xdotool`/`pw-record`) affecting
  test_insertion/test_recorder/test_preview/test_overlay. No
  regressions.
- Wheel packaging smoke: `pip install --target` carries
  `fluidvoice/locale/**/sayit-ermano.mo` ✓
- Could NOT run here: the display/GTK tier (`just test-ui`, needs
  PyGObject + Xvfb with GTK4 — no `gi` on this runner) and the live
  window-rebuild smoke. GTK-touching changes are compile + lint clean
  and string-identical under English (what the display suite asserts);
  CI's gtk-x11 lane runs them on the PR.
