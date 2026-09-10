"""Audit 2026-09-08 hardening: socket-steal refusal (C1), monitor-mic
escape (C3), config-registration meta-test (C4)."""
from __future__ import annotations

import copy

import pytest

from fluidvoice import control
from fluidvoice.config import _SAVE_WHITELIST, ALLOWED_SETTINGS, DEFAULTS

# -- C1: a live daemon's socket is never stolen ------------------------------

def test_serve_refuses_to_steal_a_live_socket(tmp_path):
    sock_path = tmp_path / "sie.sock"
    srv = control.serve(lambda req: {"ok": True, "action": req["action"]},
                        path=sock_path)
    try:
        assert control.request.__name__  # module imported
        with pytest.raises(control.ControlError, match="refusing to steal"):
            control.serve(lambda req: {"ok": True}, path=sock_path)
        # the FIRST daemon still answers (its socket was not unlinked)
        import socket as s_mod
        with s_mod.socket(s_mod.AF_UNIX, s_mod.SOCK_STREAM) as c:
            c.settimeout(2.0)
            c.connect(str(sock_path))
            c.sendall(b'{"action": "status"}\n')
            assert b'"ok"' in c.recv(65536)
    finally:
        srv.close()
        sock_path.unlink(missing_ok=True)


def test_serve_takes_over_a_stale_socket_file(tmp_path):
    sock_path = tmp_path / "stale.sock"
    sock_path.write_bytes(b"leftover")  # no listener behind it
    srv = control.serve(lambda req: {"ok": True}, path=sock_path)
    try:
        assert sock_path.exists()  # rebound fine
    finally:
        srv.close()
        sock_path.unlink(missing_ok=True)


def test_socket_env_override(tmp_path, monkeypatch):
    from fluidvoice import paths
    monkeypatch.setenv("SAYITERMANO_SOCKET", str(tmp_path / "x.sock"))
    assert paths.socket_path() == tmp_path / "x.sock"
    monkeypatch.delenv("SAYITERMANO_SOCKET")
    assert paths.socket_path().name == "sayit-ermano.sock"


# -- C3: explicit .monitor devices are never warned/auto-switched ------------

def test_monitor_device_never_reselected():
    from fluidvoice import daemon as dm
    switched = []
    d = object.__new__(dm.Daemon)
    d.cfg = copy.deepcopy(DEFAULTS)
    d.cfg["recording"]["device"] = "sie_mic.monitor"
    d.cfg["recording"]["mic_priority"] = ["bluez"]
    d._mic_missing_logged = False
    d._set_device = lambda name: switched.append(name)
    d.notify = lambda *a, **k: None
    d._mic_reselect(["alsa_input.usb-cam"])   # monitor not in the list
    assert switched == []                     # no auto-switch away
    assert d._mic_missing_logged is False     # and no warning latched


# -- C4: every DEFAULTS key is registered everywhere it must be --------------

# deliberately NOT socket-settable / not persisted (documented exceptions):
# ai.api_key is env-only by design; sample_rate is a fixed hardware constant;
# profiles.migrated_from_legacy is a one-time migration marker (P2), not a
# user setting - saved to persist the one-time semantics, never socket-set
_NOT_ALLOWED = {("ai", "api_key"), ("recording", "sample_rate"),
                ("profiles", "migrated_from_legacy")}
_NOT_SAVED = {("recording", "sample_rate")}


def test_every_default_key_is_socket_settable():
    missing = [f"{s}.{k}" for s, keys in DEFAULTS.items() for k in keys
               if f"{s}.{k}" not in
               {f"{a}.{b}" for a, bs in ALLOWED_SETTINGS.items() for b in bs}
               and (s, k) not in _NOT_ALLOWED]
    assert missing == []


def test_every_default_key_is_saved():
    missing = [f"{s}.{k}" for s, keys in DEFAULTS.items() for k in keys
               if k not in _SAVE_WHITELIST.get(s, [])
               and (s, k) not in _NOT_SAVED]
    assert missing == []
