"""AT-SPI ContextProvider: focused-field identity, role, selection and
bounded preceding text via the freedesktop accessibility bus.

The accessibility stack is imported LAZILY (python-atspi first, then
GIR Atspi) and only when an instance actually needs it; a missing or
broken dependency makes the provider report ``available() == False``
forever after (the import failure is cached) — never an exception, and
never a crash in the take path.

Locating the focused object: at-spi2 has no direct "give me the focus"
query, so the read walks desktop -> application -> ACTIVE window, then
descends for the FOCUSED object. The walk is strictly bounded
(`ReadLimits`: app/window/child counts, depth, total nodes). If no
FOCUSED descendant is found, the ACTIVE window itself is returned as
identity-only context marked ``stale`` — its app identity is still the
focused app, but role/selection/preceding are withheld (they would not
be provably about the focused field).

Both python-atspi (CamelCase: ``getRoleName``, ``queryText``) and GIR
(``get_role_name``) naming are ducked through small adapters; any
single failure degrades that field to None. Live behavior is validated
by the manual smoke matrix (docs/dev/wayland-smoke-matrix.md) — the
unit suite only ever runs against fakes.
"""
from __future__ import annotations

from .base import (
    FocusContext,
    ReadLimits,
    missing_context,
)

# Cached import state: None = not tried, "" = import failed (reason text)
_ATSPI_IMPORT_ERROR: str | None = None


def _try_import():
    """Import python-atspi or GIR Atspi, once. Returns (module, None)
    or (None, error-string). Never raises."""
    global _ATSPI_IMPORT_ERROR
    if _ATSPI_IMPORT_ERROR is not None:
        return None, _ATSPI_IMPORT_ERROR
    try:
        import pyatspi  # type: ignore[import-not-found]
        return pyatspi, None
    except Exception as e1:  # noqa: BLE001 - degrade, never crash
        try:
            import gi  # type: ignore[import-not-found]
            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi  # type: ignore[import-not-found]
            return Atspi, None
        except Exception as e2:  # noqa: BLE001
            _ATSPI_IMPORT_ERROR = (f"pyatspi: {e1}; gi Atspi: {e2}")
            return None, _ATSPI_IMPORT_ERROR


def _reset_import_cache() -> None:
    """Test hook: forget the cached import failure."""
    global _ATSPI_IMPORT_ERROR
    _ATSPI_IMPORT_ERROR = None


# ---------------------------------------------------------------------------
# Duck adapters over the pyatspi / GIR API surfaces. Every adapter
# returns None (or the empty result) on any failure — one broken node
# never aborts the read.
# ---------------------------------------------------------------------------

def _call(obj, *names, args=()):
    """First existing method `name` on obj called with args; None if
    none exists/callable or the call raised."""
    for name in names:
        fn = getattr(obj, name, None)
        if callable(fn):
            try:
                return fn(*args)
            except Exception:  # noqa: BLE001
                return None
    return None


def _attr_or_method(obj, attr, *methods):
    value = getattr(obj, attr, None)
    if value is not None:
        return value
    return _call(obj, *methods)


def _state_contains(node, const) -> bool:
    if const is None:
        return False
    state = _call(node, "getState", "get_state_set")
    if state is None:
        return False
    try:
        return bool(state.contains(const))
    except Exception:  # noqa: BLE001
        return False


def _state_const(atspi, pyatspi_name, gir_name):
    value = getattr(atspi, pyatspi_name, None)
    if value is not None:
        return value
    enum = getattr(atspi, "StateType", None)
    return getattr(enum, gir_name, None) if enum is not None else None


class _AtspiNode:
    """Minimal duck wrapper around one accessible object."""

    __slots__ = ("obj", "atspi")

    def __init__(self, obj, atspi):
        self.obj = obj
        self.atspi = atspi

    @property
    def active(self) -> bool:
        return _state_contains(
            self.obj, _state_const(self.atspi, "STATE_ACTIVE", "ACTIVE"))

    @property
    def focused(self) -> bool:
        return _state_contains(
            self.obj, _state_const(self.atspi, "STATE_FOCUSED", "FOCUSED"))

    @property
    def name(self) -> str | None:
        value = _attr_or_method(self.obj, "name", "get_name")
        try:
            text = str(value) if value is not None else None
        except Exception:  # noqa: BLE001
            return None
        return text or None

    @property
    def role_name(self) -> str | None:
        value = _call(self.obj, "getRoleName", "get_role_name")
        try:
            text = str(value) if value is not None else None
        except Exception:  # noqa: BLE001
            return None
        return text or None

    @property
    def app_name(self) -> str | None:
        app = _call(self.obj, "getApplication", "get_application")
        if app is None:
            return None
        return _AtspiNode(app, self.atspi).name

    def child_count(self) -> int:
        value = _attr_or_method(self.obj, "childCount", "get_child_count")
        try:
            return int(value) if value is not None else 0
        except Exception:  # noqa: BLE001
            return 0

    def child(self, index: int) -> "_AtspiNode | None":
        child = _call(self.obj, "getChildAtIndex", "get_child_at_index",
                      args=(index,))
        if child is None:
            try:
                child = self.obj[index]
            except Exception:  # noqa: BLE001
                child = None
        return _AtspiNode(child, self.atspi) if child is not None else None

    # -- text interface (only on text fields) -----------------------------

    def _text_iface(self):
        return _call(self.obj, "queryText")

    def character_count(self) -> int | None:
        iface = self._text_iface()
        if iface is not None:
            value = _call(iface, "getCharacterCount", "get_character_count")
        else:
            value = _call(self.obj, "getCharacterCount",
                          "get_character_count")
        try:
            return int(value) if value is not None else None
        except Exception:  # noqa: BLE001
            return None

    def caret_offset(self) -> int | None:
        iface = self._text_iface()
        if iface is not None:
            value = _call(iface, "getCaretOffset", "get_caret_offset")
        else:
            value = _call(self.obj, "getCaretOffset", "get_caret_offset")
        try:
            return int(value) if value is not None else None
        except Exception:  # noqa: BLE001
            return None

    def text_range(self, start: int, end: int) -> str | None:
        if start >= end:
            return ""
        iface = self._text_iface()
        holder = iface if iface is not None else self.obj
        value = _call(holder, "getText", "get_text", args=(start, end))
        try:
            return str(value) if value is not None else None
        except Exception:  # noqa: BLE001
            return None

    def selection_range(self) -> tuple[int, int] | None:
        iface = self._text_iface()
        if iface is not None:
            value = _call(iface, "getSelection", "get_selection", args=(0,))
        else:
            value = _call(self.obj, "getSelection", "get_selection",
                          args=(0,))
        try:
            if isinstance(value, (tuple, list)) and len(value) == 2:
                start, end = int(value[0]), int(value[1])
                return (start, end)
        except Exception:  # noqa: BLE001
            return None
        return None


def _desktop(atspi) -> _AtspiNode | None:
    registry = getattr(atspi, "Registry", None)
    obj = _call(registry, "getDesktop", "get_desktop", args=(0,)) \
        if registry is not None else None
    if obj is None:
        obj = _call(atspi, "getDesktop", "get_desktop", args=(0,))
    return _AtspiNode(obj, atspi) if obj is not None else None


def _find_active_window(desktop: _AtspiNode,
                        limits: ReadLimits) -> _AtspiNode | None:
    for ai in range(min(desktop.child_count(), limits.apps)):
        app = desktop.child(ai)
        if app is None:
            continue
        for wi in range(min(app.child_count(), limits.windows)):
            win = app.child(wi)
            if win is not None and win.active:
                return win
    return None


def _find_focused(node: _AtspiNode, limits: ReadLimits,
                  depth: int = 0) -> _AtspiNode | None:
    if not limits.visit():
        return None
    if node.focused:
        return node
    if depth >= limits.depth:
        return None
    for i in range(min(node.child_count(), limits.children)):
        child = node.child(i)
        if child is None:
            continue
        found = _find_focused(child, limits, depth + 1)
        if found is not None:
            return found
    return None


def read_focus(atspi, max_preceding: int,
               limits: ReadLimits | None = None) -> FocusContext:
    """One bounded accessibility read. Pure with respect to `atspi`
    being any duck-typed module — unit tests inject fakes."""
    limits = limits or ReadLimits()
    desktop = _desktop(atspi)
    if desktop is None:
        return missing_context("atspi")
    window = _find_active_window(desktop, limits)
    if window is None:
        return missing_context("atspi")
    focused = _find_focused(window, limits)
    if focused is not None:
        node, stale = focused, False
    else:
        # No FOCUSED state found (some toolkits skip it): identity-only.
        node, stale = window, True
    app_id = node.app_name or window.name
    return FocusContext(
        app_id=app_id,
        window_title=window.name,
        accessible_role=None if stale else node.role_name,
        selection_text=None if stale else _selection_text(node),
        preceding_text=None if stale else _preceding_text(node,
                                                          max_preceding),
        provider_name="atspi",
        stale=stale,
    )


def _selection_text(node: _AtspiNode) -> str | None:
    span = node.selection_range()
    if span is None or span[1] <= span[0] or span[0] < 0:
        return None
    return node.text_range(span[0], span[1])


def _preceding_text(node: _AtspiNode, max_preceding: int) -> str | None:
    caret = node.caret_offset()
    count = node.character_count()
    if caret is None or count is None or caret < 0:
        return None
    end = min(caret, count)
    start = max(0, end - max(0, max_preceding))
    return node.text_range(start, end)


class AtspiProvider:
    """Focused-field context over the freedesktop accessibility bus."""

    name = "atspi"

    def __init__(self, atspi_module=None):
        self._module = atspi_module  # injected fake wins (tests)

    def _resolve(self):
        if self._module is not None:
            return self._module, None
        return _try_import()

    def available(self) -> bool:
        module, _error = self._resolve()
        return module is not None

    def read_focus_context(self, max_preceding: int = 120) -> FocusContext:
        module, _error = self._resolve()
        if module is None:
            return missing_context(self.name)
        try:
            return read_focus(module, max_preceding)
        except Exception:  # noqa: BLE001 - one bad read is one bad read
            return missing_context(self.name)
