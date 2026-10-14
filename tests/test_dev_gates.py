"""Meta-tests guarding the developer gates themselves.

The suite proves application behavior; these prove the gates have not
silently rotted: the lint floor matches the oldest supported interpreter
(F10), the thread-exception filter actually fails a run that deserves
to fail (N6), and the flaky-test registry keeps its owner+expiry
contract (Q12's "never unlimited reruns")."""
from __future__ import annotations

import datetime as _dt
import subprocess
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _pyproject() -> dict:
    with (REPO / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


class TestLintTargetsOldestPython:
    """F10: ruff must lint for the OLDEST supported interpreter
    (requires-python ">=3.11"). target-version py312 would not flag
    3.12-only syntax that breaks 3.11 users. Lint is the FAST guard;
    CI's unit matrix also builds 3.11 (ci.yml) — the two agree via this
    test and ci.yml's matrix list."""

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


class TestFlakyRegistry:
    """Q12: flaky tests carry an owner and an expiry, never unlimited
    reruns. The registry lives in docs/dev/testing.md (visible next to
    the tier docs, not buried in CI config); these tests keep its shape
    honest so the practice cannot silently decay into "rerun until
    green": every row must carry a test id, dates, owner and expiry,
    and no OPEN row may sit past its expiry date."""

    DOC = Path(__file__).resolve().parent.parent / "docs" / "dev" / "testing.md"

    @classmethod
    def _rows(cls) -> list[dict]:
        text = cls.DOC.read_text(encoding="utf-8")
        header = "| Test | First seen | Symptom / lane | Owner | Expiry | Status |"
        assert header in text, \
            "flaky-test registry table missing from docs/dev/testing.md"
        start = text.index(header)
        body = text[start + len(header):].split("\n\n", 1)[0]
        rows = []
        for line in body.splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) == 6 and cells[0] and cells[0] != "---":
                rows.append(dict(zip(
                    ("test", "first_seen", "symptom", "owner", "expiry", "status"),
                    cells)))
        assert rows, "registry table present but empty of entries"
        return rows

    def test_every_row_has_owner_and_dates(self):
        for row in self._rows():
            assert row["owner"], row
            for field in ("first_seen", "expiry"):
                _dt.date.fromisoformat(row[field])  # raises on junk

    def test_no_open_row_is_past_its_expiry(self):
        today = _dt.date.today()
        for row in self._rows():
            if not row["status"].startswith("closed"):
                assert _dt.date.fromisoformat(row["expiry"]) >= today, \
                    f"flaky entry expired open — re-triage or close it: {row}"
