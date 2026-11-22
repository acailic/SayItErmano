"""Q9 harness, real subprocesses: a short soak + the full upgrade
rehearsal, both driven exactly like the committed evidence runs
(model-free: no display, no model, no network — same tier as
test_daemon_socket.py / test_cli.py)."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from tests.integration.conftest import REPO

pytestmark = pytest.mark.integration


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


soak = _load("soak_run", REPO / "scripts" / "soak_run.py")
upg = _load("upgrade_rehearsal", REPO / "scripts" / "upgrade_rehearsal.py")


@pytest.mark.timeout(180)
class TestSoakHarness:
    def test_short_soak_passes_and_writes_evidence(self, tmp_path):
        rc = soak.main([
            "--duration-minutes", "0.4",      # ~24 s
            "--interval-seconds", "4",
            "--warmup-seconds", "8",
            "--take-every-seconds", "10",
            "--take-hold-seconds", "0.5",
            "--out-dir", str(tmp_path),
        ])
        assert rc == 0
        [report] = tmp_path.glob("*.json")
        r = json.loads(report.read_text())
        assert r["verdict"]["ok"] is True
        assert r["verdict"]["rss"]["warmup_samples"] >= 2
        assert r["verdict"]["socket"]["failures"] == 0
        assert r["samples"], "at least one sample was taken"
        assert r["takes"], "at least one scripted take was driven"
        assert report.with_suffix(".md").exists()

    def test_dead_daemon_is_reported_not_hung(self, tmp_path, monkeypatch):
        """A daemon that dies mid-run must end the soak with evidence,
        not hang until the timeout (the sample loop breaks on 'dead')."""
        real_spawn = soak.spawn_daemon

        def dying_spawn(python, workdir, repo=soak.REPO, env=None):
            d = real_spawn(python, workdir, repo=repo, env=env)
            d.proc.kill()
            d.proc.wait(timeout=5)
            return d

        monkeypatch.setattr(soak, "spawn_daemon", dying_spawn)
        rc = soak.main([
            "--duration-minutes", "0.2", "--interval-seconds", "2",
            "--warmup-seconds", "4", "--out-dir", str(tmp_path),
        ])
        assert rc == 1
        r = json.loads(next(tmp_path.glob("*.json")).read_text())
        assert r["verdict"]["ok"] is False
        assert any("socket" in reason for reason in r["verdict"]["reasons"])


@pytest.mark.timeout(600)
class TestUpgradeRehearsal:
    def test_full_rehearsal_passes_and_writes_evidence(self, tmp_path):
        rc = upg.main(["--out-dir", str(tmp_path), "--work",
                       str(tmp_path / "work")])
        assert rc == 0
        [report] = tmp_path.glob("*.json")
        r = json.loads(report.read_text())
        assert r["verdict"]["ok"] is True
        assert r["installer_calls"]["daemon_stopped_before_file_swap"] \
            is True
        for phase in ("after_upgrade", "after_rollback"):
            state = r["state_checks"][phase]
            assert state["config"]["ok"], phase
            assert state["history"]["ok"], phase
        # the one-shot route never rewrites user files: bytes too
        assert r["state_checks"]["after_upgrade"]["config"][
            "byte_identical"] is True
        # the version actually moved old -> new -> old
        names = [s["step"] for s in r["steps"]]
        assert "run --update against the locally-built v1" in names
        assert "rollback (reinstall v0 via --update)" in names
        assert report.with_suffix(".md").exists()
