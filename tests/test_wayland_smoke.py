"""Wayland smoke harness (issue #13) — offline rehearsal.

The harness (scripts/wayland_smoke.py) runs real probes only inside a
logged-in session (owner action); everything here drives it with fake
session environments, fake runners and fake `which`, so the unit tier
proves the whole contract with no display, no daemon and no network:

* session detection — the env-var matrix (XDG_SESSION_TYPE /
  WAYLAND_DISPLAY / SWAYSOCK / HYPRLAND_INSTANCE_SIGNATURE /
  XDG_CURRENT_DESKTOP) reduces to exactly the five matrix sessions,
  and headless/unknown desktops say so instead of guessing;
* row selection — the pinned axes hold per session (portal hotkey on
  GNOME/KDE/Hyprland, portal ABSENT on sway, wtype insertion on
  wlroots only, ydotool on GNOME/KDE, X11 control rows);
* evaluation — tool presence, doctor needles, the portal absence proof,
  version minimums (GNOME 48, Plasma 6.7.4), info probes; manual rows
  come out MANUAL, never silently green;
* evidence — results.json + report.md under
  docs/quality/wayland/<date>-<session>/, same-day reruns merge;
* CLI — dry-run works headless and writes nothing, a real run without
  a session exits 2, any red automated row exits 1;
* doc sync — every row id has its exact invoke line in the runbook.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "wayland_smoke", REPO / "scripts" / "wayland_smoke.py")
ws = importlib.util.module_from_spec(_spec)
sys.modules["wayland_smoke"] = ws
_spec.loader.exec_module(ws)

GNOME_WL = {"XDG_SESSION_TYPE": "wayland",
            "XDG_CURRENT_DESKTOP": "ubuntu:GNOME",
            "WAYLAND_DISPLAY": "wayland-0"}
KDE_WL = {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "KDE",
          "WAYLAND_DISPLAY": "wayland-1"}
SWAY = {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "sway",
        "WAYLAND_DISPLAY": "wayland-1",
        "SWAYSOCK": "/run/user/1000/sway-ipc.1000.992.sock"}
HYPR = {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "Hyprland",
        "WAYLAND_DISPLAY": "wayland-1",
        "HYPRLAND_INSTANCE_SIGNATURE": "sig-123"}
X11 = {"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0",
       "XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}

DOCTOR_WL = ("session: wayland (gnome)  DISPLAY=:1 "
             "WAYLAND_DISPLAY=wayland-0\n"
             "  hotkey: de-shortcut (no global grabs on wayland - "
             "bind a DE shortcut)\n"
             "  insertion: ydotool (wtype missing; xdotool is X11-only)\n")
DOCTOR_X11 = ("session: x11 (gnome)  DISPLAY=:0 WAYLAND_DISPLAY=-\n"
              "  hotkey: x11-grab\n"
              "  insertion: xdotool\n")
PORTAL_OK = "(uint32 1,)"
TOOLS_EVERYWHERE = ("wtype", "ydotool", "wl-copy", "wl-paste", "xdotool",
                    "gdbus", "gnome-shell", "plasmashell", "localectl")


def which_of(*present: str):
    return lambda name: name if name in present else None


ALL_WHICH = which_of(*TOOLS_EVERYWHERE)


class FakeRunner:
    """Canned rc/output by substring; `fail_in` forces a nonzero rc."""

    def __init__(self, outputs: dict[str, str] | None = None,
                 fail_in: str = "\0none"):
        self.outputs = {
            "fluidvoice doctor": DOCTOR_WL,
            "org.freedesktop.portal": PORTAL_OK,
            "gnome-shell": "GNOME 48.2",
            "plasmashell": "plasmashell 6.7.4",
            "localectl": "System Locale: LANG=en_US.UTF-8",
            **(outputs or {}),
        }
        self.fail_in = fail_in
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv: tuple[str, ...]) -> tuple[int, str]:
        self.calls.append(argv)
        joined = " ".join(argv)
        if self.fail_in in joined:
            return 1, "boom: scripted failure"
        for key, out in self.outputs.items():
            if key in joined:
                return 0, out
        return 0, ""


def run_main(tmp_path: Path, env: dict, *extra: str,
             runner: FakeRunner | None = None, today="2026-11-21"):
    return ws.main(list(extra), env=env, run=runner or FakeRunner(),
                   which=ALL_WHICH, host="test-host", commit="abc1234",
                   today=today)


# ---------------------------------------------------------------------------
# session detection
# ---------------------------------------------------------------------------

class TestDetectSession:
    def test_maps_the_five_matrix_sessions(self):
        assert ws.detect_session(GNOME_WL).id == "gnome-wl"
        assert ws.detect_session(KDE_WL).id == "kde-wl"
        assert ws.detect_session(SWAY).id == "sway"
        assert ws.detect_session(HYPR).id == "hyprland"
        assert ws.detect_session(X11).id == "x11"

    def test_sway_recognized_by_socket_without_desktop_token(self):
        env = {"XDG_SESSION_TYPE": "wayland",
               "WAYLAND_DISPLAY": "wayland-0",
               "SWAYSOCK": "/run/user/1000/sway-ipc.sock"}
        assert ws.detect_session(env).id == "sway"

    def test_xdg_session_type_wins_over_a_stale_display(self):
        env = dict(X11, WAYLAND_DISPLAY="wayland-0")  # unit-baked stale env
        assert ws.detect_session(env).id == "x11"

    def test_headless_is_none_with_a_reason(self):
        info = ws.detect_session({})
        assert info.id is None
        assert "no graphical session" in info.detail

    def test_non_matrix_wayland_desktop_is_none_with_override_hint(self):
        info = ws.detect_session({"XDG_SESSION_TYPE": "wayland",
                                  "XDG_CURRENT_DESKTOP": "cosmic",
                                  "WAYLAND_DISPLAY": "wayland-0"})
        assert info.id is None
        assert "--session" in info.detail

    def test_facts_record_the_deciding_env_vars(self):
        info = ws.detect_session(GNOME_WL)
        assert info.env["XDG_CURRENT_DESKTOP"] == "ubuntu:GNOME"
        assert "SWAYSOCK" in info.env  # empty but present


# ---------------------------------------------------------------------------
# row selection — the pinned axes per session
# ---------------------------------------------------------------------------

class TestRowSelection:
    def ids_for(self, session: str) -> set[str]:
        return {r.id for r in ws.rows_for_session(session)}

    def test_gnome_wl_rows(self):
        ids = self.ids_for("gnome-wl")
        assert {"s2-hotkey-portal", "s7-insert-uinput", "s8-insert-clipboard",
                "s10-version-gnome", "s12-version-portal"} <= ids
        assert {"s3-hotkey-portal-absent", "s6-insert-wtype",
                "s11-version-plasma", "s13-layout-nonus"}.isdisjoint(ids)
        assert len({i for i in ids if i.startswith("a")}) == 15  # A1..A15
        assert not any(i.startswith(("b", "c")) for i in ids)

    def test_sway_rows_portal_absent_and_wtype_are_primary(self):
        ids = self.ids_for("sway")
        assert "s3-hotkey-portal-absent" in ids
        assert {"s5-hotkey-compositor-toggle", "s6-insert-wtype"} <= ids
        assert {"s2-hotkey-portal", "s7-insert-uinput",
                "s10-version-gnome"} .isdisjoint(ids)
        assert {"b1-atspi-context", "b4-privacy-sweep"} <= ids

    def test_kde_wl_rows_carry_the_plasma_and_layout_rows(self):
        ids = self.ids_for("kde-wl")
        assert {"s11-version-plasma", "s13-layout-nonus"} <= ids
        assert "s6-insert-wtype" not in ids  # no zwp on KWin (bug 512996)

    def test_hyprland_rows(self):
        ids = self.ids_for("hyprland")
        assert {"s2-hotkey-portal", "s6-insert-wtype"} <= ids
        assert "s10-version-gnome" not in ids

    def test_x11_rows_are_the_regression_control(self):
        ids = self.ids_for("x11")
        assert {"s4-hotkey-x11-grab", "s9-insert-xdotool",
                "s20-x11-regression", "c1-default-config",
                "c3-provider-atspi"} <= ids
        assert not any(i.startswith("a") or i.startswith("b")
                       or i.startswith("s6") for i in ids)

    def test_session_doctor_and_lifecycle_rows_apply_everywhere(self):
        for session in ws.ALL_SESSIONS:
            ids = self.ids_for(session)
            assert {"s1-session-doctor", "s14-lifecycle-suspend",
                    "s15-lifecycle-lock", "s16-lifecycle-autostart",
                    "s17-baseline-bare"} <= ids, session

    def test_row_ids_are_unique_and_docs_are_s_cells_or_matrix_cells(self):
        assert len(ws.ROW_IDS) == len(set(ws.ROW_IDS))
        for row in ws.ROWS:
            assert row.doc, row.id
            assert row.axis in ("session", "hotkey", "insertion", "versions",
                                "layout", "lifecycle", "context"), row.id

    def test_row_structure_is_well_formed(self):
        for row in ws.ROWS:
            assert isinstance(row.tools, tuple), row.id
            assert all(isinstance(t, str) for t in row.tools), row.id
            assert isinstance(row.probes, tuple), row.id
            for probe in row.probes:
                assert isinstance(probe, ws.Probe), row.id
                assert all(isinstance(a, str) for a in probe.argv), row.id


# ---------------------------------------------------------------------------
# evaluation (fake runner + fake which only)
# ---------------------------------------------------------------------------

class TestEvalRow:
    def row(self, rid: str) -> ws.Row:
        return next(r for r in ws.ROWS if r.id == rid)

    def test_missing_tool_fails_the_row(self):
        res = ws.eval_row(self.row("s6-insert-wtype"), FakeRunner(),
                          which_of("ydotool"))
        assert res.status == ws.FAIL
        assert "wtype is not on PATH" in res.detail

    def test_doctor_probe_passes_on_matching_output(self):
        res = ws.eval_row(self.row("s1-session-doctor"), FakeRunner(),
                          ALL_WHICH)
        assert res.status == ws.PASS

    def test_doctor_probe_fails_when_the_needle_is_missing(self):
        runner = FakeRunner(outputs={"fluidvoice doctor": "all good"})
        res = ws.eval_row(self.row("s4-hotkey-x11-grab"), runner, ALL_WHICH)
        assert res.status == ws.FAIL
        assert "session:" not in runner.outputs["fluidvoice doctor"]

    def test_portal_probe_expected_absent_on_sway(self):
        res = ws.eval_row(self.row("s3-hotkey-portal-absent"),
                          FakeRunner(), ALL_WHICH)
        assert res.status == ws.FAIL  # portal unexpectedly PRESENT
        assert "expected failure" in res.detail

        class PortalFails(FakeRunner):
            def __call__(self, argv):
                if "portal" in " ".join(argv):
                    return 1, "No such interface"
                return super().__call__(argv)
        res = ws.eval_row(self.row("s3-hotkey-portal-absent"), PortalFails(),
                          ALL_WHICH)
        assert res.status == ws.PASS

    def test_missing_probe_binary_fails_even_an_absence_proof(self):
        # gdbus missing on sway must not read as "portal absent"
        res = ws.eval_row(self.row("s3-hotkey-portal-absent"),
                          FakeRunner(), which_of())
        assert res.status == ws.FAIL
        assert "gdbus is not installed" in res.detail

    def test_gnome_version_minimum(self):
        old = FakeRunner(outputs={"gnome-shell": "GNOME 46.2"})
        assert ws.eval_row(
            self.row("s10-version-gnome"), old, ALL_WHICH).status == ws.FAIL
        assert ws.eval_row(self.row("s10-version-gnome"), FakeRunner(),
                           ALL_WHICH).status == ws.PASS  # 48.2

    def test_plasma_version_minimum(self):
        old = FakeRunner(outputs={"plasmashell": "plasmashell 6.7.3"})
        res = ws.eval_row(self.row("s11-version-plasma"), old, ALL_WHICH)
        assert res.status == ws.FAIL and "6.7.3" in res.detail
        assert ws.eval_row(self.row("s11-version-plasma"), FakeRunner(),
                           ALL_WHICH).status == ws.PASS  # 6.7.4

    def test_unparseable_version_fails(self):
        runner = FakeRunner(outputs={"gnome-shell": "no version here"})
        assert ws.eval_row(
            self.row("s10-version-gnome"), runner,
            ALL_WHICH).status == ws.FAIL

    def test_info_probe_never_fails_and_records(self):
        res = ws.eval_row(self.row("s12-version-portal"),
                          FakeRunner(), ALL_WHICH)
        assert res.status == ws.PASS
        assert any(c.detail for c in res.checks)

    def test_pure_manual_row_is_manual_never_green(self):
        res = ws.eval_row(self.row("s14-lifecycle-suspend"), FakeRunner(),
                          ALL_WHICH)
        assert res.status == ws.MANUAL
        assert res.row.manual

    def test_auto_row_with_manual_part_reports_both(self):
        res = ws.eval_row(self.row("s2-hotkey-portal"), FakeRunner(),
                          ALL_WHICH)
        assert res.status == ws.PASS and res.row.manual


# ---------------------------------------------------------------------------
# evidence tree
# ---------------------------------------------------------------------------

class TestRunMatrix:
    def _run(self, tmp_path, env=GNOME_WL, rows=None, runner=None,
             date="2026-11-21"):
        session = ws.detect_session(env)
        return ws.run_matrix(
            session.id, rows or ws.rows_for_session(session.id),
            session.env, session.detail, runner or FakeRunner(), ALL_WHICH,
            tmp_path, date=date, host="h", commit="c0ffee", invoke="x")

    def test_writes_json_and_report_under_date_session_dir(self, tmp_path):
        results, target, red = self._run(tmp_path)
        assert target == tmp_path / "2026-11-21-gnome-wl"
        assert not red
        data = json.loads((target / "results.json").read_text())
        assert data["session"] == "gnome-wl"
        assert data["result"] == ws.PASS
        assert data["commit"] == "c0ffee"
        by_id = {r["id"]: r for r in data["rows"]}
        assert by_id["s1-session-doctor"]["status"] == ws.PASS
        assert by_id["a2-empty-field"]["status"] == ws.MANUAL
        report = (target / "report.md").read_text()
        assert "RESULT: PASS" in report
        assert "- [ ] **a2-empty-field**" in report
        assert "docs/dev/wayland-smoke-matrix.md" in report

    def test_red_row_fails_the_whole_run(self, tmp_path):
        class PortalFails(FakeRunner):
            def __call__(self, argv):
                if "portal" in " ".join(argv):
                    return 0, PORTAL_OK
                if "gnome-shell" in " ".join(argv):
                    return 0, "GNOME 46.2"
                return super().__call__(argv)
        results, target, red = self._run(tmp_path, runner=PortalFails())
        assert red
        data = json.loads((target / "results.json").read_text())
        assert data["result"] == ws.FAIL
        assert "RESULT: FAIL" in (target / "report.md").read_text()

    def test_same_day_rerun_merges_rows(self, tmp_path):
        results, target, _ = self._run(tmp_path)
        data_before = json.loads((target / "results.json").read_text())
        assert len(data_before["rows"]) == len(ws.rows_for_session("gnome-wl"))
        # rerun ONE row on the same day without its tool: that row goes
        # red, the untouched rows survive, the row count stays the same
        row = next(r for r in ws.ROWS if r.id == "s7-insert-uinput")
        results, target, red = ws.run_matrix(
            "gnome-wl", [row], {}, "", FakeRunner(), which_of("wtype"),
            tmp_path, date="2026-11-21", host="h", commit="c0ffee",
            invoke="x")
        data = json.loads((target / "results.json").read_text())
        by_id = {r["id"]: r for r in data["rows"]}
        assert by_id["s7-insert-uinput"]["status"] == ws.FAIL
        assert by_id["s1-session-doctor"]["status"] == ws.PASS  # kept
        assert len(data["rows"]) == len(data_before["rows"])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestMain:
    def test_dry_run_headless_prints_the_map_writes_nothing(
            self, tmp_path, capsys):
        assert run_main(tmp_path, {}, "--dry-run",
                        "--out", str(tmp_path)) == 0
        out = capsys.readouterr().out
        assert "no graphical session" in out
        for session in ws.ALL_SESSIONS:
            assert session in out
        assert "s2-hotkey-portal" in out
        assert list(tmp_path.rglob("*")) == []

    def test_dry_run_for_a_session_lists_only_its_rows(
            self, tmp_path, capsys):
        assert run_main(tmp_path, SWAY, "--dry-run",
                        "--out", str(tmp_path)) == 0
        out = capsys.readouterr().out
        assert "s6-insert-wtype" in out and "b2-wtype-identity" in out
        assert "a2-empty-field" not in out
        assert list(tmp_path.rglob("*")) == []

    def test_headless_without_dry_run_is_an_env_error(self, tmp_path):
        assert run_main(tmp_path, {}, "--out", str(tmp_path)) == 2
        assert list(tmp_path.rglob("*")) == []

    def test_green_run_exits_zero_and_records_evidence(self, tmp_path):
        assert run_main(tmp_path, GNOME_WL, "--out", str(tmp_path)) == 0
        target = tmp_path / "2026-11-21-gnome-wl"
        assert (target / "results.json").exists()
        assert (target / "report.md").exists()

    def test_red_row_exits_one(self, tmp_path):
        class PortalSucceeds(FakeRunner):
            def __call__(self, argv):
                if "portal" in " ".join(argv):
                    return 0, PORTAL_OK  # sway expects ABSENCE
                return super().__call__(argv)
        assert run_main(tmp_path, SWAY, "--out", str(tmp_path),
                        runner=PortalSucceeds()) == 1
        data = json.loads((tmp_path / "2026-11-21-sway"
                           / "results.json").read_text())
        assert data["result"] == ws.FAIL

    def test_row_filter_runs_a_subset_and_notes_foreign_rows(
            self, tmp_path, capsys):
        assert run_main(tmp_path, GNOME_WL, "--out", str(tmp_path),
                        "--row", "s1-session-doctor") == 0
        data = json.loads((tmp_path / "2026-11-21-gnome-wl"
                           / "results.json").read_text())
        assert [r["id"] for r in data["rows"]] == ["s1-session-doctor"]
        assert run_main(tmp_path, GNOME_WL, "--out", str(tmp_path),
                        "--row", "b1-atspi-context",
                        "--row", "s1-session-doctor") == 0
        assert "not applicable to gnome-wl: b1-atspi-context" in \
            capsys.readouterr().out

    def test_row_filter_leaving_nothing_is_an_env_error(self, tmp_path):
        assert run_main(tmp_path, X11, "--out", str(tmp_path),
                        "--row", "a1-doctor-context") == 2

    def test_session_override_beats_detection(self, tmp_path, capsys):
        # override runs the rows even headless — probes report honestly,
        # so give sway its expected portal ABSENCE
        class PortalFails(FakeRunner):
            def __call__(self, argv):
                if "portal" in " ".join(argv):
                    return 1, "No such interface"
                return super().__call__(argv)
        assert run_main(tmp_path, {}, "--session", "sway",
                        "--out", str(tmp_path),
                        runner=PortalFails()) == 0
        assert (tmp_path / "2026-11-21-sway" / "results.json").exists()
        # and with the portal present, the same override goes red
        assert run_main(tmp_path, {}, "--session", "sway",
                        "--out", str(tmp_path)) == 1


# ---------------------------------------------------------------------------
# runbook sync — the doc owns the exact invoke line per row
# ---------------------------------------------------------------------------

class TestDocSync:
    def test_every_row_has_its_invoke_line_in_the_runbook(self):
        doc = (REPO / "docs" / "dev" / "wayland-smoke-matrix.md").read_text()
        for rid in ws.ROW_IDS:
            assert f"--row {rid}" in doc, rid
        assert "--dry-run" in doc
        assert "docs/quality/wayland/" in doc

    def test_doc_cell_ids_match_row_docs(self):
        doc = (REPO / "docs" / "dev" / "wayland-smoke-matrix.md").read_text()
        for row in ws.ROWS:
            if row.doc.startswith(("S", "A", "B", "C")):
                assert row.doc in doc, row.id
