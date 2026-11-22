"""Q9 process lane: the soak harness and the upgrade-rehearsal route
driven as REAL processes — daemon subprocess end-to-end for soak_run,
and the real one-shot installer dispatch for upgrade_rehearsal — both
model-free, offline, no display (the Q7 lane contract).
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not (shutil.which("bash") and
                            shutil.which("dpkg-deb")),
                       reason="bash + dpkg-deb required"),
]


def _load(name: str, script: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / script)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


soak = _load("soak_run_proc", "soak_run.py")
ur = _load("upgrade_rehearsal_proc", "upgrade_rehearsal.py")


class TestSoakHarnessRealDaemon:
    """A 45 s soak of the real daemon: takes, samples, verdict PASS."""

    def test_short_soak_end_to_end(self, tmp_path):
        report = soak.run_soak(
            minutes=0.75, interval_s=5.0, warmup_s=15.0,
            rss_tolerance=0.15, record_s=1.0, keep_sandbox=tmp_path)
        assert report["takes"]["total"] >= 2
        assert report["takes"]["failed"] == 0
        assert len(report["samples"]) >= 6
        assert all(s["socket_ok"] for s in report["samples"])
        assert all(s["rss_kb"] > 0 and s["threads"] > 0 for s in
                   report["samples"])
        verdict = report["verdict"]
        assert verdict["passed"], verdict["reasons"]
        assert verdict["growth"]["growth_frac"] <= 0.15
        # the tolerance math is visible in the evidence shape
        assert verdict["growth"]["baseline_kb"] > 0
        assert "tolerance" in verdict["reasons"][0]
        # the daemon log shows the model-free take cycle ran
        log = "\n".join(report["daemon_log_tail"])
        assert "recording" in log
        assert "transcription failed" in log  # HF offline fast-fail


class TestUpgradeRouteRealOneShot:
    """The one-shot user route end-to-end with stub-venv debs: dispatch,
    version switch, config/history retention, rollback — no pip, no
    network, the same dpkg-deb/dash/cp mechanics a real upgrade uses."""

    @staticmethod
    def stub_venv(version: str):
        def builder(venv_dir: Path, tree: Path, run=None, with_deps=True):
            binpy = venv_dir / "bin" / "python"
            binpy.parent.mkdir(parents=True)
            binpy.write_text(
                "#!/bin/sh\n"
                "# stub venv python: `-m fluidvoice --version` cats the\n"
                "# venv's bundled version file (what an upgrade replaces)\n"
                'case " $* " in *" --version "*)\n'
                '    cat "$0/../version" 2>/dev/null || echo "0.0.0"\n'
                "    exit 0;; esac\n"
                "exit 0\n")
            binpy.chmod(0o755)
            (venv_dir / "version").write_text(version + "\n")
        return builder

    def test_install_upgrade_rollback_retention(self, tmp_path):
        tree = tmp_path / "tree"
        (tree / "fluidvoice").mkdir(parents=True)
        old = ur.build_deb(tmp_path / "pkg-old", tree, "0.9.0",
                           venv_builder=self.stub_venv("0.9.0"))
        new = ur.build_deb(tmp_path / "pkg-new", tree, "0.9.1",
                           venv_builder=self.stub_venv("0.9.1"))
        home = tmp_path / "home"
        home.mkdir()

        rc, out = ur.run_one_shot(home, old, update=False)
        assert rc == 0, out
        assert ur.installed_version(home) == "0.9.0"

        cfg = home / ".config" / "sayit-ermano" / "config.toml"
        hist = home / ".local" / "share" / "sayit-ermano" / "history.jsonl"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        hist.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text('[general]\nlanguage = "de"\n\n[sounds]\nvolume = 0.3\n')
        hist.write_text('{"ts": 1, "text": "a"}\n{"ts": 2, "text": "b"}\n')
        cfg_bytes, hist_bytes = cfg.read_bytes(), hist.read_bytes()

        rc, out = ur.run_one_shot(home, new, update=True)
        assert rc == 0, out
        assert "User installation found" in out
        assert ur.installed_version(home) == "0.9.1"
        assert cfg.read_bytes() == cfg_bytes
        assert hist.read_bytes() == hist_bytes

        rc, out = ur.run_one_shot(home, old, update=True)
        assert rc == 0, out
        assert ur.installed_version(home) == "0.9.0"
        assert cfg.read_bytes() == cfg_bytes
        assert hist.read_bytes() == hist_bytes

        unit = home / ".config" / "systemd" / "user" / "sayit-ermano.service"
        assert unit.exists()

    def test_build_deb_rejects_failed_dpkg(self, tmp_path):
        class FailProc:
            returncode = 1
            stdout = ""
            stderr = "boom"

        def fail_run(cmd, **kw):
            if cmd and cmd[0] == "dpkg-deb":
                return FailProc()
            return subprocess.CompletedProcess(cmd, 0, "", "")

        (tmp_path / "tree" / "fluidvoice").mkdir(parents=True)
        with pytest.raises(RuntimeError, match="dpkg-deb"):
            ur.build_deb(tmp_path / "pkg", tmp_path / "tree", "0.9.0",
                         run=fail_run, venv_builder=self.stub_venv("0"))
