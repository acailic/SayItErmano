"""Paddle webhook -> license key (issue #16): signature gate, replay
defense, idempotent issuance, and the support lookup log.

Everything runs against the same code the deployed endpoint executes
(scripts/license_webhook.py) — handle_event for the logic, one loopback
HTTP pass for the wire layer. Signatures are computed the way Paddle
Billing does it (`Paddle-Signature: ts=...,h1=...`, HMAC-SHA256 over
``{ts}:{body}``), so a scheme drift here fails before it can reach a
production endpoint.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import http.client
import json
import threading
import time

import pytest

import scripts.license_keygen as keygen
import scripts.license_lookup as lookup
import scripts.license_webhook as webhook
from fluidvoice import license as lic

SECRET = "pdl_ntfset_test_secret"
OTHER_SECRET = "pdl_ntfset_wrong_secret"
# RFC 8032 test-1 secret: deterministic, never a real key.
SEED = bytes.fromhex(
    "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
PRODUCT = "pro_01testproduct"
NOW = 1_800_000_000.0  # fixed clock for determinism


_DEFAULT = object()  # distinguishes "not passed" from an explicit None


def paddle_event(event_id="evt_01test", txn="txn_01test",
                 email="buyer@example.com", product_id=PRODUCT,
                 occurred="2026-11-21T10:00:00.000Z", items=None,
                 customer=_DEFAULT, status="completed", event_type=None):
    data = {
        "id": txn,
        "status": status,
        "customer": {"id": "ctm_01", "email": email, "name": "Buyer Name"}
        if customer is _DEFAULT else customer,
        "items": items if items is not None else [
            {"price": {"id": "pri_01", "product_id": product_id},
             "product": {"id": product_id, "name": "SayItErmano"}},
        ],
    }
    return {"event_type": event_type or "transaction.completed",
            "event_id": event_id, "occurred_at": occurred, "data": data}


def sign_body(body: bytes, secret: str, ts: float) -> str:
    mac = hmac.new(secret.encode(), f"{int(ts)}:".encode() + body,
                   hashlib.sha256).hexdigest()
    return f"ts={int(ts)},h1={mac}"


@pytest.fixture()
def cfg(tmp_path):
    return webhook.WebhookConfig(
        secret=SECRET,
        seed=SEED,
        product_map={PRODUCT: "1", "pro_01forever": "forever"},
        issue_log=webhook.IssueLog(tmp_path / "issued.jsonl"))


def deliver(cfg, event, *, secret=SECRET, ts=NOW - 10, body=None,
            header=None):
    raw = body if body is not None else json.dumps(event).encode()
    header = sign_body(raw, secret, ts) if header is None else header
    return webhook.handle_event(raw, header, cfg, now=NOW)


def log_entries(cfg):
    return list(cfg.issue_log.entries())


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_valid_delivery_issues_offline_verifiable_key(cfg):
    status, resp = deliver(cfg, paddle_event())
    assert status == 200
    assert resp["issued"] is True
    assert resp["email"] == "buyer@example.com"
    info = lic.parse_and_verify(resp["key"], keys=(keygen.public_key(SEED),),
                                now=dt.date(2026, 11, 22))
    assert info.email == "buyer@example.com"
    assert info.max_version == "1"
    (entry,) = log_entries(cfg)
    assert entry["transaction_id"] == "txn_01test"
    assert entry["event_id"] == "evt_01test"
    assert entry["key"] == resp["key"]
    assert entry["purchased"] == "2026-11-21"
    assert entry["name"] == "Buyer Name"


def test_forever_product_maps_to_forever_coverage(cfg):
    forever_item = [{"price": {"product_id": "pro_01forever"}}]
    status, resp = deliver(cfg, paddle_event(items=forever_item))
    assert status == 200
    info = lic.parse_and_verify(resp["key"], keys=(keygen.public_key(SEED),),
                                now=dt.date(2026, 11, 22))
    assert info.max_version == lic.FOREVER


# ---------------------------------------------------------------------------
# Signature gate: forgery, tampering, replay
# ---------------------------------------------------------------------------

def test_wrong_secret_rejected(cfg):
    status, resp = deliver(cfg, paddle_event(), secret=OTHER_SECRET)
    assert (status, resp) == (401, {"error": "bad signature"})
    assert log_entries(cfg) == []


def test_tampered_body_rejected(cfg):
    header = sign_body(json.dumps(paddle_event()).encode(), SECRET, NOW - 10)
    status, resp = deliver(cfg, None, header=header,
                           body=b'{"event_type":"transaction.completed",'
                                b'"data":{"id":"txn_evil"}}')
    assert status == 401
    assert log_entries(cfg) == []


@pytest.mark.parametrize("header", [
    "", "garbage", "ts=abc,h1=def", "h1=deadbeef", "ts=1799999990",
])
def test_malformed_signature_header_rejected(cfg, header):
    status, _ = deliver(cfg, paddle_event(), header=header)
    assert status == 401


@pytest.mark.parametrize("ts", [NOW - 3600, NOW + 3600])
def test_stale_or_future_timestamp_rejected(cfg, ts):
    # A captured delivery replayed later (or clock-shifted) must not
    # mint a key: the ts inside the signed blob bounds its validity.
    status, resp = deliver(cfg, paddle_event(), ts=ts)
    assert (status, resp) == (401, {"error": "bad signature"})
    assert log_entries(cfg) == []


def test_timestamp_at_tolerance_boundary_accepted(cfg):
    status, resp = deliver(cfg, paddle_event(), ts=NOW - cfg.tolerance)
    assert status == 200 and resp["issued"] is True


# ---------------------------------------------------------------------------
# Event handling: parse failures, non-purchase events, bad payloads
# ---------------------------------------------------------------------------

def test_non_json_body_with_valid_signature_is_400(cfg):
    status, resp = deliver(cfg, None, body=b"not json")
    assert status == 400
    assert log_entries(cfg) == []


def test_other_event_types_are_acked_without_issuing(cfg):
    status, resp = deliver(cfg, paddle_event(event_type="customer.created"))
    assert status == 200
    assert resp == {"ignored": "customer.created"}
    assert log_entries(cfg) == []


@pytest.mark.parametrize("kwargs,reason", [
    ({"customer": {"email": None}}, "customer email"),
    ({"customer": None}, "customer email"),
    ({"items": []}, "product items"),
    ({"occurred": "yesterday"}, "occurred_at"),
    ({"status": "draft"}, "status"),
])
def test_unissuable_payloads_are_422(cfg, kwargs, reason):
    status, resp = deliver(cfg, paddle_event(**kwargs))
    assert status == 422
    assert reason in resp["error"]
    assert log_entries(cfg) == []


def test_unknown_product_is_422_and_logs_nothing(cfg):
    status, resp = deliver(cfg, paddle_event(product_id="pro_unknown"))
    assert status == 422
    assert "not in the product map" in resp["error"]
    assert log_entries(cfg) == []


def test_partial_mapping_is_rejected(cfg):
    items = [{"price": {"product_id": PRODUCT}},
             {"price": {"product_id": "pro_unknown"}}]
    status, resp = deliver(cfg, paddle_event(items=items))
    assert status == 422
    assert "pro_unknown" in resp["error"]


def test_event_id_fallbacks_cover_paddle_shapes(cfg):
    # no top-level event_id: falls back to notification_id, then the
    # transaction id — dedup must still work with what's there.
    event = paddle_event()
    event.pop("event_id")
    event["notification_id"] = "ntf_01x"
    status, resp = deliver(cfg, event)
    assert status == 200
    status2, resp2 = deliver(cfg, event)
    assert status2 == 200 and resp2["issued"] is False
    assert resp2["key"] == resp["key"]


# ---------------------------------------------------------------------------
# Idempotency: redelivery returns the same key, never a second issue
# ---------------------------------------------------------------------------

def test_redelivery_is_idempotent(cfg):
    status1, resp1 = deliver(cfg, paddle_event())
    assert resp1["issued"] is True
    status2, resp2 = deliver(cfg, paddle_event())  # fresh ts + signature
    assert status2 == 200
    assert resp2 == {"issued": False, "duplicate": True,
                     "key": resp1["key"], "email": resp1["email"]}
    assert len(log_entries(cfg)) == 1


def test_duplicate_detection_also_keys_on_transaction_id(cfg):
    deliver(cfg, paddle_event(event_id="evt_a", txn="txn_same"))
    status, resp = deliver(cfg, paddle_event(event_id="evt_b",
                                             txn="txn_same"))
    assert resp["issued"] is False and resp["duplicate"] is True
    assert len(log_entries(cfg)) == 1


# ---------------------------------------------------------------------------
# Issue-log durability details (ADR-0002 pattern)
# ---------------------------------------------------------------------------

def test_issue_log_skips_torn_tail_line(cfg, tmp_path):
    deliver(cfg, paddle_event())
    log_path = tmp_path / "issued.jsonl"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write('{"torn": ')  # crash mid-append
    entries = log_entries(cfg)
    assert len(entries) == 1 and entries[0]["email"]
    # dedup still sees the surviving entry
    status, resp = deliver(cfg, paddle_event())
    assert resp["issued"] is False


def test_issue_log_missing_file_is_empty_iterator(tmp_path):
    assert list(webhook.IssueLog(tmp_path / "nope.jsonl").entries()) == []


# ---------------------------------------------------------------------------
# Product map parsing
# ---------------------------------------------------------------------------

def test_parse_product_map():
    assert webhook.parse_product_map("pro_a=1, pro_b=forever") == \
        {"pro_a": "1", "pro_b": "forever"}
    with pytest.raises(SystemExit, match="bad product map"):
        webhook.parse_product_map("pro_a=1.2.3")
    with pytest.raises(SystemExit, match="empty"):
        webhook.parse_product_map(" , ")


# ---------------------------------------------------------------------------
# Support lookup (scripts/license_lookup.py)
# ---------------------------------------------------------------------------

def test_support_lookup_by_email_and_ids(cfg, tmp_path, capsys):
    deliver(cfg, paddle_event(event_id="evt_1", txn="txn_1",
                              email="alice@example.com"))
    deliver(cfg, paddle_event(event_id="evt_2", txn="txn_2",
                              email="bob@example.com"))
    log_path = tmp_path / "issued.jsonl"
    assert len(lookup.lookup_entries(log_path, "ALICE@")) == 1  # ci substring
    assert len(lookup.lookup_entries(log_path, "txn_2")) == 1
    assert lookup.lookup_entries(log_path, "nobody@x") == []
    assert lookup.main(["alice@example.com", "--log", str(log_path)]) == 0
    out = capsys.readouterr().out
    assert "alice@example.com" in out and "txn_1" in out
    assert lookup.main(["ghost@x", "--log", str(log_path)]) == 1


# ---------------------------------------------------------------------------
# The wire layer: one loopback pass through the real HTTP server
# ---------------------------------------------------------------------------

def test_http_endpoint_round_trip(cfg, monkeypatch):
    monkeypatch.setattr(lic, "PUBLIC_KEYS", (keygen.public_key(SEED),))
    server = webhook.serve(cfg, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]

        def post(path, body, headers):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request("POST", path, body=body, headers=headers)
            resp = conn.getresponse()
            payload = json.loads(resp.read())
            conn.close()
            return resp.status, payload

        body = json.dumps(paddle_event()).encode()
        status, resp = post("/webhook", body,
                            {"Paddle-Signature": sign_body(body, SECRET,
                                                           time.time()),
                             "Content-Type": "application/json"})
        assert status == 200 and resp["issued"] is True
        # acceptance shape: the app accepts the issued key offline
        # (stored under the suite's isolated XDG config dir)
        info = lic.import_key(resp["key"])
        assert info.email == "buyer@example.com"

        bad = post("/webhook", body, {"Paddle-Signature": "ts=1,h1=bad"})
        assert bad[0] == 401
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/healthz")
        health = conn.getresponse()
        assert health.status == 200
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
