"""MCP server bridge (upstream discussion #927): lets MCP-capable agents
use SayItErmano as their STT backend.

Speaks the Model Context Protocol over stdio (newline-delimited JSON-RPC
2.0, handshake `initialize` -> `notifications/initialized` -> tools) and
forwards every tool call to the RUNNING daemon through the existing
unix-socket control channel - no new listener, no TCP, and the daemon's
warm model does the work (a second model is never loaded).

SECURITY: launching this bridge grants the connected MCP client access to
local dictation history, transcription files, and dictation control
(toggle/start-stop takes) on this machine - register it only with clients
you trust (see README).

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

# MCP protocol revisions this bridge speaks (stdio framing, tools only).
# Negotiation (MCP basic/lifecycle): when a client requests a version we
# do not support we answer with our LATEST supported revision; the client
# then decides whether to continue or disconnect.
SUPPORTED_VERSIONS = ("2024-11-05",)
PROTOCOL_VERSION = SUPPORTED_VERSIONS[-1]

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

# JSON-RPC 2.0 error codes (https://www.jsonrpc.org/specification)
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
# Server error: the bridge could not reach the local daemon
DAEMON_UNREACHABLE = -32000


def _response(msg_id: Any, *, result: Any = None,
              error: dict[str, Any] | None = None) -> dict[str, Any]:
    """The one response envelope: every reply is a valid JSON-RPC 2.0
    object with the mandatory jsonrpc member and the request id echoed
    (null when the request's id was unusable, e.g. parse errors)."""
    resp: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        resp["error"] = error
    else:
        resp["result"] = result
    return resp


def _err(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return _response(msg_id, error={"code": code, "message": message})


def _usable_id(value: Any) -> bool:
    """JSON-RPC 2.0: id, if present, is String, Number, or NULL. bool is
    an int subclass in Python but is NOT a JSON-RPC id."""
    if value is None or isinstance(value, str):
        return True
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate(msg: Any) -> tuple[dict | None, dict | None]:
    """Structural JSON-RPC 2.0 validation.

    Returns (request, error): exactly one is None except for a valid
    notification, which returns (None, None) - no reply at all. Error
    replies carry the request id when it is usable, null otherwise.
    """
    if not isinstance(msg, dict):
        return None, _err(None, INVALID_REQUEST,
                          "request must be a JSON object")
    if "id" in msg and not _usable_id(msg["id"]):
        return None, _err(None, INVALID_REQUEST,
                          "id must be a string, number, or null")
    msg_id = msg.get("id")
    if msg.get("jsonrpc") != "2.0":
        return None, _err(msg_id, INVALID_REQUEST,
                          'missing or non-"2.0" jsonrpc member')
    method = msg.get("method")
    if not isinstance(method, str):
        return None, _err(msg_id, INVALID_REQUEST,
                          "method must be a string")
    if "params" in msg and msg["params"] is not None \
            and not isinstance(msg["params"], (dict, list)):
        return None, _err(msg_id, INVALID_PARAMS,
                          "params must be an object or array")
    if "id" not in msg:
        return None, None  # structurally valid notification: no reply
    return msg, None


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
    req, err = validate(msg)
    if err is not None:
        return err
    if req is None:
        return None  # valid notification (initialized, cancelled, ...): no reply
    method: str = req["method"]
    msg_id = req["id"]
    params = req.get("params")
    if params is None:
        params = {}
    try:
        if method == "initialize":
            if not isinstance(params, dict):
                return _err(msg_id, INVALID_PARAMS,
                            "params must be an object for initialize")
            requested = params.get("protocolVersion")
            # MCP version negotiation: echo a supported request; answer an
            # unsupported one with our latest (the client disconnects if
            # that is unacceptable to it)
            version = requested if requested in SUPPORTED_VERSIONS \
                else PROTOCOL_VERSION
            return _response(msg_id, result={
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "sayit-ermano",
                               "version": __version__}})
        if method == "tools/list":
            return _response(msg_id, result={"tools": [
                {"name": n, "description": t["description"],
                 "inputSchema": t["inputSchema"]}
                for n, t in TOOLS.items()]})
        if method == "tools/call":
            if not isinstance(params, dict):
                return _err(msg_id, INVALID_PARAMS,
                            "params must be an object for tools/call "
                            "(named parameters)")
            name = params.get("name")
            if not isinstance(name, str) or name not in TOOLS:
                return _err(msg_id, INVALID_PARAMS,
                            f"unknown tool {name!r}")
            arguments = params.get("arguments")
            if arguments is not None and not isinstance(arguments, dict):
                return _err(msg_id, INVALID_PARAMS,
                            "arguments must be an object")
            return _response(msg_id,
                             result=_tool_call(name, arguments or {},
                                               request))
        if method == "ping":
            return _response(msg_id, result={})
        return _err(msg_id, METHOD_NOT_FOUND,
                    f"unknown method {method!r}")
    except control.ControlError as e:
        # daemon unreachable: a protocol-level error the client will show
        return _err(msg_id, DAEMON_UNREACHABLE, str(e))
    except OSError as e:
        # transport failures out of control.request: its 15 s socket
        # timeout on a long transcribe_file (TimeoutError is an OSError),
        # ConnectionResetError from a daemon restarting mid-call, an
        # ENOENT race on the socket path. A clean protocol-level error
        # response - the bridge process itself must survive (F1).
        return _err(msg_id, DAEMON_UNREACHABLE,
                    f"cannot reach daemon: {e}")
    except Exception as e:  # noqa: BLE001 - one bad call must never kill
        # the bridge: answer with an internal error and keep serving
        return _err(msg_id, INTERNAL_ERROR, f"internal error: {e}")


def serve(stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout,
          request: Callable[..., dict] = control.request) -> None:
    """The stdio loop: one JSON object per line in, one reply line out.
    `request` is injectable so tests never touch a real daemon socket."""
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            reply = _err(None, PARSE_ERROR, "parse error")
        else:
            try:
                reply = handle_message(msg, request)
            except Exception as e:  # noqa: BLE001 - the loop must survive
                # even a bug inside the bridge itself (F1)
                reply = _err(None, INTERNAL_ERROR, f"internal error: {e}")
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()


def main() -> int:
    serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
