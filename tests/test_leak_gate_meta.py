"""Leak-gate meta-tests (quality plan Q2 acceptance, finding E2).

These verify the GATE, not application behavior: intentionally leaking
a child process or a pending RuntimeTasks timer must produce a nonzero
exit in a single-file run, a full run, and a `-n 2 --dist loadfile` run
— including workers that never execute tests/test_runner_hygiene.py
(the audit's exact repro: the leak verdict used to live in that module,
so a worker without it reported green).

Every scenario runs pytest as a SUBPROCESS in a disposable tmp dir with
the plugin loaded explicitly (`-p tests._runner_hygiene`, PYTHONPATH
pointing at this repo) — no repo conftest, no repo test files — so what
is proven is exit status and diagnostic attribution, not the presence of
configuration strings. Leaks are faked with the same primitives real
leaks use (subprocess.Popen without wait; RuntimeTasks.schedule without
shutdown), and no scenario needs the network.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

LEAK_CHILD = textwrap.dedent("""
    import subprocess, sys

    def test_leaks_a_child():
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        assert True
""")

LEAK_TIMER = textwrap.dedent("""
    from fluidvoice.runtime_tasks import RuntimeTasks

    def test_leaks_a_pending_timer():
        rt = RuntimeTasks()
        rt.schedule("capture-watchdog", lambda: None, 300.0)
        assert True
""")

CLEAN = textwrap.dedent("""
    import subprocess, sys

    def test_cleans_up_after_itself():
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        assert p.wait(timeout=10) == 0
""")

LEAK_AND_FAIL = textwrap.dedent("""
    import subprocess, sys

    def test_fails_and_leaks():
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        assert 1 == 2, "deliberate failure alongside a leak"
""")

LEAK_IN_SETUP = textwrap.dedent("""
    import subprocess, sys
    import pytest

    @pytest.fixture()
    def spawns_then_breaks():
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        raise RuntimeError("deliberate setup failure")

    def test_never_runs(spawns_then_breaks):
        assert True
""")

HANGS_AND_LEAKS = textwrap.dedent("""
    import subprocess, sys, time

    def test_hangs_and_leaks():
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        time.sleep(60)
""")


def _run_pytest(files: dict[str, str], tmp_path: Path, *args: str,
                env_extra: dict | None = None) -> subprocess.CompletedProcess:
    """pytest in a disposable dir with ONLY the hygiene plugin from this
    repo — exactly the focused-run shape that used to dodge the gate."""
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(REPO)}
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q",
         "-p", "tests._runner_hygiene", *args, *files],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=180)


class TestLeakFailsEveryRunShape:
    def test_child_leak_fails_a_single_file_run(self, tmp_path):
        proc = _run_pytest({"test_leaky.py": LEAK_CHILD}, tmp_path)
        assert proc.returncode != 0, proc.stdout + proc.stderr
        assert "subprocess pid=" in proc.stdout, proc.stdout
        # diagnostic ATTRIBUTION: the leak line names the leaking test
        assert "test_leaky.py::test_leaks_a_child" in proc.stdout

    def test_timer_leak_fails_a_single_file_run(self, tmp_path):
        proc = _run_pytest({"test_leaky_timer.py": LEAK_TIMER}, tmp_path)
        assert proc.returncode != 0, proc.stdout + proc.stderr
        assert "capture-watchdog" in proc.stdout, proc.stdout
        assert "test_leaky_timer.py::test_leaks_a_pending_timer" in proc.stdout

    def test_clean_run_stays_green(self, tmp_path):
        proc = _run_pytest({"test_clean.py": CLEAN}, tmp_path)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "1 passed" in proc.stdout

    def test_xdist_workers_without_hygiene_module_still_fail(self, tmp_path):
        """The audit's E2 repro, verbatim shape: two files under
        `-n 2 --dist loadfile`, no test_runner_hygiene.py anywhere near
        the run — the verdict now ships with every worker via the
        plugin, so the run must be red."""
        proc = _run_pytest(
            {"test_leaky.py": LEAK_CHILD, "test_filler.py": CLEAN},
            tmp_path, "-n", "2", "--dist", "loadfile")
        assert proc.returncode != 0, proc.stdout + proc.stderr
        assert "subprocess pid=" in proc.stdout + proc.stderr

    def test_leak_reported_even_when_the_test_fails(self, tmp_path):
        proc = _run_pytest({"test_both.py": LEAK_AND_FAIL}, tmp_path)
        assert proc.returncode != 0
        out = proc.stdout + proc.stderr
        assert "deliberate failure alongside a leak" in out  # the failure
        assert "subprocess pid=" in out                        # AND the leak

    def test_setup_failure_still_cleans_up_and_reports_both(self, tmp_path):
        """A fixture that breaks in setup after spawning: the autouse
        sweep still runs (child reaped and REPORTED — cleanup is kept
        when the test fails), and the run is red from the setup error
        too. Nothing hangs and nothing is silently forgiven."""
        proc = _run_pytest({"test_setup.py": LEAK_IN_SETUP}, tmp_path)
        assert proc.returncode != 0
        out = proc.stdout + proc.stderr
        assert "deliberate setup failure" in out   # the setup error
        assert "subprocess pid=" in out              # AND the reaped leak

    def test_timed_out_test_leak_fails_promptly(self, tmp_path):
        """A hanging test (pytest-timeout kills it) with a leaked child:
        nonzero exit, and the whole subprocess finishes fast — no
        post-summary hang, the baseline failure mode."""
        import time as _time
        started = _time.monotonic()
        proc = _run_pytest({"test_hang.py": HANGS_AND_LEAKS}, tmp_path,
                           "--timeout=3", "--timeout-method=signal")
        elapsed = _time.monotonic() - started
        assert proc.returncode != 0
        assert elapsed < 60, f"subprocess took {elapsed:.0f}s — hang?"
        assert "subprocess pid=" in proc.stdout + proc.stderr


class TestPluginIsUniversallyLoaded:
    def test_root_conftest_registers_the_plugin(self):
        root = (REPO / "conftest.py").read_text(encoding="utf-8")
        assert 'pytest_plugins = ["tests._runner_hygiene"]' in root

    def test_focused_repo_run_loads_the_plugin(self):
        """The original E2 gap was FOCUSED runs: a single test file (or
        node) with tests/test_runner_hygiene.py nowhere in the selection
        used to run with no leak verdict. A focused repo-anchored run
        now loads the plugin through the root conftest — proven by
        --trace-config, which lists every registered plugin."""
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "--trace-config",
             "--collect-only", "-q",
             "tests/test_insertion.py"],
            cwd=REPO, capture_output=True, text=True, timeout=180)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "tests._runner_hygiene" in proc.stdout + proc.stderr
