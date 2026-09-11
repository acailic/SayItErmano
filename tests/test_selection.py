from __future__ import annotations

from types import SimpleNamespace

import pytest

from fluidvoice import selection
from fluidvoice.selection import (
    MAX_HOLD_BYTES,
    SelectionHold,
    SelectionUnavailable,
    _is_text_target,
    _new_reader,
)


class TestPureHelpers:
    def test_is_text_target(self):
        assert _is_text_target("UTF8_STRING")
        assert _is_text_target("text/plain")
        assert _is_text_target("text/plain;charset=utf-8")
        assert _is_text_target("STRING")
        assert not _is_text_target("TARGETS")
        assert not _is_text_target("image/png")
        assert not _is_text_target(0)  # atom ids are not names

    def test_new_reader_finds_first_new_window(self):
        events = [(1.0, 0xAAA, "UTF8_STRING"), (1.5, 0xBBB, "UTF8_STRING"),
                  (2.0, 0xCCC, "TARGETS")]
        assert _new_reader(events, since=1.2, exclude=[0xAAA]) == 0xBBB

    def test_new_reader_respects_since(self):
        # a read before `since` (e.g. during quiesce) never counts
        events = [(1.0, 0xBBB, "UTF8_STRING")]
        assert _new_reader(events, since=1.2, exclude=[]) is None

    def test_new_reader_excludes_all_known(self):
        events = [(1.0, 0xAAA, "a"), (1.1, 0xBBB, "b")]
        assert _new_reader(events, since=0.5, exclude=[0xAAA, 0xBBB]) is None

    def test_new_reader_follows_list_order(self):
        # events are appended chronologically by the drain, so list order
        # IS read order: the first non-excluded entry wins
        events = [(1.0, 0xAAA, "a"), (1.5, 0xBBB, "b")]
        assert _new_reader(events, since=0.0, exclude=[]) == 0xAAA

    def test_new_reader_ignores_zero_requestor(self):
        events = [(1.0, 0, "x")]
        assert _new_reader(events, since=0.0, exclude=()) is None


# ---------------------------------------------------------------------------
# Fake X plumbing (hermetic: no X connection, no subprocess)
# ---------------------------------------------------------------------------

class FakeWindow:
    def __init__(self, disp, wid):
        self.id = wid
        self.disp = disp
        self.props = []
        self.destroyed = False

    def set_selection_owner(self, sel, time, onerror=None):
        if not self.disp.refuse:
            self.disp.owner = self

    def change_property(self, prop, ptype, fmt, data, mode=0, onerror=None):
        self.props.append((prop, ptype, fmt, data))

    def destroy(self, onerror=None):
        self.destroyed = True


class FakeDisplay:
    """Scripted Display: answers intern_atom, serves the queued events,
    records send_event / SetSelectionOwner / close."""

    _next_wid = [0x1000]

    def __init__(self, events=(), refuse_ownership=False):
        self.events = list(events)
        self.sent = []
        self.requests = []
        self.closed = False
        self.flush_count = 0
        self.owner = None
        self.refuse = refuse_ownership
        self._atoms: dict[str, int] = {}
        self.display = self  # raw-protocol handle (see release())
        self.win = None
        self.root = SimpleNamespace(create_window=self._create_window)
        self.screen = lambda: SimpleNamespace(root=self.root)

    def _create_window(self, *args, **kwargs):
        FakeDisplay._next_wid[0] += 1
        self.win = FakeWindow(self, FakeDisplay._next_wid[0])
        return self.win

    def intern_atom(self, name, only_if_exists=False):
        if name not in self._atoms:
            self._atoms[name] = 1000 + len(self._atoms)
        return self._atoms[name]

    def atom(self, name):
        return self._atoms.get(name)

    def flush(self):
        self.flush_count += 1

    def close(self):
        self.closed = True

    def get_selection_owner(self, selection):
        return self.owner if self.owner is not None else 0

    def pending_events(self):
        return len(self.events)

    def next_event(self):
        return self.events.pop(0)

    def send_event(self, destination, event, **kw):
        self.sent.append((destination, event))

    def send_request(self, req, need_send=True):
        self.requests.append(req)


def make_event(disp, etype, requestor_wid=0x999, target="UTF8_STRING",
               prop="FV_PROP"):
    """A SelectionRequest/SelectionClear with resolved atom ids."""
    target_atom = (target if isinstance(target, int)
                   else disp.atom(target) or disp.intern_atom(target))
    prop_atom = prop if isinstance(prop, int) else disp.intern_atom(prop)
    sel = disp.atom("CLIPBOARD")
    ev = SimpleNamespace(type=etype, target=target_atom,
                         property=prop_atom, selection=sel, time=12345)
    if etype != selection.X.SelectionClear:
        ev.requestor = FakeWindow(disp, requestor_wid)
    return ev


def make_hold(monkeypatch, events=(), refuse=False, data=b"dictation text",
              hygiene=(("application/x-copyq-hidden", b"1"),)):
    disp = FakeDisplay(events, refuse_ownership=refuse)
    monkeypatch.setattr(selection, "display",
                        SimpleNamespace(Display=lambda: disp))
    hold = SelectionHold(data, hygiene)
    return hold, disp


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    monkeypatch.setattr(selection.time, "sleep", lambda s: None)


class TestOwnership:
    def test_takes_ownership(self, monkeypatch):
        hold, disp = make_hold(monkeypatch)
        assert disp.owner is disp.win
        assert disp.owner.id == hold._win.id

    def test_refused_ownership_raises_and_closes(self, monkeypatch):
        disp = FakeDisplay(refuse_ownership=True)
        monkeypatch.setattr(selection, "display",
                            SimpleNamespace(Display=lambda: disp))
        with pytest.raises(SelectionUnavailable):
            SelectionHold(b"data")
        # the display was closed (no leaked X connection)
        assert disp.closed

    def test_oversized_payload_refused(self, monkeypatch):
        monkeypatch.setattr(selection, "display", SimpleNamespace(Display=lambda: FakeDisplay()))
        with pytest.raises(SelectionUnavailable):
            SelectionHold(b"x" * (MAX_HOLD_BYTES + 1))

    def test_no_xlib_raises(self, monkeypatch):
        monkeypatch.setattr(selection, "_XLIB_OK", False)
        with pytest.raises(SelectionUnavailable):
            SelectionHold(b"data")

    def test_release_is_idempotent_and_closes(self, monkeypatch):
        hold, disp = make_hold(monkeypatch)
        hold.release()
        assert disp.closed
        # owner=0 disown request was issued exactly once (wire: opcode,
        # pad, length, window, selection, time -> owner is bytes 4..8)
        import struct
        disowns = [r for r in disp.requests
                   if r.__class__.__name__ == "SetSelectionOwner"]
        assert len(disowns) == 1
        assert struct.unpack("<I", disowns[0]._binary[4:8])[0] == 0
        hold.release()
        assert len([r for r in disp.requests
                    if r.__class__.__name__ == "SetSelectionOwner"]) == 1


class TestServingRequests:
    def test_targets_includes_text_and_hygiene_atoms(self, monkeypatch):
        hold, disp = make_hold(monkeypatch)
        ev = make_event(disp, selection.X.SelectionRequest, target="TARGETS")
        disp.events.append(ev)
        hold.quiesce(0)
        prop, ptype, fmt, atoms = ev.requestor.props[0]
        assert ptype == selection.XA_ATOM and fmt == 32
        for name in ("UTF8_STRING", "text/plain", "STRING"):
            assert disp.atom(name) in atoms
        assert disp.atom("application/x-copyq-hidden") in atoms
        assert disp.atom("TIMESTAMP") in atoms

    def test_text_target_serves_the_payload(self, monkeypatch):
        hold, disp = make_hold(monkeypatch, data=b"hello dictation")
        ev = make_event(disp, selection.X.SelectionRequest,
                        target="UTF8_STRING")
        disp.events.append(ev)
        hold.quiesce(0)
        prop, ptype, fmt, data = ev.requestor.props[0]
        assert (ptype, fmt, data) == (disp.atom("UTF8_STRING"), 8,
                                      b"hello dictation")
        # and the SelectionNotify confirmed the property (non-zero)
        notify = disp.sent[-1][1]
        assert disp.sent[-1][0] is ev.requestor
        assert notify.property != 0

    def test_hygiene_marker_served(self, monkeypatch):
        hold, disp = make_hold(monkeypatch)
        ev = make_event(disp, selection.X.SelectionRequest,
                        target="application/x-copyq-hidden")
        disp.events.append(ev)
        hold.quiesce(0)
        prop, ptype, fmt, data = ev.requestor.props[0]
        assert data == b"1"

    def test_unknown_target_refused(self, monkeypatch):
        hold, disp = make_hold(monkeypatch)
        ev = make_event(disp, selection.X.SelectionRequest,
                        target="image/bmp")
        disp.events.append(ev)
        hold.quiesce(0)
        assert ev.requestor.props == []  # nothing written
        notify = disp.sent[-1][1]
        assert notify.property == 0  # polite refusal

    def test_timestamp_target_served(self, monkeypatch):
        hold, disp = make_hold(monkeypatch)
        ev = make_event(disp, selection.X.SelectionRequest,
                        target="TIMESTAMP")
        disp.events.append(ev)
        hold.quiesce(0)
        prop, ptype, fmt, data = ev.requestor.props[0]
        assert ptype == selection.XA_INTEGER and fmt == 32
        assert isinstance(data[0], int)


class TestEventRecording:
    def test_quiesce_collects_requestors(self, monkeypatch):
        hold, disp = make_hold(monkeypatch)
        a = make_event(disp, selection.X.SelectionRequest, requestor_wid=0xAAA)
        b = make_event(disp, selection.X.SelectionRequest, requestor_wid=0xBBB)
        disp.events += [a, b]
        known = hold.quiesce(0)
        assert known == {0xAAA, 0xBBB}
        assert len(hold.events) == 2

    def test_selection_clear_sets_lost_ownership(self, monkeypatch):
        hold, disp = make_hold(monkeypatch)
        disp.events.append(make_event(disp, selection.X.SelectionClear))
        hold.quiesce(0)
        assert hold.lost_ownership is True

    def test_wait_read_returns_new_window_only(self, monkeypatch):
        hold, disp = make_hold(monkeypatch)
        # 0xAAA read during quiesce
        disp.events.append(make_event(disp, selection.X.SelectionRequest,
                                      requestor_wid=0xAAA))
        known = hold.quiesce(0)
        # the paste target reads afterwards
        disp.events.append(make_event(disp, selection.X.SelectionRequest,
                                      requestor_wid=0xCCC))
        assert hold.wait_read(0.01, exclude_windows=known) == 0xCCC

    def test_wait_read_times_out(self, monkeypatch):
        hold, disp = make_hold(monkeypatch)
        assert hold.wait_read(0, exclude_windows=set()) is None

    def test_wait_read_returns_none_when_lost(self, monkeypatch):
        hold, disp = make_hold(monkeypatch)
        disp.events.append(make_event(disp, selection.X.SelectionClear))
        assert hold.wait_read(0.01, exclude_windows=set()) is None

    def test_wait_read_after_release_is_none(self, monkeypatch):
        hold, disp = make_hold(monkeypatch)
        hold.release()
        assert hold.wait_read(1, exclude_windows=()) is None

    def test_events_recorded_with_monotonic_times(self, monkeypatch):
        hold, disp = make_hold(monkeypatch)
        before = selection.time.monotonic()
        disp.events.append(make_event(disp, selection.X.SelectionRequest,
                                      requestor_wid=0xAAA))
        hold.quiesce(0)
        at, requestor, _target = hold.events[0]
        assert at >= before and requestor == 0xAAA
