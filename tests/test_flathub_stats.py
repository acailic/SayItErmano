"""Flathub funnel tracker (issue #18) — offline, fixture-driven.

Exercises the same pure functions scripts/flathub_stats.py runs in
production: search-API parsing (fixture responses use the live shape:
hits[] with flatpakAppId/name/installs_last_month — GET returns 405,
POST-only), append-only JSONL semantics, weekly report rendering, and
the NOT-LIVE store-stage stubs (store is #17, not launched). No test
opens a network connection.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "flathub_stats", REPO / "scripts" / "flathub_stats.py")
fs = importlib.util.module_from_spec(_spec)
# register before exec: dataclass field resolution looks the module up in
# sys.modules by __name__ (None there otherwise -> AttributeError)
sys.modules["flathub_stats"] = fs
_spec.loader.exec_module(fs)


# ---------------------------------------------------------------------------
# fixtures — Flathub search API response shape (verified 2026-11-21)
# ---------------------------------------------------------------------------

def hits_fixture() -> dict[str, dict]:
    """One canned POST-search response per tracked query."""
    return {
        "SayItErmano": {"hits": [
            {"flatpakAppId": "io.github.mkiol.SpeechNote", "name":
             "Speech Note", "installs_last_month": 8778},
        ], "total": 1},
        "speech note": {"hits": [
            {"flatpakAppId": "io.github.mkiol.SpeechNote", "name":
             "Speech Note", "installs_last_month": 8778},
            {"flatpakAppId": "com.example.Other", "name":
             "Other Thing", "installs_last_month": 12},
        ], "total": 2},
        "speed of sound": {"hits": [
            {"flatpakAppId": "io.github.argosiv.SpeedOfSound", "name":
             "Speed of Sound", "installs_last_month": 1515},
        ], "total": 1},
    }


def fake_fetch(responses: dict[str, dict], fail_queries: set[str] | None = None):
    """A stand-in for fs.fetch_json: serves canned responses by query."""
    fail_queries = fail_queries or set()

    def _fetch(url: str, payload: dict):
        assert url == fs.SEARCH_URL, "tracker must hit the search API"
        q = payload["query"]
        if q in fail_queries:
            raise OSError("simulated flathub outage")
        return responses[q]
    return _fetch


def row_for(date: str, sayitermano: int | None = None,
            speech_note: int = 8778, speed_of_sound: int = 1515,
            listed: bool = True) -> dict:
    return {"date": date, "apps": {
        "sayitermano": {"listed": listed,
                        "installs_last_month": sayitermano},
        "speech_note": {"listed": True, "installs_last_month": speech_note},
        "speed_of_sound": {"listed": True,
                           "installs_last_month": speed_of_sound},
    }}


# ---------------------------------------------------------------------------
# parsing / matching
# ---------------------------------------------------------------------------

def test_snapshot_parses_installs_and_not_listed():
    row = fs.snapshot(fetch=fake_fetch(hits_fixture()),
                      today=dt.date(2026, 11, 21))
    assert row["date"] == "2026-11-21"
    # not among the hits -> honestly not listed (submission #12 pending)
    assert row["apps"]["sayitermano"] == {"listed": False,
                                          "installs_last_month": None}
    assert row["apps"]["speech_note"] == {"listed": True,
                                          "installs_last_month": 8778}
    assert row["apps"]["speed_of_sound"] == {"listed": True,
                                             "installs_last_month": 1515}


def test_snapshot_matches_own_app_id_first():
    listed = hits_fixture()
    listed["SayItErmano"] = {"hits": [
        {"flatpakAppId": "io.github.acailic.SayItErmano", "name":
         "SayItErmano", "installs_last_month": 7},
    ], "total": 1}
    row = fs.snapshot(fetch=fake_fetch(listed))
    assert row["apps"]["sayitermano"] == {"listed": True,
                                          "installs_last_month": 7}


def test_snapshot_records_fetch_error_without_raising():
    row = fs.snapshot(fetch=fake_fetch(hits_fixture(),
                                       fail_queries={"speech note"}))
    assert row["apps"]["speech_note"]["listed"] is False
    assert "simulated flathub outage" in row["apps"]["speech_note"]["error"]
    # other apps still recorded
    assert row["apps"]["speed_of_sound"]["installs_last_month"] == 1515


# ---------------------------------------------------------------------------
# append-only JSONL data file
# ---------------------------------------------------------------------------

def test_append_row_appends_new_dates_only(tmp_path: Path):
    data = tmp_path / "flathub.json"
    fs.append_row(data, row_for("2026-11-14"))
    fs.append_row(data, row_for("2026-11-21", sayitermano=7))
    lines = data.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["date"] == "2026-11-14"
    assert json.loads(lines[1])["apps"]["sayitermano"]["installs_last_month"] == 7


def test_append_row_same_day_rerun_rewrites_only_trailing_row(tmp_path: Path):
    data = tmp_path / "flathub.json"
    fs.append_row(data, row_for("2026-11-14", speech_note=8712))
    fs.append_row(data, row_for("2026-11-21", speech_note=8778))
    fs.append_row(data, row_for("2026-11-21", speech_note=8790))
    lines = data.read_text().splitlines()
    assert len(lines) == 2  # one row per day, never duplicated
    assert json.loads(lines[0])["apps"]["speech_note"]["installs_last_month"] == 8712
    assert json.loads(lines[1])["apps"]["speech_note"]["installs_last_month"] == 8790


def test_load_rows_roundtrip(tmp_path: Path):
    data = tmp_path / "flathub.json"
    fs.append_row(data, row_for("2026-11-14"))
    assert [r["date"] for r in fs.load_rows(data)] == ["2026-11-14"]
    assert fs.load_rows(tmp_path / "missing.json") == []


# ---------------------------------------------------------------------------
# weekly report rendering
# ---------------------------------------------------------------------------

def test_report_week_over_week_deltas():
    rows = [row_for("2026-11-14", speech_note=8712, speed_of_sound=1498),
            row_for("2026-11-21", speech_note=8778, speed_of_sound=1515)]
    report = fs.render_report(rows, paddle=None)
    assert "2026-11-14" in report and "2026-11-21" in report
    assert "+66" in report   # speech note delta
    assert "+17" in report   # speed of sound delta
    assert "NOT-LISTED" in report  # sayitermano pre-submission


def test_report_single_sample_no_deltas_no_crash():
    report = fs.render_report([row_for("2026-11-21")], paddle=None)
    assert "need" in report and "NOT-LISTED" in report


def test_report_store_stages_not_live_without_paddle():
    report = fs.render_report([row_for("2026-11-21", sayitermano=40)],
                              paddle=None)
    assert "NOT-LIVE" in report
    for _key, label in fs.PADDLE_STAGES:
        assert label in report
    assert "NOT-LIVE" in report.split("Flathub → store click-through")[1]


def test_report_store_stages_from_paddle_export(tmp_path: Path):
    paddle = {"period": {"start": "2026-11-15", "end": "2026-11-21"},
              "store_visits": 3, "checkouts_started": 1,
              "checkouts_completed": 1, "refunds": 0}
    report = fs.render_report([row_for("2026-11-21", sayitermano=40)],
                              paddle=paddle)
    assert "NOT-LIVE" not in report
    assert "3" in report and "| 1 |" in report
    # CTR: 3 visits / 40 installs = 7.5% > 2% floor
    assert "7.5%" in report and "above floor" in report


def test_report_ctr_under_floor_flags_listing_copy():
    paddle = {"store_visits": 1, "checkouts_started": 0,
              "checkouts_completed": 0, "refunds": 0}
    report = fs.render_report([row_for("2026-11-21", sayitermano=100)],
                              paddle=paddle)
    assert "1.0%" in report and "UNDER floor" in report


def test_report_links_decision_rule_doc():
    report = fs.render_report([row_for("2026-11-21")], paddle=None)
    assert "../../pricing-funnel-decision-rule.md" in report


# ---------------------------------------------------------------------------
# end-to-end through main()'s file seams (still offline: fake fetch injected)
# ---------------------------------------------------------------------------

def test_snapshot_to_report_end_to_end(tmp_path: Path, monkeypatch):
    data = tmp_path / "flathub.json"
    fs.append_row(data, row_for("2026-11-14", speech_note=8712))
    row = fs.snapshot(fetch=fake_fetch(hits_fixture()),
                      today=dt.date(2026, 11, 21))
    fs.append_row(data, row)
    report = fs.render_report(fs.load_rows(data),
                              fs.load_paddle(tmp_path / "paddle.json"))
    assert "+66" in report
    assert fs.load_paddle(tmp_path / "paddle.json") is None  # NOT-LIVE path
