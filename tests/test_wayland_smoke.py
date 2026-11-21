"""Live Wayland/X11 smoke-matrix harness (issue #13) — offline rehearsal.

The harness (scripts/wayland_smoke.py) runs live sessions only on the
owner's desktop (real Wayland/X11 logins this runner does not have);
everything here drives it with faked session env vars, faked tool
probes and fake command runners, so the unit tier proves the whole
contract with no display, no daemon and no compositor:

* session detection — the XDG_SESSION_TYPE/WAYLAND_DISPLAY/DISPLAY
  precedence and the compositor mapping pick the right matrix session;
* row selection — every session selects exactly its own rows, and the
  pinned axes (session x hotkey x insertion + lifecycle) are covered
  per session, with absent backends recorded as GAP rows, not dropped;
* execution honesty — auto rows run their command (fake runner),
  required-missing tools are red, optional-missing tools SKIP with a
  reason, manual/gap rows never execute anything;
* evidence — <date>-<session>/results.json (latest run) + report.md
  (append-only journal with a manual checklist the owner ticks);
* CLI — --dry-run plans without a display and records nothing, a
  headless live run without --session is exit 2, a red row is exit 1;
* the row/invoke table embedded in docs/dev/wayland-smoke-matrix.md is
  exactly what --print-rows generates (no drift).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "wayland_smoke", REPO / "scripts" / "wayland_smoke.py")
ws = importlib.util.module_from_spec(_spec)
sys.modules["wayland_smoke"] = ws  # dataclasses resolves annotations via this
_spec.loader.exec_module(ws)


SWAY_ENV = {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0",
            "XDG_CURRENT_DESKTOP": "sway"}
GNOME_ENV = {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0",
             "XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}
KDE_ENV = {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-1",
           "XDG_CURRENT_DESKTOP": "KDE"}
HYPR_ENV = {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-1",
            "XDG_CURRENT_DESKTOP": "Hyprland"}
X11_ENV = {"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0",
           "XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}

AXIS_SESSIONS = ("x11", "gnome-wayland", "kde-wayland", "sway", "hyprland")


def all_tools(present: tuple[str, ...] = ws.TOOLS) -> dict[str, bool]:
    return {name: name in present for name in ws.TOOLS}


def ok_runner(argv: tuple[str, ...]) -> tuple[int, str]:
    return 0, "fake-ok"


class FailRunner:
    """Fails every command whose argv contains `fail_in`."""

    def __init__(self, fail_in: str = "\0none"):
        self.fail_in = fail_in
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv: tuple[str, ...]) -> tuple[int, str]:
        self.calls.append(argv)
        if self.fail_in in " ".join(argv):
            return 3, "boom: scripted failure"
        return 0, "fake-ok"


# ---------------------------------------------------------------------------
# session detection (pure env reading)
# ---------------------------------------------------------------------------

class TestDetectSession:
    @pytest.mark.parametrize("env,key", [
        (SWAY_ENV, "sway"),
        (GNOME_ENV, "gnome-wayland"),          # ubuntu:GNOME token
        ({"XDG_SESSION_TYPE": "wayland",
          "XDG_CURRENT_DESKTOP": "pop:GNOME"}, "gnome-wayland"),
        (KDE_ENV, "kde-wayland"),
        (HYPR_ENV, "hyprland"),
        (X11_ENV, "x11"),                      # x11 login, GNOME desktop
    ])
    def test_compositor_mapping(self, env, key):
        assert ws.detect_session(env).key == key

    def test_explicit_x11_wins_over_wayland_display(self):
        # the systemd unit's baked DISPLAY case, mirrored from
        # fluidvoice.session: XDG says x11, a stale WAYLAND_DISPLAY loses
        info = ws.detect_session({"XDG_SESSION_TYPE": "x11",
                                  "WAYLAND_DISPLAY": "wayland-0"})
        assert info.key == "x11" and info.stype == "x11"

    def test_explicit_wayland_wins_over_display(self):
        info = ws.detect_session({"XDG_SESSION_TYPE": "wayland",
                                  "DISPLAY": ":1", "WAYLAND_DISPLAY": ""})
        assert info.key == "wayland" and info.stype == "wayland"

    def test_non_session_value_falls_through_to_display_vars(self):
        info = ws.detect_session({"XDG_SESSION_TYPE": "tty",
                                  "WAYLAND_DISPLAY": "wayland-0",
                                  "XDG_CURRENT_DESKTOP": "sway"})
        assert info.key == "sway"

    def test_unknown_wayland_compositor(self):
        info = ws.detect_session({"XDG_SESSION_TYPE": "wayland",
                                  "WAYLAND_DISPLAY": "wayland-0"})
        assert info.key == "wayland"

    def test_headless_is_undetectable(self):
        info = ws.detect_session({})
        assert info.key is None and info.stype == "unknown"

    def test_desktop_first_token(self):
        assert ws.detect_session(GNOME_ENV).desktop == "ubuntu"


# ---------------------------------------------------------------------------
# row selection — the pinned axes per session, gaps never dropped
# ---------------------------------------------------------------------------

class TestRowSelection:
    def test_sway_selects_its_rows_only(self):
        rows = ws.select_rows("sway")
        ids = {r.id for r in rows}
        assert all(r.id.startswith("sway-") for r in rows)
        assert not any(i.startswith(("x11-", "gnome-", "kde-"))
                       for i in ids)
        assert "sway-insert-wtype-tools" in ids       # zwp primary
        assert "sway-hotkey-compositor-bind" in ids   # the sway hotkey path
        assert "sway-hotkey-portal" in ids            # recorded as a gap
        assert "sway-context-matrix" in ids           # Matrix B
        assert "sway-lifecycle-autostart" in ids

    def test_sway_has_no_portal_hotkey_backend_row_that_runs(self):
        portal = next(r for r in ws.select_rows("sway")
                      if r.id == "sway-hotkey-portal")
        assert portal.kind == "gap"
        assert "xdg-desktop-portal-wlr" in portal.note

    def test_x11_rows_reflect_the_x11_axes(self):
        rows = ws.select_rows("x11")
        ids = {r.id for r in rows}
        assert "x11-hotkey-xgrabkey" in ids
        assert "x11-insert-xdotool-tools" in ids
        assert "x11-insert-xclip-tools" in ids
        assert "x11-context-matrix" in ids            # Matrix C
        assert "x11-regression-zero-delta" in ids
        assert not any("portal" in i or "wtype" in i or "wl-clipboard" in i
                       for i in ids)

    def test_gnome_rows_ydotool_primary_wtype_gap(self):
        rows = {r.id: r for r in ws.select_rows("gnome-wayland")}
        assert rows["gnome-insert-ydotool-tools"].on_missing == "fail"
        wtype = rows["gnome-insert-wtype"]
        assert wtype.kind == "gap" and "mutter#1974" in wtype.note
        assert "gnome-context-matrix" in rows           # Matrix A

    def test_kde_wtype_gap_names_the_plasma_bug(self):
        wtype = next(r for r in ws.select_rows("kde-wayland")
                     if r.id == "kde-insert-wtype")
        assert "512996" in wtype.note

    def test_every_axis_session_covers_the_pinned_groups(self):
        for skey in AXIS_SESSIONS:
            rows = ws.select_rows(skey)
            cases = {r.case for r in rows}
            assert any(r.group == "hotkey" for r in rows), skey
            assert any(r.group == "insertion" for r in rows), skey
            assert {"sleep/wake", "lock/unlock",
                    "autostart enabled"} <= cases, skey
            assert any("baseline-doctor" in r.id for r in rows), skey
            assert any("non-US keyboard layout" in r.case for r in rows), \
                skey

    def test_every_wayland_session_records_the_eis_gap(self):
        for skey in ws.WAYLAND_KEYS + ("wayland",):
            eis = [r for r in ws.select_rows(skey)
                   if r.case == "EIS keysym insertion"]
            assert len(eis) == 1 and eis[0].kind == "gap", skey

    def test_registry_shape_is_consistent(self):
        assert len(ws.ROWS) == len(set(ws.ROWS))
        for row in ws.ROWS.values():
            assert row.group in ws.GROUPS, row.id
            assert row.kind in ("auto", "manual", "gap"), row.id
            assert row.sessions and all(s in ws.SESSION_KEYS
                                        for s in row.sessions), row.id
            if row.kind != "auto":
                assert not row.argv, row.id  # nothing to execute
            else:
                assert row.tools or row.argv, row.id

    def test_every_row_has_an_invoke_or_check_line(self):
        for row in ws.ROWS.values():
            assert ws._row_check_line(row).strip(), row.id


# ---------------------------------------------------------------------------
# execution honesty (fake tools + fake runners only)
# ---------------------------------------------------------------------------

class TestExecuteRow:
    def test_auto_command_pass_and_fail(self):
        row = ws.ROWS["sway-baseline-doctor"]
        ok = ws.execute_row(row, all_tools(), ok_runner)
        assert ok.status == ws.PASS and "exited" not in ok.detail
        bad = ws.execute_row(row, all_tools(), FailRunner("doctor"))
        assert bad.status == ws.FAIL and "exited 3" in bad.detail

    def test_required_tool_missing_is_red(self):
        row = ws.ROWS["sway-insert-wtype-tools"]
        tools = all_tools(())  # nothing installed
        result = ws.execute_row(row, tools, ok_runner)
        assert result.status == ws.FAIL and result.red
        assert "wtype" in result.detail and "install wtype" in result.detail

    def test_optional_tool_missing_skips_with_reason(self):
        row = ws.ROWS["sway-insert-ydotool-tools"]  # alternate path
        result = ws.execute_row(row, all_tools(()), ok_runner)
        assert result.status == ws.SKIP and "ydotool" in result.detail
        assert not result.red

    def test_environment_tool_missing_skips_not_fails(self):
        row = ws.ROWS["sway-baseline-doctor"]  # needs the CLI, not product
        result = ws.execute_row(row, all_tools(("systemctl",)), ok_runner)
        assert result.status == ws.SKIP and "sayit-ermano" in result.detail

    def test_presence_row_passes_without_running_anything(self):
        row = ws.ROWS["x11-insert-xdotool-tools"]
        result = ws.execute_row(row, all_tools(("xdotool",)), ok_runner)
        assert result.status == ws.PASS and "xdotool" in result.detail

    def test_manual_and_gap_rows_never_execute(self):
        runner = FailRunner(fail_in="")  # would fail anything it runs
        manual = ws.execute_row(ws.ROWS["sway-hotkey-compositor-bind"],
                                all_tools(()), runner)
        assert manual.status == ws.MANUAL and runner.calls == []
        gap = ws.execute_row(ws.ROWS["sway-hotkey-portal"],
                             all_tools(()), runner)
        assert gap.status == ws.GAP and gap.detail and runner.calls == []

    def test_full_matrix_red_iff_a_row_failed(self):
        session = ws.detect_session(SWAY_ENV)
        rows = ws.select_rows(session.key)
        green = ws.run_matrix(session, rows, all_tools(), ok_runner)
        assert not ws.summarize(green)["red"]
        red = ws.run_matrix(session, rows, all_tools(),
                            FailRunner("sayit-ermano"))
        summary = ws.summarize(red)
        assert summary["red"] and summary[ws.FAIL] == 3  # status+doctor+autostart


# ---------------------------------------------------------------------------
# evidence files
# ---------------------------------------------------------------------------

def record(tmp_path: Path, results, env=SWAY_ENV, host="smoke-vm",
           sha="abc1234", when=None):
    import datetime as dt

    when = when or dt.datetime(2026, 11, 21, 9, 30, tzinfo=dt.timezone.utc)
    return ws.record_evidence(tmp_path, ws.detect_session(env), results,
                              env, all_tools(), host=host, sha=sha,
                              now=when)


class TestEvidence:
    def _results(self, env=SWAY_ENV, run=None):
        session = ws.detect_session(env)
        return ws.run_matrix(session, ws.select_rows(session.key),
                             all_tools(), run or ok_runner)

    def test_writes_dated_session_dir_with_json_and_markdown(
            self, tmp_path):
        run_dir = record(tmp_path, self._results())
        assert run_dir == tmp_path / "2026-11-21-sway"
        payload = json.loads((run_dir / "results.json").read_text())
        assert payload["session"]["key"] == "sway"
        assert payload["host"] == "smoke-vm" and payload["commit"] == "abc1234"
        assert payload["summary"][ws.PASS] >= 1
        report = (run_dir / "report.md").read_text()
        assert report.startswith("# Wayland smoke matrix evidence — sway")
        assert "## Run 2026-11-21T09:30:00Z — smoke-vm" in report

    def test_json_rows_carry_status_and_invoke_lines(self, tmp_path):
        run_dir = record(tmp_path, self._results())
        rows = {r["id"]: r for r in json.loads(
            (run_dir / "results.json").read_text())["rows"]}
        manual = rows["sway-hotkey-compositor-bind"]
        assert manual["status"] == ws.MANUAL
        assert "bindsym $mod+d exec sayit-ermano-toggle" \
            in manual["invoke"]
        assert rows["sway-hotkey-portal"]["status"] == ws.GAP

    def test_report_has_row_table_manual_checklist_and_verdict(
            self, tmp_path):
        run_dir = record(tmp_path, self._results())
        report = (run_dir / "report.md").read_text()
        assert "| sway-baseline-doctor | baseline | PASS |" in report
        assert "- [ ] **sway-hotkey-compositor-bind**" in report
        assert "Invoke: " in report
        assert "Verdict: GREEN" in report
        assert "restore every config key you flip" in report  # housekeeping

    def test_red_run_records_red_verdict(self, tmp_path):
        results = self._results(run=FailRunner("sayit-ermano"))
        run_dir = record(tmp_path, results)
        report = (run_dir / "report.md").read_text()
        assert "Verdict: RED" in report
        payload = json.loads((run_dir / "results.json").read_text())
        assert payload["summary"]["red"] is True

    def test_reruns_append_journal_and_json_reflects_the_latest(
            self, tmp_path):
        import datetime as dt

        run_dir = record(tmp_path, self._results())
        report = run_dir / "report.md"
        report.write_text(report.read_text().replace(
            "- [ ] **sway-hotkey-compositor-bind**",
            "- [x] **sway-hotkey-compositor-bind**  (ticked by owner)"))
        later = dt.datetime(2026, 11, 21, 18, 5, tzinfo=dt.timezone.utc)
        record(tmp_path, self._results(), when=later)
        text = report.read_text()
        assert text.count("## Run ") == 2               # journal appends
        assert "(ticked by owner)" in text              # ticks survive
        payload = json.loads((run_dir / "results.json").read_text())
        assert payload["ran_at"] == "2026-11-21T18:05:00Z"

    def test_session_scoped_dirs_do_not_collide(self, tmp_path):
        record(tmp_path, self._results(env=X11_ENV), env=X11_ENV)
        assert (tmp_path / "2026-11-21-x11" / "results.json").exists()


# ---------------------------------------------------------------------------
# CLI (offline: faked env + tools + runner)
# ---------------------------------------------------------------------------

class TestCli:
    def _pin_env(self, monkeypatch, env):
        for var in ("XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP",
                    "WAYLAND_DISPLAY", "DISPLAY"):
            monkeypatch.delenv(var, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)

    def _fake(self, monkeypatch, present=ws.TOOLS, runner=None):
        monkeypatch.setattr(ws, "probe_tools",
                            lambda *a, **k: all_tools(tuple(present)))
        monkeypatch.setattr(ws, "real_run", runner or ok_runner)
        monkeypatch.setattr(ws, "repo_sha", lambda: "abc1234")

    def test_dry_run_in_session_prints_plan_and_records_nothing(
            self, tmp_path, monkeypatch, capsys):
        self._pin_env(monkeypatch, SWAY_ENV)
        self._fake(monkeypatch)
        assert ws.main(["--dry-run", "--out", str(tmp_path)]) == 0
        out = capsys.readouterr().out
        assert "session: sway" in out
        assert "sway-insert-wtype-tools" in out
        assert "dry run — nothing executed" in out
        assert not list(tmp_path.iterdir())  # nothing recorded

    def test_dry_run_headless_with_forced_session_needs_no_display(
            self, tmp_path, monkeypatch, capsys):
        self._pin_env(monkeypatch, {})  # no display, no session vars at all
        self._fake(monkeypatch)
        assert ws.main(["--dry-run", "--session", "gnome-wayland",
                        "--out", str(tmp_path)]) == 0
        out = capsys.readouterr().out
        assert "gnome-insert-ydotool-tools" in out
        assert not list(tmp_path.iterdir())  # nothing recorded

    def test_headless_live_run_without_session_is_exit_2(
            self, tmp_path, monkeypatch, capsys):
        self._pin_env(monkeypatch, {})
        assert ws.main(["--out", str(tmp_path)]) == 2
        assert "no session detected" in capsys.readouterr().err

    def test_row_filter_selects_and_unknown_ids_are_rejected(
            self, tmp_path, monkeypatch, capsys):
        self._pin_env(monkeypatch, SWAY_ENV)
        self._fake(monkeypatch)
        argv = ["--dry-run", "--out", str(tmp_path),
                "--row", "sway-baseline-doctor"]
        assert ws.main(argv) == 0
        plan = capsys.readouterr().out
        assert "1 row(s) selected" in plan
        assert not list(tmp_path.iterdir())
        argv = ["--dry-run", "--out", str(tmp_path),
                "--row", "x11-hotkey-xgrabkey"]
        assert ws.main(argv) == 2  # not a sway row
        assert "unknown row id(s) for sway" in capsys.readouterr().err

    def test_live_run_records_green_evidence(self, tmp_path, monkeypatch):
        self._pin_env(monkeypatch, SWAY_ENV)
        self._fake(monkeypatch)
        rc = ws.main(["--out", str(tmp_path)])
        assert rc == 0
        (run_dir,) = tmp_path.iterdir()
        assert run_dir.name.endswith("-sway")
        payload = json.loads((run_dir / "results.json").read_text())
        assert payload["summary"]["red"] is False

    def test_live_run_exit_1_iff_a_row_is_red(self, tmp_path, monkeypatch,
                                              capsys):
        self._pin_env(monkeypatch, SWAY_ENV)
        self._fake(monkeypatch, runner=FailRunner("sayit-ermano"))
        assert ws.main(["--out", str(tmp_path)]) == 1
        out = capsys.readouterr().out
        assert "sway-baseline-doctor: RED" in out
        (run_dir,) = tmp_path.iterdir()
        report = (run_dir / "report.md").read_text()
        assert "Verdict: RED" in report

    def test_live_run_required_tool_missing_is_red(self, tmp_path,
                                                   monkeypatch):
        self._pin_env(monkeypatch, SWAY_ENV)
        self._fake(monkeypatch, present=("sayit-ermano", "systemctl",
                                         "ydotool", "wl-copy", "wl-paste"))
        assert ws.main(["--out", str(tmp_path)]) == 1  # wtype missing

    def test_print_rows_emits_the_sentinel_table(self, capsys):
        assert ws.main(["--print-rows"]) == 0
        out = capsys.readouterr().out
        assert out.startswith(ws.ROWS_BEGIN)
        assert out.rstrip("\n").endswith(ws.ROWS_END)
        assert "`sway-insert-wtype-tools`" in out


# ---------------------------------------------------------------------------
# the doc-embedded row table cannot drift from the registry
# ---------------------------------------------------------------------------

class TestDocRowsNoDrift:
    def test_matrix_doc_embeds_exactly_the_generated_table(self):
        doc = (REPO / "docs" / "dev" / "wayland-smoke-matrix.md"
               ).read_text(encoding="utf-8")
        assert doc.count(ws.ROWS_BEGIN) == 1 and doc.count(ws.ROWS_END) == 1
        section = doc.split(ws.ROWS_BEGIN)[1].split(ws.ROWS_END)[0]
        assert section.strip() == ws.rows_markdown().strip()
