"""P2 consumers: the pipeline's insertion-time context read, continuation
formatting, GAAV hints, spoken-send re-check, terminal safety through
profiles, and the privacy invariants (never in history; read once, at
insert, never before). All providers/readers are fakes."""
from __future__ import annotations

import copy
import json

import pytest

from fluidvoice import insertion
from fluidvoice.config import DEFAULTS
from fluidvoice.context import FocusContext
from fluidvoice.pipeline import DictationPipeline
from tests.test_daemon import StubBackend, make_wav


@pytest.fixture()
def cfg():
    return copy.deepcopy(DEFAULTS)


@pytest.fixture()
def quiet(monkeypatch, tmp_path):
    """Silence notifications; collect logs; isolate history."""
    logs = []
    import fluidvoice.pipeline as pl
    monkeypatch.setattr(pl.ui, "notify",
                        lambda title, body="", timeout_ms=0, enabled=True:
                        None)
    monkeypatch.setattr(pl.history_mod.paths, "history_file",
                        lambda: tmp_path / "h.jsonl")
    return logs


def _reader(ctx: FocusContext | None, calls: list | None = None):
    def read() -> FocusContext | None:
        if calls is not None:
            calls.append(1)
        return ctx
    return read


def _focus(**kw) -> FocusContext:
    kw.setdefault("provider_name", "fake")
    return FocusContext(**kw)


def _run(tmp_path, cfg, backend=None, *, app_hint="TestApp", **kw):
    wav = make_wav(tmp_path / "utt.wav")
    pipe = DictationPipeline(cfg, backend or StubBackend("hello there"),
                             logger=lambda m: None, **kw)
    return pipe, pipe.run(wav, app_hint)


# -- missing context = exactly today's behavior (compat floor) ----------------

class TestMissingContextCompat:
    def test_no_reader_no_formatting_change(self, tmp_path, cfg, quiet):
        inserted = []
        _pipe, out = _run(
            tmp_path, cfg, inserter=lambda t, c: (inserted.append(t),
                                                  "typed")[1])
        assert inserted == ["hello there"]
        assert out["text"] == "hello there"

    def test_reader_returning_missing_context_no_change(self, tmp_path, cfg,
                                                        quiet):
        inserted = []
        _pipe, out = _run(
            tmp_path, cfg,
            context_reader=_reader(_focus(missing=True)),
            inserter=lambda t, c: (inserted.append(t), "typed")[1])
        assert inserted == ["hello there"]

    def test_broken_reader_no_change(self, tmp_path, cfg, quiet):
        def boom():
            raise RuntimeError("provider exploded")

        inserted = []
        _pipe, out = _run(tmp_path, cfg, context_reader=boom,
                          inserter=lambda t, c: (inserted.append(t),
                                                 "typed")[1])
        assert inserted == ["hello there"]
        assert out is not None


# -- continuation (spacing + capitalization) ------------------------------------

class TestContinuation:
    def test_mid_sentence_gets_space_no_capital(self, tmp_path, cfg, quiet):
        inserted = []
        _run(tmp_path, cfg,
             context_reader=_reader(_focus(preceding_text="mid sentence")),
             inserter=lambda t, c: (inserted.append(t), "typed")[1])
        assert inserted == [" hello there"]

    def test_sentence_start_gets_capital_no_space(self, tmp_path, cfg,
                                                  quiet):
        inserted = []
        _run(tmp_path, cfg,
             context_reader=_reader(_focus(preceding_text="Done. ")),
             inserter=lambda t, c: (inserted.append(t), "typed")[1])
        assert inserted == ["Hello there"]

    def test_empty_field_capitalizes(self, tmp_path, cfg, quiet):
        inserted = []
        _run(tmp_path, cfg,
             context_reader=_reader(_focus(preceding_text="")),
             inserter=lambda t, c: (inserted.append(t), "typed")[1])
        assert inserted == ["Hello there"]

    def test_unknown_preceding_untouched(self, tmp_path, cfg, quiet):
        inserted = []
        _run(tmp_path, cfg,
             context_reader=_reader(_focus(preceding_text=None)),
             inserter=lambda t, c: (inserted.append(t), "typed")[1])
        assert inserted == ["hello there"]

    def test_stale_context_skips_continuation(self, tmp_path, cfg, quiet):
        inserted = []
        _run(tmp_path, cfg,
             context_reader=_reader(_focus(preceding_text="Done. ",
                                           stale=True)),
             inserter=lambda t, c: (inserted.append(t), "typed")[1])
        assert inserted == ["hello there"]  # identity-only: no formatting


# -- GAAV by role / profile ------------------------------------------------------

class TestGaav:
    def test_search_role_hint_applies_gaav(self, tmp_path, cfg, quiet):
        inserted = []
        _run(tmp_path, cfg,
             backend=StubBackend("Find the file."),
             context_reader=_reader(_focus(accessible_role="search",
                                           preceding_text="")),
             inserter=lambda t, c: (inserted.append(t), "typed")[1])
        assert inserted == ["find the file"]  # lowercase, no period

    def test_profile_formatting_mode_gaav_forces_it(self, tmp_path, cfg,
                                                    quiet):
        cfg["profiles"]["rules"] = [
            {"match": ["ChatApp"], "formatting_mode": "gaav"}]
        inserted = []
        _run(tmp_path, cfg,
             backend=StubBackend("Hello there."),
             context_reader=_reader(_focus(app_id="org.chat.ChatApp",
                                           accessible_role="text",
                                           preceding_text="")),
             inserter=lambda t, c: (inserted.append(t), "typed")[1])
        assert inserted == ["hello there"]

    def test_plain_text_role_no_gaav(self, tmp_path, cfg, quiet):
        inserted = []
        _run(tmp_path, cfg,
             backend=StubBackend("Hello there."),
             context_reader=_reader(_focus(accessible_role="text",
                                           preceding_text="")),
             inserter=lambda t, c: (inserted.append(t), "typed")[1])
        assert inserted == ["Hello there."]  # GAAV off: period kept

    def test_gaav_wins_over_capitalization(self, tmp_path, cfg, quiet):
        # empty preceding would capitalize; the search hint must not
        cfg["profiles"]["rules"] = [
            {"match": ["Searchy"], "formatting_mode": "gaav"}]
        inserted = []
        _run(tmp_path, cfg,
             backend=StubBackend("query."),
             context_reader=_reader(_focus(app_id="Searchy",
                                           accessible_role="search",
                                           preceding_text="")),
             inserter=lambda t, c: (inserted.append(t), "typed")[1])
        assert inserted == ["query"]


# -- spoken-send + terminal safety through profiles ------------------------------

class TestSpokenSendProfiles:
    def _pressed(self):
        pressed = []
        return pressed, lambda spec: pressed.append(spec)

    def test_take_start_terminal_suppression_unchanged(self, tmp_path, cfg,
                                                       quiet):
        cfg["recording"]["spoken_send_enabled"] = True
        cfg["general"]["terminal_apps"] = ["kitty"]
        pressed, press = self._pressed()
        _run(tmp_path, cfg, backend=StubBackend("ship it send it"),
             app_hint="kitty", key_presser=press,
             inserter=lambda t, c: "typed")
        assert pressed == []

    def test_none_hint_still_presses_today(self, tmp_path, cfg, quiet):
        cfg["recording"]["spoken_send_enabled"] = True
        pressed, press = self._pressed()
        _run(tmp_path, cfg, backend=StubBackend("ship it send it"),
             app_hint=None, key_presser=press,
             inserter=lambda t, c: "typed")
        assert pressed == ["enter"]

    def test_insertion_time_identity_suppresses_terminal(self, tmp_path, cfg,
                                                         quiet):
        # Wayland-like take: no take-start hint, AT-SPI names the terminal
        # only at insertion time - the re-check must still stop the Enter
        cfg["recording"]["spoken_send_enabled"] = True
        cfg["general"]["terminal_apps"] = ["kgx"]
        pressed, press = self._pressed()
        _run(tmp_path, cfg, backend=StubBackend("ship it send it"),
             app_hint=None,
             context_reader=_reader(_focus(app_id="kgx")),
             key_presser=press, inserter=lambda t, c: "typed")
        assert pressed == []

    def test_canonical_on_overrides_terminal_block(self, tmp_path, cfg,
                                                   quiet):
        cfg["recording"]["spoken_send_enabled"] = True
        cfg["profiles"]["rules"] = [
            {"match": ["kgx"], "terminal": True, "spoken_send": "on"}]
        pressed, press = self._pressed()
        _run(tmp_path, cfg, backend=StubBackend("ship it send it"),
             app_hint="kgx", key_presser=press,
             inserter=lambda t, c: "typed")
        assert pressed == ["enter"]  # explicit on wins

    def test_canonical_off_for_non_terminal(self, tmp_path, cfg, quiet):
        cfg["recording"]["spoken_send_enabled"] = True
        cfg["profiles"]["rules"] = [{"match": ["Slack"], "spoken_send":
                                     "off"}]
        pressed, press = self._pressed()
        _run(tmp_path, cfg, backend=StubBackend("ship it send it"),
             app_hint="Slack", key_presser=press,
             inserter=lambda t, c: "typed")
        assert pressed == []


# -- read discipline (once, at insertion, never elsewhere) ------------------------

class TestReadDiscipline:
    def test_reader_called_exactly_once_per_take(self, tmp_path, cfg, quiet):
        calls = []
        _run(tmp_path, cfg, context_reader=_reader(_focus(), calls=calls),
             inserter=lambda t, c: "typed")
        assert calls == [1]

    def test_not_called_on_empty_transcription(self, tmp_path, cfg, quiet):
        calls = []
        _run(tmp_path, cfg, backend=StubBackend("   "),
             context_reader=_reader(_focus(), calls=calls),
             inserter=lambda t, c: "typed")
        assert calls == []

    def test_not_called_in_rewrite_mode(self, tmp_path, cfg, quiet):
        calls = []
        cfg["ai"]["enabled"] = True
        wav = make_wav(tmp_path / "r.wav")
        pipe = DictationPipeline(cfg, StubBackend("shorten this"),
                                 logger=lambda m: None,
                                 context_reader=_reader(_focus(),
                                                        calls=calls),
                                 inserter=lambda t, c: "typed",
                                 rewriter=lambda i, ctx: "RE")
        pipe.run(wav, None, mode="rewrite")
        assert calls == []

    def test_read_happens_after_polish_not_before(self, tmp_path, cfg,
                                                  quiet):
        events = []
        cfg["ai"]["enabled"] = True

        def polisher(text):
            events.append("polish")
            return text

        def reader():
            events.append("read")
            return _focus(preceding_text="x ")

        _run(tmp_path, cfg, polisher=polisher, context_reader=reader,
             inserter=lambda t, c: "typed")
        assert events == ["polish", "read"]

    def test_context_disabled_in_defaults(self):
        # the hermetic default: DEFAULTS builds pipelines with NO reader
        pipe = DictationPipeline(copy.deepcopy(DEFAULTS),
                                 StubBackend("x"), logger=lambda m: None)
        assert pipe.context_reader is None


# -- privacy: the FocusContext never reaches history/logs -------------------------

class TestPrivacy:
    def test_history_entry_free_of_context_data(self, tmp_path, cfg, quiet):
        history = []
        marker_preceding = "ZZ-PRECEDING-SECRET-ZZ"
        marker_selection = "ZZ-SELECTION-SECRET-ZZ"
        _run(tmp_path, cfg,
             context_reader=_reader(_focus(
                 app_id="zz-app", window_title="zz title",
                 accessible_role="text",
                 preceding_text=marker_preceding,
                 selection_text=marker_selection)),
             history_writer=lambda entry, wav: history.append(entry),
             inserter=lambda t, c: "typed")
        assert history, "take must still be recorded"
        entry = history[0]
        dumped = json.dumps(entry)
        assert marker_preceding not in dumped
        assert marker_selection not in dumped
        assert "zz title" not in dumped
        assert "zz-app" not in dumped  # even the identity stays out
        # the entry shape is exactly today's (app = take-start hint only)
        assert set(entry) == {"ts", "duration_s", "raw", "text", "ai",
                              "backend", "app"}
        assert entry["app"] == "TestApp"

    def test_logs_never_contain_context_text(self, tmp_path, cfg):
        import contextlib
        import io
        logs = io.StringIO()
        cfg["notifications"]["enabled"] = False
        with contextlib.redirect_stderr(logs):
            _run(tmp_path, cfg,
                 context_reader=_reader(_focus(
                     preceding_text="QQ-LOG-LEAK-QQ",
                     selection_text="QQ-SEL-LEAK-QQ")),
                 inserter=lambda t, c: "typed")
        assert "QQ-LOG-LEAK-QQ" not in logs.getvalue()
        assert "QQ-SEL-LEAK-QQ" not in logs.getvalue()


# -- identity reaching the insertion path -----------------------------------------

class TestIdentityPassThrough:
    """The default inserter wrapper threads the insertion-time identity
    into insert_text as wm_class (observed through the terminal trailing
    -space decision - the monkeypatched insert_typed never spawns a
    subprocess)."""

    def _spy(self, monkeypatch):
        typed = []
        monkeypatch.setattr(insertion, "insert_typed",
                            lambda text, delay, tool=None:
                            typed.append(text) or "typed")
        return typed

    def test_focus_identity_drives_terminal_quirks(self, tmp_path, cfg,
                                                   monkeypatch):
        cfg["general"]["terminal_apps"] = ["kitty"]
        typed = self._spy(monkeypatch)
        _run(tmp_path, cfg,
             context_reader=_reader(_focus(app_id="kitty")),
             )
        assert typed == ["hello there "]  # autocomplete space applied

    def test_no_context_keeps_live_lookup(self, tmp_path, cfg, monkeypatch):
        cfg["general"]["terminal_apps"] = ["kitty"]
        typed = self._spy(monkeypatch)
        monkeypatch.setattr(insertion, "active_window_class",
                            lambda: "firefox")
        _run(tmp_path, cfg)
        assert typed == ["hello there"]  # live lookup: not a terminal


# -- insertion-mode / terminal quirks through profiles ------------------------------

class TestInsertionConsumesProfiles:
    def test_profile_insertion_mode_override_forces_paste(self, cfg,
                                                           monkeypatch):
        cfg["profiles"]["rules"] = [
            {"match": ["BigApp"], "insertion_mode": "paste"}]
        pasted, typed = [], []
        monkeypatch.setattr(insertion, "insert_paste",
                            lambda text, **kw: pasted.append(text)
                            or "paste")
        monkeypatch.setattr(insertion, "insert_typed",
                            lambda text, delay, tool=None:
                            typed.append(text) or "typed")
        assert insertion.insert_text("hi", cfg, wm_class="BigApp") == "paste"
        assert pasted == ["hi"] and typed == []
        # a non-matching app inherits the global typed mode
        assert insertion.insert_text("hi", cfg, wm_class="other") == "typed"

    def test_terminal_quirks_via_canonical_rule(self, cfg, monkeypatch):
        cfg["profiles"]["rules"] = [
            {"match": ["myterm"], "terminal": True, "spoken_send": "off"}]
        cfg["general"]["terminal_apps"] = []  # legacy list empty
        keys = []
        monkeypatch.setattr(insertion, "insert_paste",
                            lambda text, **kw: keys.append(kw["key"])
                            or "pasted")
        monkeypatch.setattr(insertion, "insert_typed",
                            lambda text, delay, tool=None: text)
        strategy = insertion.insert_text("-leading dash needs paste", cfg,
                                         wm_class="myterm-host")
        assert strategy == "paste"
        assert keys == ["ctrl+shift+v"]  # canonical rule, not legacy list

    def test_wayland_terminal_quirks_only_with_identity(self, cfg,
                                                        monkeypatch):
        from fluidvoice import session as session_mod
        monkeypatch.setattr(session_mod, "current",
                            lambda: session_mod.probe(
                                {"XDG_SESSION_TYPE": "wayland"}))
        monkeypatch.setattr(insertion, "_resolve_wayland_tool",
                            lambda c, which=None: ("wtype", ""))
        keys = {}

        def fake_paste(text, *, key="ctrl+v", tool=None, on_notice=None):
            keys["paste"] = key
            raise insertion.InsertError("paste failed")

        typed_texts = []

        def fake_typed(text, delay, tool=None):
            typed_texts.append(text)

        monkeypatch.setattr(insertion, "_insert_paste_wayland", fake_paste)
        monkeypatch.setattr(insertion, "insert_typed", fake_typed)
        cfg["general"]["terminal_apps"] = ["kitty"]
        # WITH identity (leading dash forces paste): terminal paste key,
        # then typed fallback carries the autocomplete space
        insertion.insert_text("-dashed word", cfg, wm_class="kitty")
        assert keys["paste"] == "ctrl+shift+v"
        assert typed_texts[-1] == "-dashed word "
        # WITHOUT identity: today's wayland behavior, quirks inert
        insertion.insert_text("-dashed word", cfg)
        assert keys["paste"] == "ctrl+v"
        assert typed_texts[-1] == "-dashed word"
