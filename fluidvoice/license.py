"""Offline license-key verification (ADR-0005, issue #16).

The paid SKU sells convenience and support, not gated software (GPL-3.0:
recipients may redistribute). The key's job is to record an entitlement,
not to stop determined rebuilds — so verification is fully offline and
never gates the core dictation loop.

Key format (compact JWS-like, no dependency on a JWT library):

    key := b64url(payload_json) "." b64url(signature_64_bytes)

    payload := {
        "product":   "sayitermano",          # must match PRODUCT_ID
        "email":     buyer email (support identity),
        "purchased": "YYYY-MM-DD" purchase date,
        "v":         covered major version ("1") or "forever"
    }

The signature is Ed25519 (RFC 8032) over the raw payload bytes, made by
the PRIVATE keygen tool that stays out of this repository. The matching
public keys are embedded below; key rotation appends, never replaces
(key_id records which one verified).

Ed25519 verification is implemented inline from the RFC reference so the
runtime carries zero new dependencies — correctness is pinned by the
RFC 8032 test vectors in tests/test_license.py (the tests also carry a
reference signer for round-trip coverage; it never ships).
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
from dataclasses import dataclass

from . import paths

PRODUCT_ID = "sayitermano"
FOREVER = "forever"

# Embedded Ed25519 public keys (hex). Rotation appends, never replaces;
# key_id records which one verified. Generated 2026-11-21 by the private
# keygen (issue #16) — the matching secret never enters this repository.
PUBLIC_KEYS: tuple[bytes, ...] = (
    bytes.fromhex("6152501d39a6bb35608ace575a23c336aac6ec9fa704464ca3f87c2a341b8721"),
)

# Clock-skew tolerance for the purchase-date sanity check.
_DATE_TOLERANCE = dt.timedelta(days=1)


class LicenseError(ValueError):
    """A key is malformed, expired-for-version, or fails verification."""


@dataclass(frozen=True)
class LicenseInfo:
    email: str
    purchased: dt.date
    max_version: str  # FOREVER or a major-version digit string
    key_id: int       # index into PUBLIC_KEYS that verified


# ---------------------------------------------------------------------------
# Ed25519 verify (RFC 8032 §5.1.7) — reference implementation, verify-only.
# ---------------------------------------------------------------------------

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
        raise ValueError("not a square")  # pragma: no cover - decode path guards
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


def _point_decompress(s: bytes) -> tuple | None:
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    if y >= _p:
        return None
    try:
        x = _xrecover(y)
    except ValueError:
        return None  # y is not on the curve (e.g. a corrupted signature)
    if x == 0 and sign:
        return None
    if x & 1 != sign:
        x = _p - x
    return (x, y, 1, x * y % _p)


def _point_compress(P: tuple) -> bytes:
    x, y, z, _t = P
    zi = pow(z, _p - 2, _p)
    x = x * zi % _p
    y = y * zi % _p
    return ((y | ((x & 1) << 255))).to_bytes(32, "little")


def _ed25519_verify(pk: bytes, msg: bytes, sig: bytes) -> bool:
    """RFC 8032 §5.1.7 (non-cofactored equation; the RFC's primary form)."""
    if len(pk) != 32 or len(sig) != 64:
        return False
    A = _point_decompress(pk)
    if A is None:
        return False
    R_enc = sig[:32]
    R = _point_decompress(R_enc)
    if R is None:
        return False
    S = int.from_bytes(sig[32:], "little")
    if S >= _l:
        return False
    k = int.from_bytes(hashlib.sha512(R_enc + pk + msg).digest(), "little")
    lhs = _scalar_mult(S, _B)
    rhs = _edwards_add(R, _scalar_mult(k, A))
    return _point_compress(lhs) == _point_compress(rhs)


# ---------------------------------------------------------------------------
# Key parsing / storage
# ---------------------------------------------------------------------------

def _b64url_decode(part: str) -> bytes:
    if not part or "=" in part:
        raise LicenseError("bad encoding")
    try:
        return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
    except (ValueError, TypeError) as e:
        raise LicenseError("bad encoding") from e


def parse_and_verify(key: str, *, keys: tuple[bytes, ...] | None = None,
                     now: dt.date | None = None) -> LicenseInfo:
    """Validate one license key offline. Raises LicenseError with a reason."""
    keys = PUBLIC_KEYS if keys is None else keys
    if not keys:
        raise LicenseError("no license keys configured in this build")
    key = key.strip()
    payload_part, sep, sig_part = key.partition(".")
    if not sep or not payload_part or not sig_part:
        raise LicenseError("not a key (expected payload.signature)")
    payload_bytes = _b64url_decode(payload_part)
    sig = _b64url_decode(sig_part)
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise LicenseError("payload is not JSON") from e
    if not isinstance(payload, dict):
        raise LicenseError("payload is not an object")
    if payload.get("product") != PRODUCT_ID:
        raise LicenseError(f"key is for another product: {payload.get('product')!r}")
    email = payload.get("email")
    if not isinstance(email, str) or not email:
        raise LicenseError("payload has no email")
    purchased_raw = payload.get("purchased")
    try:
        purchased = dt.date.fromisoformat(str(purchased_raw))
    except (TypeError, ValueError) as e:
        raise LicenseError("payload has no valid purchase date") from e
    now = now or dt.date.today()
    if purchased > now + _DATE_TOLERANCE:
        raise LicenseError("purchase date is in the future")
    max_version = payload.get("v", FOREVER)
    if max_version != FOREVER and not (isinstance(max_version, str) and max_version.isdigit()):
        raise LicenseError(f"bad version coverage: {max_version!r}")

    for key_id, pk in enumerate(keys):
        if _ed25519_verify(pk, payload_bytes, sig):
            return LicenseInfo(email=email, purchased=purchased,
                               max_version=max_version, key_id=key_id)
    raise LicenseError("signature check failed")


def covers_version(info: LicenseInfo, current_version: str) -> bool:
    """True when the key's covered major includes `current_version`."""
    if info.max_version == FOREVER:
        return True
    try:
        major = int(current_version.split(".")[0])
    except ValueError:
        return False
    return major <= int(info.max_version)


def import_key(key: str, **kwargs) -> LicenseInfo:
    """Verify then persist the key (mode 0600, beside the config)."""
    info = parse_and_verify(key, **kwargs)
    target = paths.license_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(key.strip() + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(target)
    return info


def status() -> LicenseInfo | None:
    """The imported key's status, or None when nothing is stored.

    A stored key that no longer verifies (key-format change, corrupt
    file) is reported as None, not an error — the app stays usable.
    """
    target = paths.license_file()
    if not target.exists():
        return None
    try:
        return parse_and_verify(target.read_text(encoding="utf-8"))
    except LicenseError:
        return None
