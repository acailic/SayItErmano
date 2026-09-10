"""Real CLI invocations through the actual console script."""
import os
import subprocess

import pytest

from tests.integration.conftest import REPO

pytestmark = pytest.mark.integration

FV = str(REPO / ".venv/bin/fluidvoice")


def run_cli(args, env=None, timeout=300):
    return subprocess.run([FV, *args], capture_output=True, text=True,
                          timeout=timeout, env={**os.environ, **(env or {})})


class TestCli:
    def test_version(self):
        out = run_cli(["--version"])
        assert out.returncode == 0 and out.stdout.strip().startswith("0.")

    def test_help_lists_commands(self):
        out = run_cli(["--help"], env={"COLUMNS": "200"})
        for cmd in ("daemon", "toggle", "cancel", "status", "transcribe",
                    "history", "config", "settings", "doctor"):
            assert cmd in out.stdout
        # paste-last may be elided from the usage line on narrow terminals,
        # but must appear in the detailed subcommand list
        assert "paste-last" in out.stdout

    def test_doctor_reports_ready(self):
        out = run_cli(["doctor"])
        assert out.returncode in (0, 1)
        assert "session:" in out.stdout and "speech backends:" in out.stdout

    def test_config_init_print_path_roundtrip(self, isolated_env, tmp_path):
        out = run_cli(["config", "init"])
        assert out.returncode == 0
        assert "Right_Control" in run_cli(["config", "print"]).stdout
        # user edits survive a print (no rewriting of the file)
        cfg = tmp_path / "config.toml"
        cfg.write_text('[hotkey]\nkey = "F9"\n')
        out = run_cli(["config", "print"])
        assert 'key = "F9"' in out.stdout

    def test_transcribe_one_shot(self, isolated_env, jfk_wav):
        from tests.integration.conftest import skip_if_gpu_busy
        skip_if_gpu_busy()
        out = run_cli(["transcribe", str(jfk_wav)])
        assert out.returncode == 0
        assert "americans" in out.stdout.lower()

    def test_history_output(self, isolated_env):
        from fluidvoice import history
        history.append({"ts": 1700000000, "text": "test entry", "ai": False})
        out = run_cli(["history", "-n", "5"])
        assert "test entry" in out.stdout

    def test_toggle_without_daemon_fails_cleanly(self, isolated_env):
        out = run_cli(["toggle"])
        assert out.returncode == 1
        assert "daemon not running" in out.stderr
