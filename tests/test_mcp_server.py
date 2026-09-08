"""MCP server bridge (upstream #927): JSON-RPC routing, tools/list,
tools/call against a fake daemon socket, notifications, error paths.
Drives serve() through StringIO - no real stdio, no daemon."""
from __future__ import annotations

import io
import json

from fluidvoice import control
from fluidvoice.mcp_server import handle_message, serve


def rpc(method, **params):
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}


def test_initialize_handshake():
    r = handle_message(rpc("initialize"))
    result = r["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert "tools" in result["capabilities"]
    assert result["serverInfo"]["name"] == "sayit-ermano"


def test_notification_gets_no_reply():
    assert handle_message(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_tools_list():
    r = handle_message(rpc("tools/list"))
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
    assert calls == [("transcribe", {"path": "/tmp/x.wav", "process": True})]
    assert "isError" not in r["result"]   # success result
    text = json.loads(r["result"]["content"][0]["text"])
    assert text["text"] == "hello world"


def test_tool_call_daemon_error_is_tool_error():
    def fake(action, **kw):
        return {"ok": False, "error": "busy (recording)"}

    r = handle_message(rpc("tools/call", name="toggle"), request=fake)
    assert r["result"]["isError"] is True
    assert "busy" in r["result"]["content"][0]["text"]


def test_tool_call_daemon_down_is_protocol_error():
    def boom(action, **kw):
        raise control.ControlError("daemon not running")

    r = handle_message(rpc("tools/call", name="status"), request=boom)
    assert r["error"]["code"] == -32000
    assert "daemon not running" in r["error"]["message"]


def test_unknown_tool_and_method():
    r = handle_message(rpc("tools/call", name="nope"))
    assert r["error"]["code"] == -32602
    r = handle_message(rpc("resources/list"))
    assert r["error"]["code"] == -32601


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
