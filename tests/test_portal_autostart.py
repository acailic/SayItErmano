"""Background-portal autostart (issue #11, ADR-0006): fake D-Bus bus
objects — no daemon, no portal backend (dbus-next itself is a dev dep,
the same version the flatpak vendors).

Covers: the RequestBackground wire format (member, signature, the
autostart/reason/commandline options, our autostart argv), response
handling (enabled / refused), and the ask-once state machine in
portal-state.json — including the merge invariant shared with the EIS
restore token.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from fluidvoice import portal_autostart as pa
from fluidvoice.portal_autostart import (
    AUTOSTART_COMMANDLINE,
    DENIED,
    ENABLED,
    STATE_KEY,
    UNAVAILABLE,
    ask_autostart_once,
)


class FakeReply:
    def __init__(self, body, message_type=2):
        self.body = body
        self.message_type = message_type  # 3 = ERROR


class FakeBus:
    """Records the one call; plays back a Response signal on demand."""

    def __init__(self, response_code=0, response_results=None):
        self.messages = []
        self.handlers = []
        self.reply_path = ""
        self._response_code = response_code
        self._response_results = response_results or {}

    async def call(self, msg):
        self.messages.append(msg)
        self.reply_path = (f"/org/freedesktop/portal/desktop/request/"
                           f"{len(self.messages)}")
        return FakeReply([self.reply_path])

    def deliver_response(self):
        signal = SimpleNamespace(
            path=self.reply_path,
            interface="org.freedesktop.portal.Request",
            member="Response",
            body=[self._response_code, dict(self._response_results)])
        for handler in list(self.handlers):
            handler(signal)

    def add_message_handler(self, handler):
        self.handlers.append(handler)

    def remove_message_handler(self, handler):
        self.handlers.remove(handler)

    def disconnect(self):
        pass


@pytest.fixture
def fake_bus(monkeypatch):
    """Route BackgroundPortal.connect() at a FakeBus."""
    holder = SimpleNamespace(bus=None)

    def _make(response_code=0, response_results=None):
        holder.bus = FakeBus(response_code, response_results)

        async def _connect():
            return holder.bus

        monkeypatch.setattr("dbus_next.aio.MessageBus",
                            lambda: SimpleNamespace(connect=_connect))
        return holder.bus

    return _make


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestRequestAutostartWire:
    def _request(self, bus) -> bool:
        portal = pa.BackgroundPortal()

        async def scenario() -> bool:
            await portal.connect()
            task = asyncio.ensure_future(
                portal.request_autostart(True, "why", ["a", "--b"]))
            # wait for the call AND the response subscription —
            # delivering before the handler is registered loses the
            # signal and hangs the request
            while not bus.messages or not bus.handlers:
                await asyncio.sleep(0)
            bus.deliver_response()
            return await task

        try:
            return _run(scenario())
        finally:
            _run(portal.close())

    def test_options_and_signature(self, fake_bus):
        bus = fake_bus(response_results={"autostart": True})
        assert self._request(bus) is True
        msg = bus.messages[0]
        assert (msg.destination, msg.interface, msg.member) == (
            "org.freedesktop.portal.Desktop",
            "org.freedesktop.portal.Background", "RequestBackground")
        assert msg.signature == "sa{sv}"
        parent_window, options = msg.body
        assert parent_window == ""
        assert options["autostart"].value is True
        assert options["reason"].value == "why"
        assert options["commandline"].value == ["a", "--b"]
        assert options["handle_token"].value  # request routing

    def test_commandline_omitted_when_disabling(self, fake_bus):
        bus = fake_bus()
        portal = pa.BackgroundPortal()

        async def scenario() -> bool:
            await portal.connect()
            task = asyncio.ensure_future(
                portal.request_autostart(False, "off", []))
            while not bus.messages or not bus.handlers:
                await asyncio.sleep(0)
            bus.deliver_response()
            return await task

        try:
            assert _run(scenario()) is False
        finally:
            _run(portal.close())
        options = bus.messages[0].body[1]
        assert "commandline" not in options
        assert options["autostart"].value is False

    def test_refused_consent_is_permission_error(self, fake_bus):
        bus = fake_bus(response_code=1)
        with pytest.raises(PermissionError):
            self._request(bus)

    def test_result_false_means_not_enabled(self, fake_bus):
        bus = fake_bus(response_results={"autostart": False})
        assert self._request(bus) is False

    def test_set_autostart_round_trip(self, fake_bus, monkeypatch):
        fake_bus(response_results={"autostart": True})
        seen = {}

        async def fake_round(portal_self, enable, reason, commandline):
            seen["argv"] = list(commandline)
            return True

        monkeypatch.setattr(pa.BackgroundPortal, "request_autostart",
                            fake_round)
        assert pa.set_autostart(True) is True
        assert seen["argv"] == AUTOSTART_COMMANDLINE


class TestAskOnceStateMachine:
    def _state_file(self, tmp_path):
        return tmp_path / "portal-state.json"

    @pytest.fixture
    def sandboxed(self, monkeypatch):
        monkeypatch.setattr("fluidvoice.gtkui.daemon_owner.in_flatpak",
                            lambda: True)

    def test_host_skips_entirely(self, monkeypatch, tmp_path):
        monkeypatch.setattr("fluidvoice.gtkui.daemon_owner.in_flatpak",
                            lambda: False)
        assert ask_autostart_once(self._state_file(tmp_path)) == "skipped"

    def test_enabled_outcome_is_persisted(self, monkeypatch, tmp_path,
                                          sandboxed):
        monkeypatch.setattr(pa, "set_autostart", lambda *a, **k: True)
        sf = self._state_file(tmp_path)
        assert ask_autostart_once(sf, log=lambda *a: None) == ENABLED
        assert json.loads(sf.read_text())[STATE_KEY] == ENABLED

    def test_denial_persists_and_never_reasks(self, monkeypatch, tmp_path,
                                              sandboxed):
        calls = []

        def deny(*a, **k):
            calls.append(1)
            raise PermissionError("refused (code 1)")

        monkeypatch.setattr(pa, "set_autostart", deny)
        sf = self._state_file(tmp_path)
        assert ask_autostart_once(sf, log=lambda *a: None) == DENIED
        assert ask_autostart_once(sf, log=lambda *a: None) == DENIED
        assert calls == [1]  # portal hit exactly once
        assert json.loads(sf.read_text())[STATE_KEY] == DENIED

    def test_no_backend_is_unavailable_not_denied(self, monkeypatch,
                                                  tmp_path, sandboxed):
        def boom(*a, **k):
            raise RuntimeError("no Background backend")

        monkeypatch.setattr(pa, "set_autostart", boom)
        sf = self._state_file(tmp_path)
        assert ask_autostart_once(sf, log=lambda *a: None) == UNAVAILABLE

    def test_prior_state_short_circuits(self, monkeypatch, tmp_path,
                                        sandboxed):
        monkeypatch.setattr(pa, "set_autostart",
                            lambda *a, **k: pytest.fail("must not call"))
        sf = self._state_file(tmp_path)
        sf.write_text(json.dumps({STATE_KEY: DENIED}))
        assert ask_autostart_once(sf) == DENIED

    def test_state_merge_preserves_sibling_keys(self, tmp_path):
        sf = self._state_file(tmp_path)
        sf.write_text(json.dumps({"remotedesktop_restore_token": "tok"}))
        pa._update_state(sf, **{STATE_KEY: ENABLED})
        data = json.loads(sf.read_text())
        assert data == {"remotedesktop_restore_token": "tok",
                        STATE_KEY: ENABLED}


class TestAutostartCommandline:
    def test_command_owns_the_daemon_headlessly(self):
        # the autostart entry must launch the --background owner, not a
        # windowed app or a bare daemon (ADR-0006 lifecycle)
        assert AUTOSTART_COMMANDLINE[:2] == ["sayit-ermano", "app"]
        assert "--background" in AUTOSTART_COMMANDLINE
