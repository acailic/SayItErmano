# First-purchase email (#17)

What a buyer receives right after paying. Two emails are involved, and
their jobs are distinct:

1. **Paddle's transaction receipt** — sent automatically by Paddle as
   merchant of record (branding/from-name set in the Paddle dashboard →
   Brand). It carries the line item, the **VAT invoice**, and the
   refund link. Do not duplicate it.
2. **The key email below** — delivers the license key and the download
   link. Sent by the seller side when the webhook issues the key
   ([license ops](license-ops.md): the webhook mints and logs the key;
   today the owner sends this email, paste-ready, from the issue-log
   entry — automation can send it later, the copy does not change).

## Template

Subject: `Your SayItErmano supporter license (key inside)`

```text
Hi {name},

thank you for supporting SayItErmano — here is everything you bought.

  License key:  {KEY}

  Import it:    sayit-ermano license {KEY}
  (or Settings → About → License — works fully offline)

  Download:     https://github.com/acailic/SayItErmano/releases/latest

What the key does: it records your entitlement to the packaged builds,
the update channel, and priority support (first response within 2
business days). It never gates dictation — every feature of the app
works without it, forever: SayItErmano is GPL-3.0 software.

Keep this email. If you lose the key, just reply here — support
re-sends it from the purchase record.

Refunds: 14 days, no questions asked — reply to the Paddle receipt
email or see the refund policy:
https://acailic.github.io/SayItErmano/refund.html

Your VAT invoice/receipt came in a separate email from Paddle (they
are the seller of record for tax purposes).

— SayItErmano (community Linux port of FluidVoice)
  https://acailic.github.io/SayItErmano/
```

## Why each line is there

- The key line is the deliverable; the import line is the fastest path
  to value (one paste, no account, offline).
- The "never gates dictation" sentence is the GPL statement from
  [PRICING.md](../PRICING.md) in email form — buyers of GPL software
  should never be surprised by what the money did and did not buy.
- Reply-to-re-request for lost keys mirrors the support flow in
  [license.md](../guides/license.md) (every issued key is in the
  issue log, looked up by purchase email).
- The refund line and the Paddle-invoice pointer keep the two emails'
  jobs separate: money questions go to Paddle, product questions come
  to us.

## Read-through check (acceptance)

Before go-live, send the template to yourself with a real sandbox key
and check: subject survives the inbox, the import command copies as
one line, links resolve, and the whole thing reads like a person wrote
it. Record the read-through on #17.
