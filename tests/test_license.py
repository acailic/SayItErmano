"""Offline license keys: RFC 8032 vectors + round-trip + storage (issue #16).

The runtime module ships VERIFY only. These tests carry a reference
Ed25519 signer (RFC 8032 §5.1.6, same public-domain reference family)
solely to produce signatures — it never ships in fluidvoice/.

Correctness of both directions is pinned by the official RFC 8032 test
vectors 1-3: if the inline implementation regresses, those fail before
any round-trip could mask it.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import pytest

import scripts.license_keygen as keygen
from fluidvoice import license as lic

REPO = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Reference signer (tests only)
# ---------------------------------------------------------------------------

def _secret_expand(sk: bytes) -> tuple[int, bytes]:
    h = hashlib.sha512(sk).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= (1 << 254)
    return a, h[32:]


def _ref_sign(sk: bytes, msg: bytes) -> tuple[bytes, bytes]:
    """Returns (signature, public_key)."""
    a, prefix = _secret_expand(sk)
    A = lic._point_compress(lic._scalar_mult(a, lic._B))
    r = int.from_bytes(hashlib.sha512(prefix + msg).digest(), "little") % lic._l
    R = lic._point_compress(lic._scalar_mult(r, lic._B))
    h = int.from_bytes(hashlib.sha512(R + A + msg).digest(), "little") % lic._l
    s = (r + h * a) % lic._l
    return R + s.to_bytes(32, "little"), A


def _make_key(sk_hex: str, payload: dict) -> tuple[str, bytes]:
    import base64
    sk = bytes.fromhex(sk_hex)
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode()
    sig, pk = _ref_sign(sk, payload_bytes)
    b = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return f"{b(payload_bytes)}.{b(sig)}", pk


_VALID_PAYLOAD = {
    "product": "sayitermano",
    "email": "buyer@example.com",
    "purchased": "2026-11-21",
    "v": "1",
}
# Deterministic test secret (RFC 8032 test-1 secret doubles fine here).
_TEST_SK = "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"


# ---------------------------------------------------------------------------
# RFC 8032 official vectors — the correctness anchor (both directions:
# the verifier ships, the signer lives in scripts/license_keygen.py)
# ---------------------------------------------------------------------------

_RFC_VECTORS = [
    # RFC 8032 §7.1 TEST 1 (empty message)
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
     "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
     "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    # RFC 8032 §7.1 TEST 2 (one byte 0x72)
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
     "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
     "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    # RFC 8032 §7.1 TEST 3 (two bytes 0xaf82)
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
     "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac"
     "18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
]


@pytest.mark.parametrize("sk_hex,pk_hex,msg_hex,sig_hex", _RFC_VECTORS)
def test_rfc8032_vectors_verify(sk_hex, pk_hex, msg_hex, sig_hex):
    assert lic._ed25519_verify(bytes.fromhex(pk_hex), bytes.fromhex(msg_hex),
                               bytes.fromhex(sig_hex))


@pytest.mark.parametrize("sk_hex,pk_hex,msg_hex,sig_hex", _RFC_VECTORS)
def test_rfc8032_vectors_sign(sk_hex, pk_hex, msg_hex, sig_hex):
    # The owner-side signer must produce the official bytes bit-for-bit.
    sig, pk = keygen.sign(bytes.fromhex(sk_hex), bytes.fromhex(msg_hex))
    assert sig == bytes.fromhex(sig_hex)
    assert pk == bytes.fromhex(pk_hex)


@pytest.mark.parametrize("which", [0, 1, 32, 63])
def test_rfc8032_vector_tampering_fails(which):
    pk = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    sig = bytearray(bytes.fromhex(
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
        "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"))
    sig[which] ^= 0x01
    assert not lic._ed25519_verify(pk, b"", bytes(sig))


# ---------------------------------------------------------------------------
# Key round-trip and rejection (reference signer)
# ---------------------------------------------------------------------------

@pytest.fixture()
def one_key(monkeypatch):
    key, pk = _make_key(_TEST_SK, _VALID_PAYLOAD)
    monkeypatch.setattr(lic, "PUBLIC_KEYS", (pk,))
    return key, pk


def test_valid_key_verifies(one_key):
    key, _pk = one_key
    info = lic.parse_and_verify(key, now=dt.date(2026, 11, 21))
    assert info.email == "buyer@example.com"
    assert info.purchased == dt.date(2026, 11, 21)
    assert info.max_version == "1"
    assert info.key_id == 0


def test_verification_uses_every_configured_key(monkeypatch):
    key_a, pk_a = _make_key(_TEST_SK, _VALID_PAYLOAD)
    other_sk = "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb"
    _key_b, pk_b = _make_key(other_sk, _VALID_PAYLOAD)
    monkeypatch.setattr(lic, "PUBLIC_KEYS", (pk_b, pk_a))  # rotation: newest last
    info = lic.parse_and_verify(key_a)
    assert info.key_id == 1


def test_rejects_wrong_product(one_key):
    key, pk = one_key
    wrong, _ = _make_key(_TEST_SK, {**_VALID_PAYLOAD, "product": "other-app"})
    with pytest.raises(lic.LicenseError, match="another product"):
        lic.parse_and_verify(wrong, keys=(pk,))


def test_rejects_garbage(one_key):
    _key, pk = one_key
    for bad in ["", "nonsense", "aaaa.bbbb", "a.b.c", "=/+.=", "...."]:
        with pytest.raises(lic.LicenseError):
            lic.parse_and_verify(bad, keys=(pk,))


def test_rejects_future_purchase_date(one_key):
    key, pk = one_key
    future, _ = _make_key(_TEST_SK, {**_VALID_PAYLOAD, "purchased": "2030-01-01"})
    with pytest.raises(lic.LicenseError, match="future"):
        lic.parse_and_verify(future, keys=(pk,), now=dt.date(2026, 11, 21))
    # tolerance: yesterday and today both pass
    past, _ = _make_key(_TEST_SK, {**_VALID_PAYLOAD, "purchased": "2026-11-20"})
    assert lic.parse_and_verify(past, keys=(pk,), now=dt.date(2026, 11, 21))


def test_rejects_bad_version_coverage(one_key):
    _key, pk = one_key
    bad, _ = _make_key(_TEST_SK, {**_VALID_PAYLOAD, "v": "1.2.3"})
    with pytest.raises(lic.LicenseError, match="version coverage"):
        lic.parse_and_verify(bad, keys=(pk,))


def test_no_keys_configured_is_an_honest_error():
    # A build with no embedded keys (pre-launch state) reports it
    # honestly instead of crashing or silently accepting.
    with pytest.raises(lic.LicenseError, match="no license keys configured"):
        lic.parse_and_verify("whatever.key", keys=())


def test_covers_version():
    info = lic.LicenseInfo(email="e@x.com", purchased=dt.date(2026, 1, 1),
                           max_version="1", key_id=0)
    assert lic.covers_version(info, "0.8.3")
    assert lic.covers_version(info, "1.9.0")
    assert not lic.covers_version(info, "2.0.0")
    forever = lic.LicenseInfo(email="e@x.com", purchased=dt.date(2026, 1, 1),
                              max_version=lic.FOREVER, key_id=0)
    assert lic.covers_version(forever, "99.0")


# ---------------------------------------------------------------------------
# Storage: import / status (XDG isolated by the suite conftest)
# ---------------------------------------------------------------------------

def test_import_and_status_roundtrip(one_key):
    from fluidvoice import paths
    key, _pk = one_key
    paths.license_file().unlink(missing_ok=True)
    assert lic.status() is None
    info = lic.import_key(key)
    assert paths.license_file().exists()
    assert oct(paths.license_file().stat().st_mode & 0o777) == "0o600"
    stored = lic.status()
    assert stored == info


def test_status_survives_unverifiable_file(one_key, monkeypatch):
    from fluidvoice import paths
    key, _pk = one_key
    lic.import_key(key)
    paths.license_file().write_text("corrupt-not-a-key\n")
    assert lic.status() is None  # honest None, not a crash
    # the corrupt file does not block re-import
    again = lic.import_key(key)
    assert again.email == "buyer@example.com"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_license_status_then_import(one_key, capsys, monkeypatch):
    from fluidvoice import paths
    from fluidvoice.cli import main
    key, _pk = one_key
    paths.license_file().unlink(missing_ok=True)
    assert main(["license"]) == 0
    out = capsys.readouterr().out
    assert "no license" in out.lower()
    assert main(["license", key]) == 0
    out = capsys.readouterr().out
    assert "buyer@example.com" in out
    assert paths.license_file().exists()


def test_cli_license_rejects_bad_key(one_key, capsys):
    from fluidvoice.cli import main
    _key, pk = one_key
    bad, _ = _make_key(_TEST_SK, {**_VALID_PAYLOAD, "email": ""})
    with pytest.raises(lic.LicenseError):
        lic.parse_and_verify(bad, keys=(pk,))
    assert main(["license", "garbage.key"]) == 1
    err = capsys.readouterr().err
    assert "error" in err.lower()


# ---------------------------------------------------------------------------
# The owner-side keygen script (scripts/license_keygen.py): whatever it
# prints, the shipped verifier must accept — through the REAL script,
# not the reference signer above.
# ---------------------------------------------------------------------------

def test_keygen_issue_roundtrip_through_shipped_verifier(tmp_path):
    seed = bytes.fromhex(_TEST_SK)
    pk = keygen.public_key(seed)
    key = keygen.issue_key(
        seed, keygen.build_payload("buyer@example.com",
                                   dt.date(2026, 11, 21), "1", "Jane"))
    info = lic.parse_and_verify(key, keys=(pk,), now=dt.date(2026, 11, 21))
    assert info.email == "buyer@example.com"
    assert info.max_version == "1"
    # the optional name rides along in the payload (ignored by verify)
    import base64
    raw = base64.urlsafe_b64decode(
        key.split(".")[0] + "=" * (-len(key.split(".")[0]) % 4))
    payload = json.loads(raw)
    assert payload["name"] == "Jane"
    assert payload["product"] == lic.PRODUCT_ID


def test_keygen_cli_generate_issue_verify(tmp_path, capsys, monkeypatch):
    seed_path = tmp_path / "offline.seed"
    assert keygen.main(["generate", "--private-key", str(seed_path)]) == 0
    out = capsys.readouterr()
    assert "public key (hex):" in out.out  # pubkey on stdout, hints stderr
    assert len(out.out.strip().split()[-1]) == 64  # the hex pubkey
    assert "offline media" in out.err or "PUBLIC_KEYS" in out.err
    assert oct(seed_path.stat().st_mode & 0o777) == "0o600"
    seed = bytes.fromhex(seed_path.read_text().strip())

    monkeypatch.setattr(lic, "PUBLIC_KEYS", (keygen.public_key(seed),))
    assert keygen.main(["issue", "--private-key", str(seed_path),
                        "--email", "cli@x.com", "--v", "forever"]) == 0
    key = capsys.readouterr().out.strip()
    assert "\n" not in key  # exactly one pipeable line

    assert keygen.main(["verify", key]) == 0
    assert "cli@x.com" in capsys.readouterr().out


def test_keygen_generate_refuses_repo_and_existing(tmp_path):
    inside = REPO / "leaked.seed"
    with pytest.raises(SystemExit, match="inside this repository"):
        keygen.main(["generate", "--private-key", str(inside)])
    assert not inside.exists()
    existing = tmp_path / "seed"
    existing.write_text("00" * 32 + "\n")
    with pytest.raises(SystemExit, match="already exists"):
        keygen.main(["generate", "--private-key", str(existing)])


def test_keygen_rejects_bad_version_and_seed(tmp_path):
    seed_path = tmp_path / "seed"
    seed_path.write_text(_TEST_SK + "\n")
    with pytest.raises(SystemExit, match="--v"):
        keygen.main(["issue", "--private-key", str(seed_path),
                     "--email", "x@y.z", "--v", "1.2.3"])
    seed_path.write_text("abcd")  # not a 32-byte seed
    with pytest.raises(SystemExit, match="64 hex"):
        keygen.main(["issue", "--private-key", str(seed_path),
                     "--email", "x@y.z", "--v", "1"])
