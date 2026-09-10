"""ControlServer (P0.2): responsive, bounded control socket.

Proves the plan's control matrix: a two-second transcription does not
delay ``status`` beyond 250 ms; concurrent transcriptions still yield one
active job (the busy arbitration stays in the handler); the 1 MiB request
and 16 MiB response limits answer with structured errors instead of
crashing or hanging; the socket is mode 0600; ``shutdown()`` joins the
accept thread and every worker leaving ``threading.enumerate()`` clean.
"""
from __future__ import annotations

import json
import os
import socket
import stat
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from fluidvoice import control
from fluidvoice.control_server import ControlServer

# -- helpers ------------------------------------------------------------------

def _client(path: Path, timeout: float = 10.0) -> socket.socket:
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(timeout)
    c.connect(str(path))
    return c


def _read_line(c: socket.socket) -> bytes:
    buf = b""
    while b"\n" not in buf:
        chunk = c.recv(65536)
        if not chunk:
            break
        buf += chunk
    return buf


def _roundtrip(c: socket.socket, payload: bytes) -> dict:
    c.sendall(payload)
    line = _read_line(c)
    assert line, "server closed without a response line"
    return json.loads(line.decode())


def _rt_obj(c: socket.socket, obj: dict) -> dict:
    return _roundtrip(c, json.dumps(obj).encode() + b"\n")


@contextmanager
def _server(handler, tmp_path: Path, name: str = "c.sock", **kwargs):
    srv = ControlServer(handler, tmp_path / name, **kwargs)
    srv.start()
    try:
        yield srv
    finally:
        srv.shutdown()


def _control_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate()
            if t.name.startswith("fluidvoice-control")]


# -- responsiveness: the headline defect ---------------------------------------

def test_two_second_transcription_does_not_delay_status(tmp_path):
    started = threading.Event()

    def handler(req):
        if req["action"] == "transcribe":
            started.set()
            time.sleep(2.0)  # a long job holding one worker
            return {"ok": True, "text": "slow"}
        return {"ok": True, "action": req["action"]}

    with _server(handler, tmp_path) as srv:
        result = {}

        def slow_client():
            with _client(srv.path) as c:
                result["transcribe"] = _rt_obj(c, {"action": "transcribe"})

        t = threading.Thread(target=slow_client, daemon=True)
        t.start()
        assert started.wait(5), "transcribe never started"
        t0 = time.monotonic()
        with _client(srv.path) as c:
            resp = _rt_obj(c, {"action": "status"})
        elapsed = time.monotonic() - t0
        assert resp == {"ok": True, "action": "status"}
        assert elapsed < 0.25, f"status waited {elapsed * 1000:.0f} ms behind the transcription"
        t.join(timeout=10)
        assert result["transcribe"] == {"ok": True, "text": "slow"}


def test_concurrent_transcriptions_yield_one_active_job(tmp_path):
    """The busy guarantee is the daemon handler's; the server must simply
    not serialize the losers behind the winner (serialized losers would
    each run in turn and all succeed)."""
    lock = threading.Lock()
    state = {"busy": False, "completed": 0}

    def handler(req):
        if req["action"] == "transcribe":
            with lock:
                if state["busy"]:
                    return {"ok": False, "error": "busy"}
                state["busy"] = True
            try:
                time.sleep(1.0)  # one active job at a time
                with lock:
                    state["completed"] += 1
                return {"ok": True}
            finally:
                with lock:
                    state["busy"] = False
        return {"ok": True}

    with _server(handler, tmp_path) as srv:
        results = [None] * 4
        barriers = threading.Barrier(4)

        def one(i):
            with _client(srv.path) as c:
                barriers.wait(timeout=5)
                results[i] = _rt_obj(c, {"action": "transcribe"})

        threads = [threading.Thread(target=one, args=(i,), daemon=True)
                   for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert not any(t.is_alive() for t in threads)
        assert sorted(r["ok"] for r in results) == [False, False, False, True]
        assert state["completed"] == 1  # exactly one active job ran


def test_burst_of_more_clients_than_workers_all_answered(tmp_path):
    def handler(req):
        time.sleep(0.05)
        return {"ok": True, "i": req["i"]}

    with _server(handler, tmp_path, workers=2) as srv:
        results = [None] * 10

        def one(i):
            with _client(srv.path) as c:
                results[i] = _rt_obj(c, {"action": "ping", "i": i})

        threads = [threading.Thread(target=one, args=(i,), daemon=True)
                   for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert results == [{"ok": True, "i": i} for i in range(10)]


# -- bounds --------------------------------------------------------------------

def test_request_over_one_mib_gets_structured_error(tmp_path):
    def handler(req):  # pragma: no cover - must never be reached
        return {"ok": True}

    with _server(handler, tmp_path) as srv:
        pad = b"x" * (1024 * 1024)  # pushes the line past 1 MiB
        line = b'{"action": "status", "pad": "' + pad + b'"}\n'
        with _client(srv.path) as c:
            resp = _roundtrip(c, line)  # one sendall, then read
        assert resp["ok"] is False
        assert "too large" in resp["error"]
        # the server survived and still serves
        with _client(srv.path) as c:
            assert _rt_obj(c, {"action": "status"}) == {"ok": True}


def test_request_just_under_one_mib_is_served(tmp_path):
    seen = {}

    def handler(req):
        seen.update(req)
        return {"ok": True, "len": len(req["pad"])}

    with _server(handler, tmp_path) as srv:
        # whole line (JSON + newline) just below 1 MiB
        pad_len = 1024 * 1024 - 1024
        with _client(srv.path) as c:
            resp = _roundtrip(
                c, b'{"action": "echo", "pad": "' + b"y" * pad_len + b'"}\n')
        assert resp["ok"] is True
        assert resp["len"] == pad_len and len(seen["pad"]) == pad_len


def test_response_over_16_mib_gets_structured_error(tmp_path):
    def handler(req):
        if req["action"] == "history":
            return {"ok": True, "blob": "z" * (17 * 1024 * 1024)}
        return {"ok": True}

    with _server(handler, tmp_path) as srv:
        with _client(srv.path, timeout=30) as c:
            t0 = time.monotonic()
            resp = _rt_obj(c, {"action": "history", "limit": 200})
            elapsed = time.monotonic() - t0
        assert resp["ok"] is False
        assert "too large" in resp["error"]
        assert elapsed < 15  # answered, not hung
        # and the server is still healthy
        with _client(srv.path) as c:
            assert _rt_obj(c, {"action": "status"}) == {"ok": True}


def test_socket_mode_is_0600(tmp_path):
    with _server(lambda req: {"ok": True}, tmp_path):
        mode = stat.S_IMODE(os.stat(tmp_path / "c.sock").st_mode)
        assert mode == 0o600


# -- deterministic shutdown -----------------------------------------------------

def test_shutdown_leaves_no_worker_threads(tmp_path):
    with _server(lambda req: {"ok": True}, tmp_path) as srv:
        with _client(srv.path) as c:
            assert _rt_obj(c, {"action": "status"}) == {"ok": True}
        assert len(_control_threads()) == 1 + 8  # accept + workers
    # context manager already called shutdown()
    deadline = time.monotonic() + 5
    while _control_threads() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _control_threads(), \
        f"threads survived shutdown: {[t.name for t in _control_threads()]}"
    assert not (tmp_path / "c.sock").exists()
    srv.shutdown()  # idempotent


def test_shutdown_lets_the_inflight_request_finish(tmp_path):
    entered = threading.Event()

    def handler(req):
        entered.set()
        time.sleep(0.5)
        return {"ok": True, "slow": True}

    srv = ControlServer(handler, tmp_path / "c.sock")
    srv.start()
    result = {}

    def slow_client():
        with _client(srv.path) as c:
            result["resp"] = _rt_obj(c, {"action": "transcribe"})

    t = threading.Thread(target=slow_client, daemon=True)
    t.start()
    assert entered.wait(5)
    time.sleep(0.1)  # request is mid-handler now
    t0 = time.monotonic()
    srv.shutdown()
    shutdown_s = time.monotonic() - t0
    t.join(timeout=5)
    assert result["resp"] == {"ok": True, "slow": True}  # joined, not cut off
    assert shutdown_s < 5
    assert not _control_threads()


def test_idle_read_timeout_closes_connection(tmp_path):
    with _server(lambda req: {"ok": True}, tmp_path, idle_timeout=0.3) as srv:
        with _client(srv.path) as c:
            t0 = time.monotonic()
            assert c.recv(65536) == b""  # clean close, no response
            assert time.monotonic() - t0 < 5


# -- protocol validation (structured errors) -------------------------------------

def test_invalid_json_gets_structured_error(tmp_path):
    with _server(lambda req: {"ok": True}, tmp_path) as srv:
        with _client(srv.path) as c:
            resp = _roundtrip(c, b"this is not json\n")
        assert resp == {"ok": False, "error": "invalid JSON"}


@pytest.mark.parametrize("payload", [
    b"[1, 2, 3]\n",           # array
    b'"a string"\n',          # string
    b"42\n",                  # number
    b"null\n",                # literal
])
def test_non_object_json_rejected(tmp_path, payload):
    with _server(lambda req: {"ok": True}, tmp_path) as srv:
        with _client(srv.path) as c:
            resp = _roundtrip(c, payload)
        assert resp["ok"] is False
        assert "JSON object" in resp["error"]


def test_missing_or_non_string_action_rejected(tmp_path):
    calls = []
    with _server(lambda req: calls.append(req) or {"ok": True}, tmp_path) \
            as srv:
        with _client(srv.path) as c:
            resp = _roundtrip(c, b'{"nope": 1}\n')
        assert resp["ok"] is False and "action" in resp["error"]
        with _client(srv.path) as c:
            resp = _roundtrip(c, b'{"action": 7}\n')
        assert resp["ok"] is False and "action" in resp["error"]
    assert calls == []  # the handler never saw them


def test_unknown_string_action_still_reaches_the_handler(tmp_path):
    """Unknown actions remain the handler's business (the daemon answers
    `unknown action 'explode'`) - the server only validates shape."""
    def handler(req):
        return {"ok": False, "error": f"unknown action {req['action']!r}"}

    with _server(handler, tmp_path) as srv:
        with _client(srv.path) as c:
            resp = _rt_obj(c, {"action": "explode"})
    assert resp == {"ok": False, "error": "unknown action 'explode'"}


def test_blank_line_closes_cleanly_without_response(tmp_path):
    with _server(lambda req: {"ok": True}, tmp_path) as srv:
        with _client(srv.path) as c:
            c.sendall(b"\n")
            assert c.recv(65536) == b""


# -- worker resilience -----------------------------------------------------------

def test_handler_exception_is_a_payload_and_workers_survive(tmp_path):
    def handler(req):
        if req["action"] == "boom":
            raise ValueError("boom")
        return {"ok": True}

    with _server(handler, tmp_path) as srv:
        before = len(_control_threads())
        # hammer all eight workers with failures at once
        errors = [None] * 8
        barrier = threading.Barrier(8)

        def one(i):
            with _client(srv.path) as c:
                barrier.wait(timeout=5)
                errors[i] = _rt_obj(c, {"action": "boom"})

        threads = [threading.Thread(target=one, args=(i,), daemon=True)
                   for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert errors == [{"ok": False, "error": "boom"}] * 8
        assert len(_control_threads()) == before  # nobody died
        with _client(srv.path) as c:
            assert _rt_obj(c, {"action": "status"}) == {"ok": True}


# -- module facade compatibility ---------------------------------------------------

def test_serve_facade_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(control.paths, "socket_path",
                        lambda: tmp_path / "s.sock")
    state = {"recording": False}

    def handler(req):
        if req["action"] == "toggle":
            state["recording"] = not state["recording"]
        return {"ok": True, "recording": state["recording"]}

    srv = control.serve(handler)
    try:
        assert isinstance(srv, ControlServer)
        assert control.request("toggle") == {"ok": True, "recording": True}
        assert control.request("toggle") == {"ok": True, "recording": False}
    finally:
        srv.close()  # the old API: callers closed the returned socket
    assert not (tmp_path / "s.sock").exists()
    assert not _control_threads()
