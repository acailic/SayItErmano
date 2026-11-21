"""scripts/install-one-shot.sh --update — the in-place update dispatch.

Sandboxed per the #22 acceptance ("installer self-update"): HOME pointed
at tmp_path and PATH prefixed with stub dpkg/systemctl/sudo/apt/pipx/
curl/pkill binaries that record their calls. Nothing real runs - no
network (the curl stub never connects), no package operations - the
tests pin the DECISION logic: which route an existing install takes and
that config/history survive an in-place user-venv update.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "install-one-shot.sh"

pytestmark = pytest.mark.skipif(
    not (shutil.which("bash") and shutil.which("dpkg-deb")),
    reason="bash + dpkg-deb required to sandbox the installer")


def _stub(bindir: Path, name: str, body: str) -> None:
    p = bindir / name
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(0o755)


class Sandbox:
    """A fake machine for the installer: $HOME, PATH stubs, a call log."""

    def __init__(self, tmp_path: Path):
        self.root = tmp_path
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.bin = tmp_path / "stubs"
        self.bin.mkdir()
        self.log = tmp_path / "calls.log"
        self.log.write_text("")
        self.marker = tmp_path / "sayit-ermano.installed"

        common = f'CALLS="{self.log}"\nlog() {{ printf "%s\\n" "$*" >> "$CALLS"; }}\n'

        _stub(self.bin, "systemctl", common + "log systemctl $*\nexit 0\n")
        _stub(self.bin, "pkill", common + "log pkill $*\nexit 0\n")
        _stub(self.bin, "sudo", common + 'log "sudo $*"\nexec "$@"\n')
        _stub(self.bin, "apt", common + 'log "apt $*"\nexit 0\n')
        _stub(self.bin, "pipx", common + 'log "pipx $*"\nexit 0\n')
        _stub(self.bin, "pw-record", common + "exit 0\n")
        # dpkg: -s sayit-ermano answers from the marker file; every other
        # package counts as installed (the dependency prompt stays out of
        # the way); arch is always amd64
        _stub(self.bin, "dpkg", f'''{common}MARKER="{self.marker}"
if [ "$1" = "-s" ] && [ "$2" = "sayit-ermano" ]; then
    [ -e "$MARKER" ] && exit 0
    exit 1
fi
if [ "$1" = "--print-architecture" ]; then echo amd64; exit 0; fi
exit 0
''')
        # curl: the release-API GET returns canned JSON; the -o download
        # writes a plausibly sized payload. Never touches the network.
        _stub(self.bin, "curl", common + '''prev=""
for a in "$@"; do
    if [ "$prev" = "-o" ]; then
        head -c 12000000 /dev/zero > "$a"
        log "curl -o $a"
        exit 0
    fi
    prev="$a"
done
log "curl $*"
printf '{"assets":[{"browser_download_url":"https://x/sayit-ermano_9.9.9-1_amd64.deb"}]}'
''')

    # -- machine state -------------------------------------------------------

    def deb_installed(self, on: bool = True) -> None:
        if on:
            self.marker.write_text("")
        elif self.marker.exists():
            self.marker.unlink()

    def apt_repo_configured(self) -> None:
        lst = self.root / "apt-list.d" / "sayit-ermano.list"
        lst.parent.mkdir(exist_ok=True)
        lst.write_text("deb https://acailic.github.io/SayItErmano/apt stable main\n")

    def user_venv(self) -> None:
        venv = self.home / ".local" / "share" / "sayit-ermano" / "venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "python").write_text("old")

    def pipx_venv(self) -> Path:
        venv = self.home / ".local" / "share" / "pipx" / "venvs" / "sayit-ermano"
        venv.mkdir(parents=True)
        return venv

    # -- run -----------------------------------------------------------------

    def run(self, *args: str, dry_run: bool = False) -> subprocess.CompletedProcess:
        env = {
            **{k: v for k, v in os.environ.items()
               if not k.startswith(("LC_", "LANG"))},
            "HOME": str(self.home),
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "SAYITERMANO_APT_LIST": str(
                self.root / "apt-list.d" / "sayit-ermano.list"),
        }
        if dry_run:
            env["DRY_RUN"] = "1"
        return subprocess.run(
            ["bash", str(SCRIPT), *args], env=env, capture_output=True,
            text=True, timeout=120)

    def calls(self) -> str:
        return self.log.read_text()


@pytest.fixture()
def box(tmp_path: Path) -> Sandbox:
    return Sandbox(tmp_path)


class TestUpdateDispatch:
    def test_nothing_installed_refuses(self, box):
        r = box.run("--update")
        assert r.returncode != 0
        assert "no existing installation found" in r.stderr
        assert box.calls() == ""  # nothing was executed

    def test_pipx_route(self, box):
        """The Fedora-capable route: pipx installs update via pipx, no
        dpkg/deb involved at all."""
        venv = box.pipx_venv()
        r = box.run("--update")
        assert r.returncode == 0
        assert str(venv) in r.stdout
        assert "pipx upgrade sayit-ermano" in box.calls()

    def test_pipx_route_dry_run(self, box):
        box.pipx_venv()
        r = box.run("--update", dry_run=True)
        assert r.returncode == 0
        assert "DRY_RUN=1: would run: pipx upgrade sayit-ermano" in r.stdout
        assert box.calls() == ""  # nothing executed

    def test_system_deb_via_apt_repo(self, box):
        """deb + the repo the deb configured: pure apt upgrade traffic."""
        box.deb_installed()
        box.apt_repo_configured()
        r = box.run("--update")
        assert r.returncode == 0
        calls = box.calls()
        assert "sudo apt update" in calls
        assert "apt install -y --only-upgrade sayit-ermano" in calls
        assert "curl" not in calls  # no manual download needed

    def test_system_deb_without_repo_downloads_deb(self, box):
        box.deb_installed()
        r = box.run("--update")
        assert r.returncode == 0
        calls = box.calls()
        assert "apt install -y " in calls and "--only-upgrade" not in calls
        assert "curl -o " in calls

    def test_system_update_requires_system_package(self, box):
        r = box.run("--system", "--update")
        assert r.returncode != 0
        assert "no system package (sayit-ermano) installed" in r.stderr

    def test_user_venv_precedence_over_system_deb(self, box):
        """Both layouts present: the user install (the one that runs) is
        refreshed and the duplicate is called out."""
        box.user_venv()
        box.deb_installed()
        r = box.run("--update", dry_run=True)
        assert r.returncode == 0
        assert "User installation found" in r.stdout
        assert "system-wide .deb" in r.stdout
        assert "apt" not in box.calls()  # the system deb was NOT touched


@pytest.fixture(scope="module")
def mini_deb(tmp_path_factory) -> Path:
    """A real (tiny) deb with the expected layout, built with dpkg-deb."""
    root = tmp_path_factory.mktemp("minideb")
    stage = root / "stage"
    (stage / "opt" / "sayit-ermano" / "venv" / "bin").mkdir(parents=True)
    (stage / "opt" / "sayit-ermano" / "venv" / "bin" / "python").write_text(
        "#!/bin/sh\n")
    apps = stage / "usr" / "share" / "applications"
    apps.mkdir(parents=True)
    (apps / "sayit-ermano.desktop").write_text(
        "[Desktop Entry]\nExec=/usr/bin/sayit-ermano\n")
    auto = stage / "etc" / "xdg" / "autostart"
    auto.mkdir(parents=True)
    (auto / "sayit-ermano.desktop").write_text(
        "[Desktop Entry]\nExec=/usr/bin/sayit-ermano\n")
    (stage / "usr" / "share" / "icons" / "hicolor").mkdir(parents=True)
    (stage / "DEBIAN").mkdir()
    (stage / "DEBIAN" / "control").write_text(
        "Package: sayit-ermano\nVersion: 9.9.9-1\nArchitecture: amd64\n"
        "Maintainer: t\nDescription: test payload\n")
    out = root / "sayit-ermano_9.9.9-1_amd64.deb"
    subprocess.run(["dpkg-deb", "--build", "--root-owner-group",
                    str(stage), str(out)], check=True, capture_output=True)
    return out


class TestUserVenvInPlace:
    def test_update_replaces_venv_keeps_config_and_history(self, box, mini_deb):
        """The #22 acceptance core at script level: an in-place update
        replaces the runtime, never the user's config/history."""
        box.user_venv()
        cfg = box.home / ".config" / "sayit-ermano" / "config.toml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text('[updates]\ncheck = false\n')
        history = box.home / ".local" / "share" / "sayit-ermano" / "history.jsonl"
        history.write_text('{"ts": 1, "text": "kept"}\n')

        r = box.run("--update", str(mini_deb))
        assert r.returncode == 0, r.stderr
        # the venv was replaced (new python from the deb payload)
        new_python = box.home / ".local" / "share" / "sayit-ermano" / "venv" / "bin" / "python"
        assert new_python.read_text() == "#!/bin/sh\n"
        # the daemon was stopped before its files moved
        assert "systemctl --user stop sayit-ermano.service" in box.calls()
        # config and history are intact, byte for byte
        assert cfg.read_text() == '[updates]\ncheck = false\n'
        assert history.read_text() == '{"ts": 1, "text": "kept"}\n'
        # launcher + unit were (re)written
        assert (box.home / ".local" / "bin" / "sayit-ermano").exists()
        unit = box.home / ".config" / "systemd" / "user" / "sayit-ermano.service"
        assert unit.exists()
        assert "daemon restarted" in r.stdout
