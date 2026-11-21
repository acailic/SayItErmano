# License keys

Decision record: [ADR-0005](../adr/ADR-0005-commercial-sku.md). The
pricing it belongs to: [PRICING](../PRICING.md).

## What a key is

A one-line string from your purchase email:

```
sayit-ermano license <PASTE KEY HERE>
```

It is an Ed25519-signed statement of what you bought — your email, the
purchase date, and the covered major version. Verification is fully
offline: no network call, no account, no phoning home. The app works
completely without a key; the key records your entitlement to the
update channel and priority support.

## What a key does and does not do

- It **never** gates the dictation loop. Every feature works with or
  without a key, forever — that is a GPL obligation and a design
  choice.
- It identifies you to support (the email in the payload).
- `v` in the payload is the covered major version (`"1"` = all 1.x
  releases; `forever` = all releases). The version check is advisory:
  it shapes the update notice, it does not disable anything.

## Commands

```
sayit-ermano license            # show status
sayit-ermano license <KEY>      # verify and import
```

Settings → About → License shows the same state (email, purchase date,
covered releases).

The key is stored at `~/.config/sayit-ermano/license.key` (mode 0600).
Delete the file to remove it. A corrupted file is reported as "no
license" and can be replaced by importing again.

## Lost keys, refunds, redistribution

- **Lost key:** support re-issues from the issue log kept at the store
  (operator runbook: [dev/license-ops.md](../dev/license-ops.md)) — the
  email in the payload is the identity.
- **Refunds:** 14 days, handled by Paddle as merchant of record.
- **Redistribution:** the software is GPL-3.0. If someone shares your
  key or their build with others, that is permitted by the license;
  what cannot be shared is the support entitlement tied to your
  purchase.
