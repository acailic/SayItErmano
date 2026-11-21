"""Paddle webhook -> offline key: signature, replay, issue log (issue #16).

Fixtures mirror Paddle Billing's `transaction.completed` sandbox shape
(developer.paddle.com/webhooks — Paddle-Signature: ts=...;h1=... where
h1 = HMAC-SHA256(secret, "ts:" + body)). The receiver never trusts an
unsigned body; the issued key must then verify in the APP's offline
verifier — that round-trip is the actual product promise.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import scripts.license_sign as license_sign
import scripts.license_webhook as lw
from fluidvoice import license as lic

# Deterministic 32-byte seed (RFC 8032 test-1 secret doubles fine).
SEED = bytes.fromhex(
    "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
WEBHOOK_SECRET = b"pdl_ntfset_test_01EXAMPLE"

TODAY = time.strftime("%Y-%m-%d")


def _event(*, email="buyer@example.com", name="Ada Buyer",
           event_id="evt_01htest", occurred=None, product_id="pro_01htest",
           price_custom=None) -> dict:
    price = {"id": "pri_01htest", "product_id": product_id}
    if price_custom is not None:
        price["custom_data"] = price_custom
    return {
        "event_type": "transaction.completed",
        "event_id": event_id,
        "occurred_at": occurred or f"{TODAY}T12:00:00.000+00:00",
        "data": {
            "id": "txn_01htest",
            "status": "completed",
            "customer": {"id": "ctm_01htest", "email": email, "name": name},
            "items": [{"price": price, "quantity": 1}],
        },
    }


def _signed(event: dict, *, ts: int | None = None,
            secret: bytes = WEBHOOK_SECRET) -> tuple[bytes, str]:
    body = json.dumps(event).encode()
    ts = int(time.time()) if ts is None else ts
    mac = hmac.new(secret, f"{ts}:".encode() + body,
                   hashlib.sha256).hexdigest()
    return body, f"ts={ts};h1={mac}"


def _ctx(tmp_path, **overrides) -> lw.Context:
    now = overrides.pop("now", None) or time.time()
    defaults = dict(
        webhook_secret=WEBHOOK_SECRET,
        signing_seed=SEED,
        log_path=tmp_path / "issued-keys.jsonl",
        clock=lambda: now,
    )
    defaults.update(overrides)
    return lw.Context(**defaults)


def _verify_offline(key: str, monkeypatch) -> lic.LicenseInfo:
    # the app trusts only its embedded keys; the receiver's seed is the
    # private half of exactly one of them
    monkeypatch.setattr(lic, "PUBLIC_KEYS", (license_sign.public_key(SEED),))
    return lic.parse_and_verify(key)


# ---------------------------------------------------------------------------
# signature layer
# ---------------------------------------------------------------------------

def test_valid_delivery_issues_key_the_app_accepts(tmp_path, monkeypatch):
    body, sig = _signed(_event())
    status, resp = lw.process(body, sig, _ctx(tmp_path))
    assert status == 200 and resp["ok"] and not resp["duplicate"]
    info = _verify_offline(resp["key"], monkeypatch)
    assert info.email == "buyer@example.com"
    assert info.max_version == lic.FOREVER  # default coverage


def test_signature_secret_mismatch_is_rejected(tmp_path):
    body, sig = _signed(_event(), secret=b"wrong-secret")
    status, resp = lw.process(body, sig, _ctx(tmp_path))
    assert status == 401 and "HMAC" in resp["error"]
    assert not _ctx(tmp_path).log_path.exists()  # nothing issued


def test_tampered_body_is_rejected(tmp_path):
    body, sig = _signed(_event())
    body = body.replace(b"buyer@example.com", b"thief@example.com")
    status, resp = lw.process(body, sig, _ctx(tmp_path))
    assert status == 401
    assert not _ctx(tmp_path).log_path.exists()


def test_stale_timestamp_is_a_replay(tmp_path):
    body, sig = _signed(_event(), ts=int(time.time()) - 10_000)
    status, resp = lw.process(body, sig, _ctx(tmp_path))
    assert status == 401 and "replay" in resp["error"]
    assert not _ctx(tmp_path).log_path.exists()


def test_future_timestamp_is_rejected(tmp_path):
    body, sig = _signed(_event(), ts=int(time.time()) + 3_600)
    status, _ = lw.process(body, sig, _ctx(tmp_path))
    assert status == 401


@pytest.mark.parametrize("header", [
    "", "garbage", "ts=123", "h1=abc", "ts=abc;h1=def",
    "ts=123;h1=zz:non:hex",
])
def test_malformed_signature_headers(tmp_path, header):
    body, _ = _signed(_event())
    status, resp = lw.process(body, header, _ctx(tmp_path))
    assert status == 401 and resp["ok"] is False


# ---------------------------------------------------------------------------
# event handling
# ---------------------------------------------------------------------------

def test_redelivery_is_idempotent(tmp_path):
    body, sig = _signed(_event())
    ctx = _ctx(tmp_path)
    first_status, first = lw.process(body, sig, ctx)
    second_status, second = lw.process(body, sig, ctx)
    assert first_status == second_status == 200
    assert first["key"] == second["key"]
    assert second["duplicate"] is True
    lines = ctx.log_path.read_text().strip().splitlines()
    assert len(lines) == 1  # exactly one issue, not one per Paddle retry
    assert json.loads(lines[0])["key"] == first["key"]


def test_issue_log_record_shape(tmp_path):
    body, sig = _signed(_event())
    ctx = _ctx(tmp_path)
    _status, _resp = lw.process(body, sig, ctx)
    record = json.loads(ctx.log_path.read_text().strip())
    assert record["event_id"] == "evt_01htest"
    assert record["paddle_txn"] == "txn_01htest"
    assert record["email"] == "buyer@example.com"
    assert record["purchased"] == TODAY
    assert record["coverage"] == "forever"
    assert record["key"]
    # name rides in the payload (additive field, ignored by older builds)
    payload_part = record["key"].split(".")[0]
    import base64
    payload = json.loads(base64.urlsafe_b64decode(
        payload_part + "=" * (-len(payload_part) % 4)))
    assert payload["name"] == "Ada Buyer"


def test_other_event_types_are_acknowledged_and_ignored(tmp_path):
    event = _event()
    event["event_type"] = "subscription.created"
    body, sig = _signed(event)
    ctx = _ctx(tmp_path)
    status, resp = lw.process(body, sig, ctx)
    assert status == 200 and resp["ok"]
    assert not ctx.log_path.exists()


def test_missing_customer_email_is_a_human_problem_not_a_crash(tmp_path):
    event = _event()
    del event["data"]["customer"]["email"]
    body, sig = _signed(event)
    ctx = _ctx(tmp_path)
    status, resp = lw.process(body, sig, ctx)
    assert status == 422 and "email" in resp["error"]
    assert not ctx.log_path.exists()


def test_product_filter_ignores_other_products(tmp_path):
    ctx = _ctx(tmp_path, product_ids=frozenset({"pro_ours"}))
    body, sig = _signed(_event(product_id="pro_someone_elses"))
    status, resp = lw.process(body, sig, ctx)
    assert status == 200 and resp["ok"]  # acknowledged: stop Paddle retries
    assert not ctx.log_path.exists()
    body, sig = _signed(_event(product_id="pro_ours"))
    status, resp = lw.process(body, sig, ctx)
    assert status == 200 and resp["ok"] and "key" in resp


def test_price_custom_data_overrides_coverage(tmp_path, monkeypatch):
    event = _event(price_custom={"license": "1"})
    body, sig = _signed(event)
    ctx = _ctx(tmp_path)
    status, resp = lw.process(body, sig, ctx)
    assert status == 200
    info = _verify_offline(resp["key"], monkeypatch)
    assert info.max_version == "1"
    assert lic.covers_version(info, "1.9.0")
    assert not lic.covers_version(info, "2.0.0")


def test_email_failure_never_fails_the_webhook(tmp_path, monkeypatch):
    import subprocess as sp
    calls = []
    monkeypatch.setattr(sp, "run",
                        lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(
                            sp.SubprocessError("smtp down")))
    body, sig = _signed(_event())
    ctx = _ctx(tmp_path, email_command="sendmail")
    status, resp = lw.process(body, sig, ctx)
    assert status == 200 and "key" in resp  # logged + returned regardless
    assert calls


# ---------------------------------------------------------------------------
# HTTP layer, end to end over loopback
# ---------------------------------------------------------------------------

def test_http_round_trip(tmp_path):
    ctx = _ctx(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), lw.make_handler(ctx))
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    try:
        body, sig = _signed(_event())
        req = urllib.request.Request(
            base + "/webhook", data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Paddle-Signature": sig})
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 200
            assert json.loads(r.read())["ok"] is True

        _bad_body, bad_sig = _signed(_event(), secret=b"nope")
        req = urllib.request.Request(
            base + "/webhook", data=body, method="POST",
            headers={"Paddle-Signature": bad_sig})
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(req, timeout=5)
        assert err.value.code == 401
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert len(ctx.log_path.read_text().strip().splitlines()) == 1
