"""Unit-tier network guard (quality plan Q1).

Acceptance behind this: with the canonical unit/contract gate, a network
attempt in a unit test must FAIL CLEARLY, while explicitly allowed local
fake HTTP/STT servers (loopback) keep working.

Activation is environmental, not positional: the guard installs itself
only when FLUIDVOICE_TEST_TIER == "unit" — the variable set by exactly one
place, scripts/run_test_tier.sh (the canonical tier runner). Normal dev
runs (`just test`, bare pytest) and the gtk/integration tiers keep
unrestricted sockets.

Mechanics: patch socket.create_connection plus socket.socket.connect and
connect_ex. Every outbound TCP client path in the stdlib and the usual
HTTP libraries funnels through those (urllib, http.client, urllib3,
requests). The address is inspected BEFORE any connection is attempted,
so tripping the guard needs no DNS and no packets. Loopback (127/8,
::1, localhost, unix sockets, empty/None addresses) is always allowed —
the suite's fake servers live there.
"""
from __future__ import annotations

import ipaddress
import os
import socket

TIER_ENV = "FLUIDVOICE_TEST_TIER"
UNIT_TIER = "unit"

_ORIGINALS: dict = {}
_INSTALLED = False


def _addr_ok(address) -> bool:
    """True when `address` is safe for the unit tier (loopback / unix /
    unspecified). Mirrors what the suite's fake servers need."""
    if address is None:
        return True                      # connect(None): no-op address
    if isinstance(address, (str, bytes)):
        return True                      # AF_UNIX socket path
    try:
        host = address[0]
    except (TypeError, IndexError):
        return True                      # not an (host, port) pair — let it fly
    if not host or host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False                     # remote hostname — blocked


def _denied(address) -> AssertionError:
    return AssertionError(
        f"unit-tier network guard: outbound socket to {address!r} — unit "
        "tests must not use the network (loopback fakes are fine). If this "
        "test genuinely needs a download/live endpoint, move it to "
        "tests/integration and mark it network/model (quality plan Q1).")


def _install(force: bool = False) -> bool:
    """Patch the connect entry points. No-op unless the tier env says
    `unit` (or force=True, used by the guard's own tests)."""
    global _INSTALLED
    if _INSTALLED:
        return True
    if not force and os.environ.get(TIER_ENV) != UNIT_TIER:
        return False

    orig_create = socket.create_connection
    orig_connect = socket.socket.connect
    orig_connect_ex = socket.socket.connect_ex

    def create_connection(address, *args, **kwargs):
        if not _addr_ok(address):
            raise _denied(address)
        return orig_create(address, *args, **kwargs)

    def connect(self, address):
        if not _addr_ok(address):
            raise _denied(address)
        return orig_connect(self, address)

    def connect_ex(self, address):
        if not _addr_ok(address):
            raise _denied(address)
        return orig_connect_ex(self, address)

    socket.create_connection = create_connection
    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    _ORIGINALS.update(
        create_connection=orig_create,
        connect=orig_connect,
        connect_ex=orig_connect_ex,
    )
    _INSTALLED = True
    return True


def uninstall() -> bool:
    """Restore the pristine socket API (guard self-tests use this)."""
    global _INSTALLED
    if not _INSTALLED:
        return False
    socket.create_connection = _ORIGINALS["create_connection"]
    socket.socket.connect = _ORIGINALS["connect"]
    socket.socket.connect_ex = _ORIGINALS["connect_ex"]
    _ORIGINALS.clear()
    _INSTALLED = False
    return True


def installed() -> bool:
    return _INSTALLED


# Plugin wiring: loading this module via PYTEST_PLUGINS (the guard's own
# meta-test does) installs the guard at configure time, env-gated. When
# tests/conftest.py imports it directly the hook is inert — conftest calls
# _install() itself at import time, before any test module loads.
def pytest_configure(config):  # noqa: ARG001 - pytest hook signature
    _install()
