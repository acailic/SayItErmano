"""Control-socket route table — org plan 5.3 (D1).

One handler per action name. ``Daemon.handle_request`` delegates here,
and both bridges dispatch through it by construction: the unix control
socket forwards request dicts straight to the daemon, and the MCP
bridge (``mcp_server.py``) builds ``{"action": ...}`` dicts and sends
them over that same socket. Adding a route touches this module alone.

Handlers take ``(daemon, request)`` and return the response dict —
verbatim extractions from Daemon.handle_request (behavior-preserving;
the socket/MCP test suites are the safety net).
"""

from __future__ import annotations

import time
from typing import Any, Callable

from . import __version__, backends
from . import history as history_mod
from . import session as session_mod

#: (daemon, request) -> response
Route = Callable[[Any, dict], dict]


def _toggle(d: Any, req: dict) -> dict:
    recording = d.toggle()
    return {"ok": True, "recording": recording}


def _cancel(d: Any, req: dict) -> dict:
    d.cancel()
    return {"ok": True, "recording": False, "cancelled": True}


def _paste_last(d: Any, req: dict) -> dict:
    ok, detail = d.paste_last()
    return {"ok": ok, "error": detail if not ok else None}


def _cycle_language(d: Any, req: dict) -> dict:
    return {"ok": True, **d._engines.cycle_language()}


def _insert_text(d: Any, req: dict) -> dict:
    ok, detail = d.insert_text_action(str(req.get("text", "")))
    return {"ok": ok, "error": detail if not ok else None}


def _command_rerun(d: Any, req: dict) -> dict:
    purpose = req.get("purpose")
    return d._commands.rerun(str(req.get("command", "")),
                             str(purpose) if purpose else None)


def _status(d: Any, req: dict) -> dict:
    upd = d._update_status()
    with d._lock:
        model_state = {
            "policy_s": d._engines.idle_threshold(),
            "loaded": d.backend is not None,
            "idle_s": round(time.monotonic() - d._engines.last_activity, 1),
        }
    return {"ok": True, "recording": d.recording, "busy": d.busy,
            "backend": d.backend.name if d.backend else None,
            # what the model ACTUALLY runs on: the loaded backend's
            # resolved device (post auto-pick and CPU fallback);
            # with no model loaded, what "auto" would pick
            "cuda": (getattr(d.backend, "device", "") == "cuda"
                     if d.backend is not None else
                     backends.cuda_available()),
            "version": __version__,
            # None = hotkey disabled/--no-hotkey; False = every
            # lock-mask combo not held (blocked, daemon retrying)
            "hotkey_grabbed": (d._hotkey.hotkey_grabbed
                               if d._hotkey is not None else None),
            # None = no button configured (or unavailable); False =
            # the button grab is refused and being retried
            "mouse_ptt_grabbed": (d._mouse_ptt.button_grabbed
                                  if d._mouse_ptt is not None
                                  else None),
            "locked": d._locked,
            # lock watch surface (lockmon status: mode/via name the
            # watched session - the doctor lock line reads this)
            "lock_watch": (d._lockmon.status()
                           if d._lockmon is not None else
                           {"active": False, "mode": "off",
                            "session": None, "via": None,
                            "locked": d._locked}),
            # session type + per-capability backends (wayland port
            # v0.3; additive keys - JSON consumers unaffected)
            "session": {"type": d._session.type,
                        "desktop": d._session.desktop},
            "capabilities": session_mod.capabilities(
                d._session, cfg=d.cfg),
            "warmup": dict(d.warmup),
            "active_model": d._engines.active_model_name(),
            "active_model_key": backends.backend_model_key(d.backend) or
                                backends.config_model_key(d.cfg),
            "today": history_mod.today_stats(history_mod.read_all()),
            # update check-and-assist (fluidvoice/update.py): the
            # dict carries everything; the two flat keys are the
            # CLI/UI convenience surface
            "update": upd,
            "update_available": upd.get("update_available"),
            "update_url": upd.get("url"),
            # idle-unload policy + live state (doctor/tray read
            # this; additive key - JSON consumers unaffected)
            "model_state": model_state,
            # language cycle + guard state (doctor/CLI/GTK read
            # this; additive key - JSON consumers unaffected)
            "language": d._engines.language_status()}


def _shutdown(d: Any, req: dict) -> dict:
    d._quit_gracefully()
    return {"ok": True}


def _set_device(d: Any, req: dict) -> dict:
    device = str(req.get("device", ""))
    d._set_device(device)
    return {"ok": True, "device": device}


def _test_dictation(d: Any, req: dict) -> dict:
    return d.test_dictation(float(req.get("seconds", 3.0)))


def _get_config(d: Any, req: dict) -> dict:
    from .config import mask_secrets
    return {"ok": True, "config": mask_secrets(d.cfg)}


def _set_config(d: Any, req: dict) -> dict:
    return d._set_config(req.get("config") or {})


def _select_model(d: Any, req: dict) -> dict:
    return d._engines.select_model(str(req.get("name", "")))


def _model_delete(d: Any, req: dict) -> dict:
    return d._engines.delete_model(str(req.get("kind", "")),
                                   str(req.get("name", "")))


def _mics(d: Any, req: dict) -> dict:
    from .tray import list_microphones
    return {"ok": True, "mics": list_microphones()}


def _transcribe(d: Any, req: dict) -> dict:
    return d._api_transcribe(str(req.get("path") or ""),
                             bool(req.get("process", False)))


def _history(d: Any, req: dict) -> dict:
    return d._api_history(req)


#: The route table — the ONE dispatch surface shared by the control
#: socket and the MCP bridge (via the socket).
ROUTES: dict[str, Route] = {
    "toggle": _toggle,
    "cancel": _cancel,
    "paste-last": _paste_last,
    "cycle-language": _cycle_language,
    "insert-text": _insert_text,
    "command-rerun": _command_rerun,
    "status": _status,
    "shutdown": _shutdown,
    "set-device": _set_device,
    "test-dictation": _test_dictation,
    "get-config": _get_config,
    "set-config": _set_config,
    "select-model": _select_model,
    "model-delete": _model_delete,
    "mics": _mics,
    "transcribe": _transcribe,
    "history": _history,
}


def dispatch(daemon: Any, req: dict) -> dict:
    """Route a control request dict; unknown actions get the same
    error response the inline dispatcher always returned."""
    action = req.get("action")
    handler = ROUTES.get(action) if isinstance(action, str) else None
    if handler is None:
        return {"ok": False, "error": f"unknown action {action!r}"}
    return handler(daemon, req)
