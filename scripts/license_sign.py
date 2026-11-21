"""Ed25519 signing half of the offline license system (ADR-0005, #16).

fluidvoice/license.py deliberately ships VERIFY ONLY — the installed app
never carries signing code, so a redistributed package cannot be used as
an issuing oracle. This module is the matching signing half. It lives in
scripts/ (owner-side tooling imported by license_keygen.py and
license_webhook.py); wheels and the deb install only fluidvoice/*, so it
never ships to buyers either.

The curve arithmetic duplicates a few constants from the verify side on
purpose: keeping the halves independent means a regression in one cannot
silently weaken the other, and both stay pinned by the RFC 8032 vectors
in tests/test_license.py plus the round-trip tests that flow through
this module (tests/test_license.py, tests/test_license_webhook.py).

Stdlib only, same as the rest of the license system.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json

PRODUCT_ID = "sayitermano"  # mirrors fluidvoice.license.PRODUCT_ID
FOREVER = "forever"

_p = 2**255 - 19
_l = 2**252 + 27742317777372353535851937790883648493
_d = -121665 * pow(121666, _p - 2, _p) % _p
_I = pow(2, (_p - 1) // 4, _p)


def _xrecover(y: int) -> int:
    xx = (y * y - 1) * pow(_d * y * y + 1, _p - 2, _p) % _p
    x = pow(xx, (_p + 3) // 8, _p)
    if (x * x - xx) % _p != 0:
        x = x * _I % _p
    if (x * x - xx) % _p != 0:
        raise ValueError("not a square")  # pragma: no cover - off-curve input
    if x % 2 != 0:
        x = _p - x
    return x


_By = 4 * pow(5, _p - 2, _p) % _p
_Bx = _xrecover(_By)
_B = (_Bx % _p, _By % _p, 1, _Bx % _p * _By % _p)  # extended coords


def _edwards_add(P: tuple, Q: tuple) -> tuple:
    x1, y1, z1, t1 = P
    x2, y2, z2, t2 = Q
    a = (y1 - x1) * (y2 - x2) % _p
    b = (y1 + x1) * (y2 + x2) % _p
    c = t1 * 2 * _d * t2 % _p
    d2 = z1 * 2 * z2 % _p
    e, f, g, h = b - a, d2 - c, d2 + c, b + a
    return (e * f % _p, g * h % _p, f * g % _p, e * h % _p)


def _scalar_mult(s: int, P: tuple) -> tuple:
    Q = (0, 1, 1, 0)  # neutral element
    while s > 0:
        if s & 1:
            Q = _edwards_add(Q, P)
        P = _edwards_add(P, P)
        s >>= 1
    return Q


def _point_compress(P: tuple) -> bytes:
    x, y, z, _t = P
    zi = pow(z, _p - 2, _p)
    x = x * zi % _p
    y = y * zi % _p
    return ((y | ((x & 1) << 255))).to_bytes(32, "little")


def public_key(seed: bytes) -> bytes:
    """Derive the Ed25519 public key for a 32-byte seed (RFC 8032 §5.1.5)."""
    if len(seed) != 32:
        raise ValueError(f"seed must be 32 bytes, got {len(seed)}")
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= (1 << 254)
    return _point_compress(_scalar_mult(a, _B))


def sign(seed: bytes, message: bytes) -> tuple[bytes, bytes]:
    """RFC 8032 §5.1.6. Returns (signature, public_key) — deterministic."""
    if len(seed) != 32:
        raise ValueError(f"seed must be 32 bytes, got {len(seed)}")
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= (1 << 254)
    prefix = h[32:]
    A = _point_compress(_scalar_mult(a, _B))
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little") % _l
    R = _point_compress(_scalar_mult(r, _B))
    k = int.from_bytes(hashlib.sha512(R + A + message).digest(), "little") % _l
    s = (r + k * a) % _l
    return R + s.to_bytes(32, "little"), A


# ---------------------------------------------------------------------------
# License payload / key encoding (mirrors fluidvoice.license key format)
# ---------------------------------------------------------------------------

def build_payload(email: str, purchased: dt.date, coverage: str,
                  name: str | None = None,
                  product: str = PRODUCT_ID) -> dict:
    """The signed statement: what was bought, by whom, when, until which
    major version. Extra keys (name) are ignored by older verifiers —
    the payload only ever grows additively."""
    if coverage != FOREVER and not coverage.isdigit():
        raise ValueError(
            f"coverage must be '{FOREVER}' or a major-version digit, "
            f"got {coverage!r}")
    payload = {"product": product, "email": email}
    if name:
        payload["name"] = name
    payload["purchased"] = purchased.isoformat()
    payload["v"] = coverage
    return payload


def payload_bytes(payload: dict) -> bytes:
    """Canonical encoding that the signature covers byte-for-byte."""
    return json.dumps(payload, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def encode(payload: dict, seed: bytes) -> str:
    """Sign the payload and render it in the wire format
    b64url(payload) '.' b64url(signature) that `sayit-ermano license`
    imports."""
    raw = payload_bytes(payload)
    sig, _pk = sign(seed, raw)
    b64 = lambda data: base64.urlsafe_b64encode(data).rstrip(b"=").decode()
    return f"{b64(raw)}.{b64(sig)}"


def make_key(email: str, seed: bytes, purchased: dt.date, coverage: str,
             name: str | None = None) -> str:
    return encode(build_payload(email, purchased, coverage, name=name), seed)
