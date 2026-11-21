# First-purchase email — copy (issue #17)

What a brand-new buyer finds in their inbox the moment they pay. Two
emails, two senders, both drafted here:

1. **Paddle order confirmation** — sent automatically by Paddle as
   merchant of record (receipt + VAT invoice). Where the dashboard
   allows seller copy on customer emails, use Email A; the attachment
   itself is Paddle's.
2. **License key + download** — the key is minted by our webhook only
   AFTER `transaction.completed`
   ([license-ops.md](license-ops.md)), so it cannot ride in Paddle's
   instant email. Email B is sent from the webhook host as soon as
   the key exists (until that SMTP step is automated, the owner sends
   it from the issue log via `scripts/license_lookup.py` — same
   template, a minute of work).

Acceptance for #17 is that Email B "reads well": scannable, honest
about what a license does and does not gate, and one reply away from
a human.

## Email A — Paddle order-confirmation seller copy

```
SayItErmano — supporter license

Thanks for supporting SayItErmano, the 100% local Linux dictation
app. Your license key and download link follow in a separate email
within a few minutes; your receipt and VAT invoice are attached here.

Questions or refunds: reply to this email — a human reads it.
Refund policy: https://acailic.github.io/SayItErmano/refund.html
```

## Email B — license key + download

Subject: `Your SayItErmano license key (+ download)`

```
Hi {{name}},

Thank you for supporting SayItErmano — you're in. Everything you
need is in this email; keep it somewhere findable.

LICENSE KEY

  {{key}}

Activate it (works fully offline):

  sayit-ermano license {{key}}

…or paste it in Settings → About → License.

DOWNLOAD

  Ubuntu 24.04 (.deb): {{deb_url}}
  Any distro (one-shot installer): {{installer_url}}
  All install routes:
  https://github.com/acailic/SayItErmano/blob/linux/docs/guides/install.md

WHAT YOU BOUGHT — AND WHAT YOU DIDN'T

  SayItErmano is GPL-3.0 free software; buying never gates
  dictation. Your license is your line to priority support (first
  response within 2 business days) and the packaged update channel.

RECEIPT & VAT

  Paddle.com is the seller of record for this purchase — the
  confirmation email from Paddle is your VAT invoice.

NOT HAPPY?

  14 days, no questions: reply to this email, or see
  https://acailic.github.io/SayItErmando/refund.html

Something not working? Reply — a human reads this inbox.

— acailic · SayItErmano (community Linux port of FluidVoice)
```

Placeholders: `{{name}}` (buyer name or "there"), `{{key}}` (from the
issue log), `{{deb_url}}` / `{{installer_url}}` (current release
links; update when a release ships).

Proofread note (2026-11-21): watch the `SayItErmando` typo trap — it
is `SayItErmano`, one "r" in the middle.
