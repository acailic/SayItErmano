"""Rewrite/Write mode - port of FluidVoice's RewriteModeService.

Flow (X11): hotkey captures the current selection via clipboard (Ctrl+C
snapshot with restore); the user dictates an instruction; the transcript
plus the selected-text context goes through the edit prompt (ported
verbatim); the result is typed at the caret, where the still-active
selection in the target app is replaced by the typed text.
"""
from __future__ import annotations

from .ai.client import AIClient, AIError
from .ai.prompts import CONTEXT_TEMPLATE, default_edit_prompt


def build_edit_messages(instruction: str, context: str | None,
                        history: list[dict] | None = None) -> list[dict]:
    """Upstream message shapes: system(+context) then user/follow-up turns."""
    system = default_edit_prompt()
    if context and context.strip():
        block = CONTEXT_TEMPLATE.replace("{context}", context.strip()) if "{context}" in CONTEXT_TEMPLATE \
            else CONTEXT_TEMPLATE.format(context=context.strip())
        system = system + "\n\n" + block
    messages: list[dict] = [{"role": "system", "content": system}]
    if not history:
        if context and context.strip():
            user = (f"User's instruction: {instruction}\n\n"
                    "Apply the instruction to the selected context. "
                    "Output ONLY the rewritten text, nothing else.")
        else:
            user = (f"User's instruction: {instruction}\n\n"
                    "Output ONLY the requested text, nothing else.")
        messages.append({"role": "user", "content": user})
        return messages
    for turn in history:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": (
        f"Follow-up instruction: {instruction}\n\n"
        "Apply this to the previous result. Output ONLY the updated text.")})
    return messages


class RewriteError(RuntimeError):
    pass


def capture_selection() -> str:
    """Snapshot the current selection: Ctrl+C, read PRIMARY-safe via clipboard,
    restore the clipboard afterwards. Returns '' when nothing is selected.
    Wayland sessions use the wtype/ydotool + wl-clipboard equivalents
    (_capture_selection_wayland); the X11 path below is unchanged."""
    import subprocess
    import time

    from . import session as session_mod
    if session_mod.current().is_wayland:
        return _capture_selection_wayland()
    from .insertion import _clipboard_read, _clipboard_write
    previous = _clipboard_read()
    subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+c"],
                   capture_output=True, timeout=5)
    time.sleep(0.12)
    selected = _clipboard_read() or b""
    text = selected.decode(errors="replace")
    # restore the user's clipboard (unless the selection copied nothing new)
    if previous is not None and previous != selected:
        _clipboard_write(previous)
    return text.strip()


def _capture_selection_wayland() -> str:
    """Rewrite-mode selection capture on wayland: ctrl+c through the
    resolved typing tool, wl-paste read, wl-copy restore (mime-aware).
    Returns '' when no typing tool / wl-clipboard is available (rewrite
    proceeds without context, like a failed X11 capture)."""
    import shutil
    import subprocess
    import time

    from .insertion import (
        _key_cmd,
        _resolve_wayland_tool,
        _wl_clipboard_read,
        _wl_clipboard_snapshot,
        _wl_clipboard_write,
    )
    if not (shutil.which("wl-copy") and shutil.which("wl-paste")):
        return ""
    tool, _reason = _resolve_wayland_tool({})
    if tool is None:
        return ""
    previous, mime = _wl_clipboard_snapshot()
    try:
        subprocess.run(_key_cmd(tool, "ctrl+c"), capture_output=True,
                       timeout=5)
    except FileNotFoundError:
        return ""
    time.sleep(0.12)
    selected = _wl_clipboard_read() or b""
    text = selected.decode(errors="replace")
    # restore the user's clipboard (unless the selection copied nothing new)
    if previous is not None and previous != selected:
        try:
            _wl_clipboard_write(previous, mime)
        except Exception:
            pass
    return text.strip()


def run_rewrite(cfg: dict, instruction: str, context: str | None,
                history: list[dict] | None = None) -> str:
    """LLM rewrite with the verbatim upstream edit prompt. Raises RewriteError."""
    if not cfg["ai"].get("enabled"):
        raise RewriteError("rewrite mode needs [ai] enabled with a model")
    client = AIClient(cfg)
    if not client.configured:
        raise RewriteError("AI polish enabled but base_url/model not configured")
    messages = build_edit_messages(instruction, context, history)
    try:
        content = client.chat_messages(messages, temperature=0.7)
    except AIError as e:
        raise RewriteError(str(e)) from e
    from .ai.client import strip_thinking
    cleaned = strip_thinking(content)
    if not cleaned:
        raise RewriteError("empty rewrite response")
    return cleaned
