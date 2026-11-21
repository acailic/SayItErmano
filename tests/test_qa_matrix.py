"""Fresh-VM install matrix harness (issue #14) — offline rehearsal.

The harness (scripts/qa_matrix.py) runs real installs only on the
owner's fresh VMs; everything here drives it with injected
environments and FAKE command runners, so the unit tier proves the
whole contract with no root, no network and no installs:

* skip honesty — every route that cannot run here says SKIP with a
  reason (wrong distro, root vs regular user, missing tool), and a
  route only executes when its probe passes;
* stage gating — onboarding/dictation need a GUI session; a failed
  install skips the rest of the funnel instead of cascading noise;
* evidence — one file per route per distro, created with a PENDING
  STATUS header and sentinel block; recording prepends entries, trims
  history, never touches manual content, and only ever LOWERS a cell
  to FAIL (the owner alone marks PASS);
* the generated README table reflects the cell files and its relative
  links resolve (the repo docs link checker covers these files in the
  gate).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "qa_matrix", REPO / "scripts" / "qa_matrix.py")
qm = importlib.util.module_from_spec(_spec)
sys.modules["qa_matrix"] = qm  # dataclasses resolves annotations via this
_spec.loader.exec_module(qm)

ALL_TOOLS = {name: True for name in qm.TOOLS}
CELLS = {(r.id, d) for r in qm.ROUTES.values() for d in r.distros}

UBUNTU = 'ID=ubuntu\nVERSION_ID="24.04"\nNAME="Ubuntu 24.04 LTS"\n'
FEDORA = 'ID=fedora\nVERSION_ID="42"\n'
ARCH = "ID=arch\n"
DEBIAN = 'ID=debian\nVERSION_ID="12"\n'


def env(distro="ubuntu-24.04", euid=1000, display=True,
        tools=None, host="vm-test") -> qm.Env:
    return qm.Env(distro=distro, euid=euid, display=display,
                  tools={**ALL_TOOLS, **(tools or {})}, host=host)


def ok_runner(cmd: qm.Cmd, scratch: Path) -> tuple[int, str]:
    return 0, "fake-ok"


class ScriptedRunner:
    """Fails the first command whose argv contains `fail_in`."""

    def __init__(self, fail_in: str = "\0none"):
        self.fail_in = fail_in
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, cmd: qm.Cmd, scratch: Path) -> tuple[int, str]:
        self.calls.append(cmd.argv)
        if self.fail_in in " ".join(cmd.argv):
            return 3, "boom: scripted failure"
        return 0, "fake-ok"


# ---------------------------------------------------------------------------
# environment probe
# ---------------------------------------------------------------------------

class TestMatrixDistro:
    def test_maps_matrix_distros(self):
        assert qm.matrix_distro(qm.parse_os_release(UBUNTU)) == "ubuntu-24.04"
        assert qm.matrix_distro(qm.parse_os_release(FEDORA)) == "fedora"
        assert qm.matrix_distro(qm.parse_os_release(ARCH)) == "arch"

    def test_off_matrix_distros_are_none(self):
        for text in (DEBIAN, 'ID=ubuntu\nVERSION_ID="24.10"\n', "",
                     'ID="unknown"\n'):
            assert qm.matrix_distro(qm.parse_os_release(text)) is None

    def test_probe_environment_wires_the_facts_together(self):
        e = qm.probe_environment(os_release_text=FEDORA, euid=1000,
                                 display=False, tools={"pipx": False},
                                 host="h")
        assert (e.distro, e.euid, e.display, e.host) == ("fedora", 1000,
                                                         False, "h")


# ---------------------------------------------------------------------------
# skip logic — a route only executes when its probe passes
# ---------------------------------------------------------------------------

class TestRouteAvailability:
    def test_off_matrix_host_skips_every_route_with_reason(self):
        e = env(distro=None)
        for route in qm.ROUTES.values():
            ok, reason = qm.route_availability(route, e)
            assert not ok and "not a matrix target" in reason, route.id

    def test_route_skips_on_distros_it_does_not_apply_to(self):
        ok, reason = qm.route_availability(qm.ROUTES["deb"], env("fedora"))
        assert not ok and "not a Fedora" in reason
        ok, reason = qm.route_availability(qm.ROUTES["aur"],
                                           env("ubuntu-24.04"))
        assert not ok and "not a Ubuntu 24.04 route" in reason

    def test_root_routes_need_root_user_routes_refuse_it(self):
        user = env("ubuntu-24.04", euid=1000)
        root = env("ubuntu-24.04", euid=0)
        for rid in ("deb", "oneshot-system"):
            ok, reason = qm.route_availability(qm.ROUTES[rid], user)
            assert not ok and "needs root" in reason and "--route" in reason
            assert qm.route_availability(qm.ROUTES[rid], root)[0]
        for rid in ("oneshot-user", "pipx", "flatpak"):
            ok, reason = qm.route_availability(qm.ROUTES[rid], root)
            assert not ok and "regular user" in reason
            assert qm.route_availability(qm.ROUTES[rid], user)[0]

    def test_missing_tool_skips_with_the_tool_named(self):
        e = env("ubuntu-24.04", tools={"pipx": False})
        ok, reason = qm.route_availability(qm.ROUTES["pipx"], e)
        assert not ok and "pipx is not installed" in reason
        e = env("arch", tools={"makepkg": False})
        ok, reason = qm.route_availability(qm.ROUTES["aur"], e)
        assert not ok and "makepkg is not installed" in reason
        e = env("fedora", tools={"flatpak": False})
        ok, reason = qm.route_availability(qm.ROUTES["flatpak"], e)
        assert not ok and "flatpak is not installed" in reason

    def test_expected_cells_of_the_matrix(self):
        assert CELLS == {
            ("oneshot-user", "ubuntu-24.04"), ("oneshot-user", "fedora"),
            ("oneshot-user", "arch"),
            ("oneshot-system", "ubuntu-24.04"), ("oneshot-system", "fedora"),
            ("deb", "ubuntu-24.04"),
            ("pipx", "ubuntu-24.04"), ("pipx", "fedora"), ("pipx", "arch"),
            ("aur", "arch"),
            ("flatpak", "ubuntu-24.04"), ("flatpak", "fedora"),
            ("flatpak", "arch"),
        }


# ---------------------------------------------------------------------------
# execution + stage gating (fake runners only — nothing real runs)
# ---------------------------------------------------------------------------

class TestRunRoute:
    def test_off_matrix_host_runs_nothing(self):
        r = qm.run_route(qm.ROUTES["pipx"], env(distro=None), ok_runner,
                         Path("."))
        assert (r.status, r.stages) == (qm.SKIP, [])
        assert "not a matrix target" in r.reason

    def test_unavailable_route_runs_no_commands(self):
        runner = ScriptedRunner()
        r = qm.run_route(qm.ROUTES["deb"], env(euid=1000), runner,
                         Path("."))
        assert r.status == qm.SKIP and "needs root" in r.reason
        assert runner.calls == []

    def test_headless_host_skips_gui_stages_not_the_funnel(self):
        runner = ScriptedRunner()
        r = qm.run_route(qm.ROUTES["pipx"], env(display=False), runner,
                         Path("."))
        by_stage = {s.stage: s.status for s in r.stages}
        assert by_stage == {"install": qm.PASS, "onboarding": qm.SKIP,
                            "dictation": qm.SKIP, "update": qm.PASS}
        assert "GUI session" in r.stages[1].detail
        assert r.status == qm.PASS  # skips with reasons are not failures
        # only the non-GUI stages' commands reached the runner
        assert runner.calls == [("pipx", "install", "sayit-ermano"),
                                ("pipx", "upgrade", "sayit-ermano")]

    def test_failed_install_skips_the_rest_of_the_funnel(self):
        runner = ScriptedRunner(fail_in="pipx install")
        r = qm.run_route(qm.ROUTES["pipx"], env(), runner, Path("."))
        by_stage = {s.stage: (s.status, s.detail) for s in r.stages}
        assert by_stage["install"][0] == qm.FAIL
        assert "exited 3" in by_stage["install"][1]
        for stage in ("onboarding", "dictation", "update"):
            assert by_stage[stage][0] == qm.SKIP
            assert "install stage failed" in by_stage[stage][1]
        assert r.status == qm.FAIL
        assert runner.calls == [("pipx", "install", "sayit-ermano")]

    def test_multi_command_stage_fails_on_first_bad_command(self):
        runner = ScriptedRunner(fail_in="makepkg")
        r = qm.run_route(qm.ROUTES["aur"], env("arch"), runner, Path("."))
        # clone ok, makepkg -si failed -> install FAIL, update not reached
        assert r.status == qm.FAIL
        assert [c for c in runner.calls if c[0] == "git"] == [
            ("git", "clone", "https://aur.archlinux.org/sayit-ermano.git",
             "aur")]
        assert r.stages[0].status == qm.FAIL


# ---------------------------------------------------------------------------
# evidence files
# ---------------------------------------------------------------------------

@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    qm.init_tree(tmp_path)
    return tmp_path


class TestInitTree:
    def test_creates_one_file_per_route_per_distro(self, tree):
        assert {(p.parent.name, p.stem) for p in tree.rglob("*.md")
                if p.parent != tree} == CELLS

    def test_every_cell_has_status_sentinels_and_checklist(self, tree):
        for route_id, distro in CELLS:
            text = qm.cell_path(tree, route_id, distro).read_text()
            assert qm.read_status(text) == "PENDING"
            assert text.count(qm.BEGIN) == 1 and text.count(qm.END) == 1
            for needle in ("onboarding", "dictation", "update",
                           "GPU machine", "CPU machine", "install guide"):
                assert needle in text, (route_id, distro, needle)
            assert "_No automated run recorded yet." in text

    def test_reinit_never_touches_existing_files(self, tree):
        cell = qm.cell_path(tree, "pipx", "fedora")
        cell.write_text(cell.read_text().replace("STATUS: PENDING",
                                                 "STATUS: PASS")
                        + "\nmanual note: mic was a USB headset\n")
        assert qm.init_tree(tree) == []
        assert qm.read_status(cell.read_text()) == "PASS"
        assert "USB headset" in cell.read_text()


class TestRecordResult:
    def _pass_result(self):
        return qm.RunResult("pipx", "fedora", qm.PASS, stages=[
            qm.StageResult("install", qm.PASS, "1 command(s) ok"),
            qm.StageResult("onboarding", qm.SKIP, "needs a GUI session"),
        ])

    def _fail_result(self):
        return qm.RunResult("pipx", "fedora", qm.FAIL, stages=[
            qm.StageResult("install", qm.FAIL, "`pipx install` exited 3"),
        ])

    def test_records_entry_inside_sentinels_only(self, tree):
        cell = qm.cell_path(tree, "pipx", "fedora")
        before = cell.read_text()
        qm.record_result(cell, self._pass_result(), env())
        after = cell.read_text()
        # manual parts untouched, placeholder gone, entry inside the block
        assert after.startswith(before.split(qm.BEGIN)[0])
        assert "_No automated run" not in after
        block = after[after.index(qm.BEGIN) + len(qm.BEGIN):
                      after.index(qm.END)]
        assert "result=PASS" in block and "| install | PASS |" in block
        assert "needs a GUI session" in block
        # PASS does not touch the owner's STATUS header
        assert qm.read_status(after) == "PENDING"

    def test_failed_run_lowers_status_and_keeps_manual_content(self, tree):
        cell = qm.cell_path(tree, "pipx", "fedora")
        text = cell.read_text().replace(
            "- [ ] GPU machine: install succeeds end to end",
            "- [x] GPU machine: install succeeds end to end  (manual tick)")
        cell.write_text(text)
        qm.record_result(cell, self._fail_result(), env())
        after = cell.read_text()
        assert qm.read_status(after) == qm.FAIL
        assert "(manual tick)" in after          # manual edit preserved
        assert "exited 3" in after

    def test_skip_result_records_the_reason_without_touching_status(
            self, tree):
        cell = qm.cell_path(tree, "deb", "ubuntu-24.04")
        qm.record_result(cell, qm.RunResult(
            "deb", "ubuntu-24.04", qm.SKIP,
            reason="needs root — on the VM run: sudo python3 …"), env())
        after = cell.read_text()
        assert qm.read_status(after) == "PENDING"
        assert "route skipped: needs root" in after

    def test_entries_prepend_and_trim_to_the_cap(self, tree):
        cell = qm.cell_path(tree, "pipx", "fedora")
        for i in range(qm.MAX_RUN_ENTRIES + 2):
            result = self._pass_result()
            qm.record_result(cell, result,
                             env(host=f"host-{i}"))
        block = cell.read_text().split(qm.BEGIN)[1].split(qm.END)[0]
        assert block.count("\n### ") == qm.MAX_RUN_ENTRIES
        assert "host-6" in block      # newest first
        assert "host-0" not in block  # oldest trimmed


# ---------------------------------------------------------------------------
# the generated README
# ---------------------------------------------------------------------------

class TestReadme:
    def test_table_has_a_row_per_cell_with_resolving_links(self, tree):
        readme = qm.build_readme(tree)
        for route_id, distro in CELLS:
            rel = f"{route_id}/{distro}.md"
            assert f"]({rel})" in readme
            assert (tree / rel).exists()
        assert readme.count("\n| ") == len(CELLS) + 1  # + header row

    def test_table_reflects_manual_and_automated_outcomes(self, tree):
        cell = qm.cell_path(tree, "deb", "ubuntu-24.04")
        qm.record_result(cell, qm.RunResult(
            "deb", "ubuntu-24.04", qm.FAIL,
            stages=[qm.StageResult("install", qm.FAIL, "apt died")]),
            env(host="gpu-vm"))
        qm.record_result(qm.cell_path(tree, "aur", "arch"),
                         qm.RunResult("aur", "arch", qm.SKIP,
                                      reason="not a Arch Linux route"),
                         env(distro=None))
        readme = qm.build_readme(tree)
        deb_row = next(line for line in readme.splitlines()
                       if "deb/ubuntu-24.04.md" in line)
        assert "| FAIL |" in deb_row and "FAIL @ 20" in deb_row
        aur_row = next(line for line in readme.splitlines()
                       if "aur/arch.md" in line)
        assert "SKIPPED (not a Arch Linux route" in aur_row
        pipx_row = next(line for line in readme.splitlines()
                        if "pipx/ubuntu-24.04.md" in line)
        assert "| PENDING | — |" in pipx_row

    def test_owner_workflow_and_red_row_policy_are_documented(self, tree):
        readme = qm.build_readme(tree)
        for needle in ("--execute", "sudo python3", "Zero red rows",
                       "known issue", "best-converting"):
            assert needle in readme, needle


# ---------------------------------------------------------------------------
# CLI (still offline: probe-only + monkeypatched env/runner)
# ---------------------------------------------------------------------------

class TestMain:
    def test_init_writes_tree_and_readme(self, tmp_path):
        out = tmp_path / "matrix"
        assert qm.main(["init", "--out", str(out)]) == 0
        assert (out / "README.md").exists()
        assert len(list(out.rglob("*.md"))) == len(CELLS) + 1
        assert qm.main(["init", "--out", str(out)]) == 0  # idempotent

    def test_check_without_execute_records_nothing(self, tmp_path, capsys):
        out = tmp_path / "matrix"
        qm.main(["init", "--out", str(out)])
        before = {p: p.read_text() for p in out.rglob("*.md")}
        # real probe of THIS host, but no --execute: plan printed, no writes
        assert qm.main(["check", "--out", str(out)]) == 0
        assert "--execute" in capsys.readouterr().out
        after = {p: p.read_text() for p in out.rglob("*.md")}
        assert after == before

    def test_check_execute_on_a_faked_vm_records_evidence(
            self, tmp_path, monkeypatch):
        out = tmp_path / "matrix"
        qm.main(["init", "--out", str(out)])
        monkeypatch.setattr(
            qm, "probe_environment",
            lambda: env("ubuntu-24.04", euid=1000, display=False,
                        host="gpu-vm"))
        monkeypatch.setattr(qm, "real_run", ok_runner)
        rc = qm.main(["check", "--out", str(out), "--execute",
                      "--route", "pipx", "--route", "deb"])
        assert rc == 0  # deb skipped (needs root) — skips are not failures
        pipx_cell = qm.cell_path(out, "pipx", "ubuntu-24.04").read_text()
        assert "result=PASS" in pipx_cell
        assert qm.read_status(pipx_cell) == "PENDING"
        deb_cell = qm.cell_path(out, "deb", "ubuntu-24.04").read_text()
        assert "route skipped: needs root" in deb_cell
        row = next(line for line in (out / "README.md").read_text()
                   .splitlines() if "pipx/ubuntu-24.04.md" in line)
        assert "PASS @ 20" in row

    def test_check_execute_exit_is_red_iff_a_stage_failed(
            self, tmp_path, monkeypatch):
        out = tmp_path / "matrix"
        qm.main(["init", "--out", str(out)])
        monkeypatch.setattr(qm, "probe_environment",
                            lambda: env("ubuntu-24.04", host="bad-vm"))
        monkeypatch.setattr(qm, "real_run", ScriptedRunner(fail_in="install"))
        rc = qm.main(["check", "--out", str(out), "--execute",
                      "--route", "pipx"])
        assert rc == 1
        assert qm.read_status(
            qm.cell_path(out, "pipx", "ubuntu-24.04").read_text()) == qm.FAIL

    def test_off_matrix_execute_host_is_printed_not_recorded(
            self, tmp_path, monkeypatch):
        out = tmp_path / "matrix"
        qm.main(["init", "--out", str(out)])
        before = qm.cell_path(out, "pipx", "fedora").read_text()
        monkeypatch.setattr(qm, "probe_environment", lambda: env(distro=None))
        monkeypatch.setattr(qm, "real_run", ok_runner)
        assert qm.main(["check", "--out", str(out), "--execute"]) == 0
        assert qm.cell_path(out, "pipx", "fedora").read_text() == before
