# UI translations (i18n)

How the interface is localized: framework, catalogs, and the gates that
keep them honest. For the product-level summary see the README's
"Speaks your language" section; this page is the contributor guide.

## Architecture

- **Source language**: English. Msgids are the literal strings in the
  code.
- **Runtime**: `fluidvoice/i18n.py` — domain `sayit-ermano`, catalogs in
  `fluidvoice/locale/<code>/LC_MESSAGES/sayit-ermano.mo` (compiled,
  committed so wheels/debs need no build-time gettext). No GTK imports:
  the daemon, the app, tests and tooling all share it.
- **Accessors**: `_()` and `_n()` from `fluidvoice.i18n`. Dynamic text
  is `_("…{name}…").format(name=…)` — never f-strings inside a
  translation call (the extractor rejects them; translations must be
  able to move placeholders). Plurals use `_n(singular, plural, n)`;
  Serbian/Bosnian use the three-form
  `nplurals=3` one/few/many rule, English the two-form rule.

## Language resolution

1. `SAYITERMANO_UI_LANGUAGE` env var (tests, screenshots, CI)
2. `[general] ui_language` config key (`"system"` default)
3. the system locale (`LANGUAGE`/`LC_ALL`/`LC_MESSAGES`/`LANG`);
   plain `sr` resolves to Cyrillic — Latin must be named explicitly
   (`sr-Latn`, `sr@latin`, `sr_Latn_RS`, …)

Switching is live everywhere: the GTK app rebuilds open windows via the
i18n change listener, and the daemon hot-applies `ui_language`
(`apply_config`) — the tray menu is rebuilt per open and notifications
translate at send time. No restart is needed.

## Adding a language

1. Add the code to `LANGUAGES` in `fluidvoice/i18n.py` (native
   endonym as the label) and to the `ui_language` enum in
   `fluidvoice/config.py`.
2. Run `python scripts/extract_translations.py` — it writes
   `po/<lang>.po` with every msgid (plus `Plural-Forms` when the
   language is registered there).
3. Translate the `msgstr` values. Copy `po/sr_Latn_translations.py` +
   `po/apply_translations.py` if you want a reviewable Python dict
   instead of editing po directly.
4. `python scripts/build_translations.py` compiles the `.mo` into
   `fluidvoice/locale/<lang>/LC_MESSAGES/` (stdlib-only msgfmt — no
   gettext tools required).
5. `just translations` must pass: zero untranslated strings, zero
   orphans (msgid in the po but gone from the code), `.mo` in sync.

## Gates

- `just translations` — `extract_translations.py --check` +
  `build_translations.py --check`; both are also part of `just gate`.
- `tests/test_i18n.py` pins lookup/fallback behavior, the plural rule,
  live switching, locale resolution, the config seam, daemon
  hot-apply, and (in repo checkouts) catalog completeness, orphans and
  `.mo` freshness.

## Conventions

- Extracted strings live in one place: the `_()`/`_n()` call sites.
  Never build a user-visible sentence by concatenating translated
  fragments — reorder the placeholder instead.
- Technical tokens stay verbatim in translations: `{placeholders}`,
  `` `commands` ``, config keys, tool names, model names, brands.
- The suite pins `SAYITERMANO_UI_LANGUAGE=en` in `tests/conftest.py`
  so string assertions stay deterministic on any host locale.
