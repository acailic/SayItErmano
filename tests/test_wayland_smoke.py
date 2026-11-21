"""One-command Wayland smoke harness (scripts/wayland_smoke.py, #13).

The harness is environment-gated by design: live rows need a real
Wayland/X11 login the unit tier does not have. What IS pinned here, with
fakes only (no display, no daemon, no network):

* session detection -> profile -> row selection (the matrix axes),
* --dry-run needs no session/display and writes nothing,
* evidence writing (JSON + markdown) under <out-root>/<date>-<profile>/,
* exit-code honesty: 1 on any red row, 2 on usage errors, 0 otherwise,
* the automated checks (doctor/status subprocess, privacy sweep) against
  a fake runner + tmp history file,
* the doc contract: every row id in docs/dev/wayland-smoke-matrix.md
  exists in the registry and vice versa (no drift either direction).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

import scripts.wayland_smoke as ws
from fluidvoice import session as session_mod

REPO = Path(__file__).resolve().parents[1]
MATRIX_DOC = REPO / "docs" / "dev" / "wayland-smoke-matrix.md"

GNOME_WL_ENV = {"XDG_SESSION_TYPE": "wayland",
                "WAYLAND_DISPLAY": "wayland-0",
                "XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}
SWAY_ENV = {"XDG_SESSION_TYPE": "wayland",
            "WAYLAND_DISPLAY": "wayland-0",
            "XDG_CURRENT_DESKTOP": "sway"}
X11_ENV = {"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}


def set_session(monkeypatch, env: dict):
    for var in ("XDG_SESSION_TYPE", "WAYLAND_DISPLAY", "DISPLAY",
                "XDG_CURRENT_DESKTOP"):
        monkeypatch.delenv(var, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)


def ok(argv=None, out="", rc=0):
    return subprocess.CompletedProcess(argv or [], rc, out, "")


def fake_runner(script: dict, calls: list | None = None):
    """Route by the `-m fluidvoice <cmd>` / version-probe argv shape."""
    def run(argv, timeout):
        if calls is not None:
            calls.append(list(argv))
        key = " ".join(argv[1:])
        if key.startswith("-m fluidvoice "):
            for sub, proc in script.items():
                if key == f"-m fluidvoice {sub}":
                    return proc() if callable(proc) else proc
        if argv[0] in script:  # version probes by binary name
            proc = script[argv[0]]
            return proc() if callable(proc) else proc
        return ok(argv, rc=1, out="fake: no route")
    return run


def clean_history(tmp_path: Path) -> Path:
    hf = tmp_path / "history.jsonl"
    hf.write_text(json.dumps({"text": "hello", "language": "en"}) + "\n"
                  + json.dumps({"text": "world"}) + "\n", encoding="utf-8")
    return hf


def run_main(argv: list[str], tmp_path: Path, **kw) -> tuple[int, Path]:
    """Every live run pins --out-root into the tmp session dir — a test
    must never write evidence into the real docs/quality/wayland tree."""
    out_root = tmp_path / "ev"
    return ws.main([*argv, "--out-root", str(out_root)], **kw), out_root


# ---------------------------------------------------------------------------
# session -> profile
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("env,expected", [
    (X11_ENV, ws.PROFILE_X11),
    ({"DISPLAY": ":0"}, ws.PROFILE_X11),          # no XDG vars at all
    (GNOME_WL_ENV, ws.PROFILE_GNOME_WL),          # ubuntu:GNOME token
    ({"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "w-0",
      "XDG_CURRENT_DESKTOP": "GNOME"}, ws.PROFILE_GNOME_WL),
    (SWAY_ENV, ws.PROFILE_SWAY),
    ({"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "w-0",
      "XDG_CURRENT_DESKTOP": "KDE"}, ws.PROFILE_KDE_WL),
    ({"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "w-0",
      "XDG_CURRENT_DESKTOP": "Hyprland"}, ws.PROFILE_HYPRLAND),
    ({"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "w-0",
      "XDG_CURRENT_DESKTOP": "river"}, ws.PROFILE_WAYLAND),
    ({}, None),                                    # undetectable
])
def test_profile_for(env, expected):
    assert ws.profile_for(session_mod.probe(env)) == expected


# ---------------------------------------------------------------------------
# row selection per profile
# ---------------------------------------------------------------------------

def _ids(profile: str) -> list[str]:
    return [r.id for r in ws.rows_for(profile)]


class TestRowSelection:
    def test_gnome_wayland_full_matrix(self):
        assert _ids(ws.PROFILE_GNOME_WL) == (
            [f"A{n}" for n in range(1, 16)] + [f"D{n}" for n in range(1, 10)])

    def test_sway(self):
        assert _ids(ws.PROFILE_SWAY) == (
            [f"B{n}" for n in range(1, 5)] + [f"D{n}" for n in range(1, 10)])

    def test_x11_control_pass(self):
        # no D1/D4/D5/D9: those axes exist only on Wayland sessions
        assert _ids(ws.PROFILE_X11) == ["C1", "C2", "C3",
                                        "D2", "D3", "D6", "D7", "D8"]

    @pytest.mark.parametrize("profile", [ws.PROFILE_KDE_WL, ws.PROFILE_HYPRLAND,
                                         ws.PROFILE_WAYLAND],
                             ids=["kde", "hyprland", "generic"])
    def test_other_wayland_axioms_only(self, profile):
        # A/B/C stay REQ-PARITY for gnome/sway/x11 only; KDE/Hyprland/
        # unknown compositor run the D axis rows
        assert _ids(profile) == [f"D{n}" for n in range(1, 10)]

    def test_registry_shape(self):
        ids = [r.id for r in ws.ROWS.values()]
        assert len(ids) == len(set(ids))
        for r in ws.ROWS.values():
            assert r.kind in ("auto", "manual")
            if r.kind == "auto":
                assert r.auto in ws.CHECKS
            assert r.expected.get("*") or all(
                p in r.expected for p in r.profiles)

    def test_every_row_reachable(self):
        covered = {i for p in ws.ALL_PROFILES for i in _ids(p)}
        assert covered == set(ws.ROWS)


class TestRowFilter:
    def test_list_range_and_dedupe(self):
        assert ws.parse_row_filter("A1, A3-A5, d2, A3") == [
            "A1", "A3", "A4", "A5", "D2"]

    def test_rejects_unknown_and_bad_range(self):
        with pytest.raises(ValueError, match="unknown row id"):
            ws.parse_row_filter("A99")
        with pytest.raises(ValueError, match="hi < lo"):
            ws.parse_row_filter("A5-A1")
        with pytest.raises(ValueError, match="unknown row id"):
            ws.parse_row_filter("X1")


# ---------------------------------------------------------------------------
# --dry-run: no session, no display, no writes
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_headless_lists_every_profile(self, monkeypatch, capsys,
                                          tmp_path):
        set_session(monkeypatch, {})
        assert ws.main(["--dry-run", "--out-root", str(tmp_path)]) == 0
        out = capsys.readouterr().out
        assert "no display session detected" in out
        for prof in ws.ALL_PROFILES:
            assert f"profile: {prof}" in out
        assert "A1" in out and "C1" in out and "D9" in out
        assert "dry-run: nothing executed, no evidence written" in out
        assert list(tmp_path.iterdir()) == []

    def test_detected_session_lists_applicable_rows_only(
            self, monkeypatch, capsys, tmp_path):
        set_session(monkeypatch, GNOME_WL_ENV)
        assert ws.main(["--dry-run", "--out-root", str(tmp_path)]) == 0
        out = capsys.readouterr().out
        assert "profile: gnome-wayland" in out
        assert "A1" in out and "D2" in out
        assert "B1" not in out and "C1" not in out
        # the exact per-row rerun line
        assert f"rerun:  {ws.RUN_CMD} --rows A7" in out
        assert list(tmp_path.iterdir()) == []

    def test_row_filter_applies(self, monkeypatch, capsys, tmp_path):
        set_session(monkeypatch, X11_ENV)
        assert ws.main(["--dry-run", "--rows", "D2-D4",
                        "--out-root", str(tmp_path)]) == 0
        out = capsys.readouterr().out
        assert "D2" in out and "D3" in out
        assert "D4" not in out  # wayland-only, filtered out for x11


# ---------------------------------------------------------------------------
# live run: evidence writing + exit codes (fakes: runner + history)
# ---------------------------------------------------------------------------

GOOD_SCRIPT = {
    "doctor": lambda: ok(out="session: wayland (gnome)\n"
                             "insertion: ydotool\ncontext: atspi\n"),
    "status --json": lambda: ok(out='{"recording": false}'),
    "gnome-shell": lambda: ok(out="GNOME Shell 48.2"),
    "xdg-desktop-portal": lambda: ok(out="xdg-desktop-portal 1.18.0"),
}


class TestLiveRunEvidence:
    def test_green_run_writes_json_and_markdown(self, monkeypatch,
                                                tmp_path):
        set_session(monkeypatch, GNOME_WL_ENV)
        calls: list = []
        rc, out_root = run_main([], tmp_path,
                                runner=fake_runner(GOOD_SCRIPT, calls),
                                history_file=clean_history(tmp_path),
                                now=lambda: date(2026, 11, 21))
        assert rc == 0
        # automated rows drive the REAL CLI shape: this interpreter,
        # -m fluidvoice, cwd-independent
        assert [sys.executable, "-m", "fluidvoice", "doctor"] in calls
        assert [sys.executable, "-m", "fluidvoice", "status",
                "--json"] in calls
        d = out_root / "2026-11-21-gnome-wayland"
        payload = json.loads((d / "results.json").read_text())
        md = (d / "results.md").read_text()
        assert payload["schema"] == ws.SCHEMA
        assert payload["date"] == "2026-11-21"
        assert payload["profile"] == "gnome-wayland"
        assert payload["session"]["type"] == "wayland"
        assert payload["commit"]  # best-effort but recorded
        assert payload["versions"]["gnome-shell"] == "GNOME Shell 48.2"
        assert [r["id"] for r in payload["rows"]] == _ids("gnome-wayland")
        # automated rows pass; manual rows skip on non-tty stdin
        by_id = {r["id"]: r for r in payload["rows"]}
        assert by_id["A1"]["status"] == "pass"
        assert "insertion: ydotool" in by_id["A1"]["evidence"]
        assert by_id["D1"]["status"] == "pass"
        assert by_id["A13"]["status"] == "pass"
        assert by_id["A2"]["status"] == "skip"
        assert "non-interactive" in by_id["A2"]["note"]
        assert payload["summary"] == {
            "pass": 3, "fail": 0, "skip": len(payload["rows"]) - 3}
        assert "| A1 |" in md and "**Verdict: GREEN**" in md
        assert "**PASS**" in md and "**SKIP**" in md

    def test_second_same_day_run_never_clobbers(self, monkeypatch,
                                                tmp_path):
        set_session(monkeypatch, GNOME_WL_ENV)
        kw = {"runner": fake_runner(GOOD_SCRIPT),
              "history_file": clean_history(tmp_path),
              "now": lambda: date(2026, 11, 21)}
        rc, out_root = run_main([], tmp_path, **kw)
        assert rc == 0
        rc, _ = run_main([], tmp_path, **kw)
        assert rc == 0
        names = sorted(p.name for p in out_root.iterdir())
        assert names == ["2026-11-21-gnome-wayland",
                         "2026-11-21-gnome-wayland-2"]

    def test_manual_answers_recorded(self, monkeypatch, tmp_path):
        set_session(monkeypatch, GNOME_WL_ENV)
        rc, out_root = run_main(
            ["--answers", "A2=pass,A3=fail:wrong-case,A4=skip"], tmp_path,
            runner=fake_runner(GOOD_SCRIPT),
            history_file=clean_history(tmp_path),
            now=lambda: date(2026, 11, 21))
        assert rc == 1  # A3 red
        d = out_root / "2026-11-21-gnome-wayland"
        by_id = {r["id"]: r for r in
                 json.loads((d / "results.json").read_text())["rows"]}
        assert by_id["A2"]["status"] == "pass"
        assert by_id["A3"]["status"] == "fail"
        assert by_id["A3"]["evidence"] == "wrong-case"
        assert by_id["A4"]["status"] == "skip"

    def test_interactive_prompt_drives_result(self, monkeypatch, tmp_path):
        from itertools import chain, repeat
        set_session(monkeypatch, GNOME_WL_ENV)
        # A2 pass+comment, A3 fail, A5 one rejected reply then skip,
        # A6 (and any further prompt) skip
        answers = iter(chain(["pass no-space", "fail", "bogus", "skip"],
                             repeat("skip")))
        monkeypatch.setattr(sys, "stdin",
                            type("T", (), {"isatty": lambda self: True})())
        rc, _ = run_main(["--rows", "A2,A3,A5,A6"], tmp_path,
                         runner=fake_runner(GOOD_SCRIPT),
                         history_file=clean_history(tmp_path),
                         now=lambda: date(2026, 11, 21),
                         prompt=lambda _: next(answers))
        assert rc == 1
        d = tmp_path / "ev" / "2026-11-21-gnome-wayland"
        by_id = {r["id"]: r for r in
                 json.loads((d / "results.json").read_text())["rows"]}
        assert by_id["A2"]["status"] == "pass"
        assert by_id["A2"]["evidence"] == "no-space"
        assert by_id["A3"]["status"] == "fail"
        assert by_id["A5"]["status"] == "skip"  # after one rejected reply
        assert by_id["A6"]["status"] == "skip"


class TestRedRows:
    def test_doctor_failure_is_red(self, monkeypatch, tmp_path):
        set_session(monkeypatch, GNOME_WL_ENV)
        script = dict(GOOD_SCRIPT,
                      doctor=lambda: ok(rc=1, out="insertion: UNAVAILABLE"))
        rc, out_root = run_main([], tmp_path, runner=fake_runner(script),
                                history_file=clean_history(tmp_path),
                                now=lambda: date(2026, 11, 21))
        assert rc == 1
        d = out_root / "2026-11-21-gnome-wayland"
        payload = json.loads((d / "results.json").read_text())
        by_id = {r["id"]: r for r in payload["rows"]}
        assert by_id["A1"]["status"] == "fail"
        assert "doctor exit 1" in by_id["A1"]["evidence"]
        assert payload["summary"]["fail"] == 1
        assert "**Verdict: RED**" in (d / "results.md").read_text()

    def test_daemon_down_fails_baseline_row(self, monkeypatch, tmp_path):
        set_session(monkeypatch, SWAY_ENV)
        script = dict(GOOD_SCRIPT,
                      **{"status --json": lambda: ok(rc=1, err="no socket")})
        rc, _ = run_main(["--rows", "D1"], tmp_path, runner=fake_runner(script),
                         history_file=clean_history(tmp_path),
                         now=lambda: date(2026, 11, 21))
        assert rc == 1

    def test_privacy_leak_is_red(self, monkeypatch, tmp_path):
        set_session(monkeypatch, GNOME_WL_ENV)
        hf = tmp_path / "history.jsonl"
        hf.write_text(json.dumps({"text": "ok"}) + "\n"
                      + json.dumps({"text": "leak",
                                    "preceding_text": "secret prefix"})
                      + "\n" + json.dumps({"text": "l2", "atspi_app": "gedit"})
                      + "\n", encoding="utf-8")
        rc, out_root = run_main(["--rows", "A13"], tmp_path,
                                runner=fake_runner(GOOD_SCRIPT),
                                history_file=hf,
                                now=lambda: date(2026, 11, 21))
        assert rc == 1
        d = out_root / "2026-11-21-gnome-wayland"
        rows = json.loads((d / "results.json").read_text())["rows"]
        assert "preceding_text" in rows[0]["evidence"]
        assert "atspi_app" in rows[0]["evidence"]

    def test_missing_history_names_the_prerequisite(self, monkeypatch,
                                                    tmp_path):
        set_session(monkeypatch, GNOME_WL_ENV)
        rc, _ = run_main(["--rows", "A13"], tmp_path,
                         runner=fake_runner(GOOD_SCRIPT),
                         history_file=tmp_path / "absent.jsonl",
                         now=lambda: date(2026, 11, 21))
        assert rc == 1

    def test_x11_profile_runs_control_rows(self, monkeypatch, tmp_path):
        set_session(monkeypatch, X11_ENV)
        script = dict(GOOD_SCRIPT,
                      doctor=lambda: ok(out="session: x11"))
        rc, out_root = run_main([], tmp_path, runner=fake_runner(script),
                                history_file=clean_history(tmp_path),
                                now=lambda: date(2026, 11, 21))
        assert rc == 0
        d = out_root / "2026-11-21-x11"
        payload = json.loads((d / "results.json").read_text())
        assert [r["id"] for r in payload["rows"]] == _ids("x11")


class TestUsageErrors:
    def test_no_session_live_run_refuses(self, monkeypatch, capsys,
                                         tmp_path):
        set_session(monkeypatch, {})
        assert ws.main(["--out-root", str(tmp_path)]) == 2
        err = capsys.readouterr().err
        assert "no display session detected" in err
        assert list(tmp_path.iterdir()) == []

    def test_bad_answers_spec(self, monkeypatch, tmp_path):
        set_session(monkeypatch, X11_ENV)
        rc, _ = run_main(["--answers", "A2=maybe"], tmp_path,
                         runner=fake_runner(GOOD_SCRIPT),
                         history_file=clean_history(tmp_path))
        assert rc == 2

    def test_rows_not_applicable(self, monkeypatch, capsys, tmp_path):
        set_session(monkeypatch, X11_ENV)
        argv = ["--rows", "A1"]
        rc, _ = run_main(argv, tmp_path, runner=fake_runner(GOOD_SCRIPT),
                         history_file=clean_history(tmp_path))
        assert rc == 2
        assert "not applicable" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# doc contract: the registry and the matrix doc stay in lockstep
# ---------------------------------------------------------------------------

class TestDocContract:
    def test_every_doc_row_in_registry_and_back(self):
        text = MATRIX_DOC.read_text(encoding="utf-8")
        doc_ids = set(re.findall(r"^\| ([ABCD]\d+) \|", text, re.M))
        assert doc_ids, "matrix doc row tables not parsed — regex drift"
        assert doc_ids == set(ws.ROWS), (
            f"rows only in doc: {sorted(doc_ids - set(ws.ROWS))}; "
            f"rows only in registry: {sorted(set(ws.ROWS) - doc_ids)}")

    def test_doc_documents_the_harness(self):
        text = MATRIX_DOC.read_text(encoding="utf-8")
        assert "scripts/wayland_smoke.py" in text
        assert "--dry-run" in text
