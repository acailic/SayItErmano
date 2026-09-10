import json
import sys
from pathlib import Path

import pytest

from fluidvoice import control
from fluidvoice.config import DEFAULTS, load_config


class TestConfig:
    def test_defaults_complete(self):
        for section in ("general", "hotkey", "recording", "model", "processing",
                        "ai", "insertion", "sounds", "notifications", "history"):
            assert section in DEFAULTS

    def test_load_missing_file_gives_defaults(self, tmp_path: Path):
        cfg = load_config(tmp_path / "missing.toml")
        assert cfg["hotkey"]["key"] == "Right_Control"
        assert cfg["ai"]["enabled"] is False

    def test_load_overrides(self, tmp_path: Path):
        f = tmp_path / "c.toml"
        f.write_text('[hotkey]\nkey = "F9"\n[ai]\nenabled = true\n')
        cfg = load_config(f)
        assert cfg["hotkey"]["key"] == "F9"
        assert cfg["hotkey"]["mode"] == "toggle"  # untouched default
        assert cfg["ai"]["enabled"] is True


class TestDoctorWhispercpp:
    """_whispercpp_lines resolution report (pure function, faked binary)."""

    def lines(self, model_value="", binary="/usr/bin/whisper-cli"):
        from fluidvoice import doctor
        self.monkeypatch.setattr(doctor.backends, "_whispercpp_binary",
                                 lambda: binary)
        cfg = {"model": {"whispercpp_model": model_value}}
        return doctor._whispercpp_lines(cfg)

    @pytest.fixture(autouse=True)
    def _cache(self, tmp_path, monkeypatch):
        self.monkeypatch = monkeypatch
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    def test_binary_missing_and_model_unset(self):
        lines = self.lines(model_value="", binary=None)
        assert any("not found" in l for l in lines)
        assert any("not set" in l for l in lines)

    def test_catalog_name_downloaded(self, tmp_path):
        from fluidvoice import model_catalog
        model_catalog.gguf_dir().mkdir(parents=True)
        model_catalog.gguf_path("ggml-base.bin").write_bytes(b"x")
        lines = self.lines(model_value="ggml-base.bin")
        assert any("downloaded" in l and "ggml-base.bin" in l for l in lines)
        assert any(str(model_catalog.gguf_path("ggml-base.bin")) in l
                   for l in lines)

    def test_catalog_name_not_downloaded(self):
        lines = self.lines(model_value="ggml-base.bin")
        assert any("not downloaded" in l for l in lines)
        assert any("downloaded ggml models: none" in l for l in lines)

    def test_path_value_found_and_missing(self, tmp_path):
        p = tmp_path / "m.bin"
        p.write_bytes(b"x")
        assert any("found" in l for l in self.lines(model_value=str(p)))
        missing = tmp_path / "gone.bin"
        assert any("MISSING" in l for l in self.lines(model_value=str(missing)))

    def test_unknown_bare_name_lists_catalog(self):
        lines = self.lines(model_value="ggml-bogus.bin")
        assert any("unknown name" in l and "ggml-base.bin" in l
                   for l in lines)


class TestDoctorParakeet:
    """_parakeet_lines resolution report (onnxruntime stubbed via sys.modules)."""

    @pytest.fixture(autouse=True)
    def _cache(self, tmp_path, monkeypatch):
        self.monkeypatch = monkeypatch
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    def lines(self, backend="", name="", downloaded=lambda n: False):
        from fluidvoice import doctor, model_catalog
        self.monkeypatch.setattr(model_catalog, "parakeet_downloaded",
                                 downloaded)
        cfg = {"model": {"backend": backend, "name": name}}
        return doctor._parakeet_lines(cfg)

    def test_not_installed(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "onnxruntime", None)
        lines = self.lines()
        assert len(lines) == 1
        assert "not installed" in lines[0]
        assert "pip install onnxruntime" in lines[0]

    def test_installed_models_not_downloaded(self):
        from fluidvoice import model_catalog
        lines = self.lines()
        assert any("onnxruntime" in l for l in lines)
        for name in model_catalog.PARAKEET_CATALOG:
            assert any(name in l and "not downloaded" in l and
                       "joiner.int8.onnx" in l for l in lines), name

    def test_configured_and_downloaded(self):
        lines = self.lines(backend="parakeet", name="parakeet-tdt-0.6b-v3",
                           downloaded=lambda n: n == "parakeet-tdt-0.6b-v3")
        assert any("parakeet-tdt-0.6b-v3: downloaded" in l for l in lines)
        assert any("active model: parakeet-tdt-0.6b-v3 (downloaded)"
                   for l in lines)

    def test_configured_missing_file(self, tmp_path):
        d = tmp_path / "sayit-ermano" / "models" / "parakeet" \
            / "parakeet-tdt-0.6b-v2"
        d.mkdir(parents=True)
        for f in ("encoder.int8.onnx", "decoder.int8.onnx", "tokens.txt"):
            (d / f).write_bytes(b"x")
        lines = self.lines(backend="parakeet", name="parakeet-tdt-0.6b-v2")
        assert any("missing: joiner.int8.onnx" in l for l in lines)
        assert any("active model: parakeet-tdt-0.6b-v2 (not downloaded)"
                   for l in lines)

    def test_configured_unknown_name_lists_catalog(self):
        lines = self.lines(backend="parakeet", name="parakeet-t5")
        assert any("unknown name 'parakeet-t5'" in l
                   and "parakeet-tdt-0.6b-v2" in l for l in lines)


class TestDoctorFormattingLines:
    """_formatting_lines: one resolution line per new formatting key."""

    def test_defaults_on(self):
        from fluidvoice import doctor
        lines = doctor._formatting_lines(DEFAULTS)
        assert len(lines) == 3
        assert any("slash/mention squeeze: on" in l
                   and "processing.slash_mention_squeeze" in l for l in lines)
        assert any("terminal autocomplete space: on" in l
                   and "insertion.terminal_autocomplete_space" in l
                   for l in lines)
        apps = DEFAULTS["general"]["terminal_apps"]
        apps_line = next(l for l in lines if "terminal_apps" in l)
        assert f"terminal_apps ({len(apps)})" in apps_line
        assert "kitty" in apps_line and "spoken-send Enter suppressed" in apps_line

    def test_disabled_shows_off(self):
        import copy

        from fluidvoice import doctor
        cfg = copy.deepcopy(DEFAULTS)
        cfg["processing"]["slash_mention_squeeze"] = False
        cfg["insertion"]["terminal_autocomplete_space"] = False
        lines = doctor._formatting_lines(cfg)
        assert any("slash/mention squeeze: off" in l for l in lines)
        assert any("terminal autocomplete space: off" in l for l in lines)

    def test_empty_list_counts_zero(self):
        import copy

        from fluidvoice import doctor
        cfg = copy.deepcopy(DEFAULTS)
        cfg["general"]["terminal_apps"] = []
        lines = doctor._formatting_lines(cfg)
        assert any("terminal_apps (0)" in l and "none" in l for l in lines)


class TestDoctorInsertionLines:
    """_insertion_lines: one resolution line per hardening key."""

    def test_defaults_on(self):
        from fluidvoice import doctor
        lines = doctor._insertion_lines(DEFAULTS)
        assert len(lines) == 2
        assert any("paste verification: on" in l
                   and "insertion.verify_paste" in l for l in lines)
        assert any("terminal paste key: ctrl+shift+v" in l
                   and "insertion.terminal_paste_key" in l for l in lines)

    def test_disabled_shows_off(self):
        import copy

        from fluidvoice import doctor
        cfg = copy.deepcopy(DEFAULTS)
        cfg["insertion"]["verify_paste"] = False
        lines = doctor._insertion_lines(cfg)
        assert any("paste verification: off" in l for l in lines)

    def test_custom_key_shown(self):
        import copy

        from fluidvoice import doctor
        cfg = copy.deepcopy(DEFAULTS)
        cfg["insertion"]["terminal_paste_key"] = "ctrl+alt+v"
        lines = doctor._insertion_lines(cfg)
        assert any("terminal paste key: ctrl+alt+v" in l for l in lines)

    def test_run_prints_section(self, capsys):
        from fluidvoice import doctor
        doctor.run()
        out = capsys.readouterr().out
        assert "insertion hardening:" in out


class TestDoctorCommandModeLines:
    """_command_mode_lines (v2): ai readiness, tool count, destructive
    pattern counts (28 built-in + n user), context window."""

    def test_not_configured_by_default(self):
        from fluidvoice import doctor
        lines = doctor._command_mode_lines(DEFAULTS)
        assert len(lines) == 4
        assert any("ai: not configured" in l for l in lines)

    def test_ready_line_with_model(self):
        import copy

        from fluidvoice import doctor
        cfg = copy.deepcopy(DEFAULTS)
        cfg["ai"].update(enabled=True, base_url="http://x:11434/v1",
                          model="qwen3:8b")
        lines = doctor._command_mode_lines(cfg)
        assert any("ai: ready (model qwen3:8b)" in l for l in lines)

    def test_tool_count_from_registry(self):
        from fluidvoice import doctor
        lines = doctor._command_mode_lines(DEFAULTS)
        assert any("tools: 1 (execute_terminal_command)" in l
                   for l in lines)

    def test_pattern_counts_builtin_plus_user(self):
        import copy

        from fluidvoice import doctor
        cfg = copy.deepcopy(DEFAULTS)
        cfg["command"]["destructive_patterns"] = ["git push", "shutdown"]
        lines = doctor._command_mode_lines(cfg)
        assert any("destructive patterns: 30 built-in + 2 user" in l
                   for l in lines)  # 19 prefixes + 11 patterns (#861 +2)

    def test_context_window_default_and_disabled(self):
        import copy

        from fluidvoice import doctor
        lines = doctor._command_mode_lines(DEFAULTS)
        assert any("context window: 300 s" in l
                   and "last 5 results per app" in l for l in lines)
        cfg = copy.deepcopy(DEFAULTS)
        cfg["command"]["context_window_s"] = 0.0
        lines = doctor._command_mode_lines(cfg)
        assert any("context window: disabled" in l
                   and "context_window_s = 0" in l for l in lines)

    def test_run_prints_section(self, capsys):
        from fluidvoice import doctor
        doctor.run()
        out = capsys.readouterr().out
        assert "command mode:" in out


class TestDoctorSuggestionsLine:
    """_suggestions_line: pending count + decisions-file path."""

    @pytest.fixture(autouse=True)
    def _paths(self, tmp_path, monkeypatch):
        from fluidvoice import paths
        hpath = tmp_path / "history.jsonl"
        spath = tmp_path / "dictionary-suggestions.json"
        monkeypatch.setattr(paths, "history_file", lambda: hpath)
        monkeypatch.setattr(paths, "dictionary_suggestions_file",
                            lambda: spath)
        self.hpath, self.spath = hpath, spath

    def test_zero_pending_without_files(self):
        from fluidvoice import doctor
        line = doctor._suggestions_line(DEFAULTS)
        assert "dictionary suggestions: 0 pending" in line
        assert str(self.spath) in line

    def test_counts_pending_from_seeded_history(self):
        from fluidvoice import doctor, history
        # two distinct pairs (each seen twice) -> two pending suggestions
        for ts, old, new in [
                (1.0, "open the miro board app", "open the Miro board app"),
                (2.0, "open the miro board now", "open the Miro board now"),
                (3.0, "please send the flud report",
                 "please send the fluid report"),
                (4.0, "the flud report again", "the fluid report again")]:
            history.append({"ts": ts, "text": old})
            history.update_text(ts, new)
        line = doctor._suggestions_line(DEFAULTS)
        assert "dictionary suggestions: 2 pending" in line
        assert str(self.spath) in line

    def test_run_prints_section(self, capsys):
        from fluidvoice import doctor
        doctor.run()
        out = capsys.readouterr().out
        assert "dictionary learning:" in out


class TestDoctorHistoryLines:
    """_history_lines: entry count, size, oldest date, test-row warning."""

    @pytest.fixture(autouse=True)
    def _paths(self, tmp_path, monkeypatch):
        from fluidvoice import paths
        self.hpath = tmp_path / "history.jsonl"
        monkeypatch.setattr(paths, "history_file", lambda: self.hpath)
        return self.hpath

    def _seed(self, entries):
        self.hpath.parent.mkdir(parents=True, exist_ok=True)
        self.hpath.write_text("".join(json.dumps(e) + "\n" for e in entries),
                              encoding="utf-8")

    def test_seeded_history_counts_size_oldest_warning(self):
        import time

        from fluidvoice import doctor
        self._seed([
            {"ts": 1000000000.0, "text": "old"},
            {"ts": 1000000600.0, "mode": "command", "command": "true 1",
             "purpose": "p"},
            {"ts": 1000000700.0, "mode": "command", "command": "exit 3",
             "purpose": "fail"},
        ])
        lines = doctor._history_lines()
        assert lines[0] == f"history: {self.hpath}"
        detail = lines[1]
        assert "entries: 3" in detail
        assert "KB)" in detail and float(detail.split("(")[1].split(" KB")[0]) > 0
        expected_oldest = time.strftime("%Y-%m-%d %H:%M",
                                         time.localtime(1000000000.0))
        assert f"oldest: {expected_oldest}" in detail
        assert "test rows: 2" in detail
        assert any("WARNING: 2 test-fingerprint rows" in l
                   and "--scrub-tests" in l for l in lines[2:])

    def test_zero_test_rows_no_warning(self):
        from fluidvoice import doctor
        self._seed([{"ts": 1000000000.0, "text": "only real"}])
        lines = doctor._history_lines()
        assert "entries: 1" in lines[1] and "test rows: 0" in lines[1]
        assert not any("WARNING" in l for l in lines)

    def test_missing_file(self):
        from fluidvoice import doctor
        lines = doctor._history_lines()
        assert lines[0] == f"history: {self.hpath}"
        assert "entries: 0 (no history yet), test rows: 0" in lines[1]
        assert not any("WARNING" in l for l in lines)

    def test_run_prints_history_section(self, capsys):
        from fluidvoice import doctor
        self._seed([{"ts": 1000000000.0, "text": "real"}])
        doctor.run()
        out = capsys.readouterr().out
        assert "history:" in out and "test rows: 0" in out


class TestControlSocket:
    def test_round_trip(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(control.paths, "socket_path", lambda: tmp_path / "s.sock")
        state = {"recording": False}

        def handler(req):
            if req["action"] == "toggle":
                state["recording"] = not state["recording"]
            return {"ok": True, "recording": state["recording"]}

        srv = control.serve(handler)
        try:
            r1 = control.request("toggle")
            r2 = control.request("toggle")
            assert r1 == {"ok": True, "recording": True}
            assert r2 == {"ok": True, "recording": False}
        finally:
            srv.close()

    def test_no_daemon(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(control.paths, "socket_path", lambda: tmp_path / "nope.sock")
        try:
            control.request("toggle")
            assert False, "expected ControlError"
        except control.ControlError:
            pass
