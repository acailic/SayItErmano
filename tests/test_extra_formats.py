"""GAAV + spoken-send + rewrite mode tests."""
from __future__ import annotations

import time

from fluidvoice import daemon as dm
from fluidvoice.processing.extra_formats import apply_gaav, parse_spoken_send
from fluidvoice.rewrite import RewriteError, build_edit_messages
from tests.test_daemon import (  # noqa: F401 (cfg/quiet_ui are pytest fixtures)
    StubBackend,
    StubRecorder,
    cfg,
    make_wav,
    quiet_ui,
)


class TestGAAV:
    def test_strips_trailing_period(self):
        assert apply_gaav("Hello world.", lowercase_first=False,
                          remove_trailing_period=True) == "Hello world"

    def test_lowercases_first_letter(self):
        assert apply_gaav("Hello world", lowercase_first=True,
                          remove_trailing_period=False) == "hello world"

    def test_both(self):
        assert apply_gaav("Search query.", lowercase_first=True, remove_trailing_period=True) == "search query"

    def test_noop_when_disabled(self):
        assert apply_gaav("Hello.", lowercase_first=False, remove_trailing_period=False) == "Hello."

    def test_single_period_only(self):
        assert apply_gaav("3.14", lowercase_first=True, remove_trailing_period=True) == "3.14"


class TestSpokenSendParser:
    def test_strips_phrase_and_flags_send(self):
        r = parse_spoken_send("hello there send it")
        assert r.text == "hello there" and r.should_send is True

    def test_no_phrase_no_send(self):
        r = parse_spoken_send("hello there")
        assert r.text == "hello there" and r.should_send is False

    def test_literal_escape_keeps_words(self):
        r = parse_spoken_send("type it out literal send it")
        assert r.should_send is False
        assert r.text.endswith("send it")

    def test_phrase_with_punctuation(self):
        r = parse_spoken_send("message body, send it.")
        assert r.text == "message body" and r.should_send is True

    def test_custom_phrase(self):
        r = parse_spoken_send("ovo je poruka pošalji", "pošalji")
        assert r.text == "ovo je poruka" and r.should_send is True

    def test_phrase_mid_text_not_stripped(self):
        r = parse_spoken_send("send it to john")
        assert r.should_send is False and r.text == "send it to john"


class TestSpokenSendPipeline:
    def _run(self, tmp_path, cfg, backend_text, app_hint=None, **pipeline_kw):
        keys = []
        inserted = []
        pipe = dm.DictationPipeline(
            cfg, StubBackend(backend_text),
            inserter=lambda t, c: inserted.append(t) or "typed",
            key_presser=lambda spec: keys.append(spec), **pipeline_kw)
        wav = make_wav(tmp_path / "utt.wav")
        return pipe, pipe.run(wav, app_hint), keys, inserted

    def test_enter_pressed_after_insertion(self, tmp_path, cfg, quiet_ui):
        cfg["recording"]["spoken_send_enabled"] = True
        _, out, keys, inserted = self._run(tmp_path, cfg, "hello world send it")
        assert out is not None and keys == ["enter"]
        assert out["text"] == "hello world"
        assert "enter" in out["strategy"]

    def test_disabled_by_default(self, tmp_path, cfg, quiet_ui):
        _, out, keys, _ = self._run(tmp_path, cfg, "hello world send it")
        assert out is not None and keys == []

    def test_custom_key_combo(self, tmp_path, cfg, quiet_ui):
        cfg["recording"]["spoken_send_enabled"] = True
        cfg["recording"]["spoken_send_key"] = "shift+enter"
        _, out, keys, _ = self._run(tmp_path, cfg, "chat reply send it")
        assert keys == ["shift+enter"]

    def test_literal_escape_no_key(self, tmp_path, cfg, quiet_ui):
        cfg["recording"]["spoken_send_enabled"] = True
        _, out, keys, _ = self._run(tmp_path, cfg, "type literal send it")
        assert keys == [] and "send it" in out["text"]

    def test_gaav_applied_after_ai(self, tmp_path, cfg, quiet_ui):
        cfg["processing"]["gaav_enabled"] = True
        _, out, _, _ = self._run(tmp_path, cfg, "Hello world.")
        assert out["text"] == "hello world"


class TestSpokenSendTerminalBlocklist:
    """In terminal apps the phrase still strips and the text still
    inserts, but Enter is never pressed (upstream strip-but-no-Enter,
    ContentView.swift:2786-2798)."""

    def _run(self, tmp_path, cfg, backend_text, app_hint):
        keys = []
        inserted = []
        pipe = dm.DictationPipeline(
            cfg, StubBackend(backend_text),
            inserter=lambda t, c: inserted.append(t) or "typed",
            key_presser=lambda spec: keys.append(spec))
        wav = make_wav(tmp_path / "utt.wav")
        return pipe.run(wav, app_hint), keys, inserted

    def test_enter_suppressed_in_terminal(self, tmp_path, cfg, quiet_ui):
        cfg["recording"]["spoken_send_enabled"] = True
        out, keys, inserted = self._run(
            tmp_path, cfg, "/ fix the deploy send it", "kitty")
        assert inserted == ["/fix the deploy"]  # phrase stripped, squeeze applied
        assert keys == []                       # Enter NEVER pressed
        assert out["strategy"] == "typed"      # no +enter suffix

    def test_enter_pressed_in_non_terminal(self, tmp_path, cfg, quiet_ui):
        cfg["recording"]["spoken_send_enabled"] = True
        out, keys, inserted = self._run(
            tmp_path, cfg, "hello world send it", "firefox")
        assert keys == ["enter"]
        assert out["strategy"] == "typed+enter"

    def test_blocklist_is_list_driven(self, tmp_path, cfg, quiet_ui):
        cfg["recording"]["spoken_send_enabled"] = True
        cfg["general"]["terminal_apps"] = []
        out, keys, _ = self._run(tmp_path, cfg, "rm -rf send it", "kitty")
        assert keys == ["enter"]  # empty list -> nothing blocked

    def test_no_app_hint_presses_enter(self, tmp_path, cfg, quiet_ui):
        # focus unknown at recording start -> cannot prove terminal -> send
        cfg["recording"]["spoken_send_enabled"] = True
        out, keys, _ = self._run(tmp_path, cfg, "hello send it", None)
        assert keys == ["enter"]

    def test_disabled_spoken_send_unaffected(self, tmp_path, cfg, quiet_ui):
        out, keys, inserted = self._run(
            tmp_path, cfg, "hello world send it", "kitty")
        assert inserted == ["hello world send it"]  # phrase kept
        assert keys == []

    def test_custom_list_blocks(self, tmp_path, cfg, quiet_ui):
        cfg["recording"]["spoken_send_enabled"] = True
        cfg["general"]["terminal_apps"] = ["contour"]
        out, keys, inserted = self._run(
            tmp_path, cfg, "hello send it", "Contour")
        assert keys == [] and inserted == ["hello"]

    def test_skip_badge_set_on_pill(self, tmp_path, cfg, quiet_ui):
        cfg["recording"]["spoken_send_enabled"] = True
        badges = []
        pipe = dm.DictationPipeline(
            cfg, StubBackend("hello send it"),
            inserter=lambda t, c: "typed",
            key_presser=lambda spec: None)
        pipe._set_pill_badge = badges.append
        wav = make_wav(tmp_path / "utt.wav")
        pipe.run(wav, "kitty")
        assert "⏎ skipped (terminal)" in badges
        assert pipe._pending_send_skipped_terminal is False  # consumed


class TestRewriteMessages:
    def test_with_context(self):
        msgs = build_edit_messages("make it shorter", "the long original text")
        assert msgs[0]["role"] == "system"
        assert "the long original text" in msgs[0]["content"]
        assert "Apply the instruction to the selected context" in msgs[1]["content"]
        assert "make it shorter" in msgs[1]["content"]

    def test_without_context(self):
        msgs = build_edit_messages("write a haiku about cats", None)
        assert "selected context" not in msgs[1]["content"]
        assert "Output ONLY the requested text" in msgs[1]["content"]

    def test_followup_history(self):
        history = [{"role": "user", "content": "first"},
                   {"role": "assistant", "content": "draft"}]
        msgs = build_edit_messages("shorter now", "ctx", history=history)
        assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
        assert "Follow-up instruction" in msgs[-1]["content"]

    def test_edit_prompt_is_upstream_verbatim(self):
        msgs = build_edit_messages("x", None)
        assert msgs[0]["content"].startswith(
            "You are a helpful writing assistant.")


class TestRewritePipeline:
    def test_rewrite_flow(self, tmp_path, cfg, quiet_ui):
        cfg["ai"]["enabled"] = True
        calls = []
        inserted = []

        def rewriter(instruction, context):
            calls.append((instruction, context))
            return "REWRITTEN"

        pipe = dm.DictationPipeline(
            cfg, StubBackend("make this shorter"),
            inserter=lambda t, c: inserted.append(t) or "typed",
            rewriter=rewriter)
        wav = make_wav(tmp_path / "r.wav")
        out = pipe.run(wav, None, mode="rewrite", rewrite_context="long text")
        assert out["mode"] == "rewrite" and out["text"] == "REWRITTEN"
        assert calls == [("make this shorter", "long text")]
        assert inserted == ["REWRITTEN"]

    def test_rewrite_error_notifies_and_skips(self, tmp_path, cfg, quiet_ui):
        cfg["ai"]["enabled"] = True

        def broken(instruction, context):
            raise RewriteError("model down")

        pipe = dm.DictationPipeline(
            cfg, StubBackend("instruction"),
            inserter=lambda t, c: (_ for _ in ()).throw(AssertionError("no insert")),
            rewriter=broken)
        wav = make_wav(tmp_path / "e.wav")
        assert pipe.run(wav, None, mode="rewrite", rewrite_context=None) is None
        assert any("Rewrite failed" in (t + b) for t, b in quiet_ui["notify"])


class TestDaemonRewriteMode:
    def test_start_rewrite_captures_and_records(self, cfg, quiet_ui, monkeypatch):
        from fluidvoice import rewrite as rw
        monkeypatch.setattr(rw, "capture_selection", lambda: "selected words")
        seen = {}

        class CapturingPipeline:
            def __init__(self, c, backend):
                seen["backend"] = backend.name

            def run(self, wav, app_hint, mode="dictate", rewrite_context=None):
                seen["mode"] = mode
                seen["context"] = rewrite_context
                return {"text": "ok", "raw": "ok", "ai": False, "strategy": "typed"}

        rec = StubRecorder()
        d = dm.Daemon(cfg, recorder=rec,
                      backend_factory=lambda c: StubBackend("shorten this"),
                      pipeline_factory=CapturingPipeline,
                      use_hotkey=False, use_sounds=False)
        d.backend = StubBackend("shorten this")
        d.start_rewrite()
        assert d.recording
        assert d._rewrite_context == "selected words"
        d.toggle()  # stop -> pipeline thread runs with mode=rewrite
        deadline = time.monotonic() + 5
        while d.busy and time.monotonic() < deadline:
            time.sleep(0.02)
        assert seen["mode"] == "rewrite"
        assert seen["context"] == "selected words"
        assert d.last_result.get("text") == "ok"

    def test_rewrite_ignored_while_busy(self, cfg, quiet_ui, monkeypatch):
        from fluidvoice import rewrite as rw
        monkeypatch.setattr(rw, "capture_selection", lambda: "")
        d = dm.Daemon(cfg, recorder=StubRecorder(),
                      backend_factory=lambda c: StubBackend("x"),
                      use_hotkey=False, use_sounds=False)
        d.busy = True
        d.start_rewrite()
        assert not d.recording
