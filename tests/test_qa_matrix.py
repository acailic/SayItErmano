"""scripts/qa_matrix.py — the install-matrix harness's decision logic.

Fake environments and fake exec runners only (the acceptance for #14's
agent scope): no root, no network, no package operations — the tests
pin WHICH rows run where, the skip reasons, the evidence files and the
generated summary/checklist. The real installs happen on fresh VMs
(owner action; see docs/quality/install-matrix/README.md).
"""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "qa_matrix", REPO / "scripts" / "qa_matrix.py")
qm = importlib.util.module_from_spec(_spec)
# register before exec: py3.11 dataclasses resolves annotations through
# sys.modules[cls.__module__] at class-creation time
sys.modules["qa_matrix"] = qm
_spec.loader.exec_module(qm)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def mk_env(**overrides) -> qm.Env:
    base = dict(
        distro_key="ubuntu-24.04", distro_pretty="Ubuntu 24.04 LTS",
        arch="x86_64", is_root=False, has_display=False,
        tools={name: True for name in qm.TOOLS},
        flathub="no", git_head=None)
    base.update(overrides)
    return qm.Env(**base)


class FakeExec:
    """Records commands, never spawns anything."""

    def __init__(self, returncode: int = 0):
        self.calls: list[tuple[str, ...]] = []
        self.returncode = returncode

    def __call__(self, argv, timeout):
        self.calls.append(tuple(argv))
        return qm.ExecResult(self.returncode, "fake output")


ALL_COMBOS = [(r.key, dk) for r in qm.ROUTES.values() for dk in r.distros]


def run_all(tmp_path: Path, env: qm.Env, exec_fn: qm.ExecFn | None = None):
    return qm.run_matrix(env, repo=REPO, out_dir=tmp_path,
                         exec_fn=exec_fn or FakeExec())


# ---------------------------------------------------------------------------
# skip logic — the row never runs unless every gate is green
# ---------------------------------------------------------------------------

def test_wrong_distro_skips_with_fresh_vm_reason() -> None:
    env = mk_env(distro_key=None, distro_pretty="Debian GNU/Linux 12")
    ev = qm.run_route(qm.ROUTES["oneshot-user"], qm.DISTROS["ubuntu-24.04"],
                      env, repo=REPO, exec_fn=FakeExec())
    assert ev["status"] == "SKIPPED"
    assert "fresh Ubuntu 24.04 VM" in ev["skip_reason"]
    # distro is checked FIRST: this env also lacks root, but the reason
    # must name the VM, not sudo (deterministic reason precedence)
    assert "root" not in ev["skip_reason"]
    assert all(s["status"] == "SKIP" for s in ev["steps"])


@pytest.mark.parametrize("route_key", ["oneshot-system", "deb"])
def test_root_routes_skip_without_root(route_key: str) -> None:
    ev = qm.run_route(qm.ROUTES[route_key], qm.DISTROS["ubuntu-24.04"],
                      mk_env(is_root=False), repo=REPO, exec_fn=FakeExec())
    assert ev["status"] == "SKIPPED"
    assert "root" in ev["skip_reason"]


def test_oneshot_user_runs_unprivileged_on_ubuntu() -> None:
    ev = qm.run_route(qm.ROUTES["oneshot-user"], qm.DISTROS["ubuntu-24.04"],
                      mk_env(is_root=False), repo=REPO, exec_fn=FakeExec())
    assert ev["skip_reason"] is None


def test_oneshot_needs_deb_tools() -> None:
    env = mk_env(tools={**{n: True for n in qm.TOOLS}, "dpkg-deb": False})
    ev = qm.run_route(qm.ROUTES["oneshot-user"], qm.DISTROS["ubuntu-24.04"],
                      env, repo=REPO, exec_fn=FakeExec())
    assert ev["status"] == "SKIPPED"
    assert "deb-based" in ev["skip_reason"]


def test_deb_route_skips_off_amd64() -> None:
    ev = qm.run_route(qm.ROUTES["deb"], qm.DISTROS["ubuntu-24.04"],
                      mk_env(is_root=True, arch="aarch64"),
                      repo=REPO, exec_fn=FakeExec())
    assert ev["status"] == "SKIPPED"
    assert "amd64" in ev["skip_reason"]


def test_aur_refuses_root_but_runs_as_user() -> None:
    route, distro = qm.ROUTES["aur"], qm.DISTROS["arch"]
    as_root = qm.run_route(route, distro, mk_env(distro_key="arch",
                                                 is_root=True),
                           repo=REPO, exec_fn=FakeExec())
    assert as_root["status"] == "SKIPPED"
    assert "root" in as_root["skip_reason"]
    as_user = qm.run_route(route, distro, mk_env(distro_key="arch",
                                                 is_root=False),
                           repo=REPO, exec_fn=FakeExec())
    assert as_user["skip_reason"] is None


def test_flatpak_blocked_until_flathub_listing_exists() -> None:
    route, distro = qm.ROUTES["flatpak"], qm.DISTROS["fedora"]
    pending = qm.run_route(route, distro, mk_env(distro_key="fedora",
                                                 flathub="no"),
                           repo=REPO, exec_fn=FakeExec())
    assert pending["status"] == "SKIPPED"
    assert "#12" in pending["skip_reason"]
    unknown = qm.run_route(route, distro, mk_env(distro_key="fedora",
                                                 flathub="unknown"),
                           repo=REPO, exec_fn=FakeExec())
    assert unknown["status"] == "SKIPPED"
    assert "unknown" in unknown["skip_reason"]


def test_pipx_route_is_cross_distro() -> None:
    for distro in ("ubuntu-24.04", "fedora", "arch"):
        env = mk_env(distro_key=distro,
                     distro_pretty=distro)
        ev = qm.run_route(qm.ROUTES["pipx"], qm.DISTROS[distro], env,
                          repo=REPO, exec_fn=FakeExec())
        assert ev["skip_reason"] is None, distro


# ---------------------------------------------------------------------------
# step execution — what actually runs when a row is available
# ---------------------------------------------------------------------------

def steps_by_key(ev: dict) -> dict:
    return dict(zip(("install", "onboard", "dictate", "update"),
                    ev["steps"]))


def test_available_row_runs_steps_and_marks_dictation_manual() -> None:
    exec_fn = FakeExec()
    ev = qm.run_route(qm.ROUTES["oneshot-user"], qm.DISTROS["ubuntu-24.04"],
                      mk_env(has_display=False), repo=REPO, exec_fn=exec_fn)
    steps = steps_by_key(ev)
    assert steps["install"]["status"] == "PASS"
    assert exec_fn.calls[0][:2] == ("bash",
                                    str(REPO / "scripts"
                                        / "install-one-shot.sh"))
    # no display: onboarding skips with a reason, not a failure
    assert steps["onboard"]["status"] == "SKIP"
    assert "display" in steps["onboard"]["detail"]
    # dictation is a human step by design — GPU + CPU machines
    assert steps["dictate"]["status"] == "MANUAL"
    assert "GPU" in steps["dictate"]["detail"]
    assert steps["update"]["status"] == "PASS"
    assert exec_fn.calls[-1][-1] == "--update"
    # manual step outstanding -> the row is PENDING, not PASS
    assert ev["status"] == "PENDING"


def test_display_lets_onboarding_run() -> None:
    exec_fn = FakeExec()
    ev = qm.run_route(qm.ROUTES["pipx"], qm.DISTROS["fedora"],
                      mk_env(distro_key="fedora", has_display=True),
                      repo=REPO, exec_fn=exec_fn)
    steps = steps_by_key(ev)
    assert steps["install"]["cmd"] == ["pipx", "install", "sayit-ermano"]
    assert steps["onboard"]["status"] == "PASS"
    assert steps["onboard"]["cmd"] == ["sayit-ermano", "app", "--onboard"]


def test_failed_install_red_row_and_downstream_skip() -> None:
    exec_fn = FakeExec(returncode=1)
    ev = qm.run_route(qm.ROUTES["pipx"], qm.DISTROS["arch"],
                      mk_env(distro_key="arch"), repo=REPO, exec_fn=exec_fn)
    steps = steps_by_key(ev)
    assert steps["install"]["status"] == "FAIL"
    assert steps["install"]["returncode"] == 1
    assert steps["onboard"]["status"] == "SKIP"
    assert steps["update"]["status"] == "SKIP"
    assert "install step did not pass" in steps["update"]["detail"]
    assert ev["status"] == "FAIL"


def test_deb_install_resolution_failure_is_red(monkeypatch) -> None:
    monkeypatch.setattr(qm, "latest_release_deb", lambda: None)
    ev = qm.run_route(qm.ROUTES["deb"], qm.DISTROS["ubuntu-24.04"],
                      mk_env(is_root=True), repo=REPO, exec_fn=FakeExec())
    steps = steps_by_key(ev)
    assert steps["install"]["status"] == "FAIL"
    assert "lookup failed" in steps["install"]["detail"]
    assert ev["status"] == "FAIL"


def test_latest_release_deb_parses_gh_output(monkeypatch) -> None:
    class Proc:
        returncode = 0
        stdout = "v0.8.3 sayit-ermano_0.8.3-1_amd64.deb\n"
    monkeypatch.setattr(qm.shutil, "which", lambda n: "/usr/bin/gh")
    monkeypatch.setattr(qm.subprocess, "run", lambda *a, **k: Proc())
    url = qm.latest_release_deb()
    assert url == ("https://github.com/acailic/SayItErmano/releases/"
                   "download/v0.8.3/sayit-ermano_0.8.3-1_amd64.deb")


# ---------------------------------------------------------------------------
# evidence files + generated README
# ---------------------------------------------------------------------------

def test_evidence_file_shape(tmp_path: Path) -> None:
    path = qm.write_evidence(
        tmp_path, qm.run_route(qm.ROUTES["deb"], qm.DISTROS["ubuntu-24.04"],
                               mk_env(), repo=REPO, exec_fn=FakeExec()))
    assert path.name == "deb-ubuntu-24.04.json"
    ev = json.loads(path.read_text(encoding="utf-8"))
    assert ev["schema"] == qm.SCHEMA
    assert (ev["route"], ev["distro"], ev["status"]) == \
        ("deb", "ubuntu-24.04", "SKIPPED")
    assert ev["skip_reason"]
    assert len(ev["steps"]) == 4
    assert ev["environment"]["distro_pretty"] == "Ubuntu 24.04 LTS"
    assert ev["notes"] == []


def test_full_matrix_on_foreign_host_writes_every_row(tmp_path: Path) -> None:
    env = mk_env(distro_key=None, distro_pretty="Debian GNU/Linux 12",
                 is_root=False, has_display=False)
    evidences = run_all(tmp_path, env)
    assert len(evidences) == len(ALL_COMBOS) == 12
    for route_key, distro_key in ALL_COMBOS:
        f = tmp_path / f"{route_key}-{distro_key}.json"
        assert f.exists(), f
        ev = json.loads(f.read_text(encoding="utf-8"))
        assert ev["status"] == "SKIPPED"
        assert "fresh" in ev["skip_reason"]
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    for route_key, distro_key in ALL_COMBOS:
        assert f"`{route_key}-{distro_key}.json`" in readme


def test_readme_carries_checklist_and_red_row_policy() -> None:
    readme = qm.render_readme(
        [qm.run_route(qm.ROUTES[r], qm.DISTROS[d], mk_env(),
                      repo=REPO, exec_fn=FakeExec())
         for r, d in ALL_COMBOS])
    assert "GPU" in readme and "CPU" in readme
    assert "docs/guides/install.md" in readme  # known-issue workaround spot
    assert "--check" in readme
    # per-distro checklist sections with each route's install command
    for section in ("Ubuntu 24.04", "Fedora", "Arch Linux"):
        assert f"### {section}" in readme
    assert "install-one-shot.sh" in readme
    assert "makepkg -si" in readme
    assert qm.FLATHUB_APP_ID in readme


def test_skipped_row_makes_no_network_calls(monkeypatch) -> None:
    """The deb route resolves the latest-release URL lazily — a skipped
    row must not so much as build its install command."""
    exec_fn = FakeExec()

    def boom():
        raise AssertionError("skipped rows must not resolve commands")
    monkeypatch.setattr(qm, "latest_release_deb", boom)
    ev = qm.run_route(qm.ROUTES["deb"], qm.DISTROS["ubuntu-24.04"],
                      mk_env(distro_key=None), repo=REPO, exec_fn=exec_fn)
    assert ev["status"] == "SKIPPED"
    assert exec_fn.calls == []


# ---------------------------------------------------------------------------
# the --check gate
# ---------------------------------------------------------------------------

def test_check_green_on_full_skipped_matrix(tmp_path: Path) -> None:
    run_all(tmp_path, mk_env(distro_key=None, distro_pretty="Debian 12"))
    assert qm.check_matrix(tmp_path) == []


def test_check_flags_red_row_missing_and_stale(tmp_path: Path) -> None:
    run_all(tmp_path, mk_env(distro_key=None, distro_pretty="Debian 12"))
    red = tmp_path / "pipx-arch.json"
    ev = json.loads(red.read_text(encoding="utf-8"))
    ev["status"] = "FAIL"
    red.write_text(json.dumps(ev), encoding="utf-8")
    problems = qm.check_matrix(tmp_path)
    assert any("red row" in p for p in problems)
    (tmp_path / "deb-ubuntu-24.04.json").unlink()
    problems = qm.check_matrix(tmp_path)
    assert any("missing evidence" in p for p in problems)
    stale = tmp_path / "aur-arch.json"
    ev = json.loads(stale.read_text(encoding="utf-8"))
    ev["schema"] = 0
    stale.write_text(json.dumps(ev), encoding="utf-8")
    assert any("schema" in p for p in qm.check_matrix(tmp_path))


def test_check_requires_readme(tmp_path: Path) -> None:
    assert any("README.md" in p for p in qm.check_matrix(tmp_path))


# ---------------------------------------------------------------------------
# os-release parsing (probe is pure up to this seam)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,key", [
    ('ID=ubuntu\nVERSION_ID="24.04"\nPRETTY_NAME="Ubuntu 24.04 LTS"\n',
     "ubuntu-24.04"),
    ("ID=fedora\nVERSION_ID=42\nPRETTY_NAME=\"Fedora Linux 42\"\n",
     "fedora"),
    ("ID=arch\nPRETTY_NAME=\"Arch Linux\"\n", "arch"),  # no VERSION_ID
    ("ID=debian\nVERSION_ID=\"12\"\n", None),           # not in matrix
])
def test_distro_key_for_os_release(text: str, key: str | None) -> None:
    assert qm.distro_key_for(qm.parse_os_release(text)) == key


def test_env_snapshot_roundtrip() -> None:
    env = mk_env(git_head="43a0013")
    ev = qm.run_route(qm.ROUTES["pipx"], qm.DISTROS["fedora"], env,
                      repo=REPO, exec_fn=FakeExec())
    assert ev["environment"] == dataclasses.asdict(env)
