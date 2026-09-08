"""CLIPBOARD selection ownership (ICCCM) - the paste-verification signal.

While we own the CLIPBOARD selection, every read by any client (a clipboard
manager or the paste target) arrives as a SelectionRequest event naming the
requestor's window. insert_paste uses that to

- let the eager readers (clipboard managers, the mutter/gnome-shell proxy)
  reveal themselves during a short quiesce window after ownership is taken,
  and then
- verify the target app actually read the dictation after the paste
  keystroke - a read from a window NOT seen during the quiesce is the
  "the paste landed" signal - before the previous clipboard is restored.

Owning the selection ourselves (instead of the legacy `xclip` flash) also
lets us advertise clipboard-manager hygiene marker targets alongside the
text so managers like CopyQ/Klipper suppress the flashed dictation from
their history (live-verified against CopyQ 7.1.0 - see docs/STATUS.md).

python-xlib is already a hard dependency of the X11 path; on any import
failure or X error SelectionUnavailable is raised and insertion falls back
to the legacy xclip flash. No subprocess is ever spawned while the hold is
active (the X socket must stay drained or a blocked `xclip -o` deadlocks).
"""
from __future__ import annotations

import time
from typing import Sequence

try:  # pragma: no cover - Xlib availability is probed, not unit-tested
    from Xlib import X, display
    from Xlib.protocol import event as xevent
    from Xlib.protocol import request as xrequest
    from Xlib.Xatom import ATOM as XA_ATOM
    from Xlib.Xatom import INTEGER as XA_INTEGER

    _XLIB_OK = True
except Exception:  # noqa: BLE001 - degrade, never crash the import
    X = None  # type: ignore[assignment]
    display = None  # type: ignore[assignment]
    xevent = None  # type: ignore[assignment]
    xrequest = None  # type: ignore[assignment]
    XA_ATOM = XA_INTEGER = 0  # type: ignore[assignment]
    _XLIB_OK = False

# The X server's max request size observed on this machine is 16 777 212
# bytes (xdpyinfo) - single-shot property transfers suffice for dictations,
# no INCR protocol needed. Larger holds refuse (caller falls back to xclip).
MAX_HOLD_BYTES = 16_000_000

# Text targets served alongside the hygiene markers (xclip's set).
TEXT_TARGETS = ("UTF8_STRING", "text/plain;charset=utf-8",
                "text/plain", "STRING")
_TEXT_TARGET_NAMES = frozenset(TEXT_TARGETS)

# Event-loop granularity during quiesce/read waits.
POLL_INTERVAL_S = 0.025


class SelectionUnavailable(RuntimeError):
    """CLIPBOARD ownership could not be taken (no Xlib, no display, X
    error, oversized payload) - callers fall back to the legacy path."""


# ---------------------------------------------------------------------------
# Pure helpers (hermetically tested)
# ---------------------------------------------------------------------------

def _is_text_target(target: object) -> bool:
    """True for the text atom names this module serves."""
    return target in _TEXT_TARGET_NAMES


def _new_reader(events: Sequence[tuple[float, int, object]], since: float,
                exclude: Sequence[int] | None = None) -> int | None:
    """First requestor window id not in `exclude` whose read was recorded
    at or after `since` - the paste-verify signal. Excluded (already
    known) windows were seen during the quiesce."""
    excluded = set(exclude or ())
    for at, requestor, _target in events:
        if at >= since and requestor not in excluded and requestor != 0:
            return requestor
    return None


# ---------------------------------------------------------------------------
# The hold
# ---------------------------------------------------------------------------

class SelectionHold:
    """Owns the CLIPBOARD selection and serves it from this process for
    the duration of a paste. Every read is answered immediately and
    recorded as ``(time.monotonic(), requestor_window_id, target_atom)``.
    """

    def __init__(self, data: bytes,
                 hygiene: Sequence[tuple[str, bytes]] = ()):
        if not _XLIB_OK:
            raise SelectionUnavailable("python-xlib unavailable")
        if not isinstance(data, (bytes, bytearray)):
            data = str(data).encode()
        if len(data) > MAX_HOLD_BYTES:
            raise SelectionUnavailable(
                f"clipboard payload too large for a selection hold "
                f"({len(data)} > {MAX_HOLD_BYTES} bytes)")
        self.data = bytes(data)
        self.hygiene = tuple(hygiene)
        self.events: list[tuple[float, int, object]] = []
        self._lost = False
        self._closed = False
        self._owner_window = None
        self._take_ownership()

    # -- lifecycle --------------------------------------------------------

    def _take_ownership(self) -> None:
        disp = None
        try:
            disp = display.Display()
            clipboard = disp.intern_atom("CLIPBOARD")
            win = disp.screen().root.create_window(
                -1, -1, 1, 1, 0, X.CopyFromParent)
            win.set_selection_owner(clipboard, X.CurrentTime)
            owner = disp.get_selection_owner(clipboard)
            owner_id = owner if isinstance(owner, int) else owner.id
            disp.flush()
            if owner_id != win.id:
                raise SelectionUnavailable(
                    "CLIPBOARD ownership refused by another client")
        except SelectionUnavailable:
            if disp is not None:
                try:
                    disp.close()
                except Exception:
                    pass
            raise
        except Exception as e:  # X connection errors, Xlib errors
            if disp is not None:
                try:
                    disp.close()
                except Exception:
                    pass
            raise SelectionUnavailable(f"X error: {e}") from None
        now_ms = int(time.time() * 1000) & 0xFFFFFFFF
        self._disp = disp
        self._clipboard = clipboard
        self._win = win
        self._acquired_ms = now_ms
        self._text_atoms = {name: disp.intern_atom(name)
                            for name in TEXT_TARGETS}
        self._atom_to_name = {atom: name
                              for name, atom in self._text_atoms.items()}
        self._hygiene_atoms = {disp.intern_atom(name): marker
                               for name, marker in self.hygiene}
        self._targets_atom = disp.intern_atom("TARGETS")
        self._timestamp_atom = disp.intern_atom("TIMESTAMP")
        self._multiple_atom = disp.intern_atom("MULTIPLE")

    @property
    def lost_ownership(self) -> bool:
        """Another client took the CLIPBOARD while we held it
        (SelectionClear) - never clobber their content with a restore."""
        return self._lost

    def release(self) -> None:
        """Disown the selection and close the X connection (idempotent)."""
        if self._closed:
            return
        self._closed = True
        try:
            # owner=0 explicitly disowns; closing the connection would
            # destroy the owner window and drop the selection anyway.
            xrequest.SetSelectionOwner(display=self._disp.display,
                                       window=0,
                                       selection=self._clipboard, time=0)
            self._disp.flush()
        except Exception:
            pass
        try:
            self._disp.close()
        except Exception:
            pass

    # -- event loop ---------------------------------------------------------

    def _drain(self) -> None:
        """Answer every queued selection request; never blocks."""
        try:
            pending = self._disp.pending_events()
        except Exception:
            return  # display gone: release() is the only sane next step
        while pending:
            try:
                ev = self._disp.next_event()
            except Exception:
                return
            self._handle(ev)
            try:
                pending = self._disp.pending_events()
            except Exception:
                return

    def _handle(self, ev) -> None:
        if ev.type == X.SelectionRequest:
            requestor_id = ev.requestor if isinstance(ev.requestor, int) \
                else ev.requestor.id
            self.events.append((time.monotonic(), requestor_id, ev.target))
            self._answer(ev)
        elif ev.type == X.SelectionClear:
            self._lost = True
        # anything else (Expose, PropertyNotify...) is ignored

    def _answer(self, ev) -> None:
        """Serve one SelectionRequest per ICCCM and reply immediately."""
        try:
            prop = ev.property
            target = ev.target
            served = False
            if target == self._targets_atom:
                atoms = list(self._text_atoms.values())
                atoms.append(self._timestamp_atom)
                atoms.extend(self._hygiene_atoms)
                ev.requestor.change_property(prop, XA_ATOM, 32, atoms)
                served = True
            elif target == self._timestamp_atom:
                ev.requestor.change_property(
                    prop, XA_INTEGER, 32, [self._acquired_ms])
                served = True
            elif target in self._atom_to_name:
                ev.requestor.change_property(prop, target, 8, self.data)
                served = True
            elif target in self._hygiene_atoms:
                ev.requestor.change_property(
                    prop, target, 8, self._hygiene_atoms[target])
                served = True
            # MULTIPLE / anything unknown: politely refused (property=0)
            reply = xevent.SelectionNotify(
                time=ev.time,
                requestor=(ev.requestor if isinstance(ev.requestor, int)
                           else ev.requestor.id),
                selection=ev.selection,
                target=ev.target,
                property=(prop if served else X.NONE))
            self._disp.send_event(ev.requestor, reply)
            self._disp.flush()
        except Exception:
            pass  # a broken requestor must never kill the hold

    # -- public waits ---------------------------------------------------------

    def quiesce(self, seconds: float,
                interval: float = POLL_INTERVAL_S) -> set[int]:
        """Drain + answer selection requests for `seconds` so the eager
        readers (clipboard managers, the mutter proxy) reveal themselves.
        Returns every requestor window id seen so far (the paste-verify
        exclusion set)."""
        deadline = time.monotonic() + seconds
        while True:
            self._drain()
            if self._closed or self._lost or time.monotonic() >= deadline:
                break
            time.sleep(interval)
        return {requestor for _, requestor, _ in self.events}

    def wait_read(self, timeout: float, exclude_windows: Sequence[int] = (),
                  interval: float = POLL_INTERVAL_S) -> int | None:
        """Wait until a window NOT in exclude_windows reads the selection
        (the "the paste landed" signal). Returns that window id, or None
        on timeout / lost ownership / closed hold."""
        if self._closed:
            return None
        since = time.monotonic()
        deadline = since + timeout
        while True:
            self._drain()
            reader = _new_reader(self.events, since, exclude_windows)
            if reader is not None:
                return reader
            if self._lost or self._closed or time.monotonic() >= deadline:
                return None
            time.sleep(interval)
