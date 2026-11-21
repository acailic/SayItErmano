"""UI translations (gettext framework, fluidvoice/i18n.py).

Covers: catalog loading + lookup, the Serbian/Bosnian three-form plural
rule, runtime language switching with change listeners, system-locale
resolution, the config seam ([general] ui_language), and — when the po/
tree exists (repo checkouts; skipped for installed wheels) — catalog
completeness and .mo freshness against the extraction source of truth.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from fluidvoice import i18n


@pytest.fixture(autouse=True)
def _restore_language():
    """Every test starts from the suite-pinned English and restores it,
    so catalog lookups in other tests never leak."""
    before = i18n.current_language()
    i18n.set_language("en")
    try:
        yield
    finally:
        i18n.set_language(before)


PO_DIR = Path(__file__).resolve().parents[1] / "po"


class TestLookup:
    def test_english_is_the_source_identity(self):
        assert i18n.set_language("en") == "en"
        assert i18n._("Settings") == "Settings"

    def test_unknown_language_falls_back_to_english(self):
        assert i18n.set_language("xx-not-a-language") == "en"
        assert i18n._("Settings") == "Settings"

    def test_sr_latn_catalog_loads(self):
        i18n.set_language("sr-Latn")
        assert i18n._("Settings") == "Postavke"
        assert i18n._("Cancel Dictation (Esc)") == "Otkaži diktiranje (Esc)"

    def test_sr_cyrillic_catalog_loads(self):
        i18n.set_language("sr")
        assert i18n._("Settings") == "Поставке"
        assert i18n._("Microphone") == "Микрофон"

    def test_bs_catalog_loads(self):
        i18n.set_language("bs")
        assert i18n._("Settings") == "Postavke"
        assert i18n._("History") == "Historija"

    def test_placeholder_strings_stay_format_safe(self):
        for lang in ("sr-Latn", "sr", "bs"):
            i18n.set_language(lang)
            out = i18n._("Transcription failed: {error}").format(error="x")
            assert out.endswith("x")
            assert "{" not in out and "}" not in out

    def test_untranslated_string_falls_back_to_msgid(self):
        # a key that will never be in the catalogs
        for lang in ("en", "sr-Latn", "sr", "bs"):
            i18n.set_language(lang)
            assert i18n._("zz-definitely-not-translated") == \
                "zz-definitely-not-translated"


def _sr_plural(n: int) -> int:
    """The Serbian/Bosnian plural rule (one/few/many)."""
    if n % 10 == 1 and n % 100 != 11:
        return 0
    if 2 <= n % 10 <= 4 and (n % 100 < 10 or n % 100 >= 20):
        return 1
    return 2


class TestPlurals:
    @pytest.mark.parametrize("lang", ["sr-Latn", "sr", "bs"])
    @pytest.mark.parametrize("n,form", [(1, 0), (2, 1), (4, 1), (5, 2),
                                        (11, 2), (21, 0), (22, 1), (101, 0)])
    def test_serbian_plural_rule(self, lang, n, form):
        """one/few/many: 1 → first, 2-4 → second, 5+ and teens → third.
        The chosen msgstr carries the form index so the rule itself is
        observable through the catalog."""
        i18n.set_language(lang)
        forms = ("FORM0", "FORM1", "FORM2")
        # build a tiny in-memory catalog to observe form selection
        import gettext as g

        class T(g.NullTranslations):
            def ngettext(self, singular, plural, n):  # noqa: A003
                return forms[self.plural(n)]

        t = T()
        t.plural = _sr_plural  # the header rule, as GNUTranslations sets it
        i18n._translation = t
        try:
            assert i18n._n("a", "b", n) == forms[form]
        finally:
            i18n.set_language(lang)  # restore the real catalog

    def test_real_catalog_plurals(self):
        i18n.set_language("sr-Latn")
        assert i18n._n("Saved {n} change", "Saved {n} changes", 1) \
            .format(n=1) == "Sačuvana 1 izmena"
        assert i18n._n("Saved {n} change", "Saved {n} changes", 2) \
            .format(n=2) == "Sačuvane 2 izmene"
        assert i18n._n("Saved {n} change", "Saved {n} changes", 5) \
            .format(n=5) == "Sačuvano 5 izmena"

    def test_english_two_way_plural(self):
        i18n.set_language("en")
        assert i18n._n("{n} day", "{n} days", 1).format(n=1) == "1 day"
        assert i18n._n("{n} day", "{n} days", 3).format(n=3) == "3 days"


class TestSwitching:
    def test_set_language_switches_without_restart(self):
        i18n.set_language("en")
        assert i18n._("Settings") == "Settings"
        i18n.set_language("sr-Latn")
        assert i18n._("Settings") == "Postavke"
        i18n.set_language("bs")
        assert i18n._("Settings") == "Postavke"

    def test_change_listener_fires_on_switch_only(self):
        seen = []
        i18n.register_on_change(seen.append)
        i18n.set_language("sr-Latn")
        i18n.set_language("sr-Latn")  # no-op: resolved code unchanged
        assert seen == ["sr-Latn"]

    def test_listener_exception_does_not_block_switch(self):
        def broken(_code):
            raise RuntimeError("boom")

        i18n.register_on_change(broken)
        assert i18n.set_language("sr") == "sr"
        assert i18n._("Settings") == "Поставке"

    def test_system_value_re_resolves(self):
        i18n.set_language("en")
        assert i18n.configured_language() == "en"
        assert i18n.set_language("system") == "en"  # suite env pins en
        assert i18n.configured_language() == "system"

    def test_combo_choices_cover_every_language(self):
        values = {value for _label, value in i18n.combo_choices()}
        for code in i18n.LANGUAGES:
            assert code in values
        assert i18n.SYSTEM in values


class TestSystemLocale:
    @pytest.mark.parametrize("env,expected", [
        ("sr_RS", "sr"),
        ("sr_RS.UTF-8", "sr"),
        ("sr-Latn", "sr-Latn"),
        ("sr_Latn_RS", "sr-Latn"),
        ("sr@latin", "sr-Latn"),
        ("bs_BA", "bs"),
        ("en_US.UTF-8", "en"),
        ("de_DE", "en"),  # not shipped → English source
        ("", "en"),
    ])
    def test_locale_mapping(self, monkeypatch, env, expected):
        for var in (i18n.ENV_OVERRIDE, "LANGUAGE", "LC_ALL",
                    "LC_MESSAGES", "LANG"):
            monkeypatch.delenv(var, raising=False)
        if env:
            monkeypatch.setenv("LANG", env)
        assert i18n.resolve_system_language() == expected

    def test_language_env_list_first_match_wins(self, monkeypatch):
        monkeypatch.delenv(i18n.ENV_OVERRIDE, raising=False)
        monkeypatch.delenv("LANGUAGE", raising=False)
        monkeypatch.setenv("LANGUAGE", "fr:sr:en")
        assert i18n.resolve_system_language() == "sr"

    def test_override_beats_config_and_locale(self, monkeypatch):
        monkeypatch.setenv("LANGUAGE", "sr")
        monkeypatch.setenv(i18n.ENV_OVERRIDE, "bs")
        assert i18n.init_i18n({"general": {"ui_language": "en"}}) == "bs"
        monkeypatch.delenv(i18n.ENV_OVERRIDE, raising=False)
        assert i18n.init_i18n({"general": {"ui_language": "sr-Latn"}}) \
            == "sr-Latn"
        assert i18n.init_i18n({}) == "sr"  # falls to the system locale


class TestConfigSeam:
    def test_registry_key_exists(self):
        from fluidvoice.config import default, enum_options
        assert default("general", "ui_language") == "system"
        assert list(enum_options("general", "ui_language")) == \
            i18n.LANGUAGE_OPTIONS

    def test_coercion_rejects_unknown_codes(self):
        from fluidvoice.config import coerce_setting
        ok, value = coerce_setting("general", "ui_language", "sr-Latn")
        assert ok and value == "sr-Latn"
        ok, _ = coerce_setting("general", "ui_language", "klingon")
        assert ok is False


class TestDaemonHotApply:
    def make_daemon(self):
        import copy

        from fluidvoice.config import DEFAULTS
        from tests.test_daemon import StubBackend, StubRecorder, TestDaemon
        cfg = copy.deepcopy(DEFAULTS)
        return TestDaemon().make(cfg, StubRecorder(), StubBackend("t"))

    def test_ui_language_applies_live(self):
        """apply_config(['general.ui_language']) switches the daemon's
        catalog immediately (tray + notifications) — no restart."""
        d = self.make_daemon()
        try:
            assert i18n.set_language("en") == "en"
            d.cfg["general"]["ui_language"] = "sr"
            feedback = d.apply_config(["general.ui_language"])
            assert "ui language" in feedback.get("applied", [])
            assert i18n.current_language() == "sr"
            assert i18n._("Settings") == "Поставке"
        finally:
            i18n.set_language("en")

    def test_tray_menu_translates(self):
        """The tray menu model is rebuilt per open: labels follow the
        active catalog at build time."""
        d = self.make_daemon()
        try:
            i18n.set_language("sr-Latn")
            labels = [i.get("label") for i in d._build_tray_menu()]
            assert "Postavke…" in labels
            assert "Izađi iz SayItErmano" in labels
            i18n.set_language("en")
            labels = [i.get("label") for i in d._build_tray_menu()]
            assert "Settings…" in labels
            assert "Quit SayItErmano" in labels
        finally:
            i18n.set_language("en")


# -- catalog integrity (repo checkouts only) ---------------------------------

REPO_ONLY = pytest.mark.skipif(
    not PO_DIR.exists(), reason="po/ catalog sources not present")


class TestCatalogIntegrity:
    @staticmethod
    def _import_tools():
        import sys
        scripts = str(PO_DIR.parent / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)

    @pytest.mark.parametrize("lang", ["sr-Latn", "sr", "bs"])
    @REPO_ONLY
    def test_every_string_translated(self, lang):
        self._import_tools()
        from extract_translations import catalog_files, completeness, extract, merge
        catalog = extract(catalog_files())
        merged, _, _ = merge(catalog, PO_DIR / f"{lang}.po")
        missing = completeness(merged, nplurals=3)
        assert not missing, \
            f"{lang}: untranslated: {missing[:5]} (of {len(missing)})"

    @pytest.mark.parametrize("lang", ["sr-Latn", "sr", "bs"])
    @REPO_ONLY
    def test_no_orphaned_msgids(self, lang):
        self._import_tools()
        from extract_translations import catalog_files, extract, parse_po
        catalog = extract(catalog_files())
        existing = parse_po(PO_DIR / f"{lang}.po")
        orphans = [m for m in existing if m != "" and m not in catalog]
        assert not orphans, f"{lang}: orphaned: {orphans[:5]}"

    @REPO_ONLY
    def test_committed_mo_files_are_fresh(self):
        self._import_tools()
        from build_translations import LOCALE_DIR, compile_mo
        from extract_translations import parse_po
        for po in sorted(PO_DIR.glob("*.po")):
            mo = LOCALE_DIR / po.stem / "LC_MESSAGES" / "sayit-ermano.mo"
            assert mo.exists(), f"{mo} missing"
            assert mo.read_bytes() == compile_mo(parse_po(po)), \
                f"{mo} is stale — rerun scripts/build_translations.py"
