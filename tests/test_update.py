"""fluidvoice/update.py — the check-and-assist updater core.

Everything here is offline: fetch seams are stubbed, install-method markers
are injected as tmp_path trees, state files are explicit paths. The
daemon/CLI/doctor surfacing tests live at the bottom (same stub idioms as
test_daemon.py / test_cli_ui_hotkey.py).
"""
from __future__ import annotations

import copy
import json
import threading
import urllib.error
from pathlib import Path

import pytest

from fluidvoice import update
from fluidvoice.config import DEFAULTS, load_config, save_config


@pytest.fixture(autouse=True)
def _pin_current_version(monkeypatch):
    """The staged releases below (0.6.0, 0.7.0, ...) are "newer" relative to
    a fictional current 0.5.0 - pin the current version in every module
    that binds it (update, cli, doctor) so a package version bump can never
    flip these expectations."""
    from fluidvoice import cli, doctor, update
    for mod in (cli, doctor, update):
        monkeypatch.setattr(mod, "__version__", "0.5.0")


REL06 = {
    "tag_name": "v0.6.0",
    "html_url": "https://github.com/acailic/SayItErmano/releases/tag/v0.6.0",
    "assets": [{
        "name": "sayit-ermano_0.6.0-1_amd64.deb",
        "browser_download_url":
            "https://github.com/acailic/SayItErmano/releases/download/"
            "v0.6.0/sayit-ermano_0.6.0-1_amd64.deb",
        "size": 79953876,
        "digest": "sha256:0f3a9b",
    }],
}


class TestSemver:
    @pytest.mark.parametrize("tag,want", [
        ("v0.5.0", (0, 5, 0)),
        ("0.5.0", (0, 5, 0)),
        ("V1.2.3", (1, 2, 3)),
        ("10.20.30", (10, 20, 30)),
        ("0.10", (0, 10)),
        ("", ()),
        ("v", ()),
        ("abc", ()),
        ("0.5.0-1", ()),      # pre-release-ish junk: fail safe
        ("0.5.x", ()),
    ])
    def test_parse_version(self, tag, want):
        assert update.parse_version(tag) == want

    @pytest.mark.parametrize("latest,current,want", [
        ("v0.6.0", "0.5.0", True),     # patch bump
        ("v0.6.0", "v0.5.9", True),
        ("0.6.0", "0.5.0", True),     # minor bump
        ("1.0.0", "0.9.9", True),     # major bump
        ("0.10.0", "0.9.1", True),    # length mismatch, numeric compare
        ("0.5.0", "0.5.0", False),    # equal
        ("v0.5.0", "0.5.0", False),   # equal modulo the v prefix
        ("0.5", "0.5.0", False),      # zero-padded equal
        ("0.5.0", "0.6.0", False),    # older
        ("", "0.5.0", False),         # junk never notifies
        ("abc", "0.5.0", False),
        ("0.6.0", "", False),
        ("0.6.0", "abc", False),
    ])
    def test_is_newer(self, latest, current, want):
        assert update.is_newer(latest, current) is want


class TestFetchLatest:
    class _Resp:
        def __init__(self, data: bytes):
            self._data = data

        def read(self) -> bytes:
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def test_good_payload(self):
        rel, err = update.fetch_latest_result(
            opener=lambda req, timeout: self._Resp(json.dumps(REL06).encode()))
        assert err is None
        assert rel["tag"] == "v0.6.0" and rel["version"] == "0.6.0"
        assert rel["url"].endswith("/tag/v0.6.0")
        assert rel["assets"][0]["name"] == "sayit-ermano_0.6.0-1_amd64.deb"
        assert rel["assets"][0]["digest"] == "sha256:0f3a9b"

    def test_fetch_latest_half(self):
        rel = update.fetch_latest(
            opener=lambda req, timeout: self._Resp(json.dumps(REL06).encode()))
        assert rel is not None and rel["version"] == "0.6.0"

    @pytest.mark.parametrize("bad", [
        b"not json at all",
        b"[]",
        b"{}",
        b'{"html_url": "x"}',  # missing tag_name
    ])
    def test_bad_payload_returns_none(self, bad):
        rel, err = update.fetch_latest_result(
            opener=lambda req, timeout: self._Resp(bad))
        assert rel is None and err

    def test_http_error(self):
        def opener(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 404, "nope", {}, None)
        rel, err = update.fetch_latest_result(opener=opener)
        assert rel is None and "404" in err

    def test_timeout_returns_none(self):
        def opener(req, timeout):
            raise TimeoutError("timed out")
        rel, err = update.fetch_latest_result(opener=opener)
        assert rel is None and err

    def test_urlerror_returns_none(self):
        def opener(req, timeout):
            raise urllib.error.URLError("name resolution failed")
        rel, err = update.fetch_latest_result(opener=opener)
        assert rel is None and err

    def test_user_agent_header_sent(self):
        seen = {}

        def opener(req, timeout):
            seen["ua"] = req.get_header("User-agent")
            return TestFetchLatest._Resp(json.dumps(REL06).encode())

        update.fetch_latest(opener=opener)
        assert seen["ua"].startswith("SayItErmano/")

    def test_find_deb_asset_and_checksum(self):
        rel = update._parse_release(REL06)
        assert update.find_deb_asset(rel)["name"] == "sayit-ermano_0.6.0-1_amd64.deb"
        assert update.deb_checksum(rel) == "sha256:0f3a9b"
        assert update.find_deb_asset({"assets": []}) is None
        assert update.deb_checksum(None) is None


class TestDetectInstallMethod:
    def _mk(self, root: Path, *parts: str) -> Path:
        exe = root.joinpath(*parts)
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.touch()
        return exe

    def test_deb_layout(self):
        # absolute system path; the branch is a pure prefix check
        info = update.detect_install_method(
            exe=Path("/opt/sayit-ermano/venv/bin/python"))
        assert info == {"method": "deb", "marker": "/opt/sayit-ermano/venv"}

    def test_user_venv_layout(self, tmp_path):
        root = tmp_path.resolve()
        exe = self._mk(root, ".local", "share", "sayit-ermano",
                       "venv", "bin", "python")
        info = update.detect_install_method(exe=exe, home=root)
        assert info["method"] == "user-venv"
        assert info["marker"] == str(root / ".local" / "share" / "sayit-ermano")

    def test_pipx_layout(self, tmp_path, monkeypatch):
        root = tmp_path.resolve()
        exe = self._mk(root, "pipx", "venvs", "sayit-ermano", "bin", "python")
        monkeypatch.setenv("PIPX_HOME", str(root / "pipx"))
        info = update.detect_install_method(exe=exe, home=root)
        assert info["method"] == "pipx"
        assert info["marker"] == str(root / "pipx" / "venvs" / "sayit-ermano")

    def test_pipx_default_home(self, tmp_path):
        root = tmp_path.resolve()
        exe = self._mk(root, ".local", "pipx", "venvs",
                       "sayit-ermano", "bin", "python")
        info = update.detect_install_method(exe=exe, home=root)  # no PIPX_HOME
        assert info["method"] == "pipx"

    def test_pipx_xdg_share_home(self, tmp_path):
        """pipx >= 1.7 distros default to ~/.local/share/pipx."""
        root = tmp_path.resolve()
        exe = self._mk(root, ".local", "share", "pipx", "venvs",
                       "sayit-ermano", "bin", "python")
        info = update.detect_install_method(exe=exe, home=root)
        assert info["method"] == "pipx"
        assert info["marker"].endswith("venvs/sayit-ermano")

    def test_source_layout(self, tmp_path):
        root = tmp_path.resolve()
        exe = self._mk(root, "repo", ".venv", "bin", "python")
        (root / "repo" / ".git").mkdir()
        (root / "repo" / "pyproject.toml").touch()
        info = update.detect_install_method(exe=exe, home=root)
        assert info["method"] == "source"
        assert info["marker"] == str(root / "repo")

    def test_unknown_layout(self, tmp_path):
        root = tmp_path.resolve()
        exe = self._mk(root, "bare", "venv", "bin", "python")
        info = update.detect_install_method(exe=exe, home=root)
        assert info["method"] == "unknown"

    def test_symlinked_venv_python_still_detects(self, tmp_path):
        """Regression: venv bin/python is a symlink to the system interpreter
        on Linux; following it collapsed every layout to /usr (abspath, not
        resolve, is what detection needs)."""
        root = tmp_path.resolve()
        sys_python = self._mk(root, "usr", "bin", "python3.12")
        venv_bin = root / "repo" / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python").symlink_to(sys_python)
        (root / "repo" / ".git").mkdir()
        (root / "repo" / "pyproject.toml").touch()
        info = update.detect_install_method(exe=venv_bin / "python", home=root)
        assert info["method"] == "source"

    def test_resolution_order_deb_beats_others(self):
        # /opt/sayit-ermano wins regardless of what home holds
        info = update.detect_install_method(
            exe=Path("/opt/sayit-ermano/venvs/sayit-ermano/bin/python"),
            home=Path("/nowhere"))
        assert info["method"] == "deb"


class TestUpgradeCommand:
    REL = update._parse_release(REL06)

    def test_deb_golden(self):
        assert update.upgrade_command("deb", self.REL) == (
            "curl -LO https://github.com/acailic/SayItErmano/releases/download/"
            "v0.6.0/sayit-ermano_0.6.0-1_amd64.deb\n"
            "sudo apt install -y ./sayit-ermano_0.6.0-1_amd64.deb")

    def test_deb_without_asset_falls_back(self):
        assert update.upgrade_command(
            "deb", {"assets": []}) == update.upgrade_command("user-venv", None)

    def test_user_venv_golden(self):
        assert update.upgrade_command("user-venv", self.REL) == (
            "curl -fsSL https://raw.githubusercontent.com/acailic/"
            "SayItErmano/linux/scripts/install-one-shot.sh | bash")

    def test_pipx_golden(self):
        assert update.upgrade_command("pipx", None) == "pipx upgrade sayit-ermano"

    def test_source_golden(self):
        assert update.upgrade_command("source", None) == \
            "git pull && ./scripts/install.sh"

    def test_unknown_golden_mentions_releases_and_pip(self):
        cmd = update.upgrade_command("unknown", None)
        assert update.RELEASES_URL in cmd
        assert "pip install -U sayit-ermano" in cmd
        assert "install-one-shot.sh" in cmd


class TestUpdateChecker:
    def _checker(self, tmp_path, fetch, notified, cfg=None):
        return update.UpdateChecker(
            cfg or copy.deepcopy(DEFAULTS), fetch=fetch,
            state_path=tmp_path / "update-state.json",
            on_notify=lambda title, body: notified.append((title, body)))

    def _rel(self, ver: str) -> dict:
        return {"tag": f"v{ver}", "version": ver, "url": "u", "assets": []}

    def test_check_notifies_exactly_once(self, tmp_path):
        notified: list = []
        current = {"rel": self._rel("0.6.0")}
        checker = self._checker(tmp_path, lambda: current["rel"], notified)
        st = checker.check_now()
        assert st["update_available"] == "0.6.0"
        assert len(notified) == 1
        checker.check_now()  # same release again: no second notification
        assert len(notified) == 1
        state = json.loads((tmp_path / "update-state.json").read_text())
        assert state["notified"] == "0.6.0"
        assert state["last_seen"] == "0.6.0"
        current["rel"] = self._rel("0.7.0")  # a NEWER release: notifies again
        checker.check_now()
        assert len(notified) == 2
        assert notified[0][0] == "SayItErmano update available"
        assert "sayit-ermano update" in notified[0][1]

    def test_notified_state_survives_new_checker(self, tmp_path):
        notified: list = []
        self._checker(tmp_path, lambda: self._rel("0.6.0"), notified).check_now()
        assert len(notified) == 1
        # a fresh daemon (new checker object, same state file): still quiet
        self._checker(tmp_path, lambda: self._rel("0.6.0"), notified).check_now()
        assert len(notified) == 1

    def test_dismissed_suppresses(self, tmp_path):
        notified: list = []
        update.dismiss_update("0.6.0", state_path=tmp_path / "update-state.json")
        checker = self._checker(tmp_path, lambda: self._rel("0.6.0"), notified)
        st = checker.check_now()
        assert st["update_available"] == "0.6.0"  # still reported...
        assert notified == []                     # ...just not notified

    def test_up_to_date_no_notification(self, tmp_path):
        notified: list = []
        checker = self._checker(tmp_path, lambda: self._rel("0.5.0"), notified)
        st = checker.check_now()
        assert st["update_available"] is None
        assert st["latest"] == "0.5.0"
        assert notified == []

    def test_offline_no_crash(self, tmp_path):
        notified: list = []

        def broken():
            raise urllib.error.URLError("net down")

        checker = self._checker(tmp_path, broken, notified)
        assert checker.check_now() is None
        st = checker.status()
        assert st["error"] and "net down" in st["error"]
        assert st["update_available"] is None
        assert st["checked"] is True
        assert notified == []

    def test_fetch_none_no_crash(self, tmp_path):
        notified: list = []
        checker = self._checker(tmp_path, lambda: None, notified)
        assert checker.check_now() is None
        assert checker.status()["error"]

    def test_state_file_corrupt_treated_empty(self, tmp_path):
        p = tmp_path / "update-state.json"
        p.write_text("{not json")
        notified: list = []
        checker = self._checker(tmp_path, lambda: self._rel("0.6.0"), notified)
        st = checker.check_now()  # must not raise
        assert st["update_available"] == "0.6.0"
        assert len(notified) == 1  # junk state did not look "already notified"

    def test_start_false_when_disabled(self, tmp_path):
        cfg = copy.deepcopy(DEFAULTS)
        cfg["updates"]["check"] = False
        notified: list = []
        checker = self._checker(tmp_path, lambda: self._rel("0.6.0"), notified, cfg)
        assert checker.start() is False
        assert checker.status()["enabled"] is False
        checker.stop()

    def test_start_false_with_env_killswitch(self, tmp_path, monkeypatch):
        monkeypatch.setenv(update.SKIP_ENV, "1")
        notified: list = []
        checker = self._checker(tmp_path, lambda: self._rel("0.6.0"), notified)
        assert checker.start() is False
        assert checker.check_now() is None  # kill-switch blocks sync checks too
        checker.stop()

    def test_start_thread_and_stop(self, tmp_path):
        notified: list = []
        checker = self._checker(tmp_path, lambda: self._rel("0.6.0"), notified)
        checker._interval = 3600  # do not loop mid-test
        assert checker.start() is True
        checker.stop()
        assert not (checker._thread and checker._thread.is_alive()
                    and checker._thread is not threading.current_thread())

    def test_status_carries_upgrade_command(self, tmp_path, monkeypatch):
        monkeypatch.setattr(update, "detect_install_method",
                            lambda exe=None, home=None:
                            {"method": "deb", "marker": "/opt/sayit-ermano/venv"})
        notified: list = []
        rel = {"tag": "v0.6.0", "version": "0.6.0", "url": "u", "assets": [{
            "name": "sayit-ermano_0.6.0-1_amd64.deb",
            "url": "https://github.com/acailic/SayItErmano/releases/download/"
                   "v0.6.0/sayit-ermano_0.6.0-1_amd64.deb"}]}
        checker = self._checker(tmp_path, lambda: rel, notified)
        st = checker.check_now()
        assert st["upgrade_command"].startswith("curl -LO https://")
        assert "sudo apt install" in st["upgrade_command"]


class TestDismiss:
    def test_dismiss_defaults_to_last_seen(self, tmp_path):
        p = tmp_path / "update-state.json"
        p.write_text(json.dumps({"last_seen": "0.6.0"}))
        assert update.dismiss_update(state_path=p) == "0.6.0"
        assert json.loads(p.read_text())["dismissed"] == "0.6.0"

    def test_dismiss_explicit_version(self, tmp_path):
        p = tmp_path / "update-state.json"
        assert update.dismiss_update("0.9.9", state_path=p) == "0.9.9"
        assert json.loads(p.read_text())["dismissed"] == "0.9.9"


class TestConfigWiring:
    def test_defaults_have_updates(self):
        assert DEFAULTS["updates"] == {"check": True, "notify": True}

    def test_save_config_roundtrip(self, tmp_path):
        cfg = copy.deepcopy(DEFAULTS)
        cfg["updates"]["check"] = False
        path = tmp_path / "config.toml"
        save_config(cfg, path)
        text = path.read_text()
        assert "[updates]" in text and "check = false" in text
        back = load_config(path)
        assert back["updates"]["check"] is False
        assert back["updates"]["notify"] is True

    def test_template_mentions_updates(self):
        from fluidvoice.config import TEMPLATE
        assert "[updates]" in TEMPLATE
        assert 'check = true' in TEMPLATE

    def test_updates_keys_allowed_and_bool(self):
        from fluidvoice.config import ALLOWED_SETTINGS, SETTING_BOOLS
        assert ALLOWED_SETTINGS["updates"] == {"check", "notify"}
        assert ("updates", "check") in SETTING_BOOLS
        assert ("updates", "notify") in SETTING_BOOLS


# ---------------------------------------------------------------------------
# Phase 2: daemon wiring, CLI `update`, doctor
# ---------------------------------------------------------------------------

from fluidvoice import (
    cli,  # noqa: E402
    doctor,  # noqa: E402
)
from fluidvoice import daemon as dm  # noqa: E402


class _Rec:
    """Minimal recorder stub (test_daemon.py StubRecorder shape)."""

    def start(self, path):
        pass

    def stop(self):
        return None

    def cancel(self):
        pass

    def elapsed(self):
        return 0.0


class _Backend:
    name = "stub"

    def transcribe(self, wav, language=None):
        return {"text": "x", "language": "en", "duration": 1.0}


class TestDaemonWiring:
    def make(self, cfg):
        d = dm.Daemon(cfg, recorder=_Rec(),
                      backend_factory=lambda c: _Backend(),
                      use_hotkey=False, use_sounds=False)
        d.backend = _Backend()
        return d

    def test_status_with_checker(self):
        d = self.make(copy.deepcopy(DEFAULTS))

        class FakeChecker:
            def status(self):
                return {"enabled": True, "checked": True, "latest": "0.6.0",
                        "update_available": "0.6.0",
                        "url": "https://github.com/x",
                        "error": None, "checked_at": 1.0, "method": "deb",
                        "upgrade_command": "sudo apt install -y ./a.deb"}

            def stop(self):
                pass

        d._update = FakeChecker()
        resp = d.handle_request({"action": "status"})
        assert resp["update_available"] == "0.6.0"
        assert resp["update_url"] == "https://github.com/x"
        assert resp["update"]["upgrade_command"].startswith("sudo apt")

    def test_status_without_checker(self):
        d = self.make(copy.deepcopy(DEFAULTS))
        resp = d.handle_request({"action": "status"})
        assert resp["update_available"] is None
        assert resp["update_url"] is None
        assert resp["update"]["enabled"] is False
        assert resp["update"]["method"]  # detected even when disabled

    def test_start_checker_env_killswitch(self, monkeypatch):
        monkeypatch.setenv(update.SKIP_ENV, "1")
        d = self.make(copy.deepcopy(DEFAULTS))
        d._start_update_checker()
        assert d._update is None

    def test_start_checker_disabled_config(self):
        cfg = copy.deepcopy(DEFAULTS)
        cfg["updates"]["check"] = False
        d = self.make(cfg)
        d._start_update_checker()
        assert d._update is None

    def test_start_checker_uses_injected_notify(self, monkeypatch):
        notified: list = []
        created = {}

        class FakeChecker:
            def __init__(self, cfg, **kw):
                created.update(kw)
                created["cfg"] = cfg

            def start(self):
                return True

            def stop(self):
                pass

        monkeypatch.setattr(dm.update_mod, "UpdateChecker", FakeChecker)
        cfg = copy.deepcopy(DEFAULTS)
        d = self.make(cfg)
        d._start_update_checker()
        assert d._update is not None
        assert callable(created["on_notify"])
        created["on_notify"]("t", "b")  # must not raise (no real ui path)
        assert created["cfg"] is cfg


class TestCliUpdate:
    def _patch(self, monkeypatch, method, marker, result=None):
        monkeypatch.setattr(update, "detect_install_method",
                            lambda exe=None, home=None:
                            {"method": method, "marker": marker})
        monkeypatch.setattr(update, "fetch_latest_result",
                            lambda *a, **kw: (result, None)
                            if result is not None else (None, "URLError: down"))

    def test_prints_deb_command(self, monkeypatch, capsys):
        self._patch(monkeypatch, "deb", "/opt/sayit-ermano/venv",
                    update._parse_release(REL06))
        assert cli.main(["update"]) == 0
        out = capsys.readouterr().out
        assert "install: deb — /opt/sayit-ermano/venv" in out
        assert "latest release: v0.6.0 — update available" in out
        assert ("curl -LO https://github.com/acailic/SayItErmano/releases/"
                "download/v0.6.0/sayit-ermano_0.6.0-1_amd64.deb" in out)
        assert "sudo apt install -y ./sayit-ermano_0.6.0-1_amd64.deb" in out
        assert "sha256: 0f3a9b" in out

    def test_prints_user_venv_command(self, monkeypatch, capsys):
        self._patch(monkeypatch, "user-venv", "/home/u/.local/share/sayit-ermano",
                    update._parse_release(REL06))
        assert cli.main(["update"]) == 0
        out = capsys.readouterr().out
        assert "install: user-venv" in out
        assert ("curl -fsSL https://raw.githubusercontent.com/acailic/"
                "SayItErmano/linux/scripts/install-one-shot.sh | bash" in out)

    def test_offline_still_prints_block(self, monkeypatch, capsys):
        self._patch(monkeypatch, "deb", "/opt/sayit-ermano/venv", result=None)
        assert cli.main(["update"]) == 0
        out = capsys.readouterr().out
        assert "latest release: unknown (offline or GitHub API error" in out
        assert "install-one-shot.sh | bash" in out  # deb fallback block

    def test_up_to_date(self, monkeypatch, capsys):
        rel = dict(REL06, tag_name="v0.5.0")
        self._patch(monkeypatch, "source", "/repo",
                    update._parse_release(rel))
        assert cli.main(["update"]) == 0
        out = capsys.readouterr().out
        assert "up to date" in out
        assert "update available" not in out

    def test_env_killswitch(self, monkeypatch, capsys):
        monkeypatch.setenv(update.SKIP_ENV, "1")

        def no_fetch(*a, **kw):
            raise AssertionError("kill-switch must prevent the fetch")

        monkeypatch.setattr(update, "fetch_latest_result", no_fetch)
        self._patch(monkeypatch, "pipx", "/home/u/.local/pipx/venvs/sayit-ermano")
        assert cli.main(["update"]) == 0
        out = capsys.readouterr().out
        assert "check skipped (SAYITERMANO_SKIP_UPDATE_CHECK=1)" in out
        assert "pipx upgrade sayit-ermano" in out

    def test_dismiss_writes_state(self, monkeypatch, capsys):
        from fluidvoice import paths

        def no_fetch(*a, **kw):
            raise AssertionError("--dismiss must not hit the network")

        monkeypatch.setattr(update, "fetch_latest_result", no_fetch)
        assert cli.main(["update", "--dismiss"]) == 0
        out = capsys.readouterr().out
        assert out.startswith("dismissed ")
        state_file = paths.update_state_file()  # isolated by conftest XDG
        assert json.loads(state_file.read_text())["dismissed"]

    def test_json_output(self, monkeypatch, capsys):
        self._patch(monkeypatch, "deb", "/opt/sayit-ermano/venv",
                    update._parse_release(REL06))
        assert cli.main(["update", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["update_available"] == "0.6.0"
        assert payload["method"] == "deb"
        assert "sudo apt install" in payload["upgrade_command"]


class TestDoctorUpdate:
    def test_offline_unknown_line(self):
        lines = doctor._update_lines(copy.deepcopy(DEFAULTS),
                                     check=lambda: None)
        assert lines[0].startswith(f"version: ") and "latest unknown" in lines[0]
        assert "offline or GitHub API error" in lines[0]
        assert any("install:" in l for l in lines)

    def test_update_available_block(self, monkeypatch):
        monkeypatch.setattr(update, "detect_install_method",
                            lambda exe=None, home=None:
                            {"method": "deb", "marker": "/opt/sayit-ermano/venv"})
        rel = update._parse_release(REL06)
        lines = doctor._update_lines(copy.deepcopy(DEFAULTS), check=lambda: rel)
        assert "update available: sayit-ermano update" in lines[0]
        assert "install: deb (/opt/sayit-ermano/venv)" in lines[1]
        assert any("curl -LO " in l for l in lines)
        assert any("sudo apt install -y ./sayit-ermano_0.6.0-1_amd64.deb" in l
                   for l in lines)
        assert any("sha256: 0f3a9b" in l for l in lines)

    def test_up_to_date_line(self):
        rel = update._parse_release(dict(REL06, tag_name="v0.5.0"))
        lines = doctor._update_lines(copy.deepcopy(DEFAULTS),
                                     check=lambda: rel)
        assert "(up to date)" in lines[0]

    def test_disabled_config_line(self):
        cfg = copy.deepcopy(DEFAULTS)
        cfg["updates"]["check"] = False

        def no_fetch():
            raise AssertionError("disabled check must not fetch")

        lines = doctor._update_lines(cfg, check=no_fetch)
        assert lines == ["update check: disabled (updates.check = false)"]

    def test_env_killswitch_line(self, monkeypatch):
        monkeypatch.setenv(update.SKIP_ENV, "1")
        lines = doctor._update_lines(copy.deepcopy(DEFAULTS))
        assert lines == [f"update check: skipped ({update.SKIP_ENV}=1)"]


class TestDoctorDuplicateLayoutPure:
    """The both-layouts branch, exercised through a temp /opt stand-in."""

    def test_both_present_warns(self, tmp_path, monkeypatch):
        from pathlib import Path as P
        home = tmp_path / "home"
        (home / ".local" / "share" / "sayit-ermano").mkdir(parents=True)
        opt = tmp_path / "opt" / "sayit-ermano"
        opt.mkdir(parents=True)
        real_is_dir = P.is_dir

        def fake_is_dir(self):
            if str(self) == "/opt/sayit-ermano":
                return opt.is_dir()
            return real_is_dir(self)

        monkeypatch.setattr(P, "is_dir", fake_is_dir)
        lines = doctor._duplicate_install_lines(home=home)
        assert len(lines) == 1 and "WARNING" in lines[0]
        assert "two daemons fight over the hotkey" in lines[0]

    def test_single_layout_silent(self, tmp_path):
        home = tmp_path
        (home / ".local" / "share" / "sayit-ermano").mkdir(parents=True)
        assert doctor._duplicate_install_lines(home=home) == []
