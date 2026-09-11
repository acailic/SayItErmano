"""Meta-tests guarding the developer gates themselves.

The suite proves application behavior; these prove the gates have not
silently rotted: the lint floor matches the oldest supported interpreter
(F10), the thread-exception filter actually fails a run that deserves
to fail (N6), and — quality plan Q1 — the tier selection keeps network/
model/GTK work out of the unit gate (E1) and the unit-tier network guard
denies remote sockets while loopback fakes keep working."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from tests import _network_guard as net_guard

REPO = Path(__file__).resolve().parent.parent


def _pyproject() -> dict:
    with (REPO / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


class TestLintTargetsOldestPython:
    """F10: ruff must lint for the OLDEST supported interpreter
    (requires-python ">=3.11"). CI's unit matrix does run 3.11, but lint
    is the static, environment-independent complement — it flags
    3.12-only syntax even in a review branch that never dispatched CI,
    and it runs in every `just gate` on any interpreter."""

    def test_ruff_target_version_matches_requires_python_floor(self):
        cfg = _pyproject()
        req = cfg["project"]["requires-python"]
        assert req.startswith(">="), req
        floor = req[2:].split(",")[0].strip()      # "3.11"
        digits = floor.replace(".", "")            # "311"
        assert cfg["tool"]["ruff"]["target-version"] == f"py{digits}"


class TestThreadExceptionGate:
    """N6: the P0.4 thread-exception gate must actually trip - a test
    whose thread raises has to FAIL the run, not pass with a warning
    nobody reads. The guarantee was only ever verified by hand (the P0.4
    commit); nothing guarded the filterwarnings line itself. Proven two
    ways: the repo config carries the filter (static), and a real
    pytest subprocess on a tiny fixture with the same filter fails on
    an in-thread exception (the robust, end-to-end half)."""

    GATE = "error::pytest.PytestUnhandledThreadExceptionWarning"

    def test_repo_config_carries_the_gate(self):
        ini = _pyproject()["tool"]["pytest"]["ini_options"]
        assert self.GATE in ini.get("filterwarnings", [])

    def test_an_in_thread_exception_fails_a_configured_suite(self, tmp_path):
        # same filter as the repo's pyproject, in an isolated rootdir
        (tmp_path / "pytest.ini").write_text(
            "[pytest]\nfilterwarnings = [\"error::pytest."
            "PytestUnhandledThreadExceptionWarning\"]\n", encoding="utf-8")
        (tmp_path / "test_boom.py").write_text(
            "import threading\n"
            "\n"
            "def test_boom():\n"
            "    t = threading.Thread(target=lambda: 1 / 0)\n"
            "    t.start()\n"
            "    t.join()\n"
            "    assert True  # the test body itself passes - only the\n"
            "    # unhandled-thread-exception gate can fail this run\n",
            encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "test_boom.py"],
            cwd=tmp_path, capture_output=True, text=True, timeout=120)
        assert proc.returncode != 0, \
            "an unhandled thread exception passed a gated suite:\n" \
            + proc.stdout + proc.stderr
        assert "PytestUnhandledThreadExceptionWarning" \
            in proc.stdout + proc.stderr


class TestTierMarkersRegistered:
    """Q1: capability markers are the contract the tier selection filters
    on. An unregistered marker would make --strict-markers fail collection
    everywhere; a missing one would silently widen the unit gate."""

    EXPECTED = ("slow", "integration", "desktop", "model", "network",
                "packaging", "gtk")

    def test_all_capability_markers_registered(self):
        ini = _pyproject()["tool"]["pytest"]["ini_options"]
        registered = {m.split(":")[0].strip() for m in ini.get("markers", [])}
        for marker in self.EXPECTED:
            assert marker in registered, marker

    def test_strict_markers_is_a_default(self):
        ini = _pyproject()["tool"]["pytest"]["ini_options"]
        assert "--strict-markers" in ini.get("addopts", "")


class TestE2eStaysIntegration:
    """E1 regression guard: the real-download e2e transcription test must
    live under tests/integration with network+model marks. The 2026-09-11
    audit found it marked only `slow` inside the advertised-offline suite,
    so every local gate silently needed the network + a 75 MB model."""

    def test_e2e_file_moved_out_of_unit_tree(self):
        assert not (REPO / "tests" / "test_e2e_transcribe.py").exists()
        e2e = REPO / "tests" / "integration" / "test_e2e_transcribe.py"
        assert e2e.is_file()
        text = e2e.read_text(encoding="utf-8")
        for needle in ("pytest.mark.network", "pytest.mark.model",
                       "pytest.mark.integration"):
            assert needle in text, needle


class TestUnitTierNetworkGuard:
    """Q1 acceptance: in the unit tier a network attempt fails clearly,
    loopback fakes keep working, and the guard is env-gated so dev runs
    and other tiers keep unrestricted sockets."""

    def test_address_classification_table(self):
        ok = [None, "", "localhost", "127.0.0.1", "127.9.9.9", "::1",
              "/run/user/1000/fluidvoice.sock", ("localhost", 8080),
              ("127.0.0.1", 0), ("", 1234)]
        bad = [("example.com", 80), ("github.com", 443),
               ("93.184.216.34", 80), ("192.168.1.4", 53),
               ("10.0.0.1", 53), ("::ffff:93.184.216.34", 80)]
        for address in ok:
            assert net_guard._addr_ok(address), address
        for address in bad:
            assert not net_guard._addr_ok(address), address

    def test_remote_denied_loopback_allowed_in_process(self):
        was_installed = net_guard.installed()
        assert net_guard._install(force=True)
        try:
            # denied BEFORE any DNS or packets (address inspection only)
            for address in (("example.com", 443), ("93.184.216.34", 80)):
                with pytest.raises(AssertionError, match="network guard"):
                    socket.create_connection(address, timeout=0.2)
            # the suite's fake-server pattern keeps working
            srv = socket.socket()
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            try:
                port = srv.getsockname()[1]
                client = socket.create_connection(("127.0.0.1", port),
                                                  timeout=1.0)
                client.close()
            finally:
                srv.close()
        finally:
            net_guard.uninstall()
            if was_installed:  # canonical unit run: restore the live guard
                net_guard._install(force=True)

    def test_guard_is_env_gated_through_plugin_wiring(self, tmp_path):
        """A subprocess proves the activation contract: tier=unit installs
        the patches, no tier leaves the socket API pristine. No real
        connection is attempted either way."""
        base_env = {**os.environ,
                    "PYTHONPATH": str(REPO),
                    "PYTEST_PLUGINS": "tests._network_guard"}
        probe = tmp_path / "test_guard_wiring.py"
        for tier, expect_guard in (("unit", True), ("", False)):
            probe.write_text(
                "import socket\n"
                "\n"
                "import tests._network_guard as g\n"
                "\n"
                f"EXPECTED = {expect_guard!r}\n"
                "\n"
                "def test_wiring():\n"
                "    assert g.installed() == EXPECTED\n"
                "    if EXPECTED:\n"
                "        try:\n"
                "            socket.create_connection(('example.com', 443),\n"
                "                                    timeout=0.2)\n"
                "        except AssertionError as exc:\n"
                "            assert 'network guard' in str(exc)\n"
                "        else:\n"
                "            raise AssertionError('guard installed but remote '\n"
                "                                 'connect was not blocked')\n",
                encoding="utf-8")
            env = dict(base_env)
            if tier:
                env[net_guard.TIER_ENV] = tier
            else:
                env.pop(net_guard.TIER_ENV, None)
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", str(probe)],
                cwd=tmp_path, env=env, capture_output=True, text=True,
                timeout=120)
            assert "1 passed" in proc.stdout, (tier, proc.stdout, proc.stderr)


class TestUnitTierSelection:
    """Q1: the canonical tier runner actually deselects every capability
    class from the unit gate — proven through the script itself, so the
    selection string can't drift from what `just test-unit`/CI run."""

    ABSENT = ("test_gtkui.py", "test_onboarding_window.py",
              "test_settings_profiles.py", "test_e2e_transcribe.py",
              "TestSettingsUI")  # the gtk-marked class inside language_switch

    def test_unit_collection_excludes_capability_tiers(self):
        proc = subprocess.run(
            ["bash", "scripts/run_test_tier.sh", "unit",
             "--collect-only", "-q", "-v"],  # -v after -q: node-id listing
            cwd=REPO, capture_output=True, text=True, timeout=300)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        for needle in self.ABSENT:
            assert needle not in proc.stdout, needle
        # and the collection is real, not empty: 3000+ node ids
        lines = [ln for ln in proc.stdout.splitlines() if "::" in ln]
        assert len(lines) > 3000, len(lines)
