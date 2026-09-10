"""Real daemon subprocess driven over the unix control socket (the native
GTK app's transport; the web UI's HTTP surface retired with it)."""
import time

import pytest

from fluidvoice import control, paths

pytestmark = [pytest.mark.integration]


class TestDaemonProcess:
    def test_status_shape(self, daemon_process):
        status = control.request("status")
        assert status["ok"] and status["recording"] is False
        assert status["backend"] and status["version"]
        assert status["warmup"]["running"] in (True, False)
        assert status["active_model"]

    def test_config_roundtrip(self, daemon_process):
        cfg = control.request("get-config")["config"]
        assert isinstance(cfg["ai"]["api_key"], bool)  # masked, never the value
        resp = control.request("set-config", config={"sounds": {"volume": 0.6}})
        assert resp["ok"] and resp["changed"] == ["sounds.volume"]
        after = control.request("get-config")["config"]
        assert after["sounds"]["volume"] == 0.6
        # rejected values never half-apply
        resp = control.request("set-config", config={"hotkey": {"mode": "explode"}})
        assert resp["ok"] is False and resp["rejected"] == ["hotkey.mode"]
        control.request("set-config", config={"sounds": {"volume": 1.0}})

    def test_toggle_cancel_cycle(self, daemon_process):
        assert control.request("toggle")["recording"] is True
        time.sleep(0.8)
        resp = control.request("cancel")
        assert resp["cancelled"] is True and resp["recording"] is False

    def test_paste_last_reports_nothing_initially(self, daemon_process):
        resp = control.request("paste-last")
        assert resp["ok"] is False and "nothing" in (resp.get("error") or "")

    def test_shutdown_cleans_socket(self, daemon_process):
        socket = paths.socket_path()
        assert socket.exists()
        daemon_process.send_signal(2)  # SIGINT -> graceful shutdown
        daemon_process.wait(timeout=10)
        deadline = time.monotonic() + 5
        while socket.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not socket.exists()

    def test_unknown_action_rejected(self, daemon_process):
        resp = control.request("explode")
        assert resp["ok"] is False

    def test_model_delete_removes_decoy_over_socket(self, daemon_process):
        """Settings → Models pruning: a decoy cached model dir is removed
        via the socket; the active model (config-derived) is refused."""
        from fluidvoice import model_catalog
        decoy = model_catalog.cache_entry_path("faster-whisper", "tiny")
        (decoy / "blobs").mkdir(parents=True)
        (decoy / "blobs" / "b").write_bytes(b"x" * 4242)
        resp = control.request("model-delete", kind="faster-whisper",
                                name="tiny")
        assert resp["ok"] is True and resp["bytes"] == 4242
        assert not decoy.exists()
        # the config's active model is refused (auto backend -> small/base)
        from fluidvoice import backends
        from fluidvoice.config import load_config
        active = backends.config_model_key(load_config())
        active_dir = model_catalog.cache_entry_path("faster-whisper", active)
        (active_dir / "blobs").mkdir(parents=True)
        (active_dir / "blobs" / "b").write_bytes(b"x")
        resp = control.request("model-delete", kind="faster-whisper",
                                name=active)
        assert resp["ok"] is False and "active model" in resp["error"]
        assert active_dir.exists()  # untouched
        # unknown kind refused
        resp = control.request("model-delete", kind="bogus", name="x")
        assert resp["ok"] is False
