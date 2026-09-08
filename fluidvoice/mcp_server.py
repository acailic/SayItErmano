"""MCP server bridge (upstream discussion #927): lets MCP-capable agents
use SayItErmano as their STT backend.

Speaks the Model Context Protocol over stdio (newline-delimited JSON-RPC
2.0, handshake `initialize` -> `notifications/initialized` -> tools) and
forwards every tool call to the RUNNING daemon through the existing
unix-socket control channel - no new listener, no TCP, and the daemon's
warm model does the work (a second model is never loaded).

Run: `sayit-ermano mcp`. Register with an MCP client, e.g. Claude
Desktop's config:

    {"mcpServers": {"sayit-ermano":
        {"command": "sayit-ermano", "args": ["mcp"]}}}

stdlib only - the no-new-dependency rule holds.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Callable, TextIO

from . import __version__, control

PROTOCOL_VERSION = "2024-11-05"

# name -> (description, input schema, control action, arg mapper)
TOOLS: dict[str, dict[str, Any]] = {
    "transcribe_file": {
        "description": "Transcribe an audio file through the running "
                       "SayItErmano daemon's warm speech model (wav plus "
                       "11 more formats; ffmpeg fallback for the rest). "
                       "Returns {text, language, duration_s}.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": "absolute path to the audio file"},
                "process": {"type": "boolean", "default": False,
                            "description": "run the filler/punctuation "
                                           "post-processing chain"},
            },
            "required": ["path"],
        },
        "action": "transcribe",
        "map": lambda a: {"path": a.get("path"),
                          "process": bool(a.get("process", False))},
    },
    "history": {
        "description": "Recent stored dictations (chronological, newest "
                       "last): {ts, duration_s, raw, text, ai, backend}.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1,
                          "maximum": 200, "default": 10},
                "since_ts": {"type": "number",
                             "description": "unix timestamp lower bound"},
            },
        },
        "action": "history",
        "map": lambda a: {k: a[k] for k in ("limit", "since_ts") if k in a},
    },
    "status": {
        "description": "Daemon state: recording/busy, backend, model, "
                       "session type, language, update availability.",
        "inputSchema": {"type": "object", "properties": {}},
        "action": "status",
        "map": lambda a: {},
    },
    "toggle": {
        "description": "Start/stop a dictation take (same as pressing the "
                       "hotkey). Use cancel (the daemon's Escape path) to "
                       "abort - not exposed here on purpose.",
        "inputSchema": {"type": "object", "properties": {}},
        "action": "toggle",
        "map": lambda a: {},
    },
}


def _tool_call(name: str, arguments: dict,
               request: Callable[..., dict]) -> dict:
    """One tools/call -> MCP tool result. Daemon errors become isError
    results (visible to the agent), never protocol errors."""
    spec = TOOLS[name]
    resp = request(spec["action"], **spec["map"](arguments or {}))
    if not resp.get("ok", True):
        return {"content": [{"type": "text",
                             "text": str(resp.get("error", "failed"))}],
                "isError": True}
    payload = {k: v for k, v in resp.items() if k != "ok"}
    return {"content": [{"type": "text",
                         "text": json.dumps(payload, ensure_ascii=False)}]}


def handle_message(msg: dict,
                   request: Callable[..., dict] = control.request) -> dict | None:
    """Route one decoded JSON-RPC message; None for notifications."""
    method = msg.get("method")
    msg_id = msg.get("id")
    is_request = "id" in msg
    if not is_request:
        return None  # notifications (initialized, cancelled, ...) get no reply
    try:
        if method == "initialize":
            return {"id": msg_id, "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "sayit-ermano",
                               "version": __version__}}}
        if method == "tools/list":
            return {"id": msg_id, "result": {"tools": [
                {"name": n, "description": t["description"],
                 "inputSchema": t["inputSchema"]}
                for n, t in TOOLS.items()]}}
        if method == "tools/call":
            params = msg.get("params") or {}
            name = str(params.get("name") or "")
            if name not in TOOLS:
                return {"id": msg_id, "error": {
                    "code": -32602, "message": f"unknown tool {name!r}"}}
            return {"id": msg_id,
                    "result": _tool_call(name, params.get("arguments")
                                         or {}, request)}
        if method == "ping":
            return {"id": msg_id, "result": {}}
        return {"id": msg_id, "error": {"code": -32601,
                                        "message": f"unknown method {method!r}"}}
    except control.ControlError as e:
        # daemon unreachable: a protocol-level error the client will show
        return {"id": msg_id, "error": {"code": -32000, "message": str(e)}}


def serve(stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
    """The stdio loop: one JSON object per line in, one reply line out."""
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            reply = {"id": None, "error": {"code": -32700,
                                           "message": "parse error"}}
        else:
            if not isinstance(msg, dict):
                reply = {"id": None, "error": {"code": -32600,
                                               "message": "invalid request"}}
            else:
                reply = handle_message(msg)
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()


def main() -> int:
    serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
