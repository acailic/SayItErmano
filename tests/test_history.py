"""history --scrub-tests: helper semantics + CLI wiring.

The one-time cleanup tool for the test-row pollution this suite used to
write into the live history (see tests/conftest_isolation.py for the leak).
Everything here runs against a tmp history file via paths.history_file.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fluidvoice import cli, history


def _seed(hpath: Path, entries: list[dict]) -> bytes:
    hpath.parent.mkdir(parents=True, exist_ok=True)
    hpath.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n"
                             for e in entries), encoding="utf-8")
    return hpath.read_bytes()


def _sample_entries() -> list[dict]:
    """2 real dictations + 2 test-fingerprint command rows (oldest first)."""
    return [
        {"ts": 1.0, "mode": "dictate", "text": "hello", "duration_s": 2.0},
        {"ts": 2.0, "mode": "command", "command": "true 1", "purpose": "p",
         "text": "$ true 1", "duration_ms": 0},
        {"ts": 3.0, "text": "world", "duration_s": 3.0},
        {"ts": 4.0, "mode": "command", "command": "exit 3", "purpose": "fail",
         "text": "$ exit 3", "duration_ms": 1},
    ]


class TestScrubTestEntries:
    @pytest.fixture(autouse=True)
    def _history(self, tmp_path, monkeypatch):
        from fluidvoice import paths
        self.hpath = tmp_path / "history.jsonl"
        monkeypatch.setattr(paths, "history_file", lambda: self.hpath)
        return self.hpath

    def test_dry_run_counts_without_writing(self):
        before = _seed(self.hpath, _sample_entries())
        assert history.scrub_test_entries() == (2, 4, None)
        assert self.hpath.read_bytes() == before
        assert list(self.hpath.parent.glob("*.bak-*")) == []

    def test_apply_removes_only_fingerprint_rows_and_backs_up(self):
        before = _seed(self.hpath, _sample_entries())
        removed, total, backup = history.scrub_test_entries(apply=True)
        assert (removed, total) == (2, 4)
        assert backup is not None
        assert backup.parent == self.hpath.parent
        assert backup.name.startswith("history.jsonl.bak-")
        assert backup.read_bytes() == before  # pre-scrub snapshot
        kept = history.read_all()
        assert [e["ts"] for e in kept] == [1.0, 3.0]  # order preserved
        # remaining lines byte-identical to their original serialization
        assert self.hpath.read_text(encoding="utf-8") == "".join(
            json.dumps(e, ensure_ascii=False) + "\n" for e in kept)

    def test_apply_no_match_leaves_file_untouched(self):
        data = _seed(self.hpath, [{"ts": 1.0, "text": "only real"}])
        mtime = self.hpath.stat().st_mtime_ns
        assert history.scrub_test_entries(apply=True) == (0, 1, None)
        assert self.hpath.stat().st_mtime_ns == mtime
        assert self.hpath.read_bytes() == data
        assert list(self.hpath.parent.glob("*.bak-*")) == []

    def test_missing_file_is_a_noop(self):
        assert history.scrub_test_entries(apply=True) == (0, 0, None)
        assert not self.hpath.exists()

    def test_exact_fingerprint_only(self):
        """Near-miss commands and non-command rows must never be scrubbed."""
        _seed(self.hpath, [
            {"ts": 1.0, "mode": "command", "command": "true 1x"},
            {"ts": 2.0, "mode": "command", "command": "true 1 && rm -rf /"},
            {"ts": 3.0, "mode": "command", "command": "Exit 3"},  # case
            {"ts": 4.0, "mode": "command", "command": "true"},
            # a dictation that happens to carry a "command" field
            {"ts": 5.0, "mode": "dictate", "command": "true 1",
             "text": "note"},
        ])
        assert history.count_test_entries() == 0
        assert history.test_command_counts() == {}
        removed, _, backup = history.scrub_test_entries(apply=True)
        assert removed == 0 and backup is None
        assert len(history.read_all()) == 5

    def test_count_and_breakdown_over_entries(self):
        entries = _sample_entries() + [
            {"ts": 5.0, "mode": "command", "command": "echo hi"}]
        assert history.count_test_entries(entries) == 3
        assert history.test_command_counts(entries) == \
            {"true 1": 1, "exit 3": 1, "echo hi": 1}


class TestScrubCli:
    @pytest.fixture(autouse=True)
    def _history(self, tmp_path, monkeypatch):
        from fluidvoice import paths
        self.hpath = tmp_path / "history.jsonl"
        monkeypatch.setattr(paths, "history_file", lambda: self.hpath)
        return self.hpath

    def test_dry_run_prints_breakdown_and_writes_nothing(self, capsys):
        before = _seed(self.hpath, _sample_entries())
        rc = cli.main(["history", "--scrub-tests"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "true 1: 1" in out and "exit 3: 1" in out
        assert "would remove 2 of 4 entries" in out
        assert "--yes" in out
        assert self.hpath.read_bytes() == before

    def test_yes_applies_and_prints_backup_path(self, capsys):
        _seed(self.hpath, _sample_entries())
        rc = cli.main(["history", "--scrub-tests", "--yes"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "removed 2 entries (kept 2)" in out
        assert "backup:" in out and ".bak-" in out
        assert [e["ts"] for e in history.read_all()] == [1.0, 3.0]

    def test_yes_without_scrub_still_lists_history(self, capsys):
        _seed(self.hpath, [{"ts": 1.0, "text": "hello"}])
        rc = cli.main(["history", "--yes"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "hello" in out
        assert "scrub" not in out and "would remove" not in out
        assert [e["ts"] for e in history.read_all()] == [1.0]

    def test_no_test_rows_apply_is_clean_noop(self, capsys):
        _seed(self.hpath, [{"ts": 1.0, "text": "hello"}])
        rc = cli.main(["history", "--scrub-tests", "--yes"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "nothing to remove (1 entries, 0 test rows)" in out
        assert [e["ts"] for e in history.read_all()] == [1.0]


class TestUsageStats:
    """Stats page backend: streaks, totals, time-saved (B6)."""

    @staticmethod
    def at(day: str, hour: int = 10) -> float:
        y, m, d = (int(x) for x in day.split("-"))
        import time as _t
        return _t.mktime((y, m, d, hour, 0, 0, 0, 0, -1))

    @classmethod
    def e(cls, day: str, text: str = "w1 w2") -> dict:
        return {"ts": cls.at(day), "duration_s": 10.0, "text": text}

    def test_streak_and_best_with_gap(self):
        from fluidvoice.history import usage_stats
        now = self.at("2026-09-05", 23)
        s = usage_stats([self.e("2026-09-05"), self.e("2026-09-04"),
                         self.e("2026-09-02")], now)
        assert s["streak"] == 2 and s["best_streak"] == 2
        assert s["words"] == 6 and s["dictations"] == 3
        assert s["avg_seconds"] == 10.0

    def test_today_open_streak_alive_via_yesterday(self):
        from fluidvoice.history import usage_stats
        now = self.at("2026-09-05", 23)
        s = usage_stats([self.e("2026-09-04"), self.e("2026-09-03")], now)
        assert s["streak"] == 2

    def test_yesterday_empty_breaks_streak(self):
        from fluidvoice.history import usage_stats
        now = self.at("2026-09-05", 23)
        s = usage_stats([self.e("2026-09-03"), self.e("2026-09-02")], now)
        assert s["streak"] == 0 and s["best_streak"] == 2

    def test_best_streak_spans_gaps(self):
        from fluidvoice.history import usage_stats
        now = self.at("2026-09-05", 23)
        s = usage_stats([self.e("2026-08-30"), self.e("2026-08-31"),
                         self.e("2026-09-01"), self.e("2026-09-04"),
                         self.e("2026-09-05")], now)
        assert s["best_streak"] == 3 and s["streak"] == 2

    def test_minutes_saved_formula_and_empty(self):
        from fluidvoice.history import DICTATION_WPM, TYPING_WPM, usage_stats
        now = self.at("2026-09-05", 23)
        s = usage_stats([self.e("2026-09-05")], now)  # 2 words
        assert s["minutes_saved"] == 2 * (1 / TYPING_WPM - 1 / DICTATION_WPM)
        empty = usage_stats([], now)
        assert empty["streak"] == 0 and empty["minutes_saved"] == 0.0
        assert empty["avg_seconds"] == 0.0
