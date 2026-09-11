"""Meta-tests for the conftest leak gate (Q2/E2).

The leak VERDICT must live in globally-loaded conftest infrastructure so
every invocation is enforced — this is the exact gap the audit found (E2):
a temporary test leaking a sleeping child returned ``1 passed`` exit 0
when run alone, and ``5 passed`` exit 0 under ``-n 2 --dist loadfile``,
because the session gate lived in tests/test_runner_hygiene.py, which
those invocations never collected.

These meta-tests spawn REAL pytest subprocesses on a disposable sandbox
directory under tests/ (so tests/conftest.py — where the gate now lives —
loads exactly as in a developer run) and verify the thing that matters:
EXIT STATUS and diagnostic attribution, not the presence of a config
string.

Sandbox safety: the probe files are named ``probe*.py`` / ``clean*.py``,
which do NOT match pytest's default ``test_*.py`` collection pattern, so
a concurrently running full suite (another worktree/session shares this
tree's tests/ only within one checkout — and even then) cannot collect
them; they run only when passed as explicit paths, which only these
meta-tests do.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

LEAKING_PROBE = textwrap.dedent("""
    import subprocess
    import sys

    from fluidvoice.runtime_tasks import RuntimeTasks

    def test_leaks_child():
        # exactly the audit's E2 diagnostic: a child nobody waits for
        subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"])

    def test_leaks_runtime_timer():
        # the baseline hang class: a pending watchdog nobody shuts down
        rt = RuntimeTasks()
        rt.schedule("watchdog-probe", lambda: None, 300.0)
""")

CLEAN_PROBE = textwrap.dedent("""
    from fluidvoice.runtime_tasks import RuntimeTasks

    def test_cleans_up():
        rt = RuntimeTasks()
        rt.schedule("short-timer", lambda: None, 0.0)
        rt.shutdown(timeout=5)
""")


def _run_pytest(*args: str, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider",
         "--rootdir", str(REPO), *args],
        cwd=REPO, capture_output=True, text=True, timeout=timeout)


@pytest.fixture()
def sandbox(tmp_path):
    """Disposable sandbox under tests/ (conftest chain applies to it)."""
    box = REPO / "tests" / f"_leakmeta-{__import__('os').getpid()}-{tmp_path.name}"
    box.mkdir()
    try:
        yield box
    finally:
        import shutil
        shutil.rmtree(box, ignore_errors=True)


class TestLeakGateEnforcedEverywhere:
    def test_focused_single_file_run_fails_on_leak(self, sandbox):
        probe = sandbox / "probe.py"
        probe.write_text(LEAKING_PROBE)
        result = _run_pytest("-q", str(probe))
        combined = result.stdout + result.stderr
        assert result.returncode != 0, (
            "a focused run that leaks a child must exit nonzero — the "
            "E2 regression (conftest gate not loaded) is back:\n"
            + combined)
        # attribution: the failing gate names the leaking tests
        assert "test_leaks_child" in combined
        assert "test_leaks_runtime_timer" in combined

    def test_xdist_loadfile_run_fails_on_leak(self, sandbox):
        probe = sandbox / "probe.py"
        probe.write_text(LEAKING_PROBE)
        bystander = sandbox / "clean_bystander.py"
        bystander.write_text(CLEAN_PROBE)
        # --dist loadfile: one worker may never see the leaking file's
        # session-mates — exactly the distribution that used to pass green
        result = _run_pytest("-q", "-n", "2", "--dist", "loadfile",
                             str(probe), str(bystander))
        combined = result.stdout + result.stderr
        assert result.returncode != 0, (
            "a -n 2 loadfile run that leaks a child must exit nonzero "
            "even though test_runner_hygiene.py is not collected:\n"
            + combined)
        assert "test_leaks_child" in combined or \
            "test_leaks_runtime_timer" in combined

    def test_clean_probe_passes_everywhere(self, sandbox):
        clean = sandbox / "cleanprobe.py"
        clean.write_text(CLEAN_PROBE)
        for extra in ([], ["-n", "2", "--dist", "loadfile"]):
            result = _run_pytest("-q", *extra, str(clean))
            assert result.returncode == 0, (
                f"the sandbox itself is broken (clean probe failed with "
                f"{extra}): {result.stdout}\n{result.stderr}")
