# Store emails — first-purchase templates (owner-only)

What a buyer receives right after checkout (#17). Two emails, two
senders, no overlap:

| Email | Sent by | Contains |
|---|---|---|
| Payment receipt | Paddle, automatically on `transaction.completed` | amount, payment method, **PDF invoice with VAT** (Paddle is merchant of record — do not duplicate or reissue the invoice) |
| License + download | the project, on key issuance | the offline license key, where to download, how to import, refund + support paths |

Paddle's receipt needs no configuration and cannot carry our key (the
key does not exist until the webhook mints it), so the project email
below is the one that delivers the product. Copy it verbatim into
whatever sends that email — today that is the owner composing from the
issue log row ([license-ops.md](license-ops.md),
`scripts/license_lookup.py`); the webhook host is the natural place to
automate it later. The whole flow is exercised by the sandbox test in
[license-ops.md](license-ops.md#sandbox-test-acceptance-for-16).

## First-purchase email (the deliverable)

Subject: `Your SayItErmano license key — thank you`

```text
Hi {{name}},

Thank you for supporting SayItErmano — your supporter license is below.

  License key: {{key}}

Import it on any of your Linux machines:

  sayit-ermano license {{key}}

(The command prints a confirmation; Settings → About → License then
shows the imported state.) Verification is fully offline; the key
never gates dictation — every feature of the free GPL build keeps
working with or without it.

Downloads: {{download_url}}
(Packaged builds with the update channel; the free build lives at
https://github.com/acailic/SayItErmano/releases.)

Your payment receipt and VAT invoice came from Paddle in a separate
email (Paddle.com is the merchant of record for this purchase). Keep
both if you need them for expenses.

Questions, refunds (14 days, no questions asked), or a lost key?
Reply to this email or open an issue:
https://github.com/acailic/SayItErmano/issues

— Aleksandar, SayItErmano
```

Placeholder notes:

- `{{name}}` — buyer name if the transaction carried one, else drop
  the line to "Hi,".
- `{{key}}` — from the issue-log row (`scripts/license_lookup.py
  <email>`); one key per transaction, redelivery returns the SAME key.
- `{{download_url}}` — the packaged-build download page. Until a
  licensed-only artifact exists, use the install guide
  (`docs/guides/install.md`); never a time-limited link, the key is
  the entitlement.

## Why it reads this way

- Under ~160 words, one action (import the key), plain text only — it
  must survive every mail client and read well in a notification pane.
- The GPL honesty rule (PRICING.md): the email says plainly that the
  key gates nothing — same promise the site makes.
- Refund and lost-key paths are one click; the license guide
  (`docs/guides/license.md`) is the deeper version of the import
  instructions if the buyer asks.
- The subject names the payload ("license key") so it is findable
  months later when the buyer re-imports on a new machine.

## Reserve-conversion note

Reserve-thread buyers (discussion #35) held the $39 intro price for 30
days after launch. For that cohort only, prepend one line: "You held
the $39 intro price by reserving — this is it." Nothing else changes.
