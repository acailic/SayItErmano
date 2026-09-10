"""MCP server bridge (upstream #927): JSON-RPC 2.0 validation + envelope
compliance, MCP version negotiation, notifications, tools/list,
tools/call against a fake daemon socket, error paths, and an
Inspector-compatible full handshake through serve() - no real stdio, no
daemon."""
from __future__ import annotations

import io
import json
import socket

from fluidvoice import control
from fluidvoice.mcp_server import (
    PROTOCOL_VERSION,
    handle_message,
    serve,
)


def rpc(method, **params):
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}


def assert_valid_response(reply, msg_id, *, result=False, error=False):
    """Every reply is a JSON-RPC 2.0 object: jsonrpc member, echoed id,
    and exactly one of result/error."""
    assert reply["jsonrpc"] == "2.0"
    assert reply["id"] == msg_id
    assert ("result" in reply) is result
    assert ("error" in reply) is error
    return reply


# ---------------------------------------------------------------------------
# Handshake + tools (behavior kept from the original bridge)
# ---------------------------------------------------------------------------

def test_initialize_handshake():
    r = assert_valid_response(handle_message(rpc("initialize")), 1,
                              result=True)
    result = r["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert "tools" in result["capabilities"]
    assert result["serverInfo"]["name"] == "sayit-ermano"


def test_notification_gets_no_reply():
    assert handle_message(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_tools_list():
    r = assert_valid_response(handle_message(rpc("tools/list")), 1,
                              result=True)
    names = {t["name"] for t in r["result"]["tools"]}
    assert names == {"transcribe_file", "history", "status", "toggle"}
    for t in r["result"]["tools"]:
        assert t["inputSchema"]["type"] == "object"
        assert t["description"]


def test_tool_call_transcribe():
    calls = []

    def fake(action, **kw):
        calls.append((action, kw))
        return {"ok": True, "text": "hello world", "language": "en"}

    r = handle_message(rpc("tools/call", name="transcribe_file",
                           arguments={"path": "/tmp/x.wav", "process": True}),
                       request=fake)
    assert_valid_response(r, 1, result=True)
    assert calls == [("transcribe", {"path": "/tmp/x.wav", "process": True})]
    assert "isError" not in r["result"]   # success result
    text = json.loads(r["result"]["content"][0]["text"])
    assert text["text"] == "hello world"


def test_tool_call_daemon_error_is_tool_error():
    def fake(action, **kw):
        return {"ok": False, "error": "busy (recording)"}

    r = handle_message(rpc("tools/call", name="toggle"), request=fake)
    assert_valid_response(r, 1, result=True)
    assert r["result"]["isError"] is True
    assert "busy" in r["result"]["content"][0]["text"]


def test_tool_call_daemon_down_is_protocol_error():
    def boom(action, **kw):
        raise control.ControlError("daemon not running")

    r = handle_message(rpc("tools/call", name="status"), request=boom)
    assert_valid_response(r, 1, error=True)
    assert r["error"]["code"] == -32000
    assert "daemon not running" in r["error"]["message"]


def test_unknown_tool_and_method():
    r = handle_message(rpc("tools/call", name="nope"))
    assert_valid_response(r, 1, error=True)
    assert r["error"]["code"] == -32602
    r = handle_message(rpc("resources/list"))
    assert_valid_response(r, 1, error=True)
    assert r["error"]["code"] == -32601


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 spec compliance (official examples)
# https://www.jsonrpc.org/specification#examples
# ---------------------------------------------------------------------------

class TestJsonRpcCompliance:
    def test_id_echo_string_number_null(self):
        for msg_id in ("abc", 7, None):
            msg = {"jsonrpc": "2.0", "id": msg_id, "method": "ping"}
            assert_valid_response(handle_message(msg), msg_id, result=True)

    def test_named_params_work(self):
        r = handle_message(rpc("tools/call", name="status", arguments={}),
                           request=lambda a, **k: {"ok": True})
        assert_valid_response(r, 1, result=True)

    def test_positional_params_are_invalid_for_mcp(self):
        # JSON-RPC allows positional params, but MCP methods take named
        # params only - the array is structurally valid, so this is an
        # invalid-params error, not an invalid request.
        msg = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
               "params": ["transcribe_file", {"path": "/x"}]}
        r = assert_valid_response(handle_message(msg), 2, error=True)
        assert r["error"]["code"] == -32602

    def test_invalid_request_shapes(self):
        cases = [
            ([1, 2, 3], None),          # batch/array: not an object
            (42, None),                 # scalar: not an object
            ({"id": 1, "method": "ping"}, 1),          # no jsonrpc member
            ({"jsonrpc": "1.0", "id": 1, "method": "ping"}, 1),  # wrong ver
            ({"jsonrpc": "2.0", "id": 1, "params": {}}, 1),  # no method
            ({"jsonrpc": "2.0", "id": False, "method": "ping"}, None),
            ({"jsonrpc": "2.0", "id": {"bad": 1}, "method": "ping"}, None),
        ]
        for msg, expected_id in cases:
            r = assert_valid_response(handle_message(msg), expected_id,
                                      error=True)
            assert r["error"]["code"] == -32600, msg

    def test_official_invalid_request_example(self):
        # verbatim from the JSON-RPC 2.0 spec: non-string method gets an
        # Invalid Request error with null id
        r = assert_valid_response(
            handle_message({"jsonrpc": "2.0", "method": 1,
                            "params": "bar"}), None, error=True)
        assert r["error"]["code"] == -32600

    def test_invalid_params_type(self):
        msg = {"jsonrpc": "2.0", "id": 5, "method": "tools/list",
               "params": "not-an-object-or-array"}
        r = assert_valid_response(handle_message(msg), 5, error=True)
        assert r["error"]["code"] == -32602

    def test_unknown_method_code(self):
        msg = {"jsonrpc": "2.0", "id": "rpc-1", "method": "no/such"}
        r = assert_valid_response(handle_message(msg), "rpc-1", error=True)
        assert r["error"]["code"] == -32601


class TestVersionNegotiation:
    def _init(self, version):
        msg = {"jsonrpc": "2.0", "id": 9, "method": "initialize",
               "params": {"protocolVersion": version}}
        return assert_valid_response(handle_message(msg), 9, result=True)

    def test_supported_version_is_echoed(self):
        r = self._init("2024-11-05")
        assert r["result"]["protocolVersion"] == "2024-11-05"

    def test_unsupported_versions_negotiate_down(self):
        # MCP negotiation: an unsupported client version still gets a
        # valid initialize result carrying OUR latest supported version;
        # the client disconnects if it cannot accept that.
        for version in ("2025-06-18", "1.0", "garbage"):
            r = self._init(version)
            assert r["result"]["protocolVersion"] == PROTOCOL_VERSION

    def test_missing_version_gets_ours(self):
        msg = {"jsonrpc": "2.0", "id": 9, "method": "initialize",
               "params": {}}
        r = assert_valid_response(handle_message(msg), 9, result=True)
        assert r["result"]["protocolVersion"] == PROTOCOL_VERSION


class TestNotifications:
    def test_valid_notifications_produce_no_output(self):
        for method in ("notifications/initialized",
                       "notifications/cancelled"):
            assert handle_message(
                {"jsonrpc": "2.0", "method": method}) is None

    def test_invalid_notification_still_gets_null_id_error(self):
        # structurally invalid (non-string method) + no id: cannot be
        # treated as a notification - the spec's Invalid Request example
        r = assert_valid_response(
            handle_message({"jsonrpc": "2.0", "method": 42}), None,
            error=True)
        assert r["error"]["code"] == -32600


# ---------------------------------------------------------------------------
# stdio loop
# ---------------------------------------------------------------------------

def test_serve_loop_end_to_end(monkeypatch):
    monkeypatch.setattr(control, "request",
                        lambda a, **k: {"ok": True, "text": "hi"})
    inbox = io.StringIO("\n".join([
        json.dumps(rpc("initialize")),
        json.dumps({"jsonrpc": "2.0",
                    "method": "notifications/initialized"}),
        "not json at all",
        json.dumps(rpc("tools/list")),
    ]) + "\n")
    out = io.StringIO()
    serve(inbox, out)
    replies = [json.loads(l) for l in out.getvalue().splitlines()]
    assert len(replies) == 3  # notification produced no reply
    assert replies[0]["result"]["serverInfo"]["name"] == "sayit-ermano"
    assert replies[1]["error"]["code"] == -32700
    assert len(replies[2]["result"]["tools"]) == 4
    for r in replies:  # every wire reply carries the jsonrpc member
        assert r["jsonrpc"] == "2.0"


def test_serve_non_object_json(monkeypatch):
    out = io.StringIO()
    serve(io.StringIO('[1, 2, 3]\n'), out)
    r = json.loads(out.getvalue())
    assert_valid_response(r, None, error=True)
    assert r["error"]["code"] == -32600


# ---------------------------------------------------------------------------
# Bridge survival (F1): transport failures must never kill the process
# ---------------------------------------------------------------------------

class TestBridgeSurvivesTransportFailures:
    """control.request raises plain OSErrors from its 15 s socket timeout
    (a slow-but-legal transcribe_file) and ConnectionResetError when the
    daemon restarts mid-call. The bridge must answer with a clean
    JSON-RPC error and keep serving - the old loop died on the first one."""

    def _call(self, raiser):
        return handle_message(
            rpc("tools/call", name="transcribe_file",
                arguments={"path": "/x.wav"}),
            request=raiser)

    def test_socket_timeout_is_a_clean_error(self):
        def slow_daemon(action, **kw):
            raise socket.timeout("timed out")  # control.request at 15 s

        r = self._call(slow_daemon)
        assert_valid_response(r, 1, error=True)
        assert r["error"]["code"] == -32000
        assert "daemon" in r["error"]["message"]

    def test_daemon_restart_mid_call_is_a_clean_error(self):
        def restarted(action, **kw):
            raise ConnectionResetError("daemon restarted")

        r = self._call(restarted)
        assert_valid_response(r, 1, error=True)
        assert r["error"]["code"] == -32000

    def test_unexpected_exception_is_internal_error_not_death(self):
        def buggy(action, **kw):
            raise KeyError("bridge bug")

        r = self._call(buggy)
        assert_valid_response(r, 1, error=True)
        assert r["error"]["code"] == -32603

    def test_serve_loop_survives_a_timeout_and_serves_the_next_line(self):
        # the sweep's exact repro shape: a transcribe_file whose model
        # time exceeds the client timeout used to kill the bridge process
        def slow_daemon(action, **kw):
            if action == "transcribe":
                raise socket.timeout("timed out")
            return {"ok": True}

        inbox = io.StringIO("\n".join([
            json.dumps(rpc("tools/call", name="transcribe_file",
                           arguments={"path": "/x.wav"})),
            json.dumps(rpc("ping")),
        ]) + "\n")
        out = io.StringIO()
        serve(inbox, out, request=slow_daemon)  # returns: the bridge lived
        replies = [json.loads(l) for l in out.getvalue().splitlines()]
        assert replies[0]["error"]["code"] == -32000
        assert replies[1]["result"] == {}  # still serving after the failure


class TestInspectorHandshake:
    """The full MCP Inspector flow over the stdio loop: initialize ->
    notifications/initialized -> tools/list -> tools/call, with response
    validity asserted at every step."""

    def _flow(self, init_params):
        def fake_request(action, **kw):
            return {"ok": True, "text": "hi", "language": "en"}

        lines = [
            json.dumps({"jsonrpc": "2.0", "id": 1,
                        "method": "initialize", "params": init_params}),
            json.dumps({"jsonrpc": "2.0",
                        "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                        "params": {"name": "transcribe_file",
                                   "arguments": {"path": "/tmp/a.wav"}}}),
            json.dumps({"jsonrpc": "2.0", "id": 4, "method": "ping"}),
        ]
        out = io.StringIO()
        serve(io.StringIO("\n".join(lines) + "\n"), out,
              request=fake_request)
        return [json.loads(l) for l in out.getvalue().splitlines()]

    def test_full_handshake(self):
        replies = self._flow(
            {"protocolVersion": "2024-11-05",
             "capabilities": {},
             "clientInfo": {"name": "Inspector"}})
        assert len(replies) == 4  # the notification stayed silent
        init = assert_valid_response(replies[0], 1, result=True)
        assert init["result"]["protocolVersion"] == "2024-11-05"
        assert "tools" in init["result"]["capabilities"]
        assert init["result"]["serverInfo"]["name"] == "sayit-ermano"
        tools = assert_valid_response(replies[1], 2, result=True)
        assert {t["name"] for t in tools["result"]["tools"]} == \
            {"transcribe_file", "history", "status", "toggle"}
        call = assert_valid_response(replies[2], 3, result=True)
        assert "isError" not in call["result"]
        assert json.loads(call["result"]["content"][0]["text"])["text"] == "hi"
        assert_valid_response(replies[3], 4, result=True)

    def test_handshake_with_unsupported_version(self):
        replies = self._flow({"protocolVersion": "2025-06-18"})
        init = assert_valid_response(replies[0], 1, result=True)
        # negotiated downgrade: our latest supported version, still valid
        assert init["result"]["protocolVersion"] == PROTOCOL_VERSION
