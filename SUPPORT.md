# Support

First stop for everything: [`sayit-ermano doctor`](docs/dev/FAQ.md) —
it diagnoses audio, hotkey, insertion, model, and update problems and
prints the fix when it knows one.

## Where to ask

| You have… | Go to |
|---|---|
| A bug (something does not work as documented) | [Open an issue](https://github.com/acailic/SayItErmano/issues/new/choose) with the bug template — the `doctor` output is required, which is what makes issues triageable |
| A question about setup or behavior | [Issues](https://github.com/acailic/SayItErmano/issues) (a question is fine) after checking the [FAQ](docs/dev/FAQ.md) and the [guides](docs/README.md) |
| A license problem (lost key, wrong email, purchase) | License questions: [license keys guide](docs/guides/license.md). Purchase and refund matters: the support address on your purchase email (Paddle is the seller of record) |
| A security report | [SECURITY.md](SECURITY.md) — do not open a public issue |

## What to include

The app ships **no telemetry** — nothing is collected, so nobody can
see your machine unless you show it:

1. `sayit-ermano --version`
2. `sayit-ermano doctor` (redact freely; keep the tool and session lines)
3. What you did, what you expected, what happened
4. For daemon misbehavior: `journalctl --user -u sayit-ermano -b --no-pager | tail -50`

## Response targets

- **Paid license holders:** priority handling, target first response
  within two business days (via the support address on your purchase
  email).
- **Community issues:** best effort, in the open, in issue order.

## Refunds

14 days, no questions, processed by Paddle as merchant of record —
see [PRICING](docs/PRICING.md). The app keeps working regardless.

## Privacy stance in support conversations

Never paste raw audio, transcripts you would not share, or API keys —
`doctor` output and logs are enough. API keys are never written to
disk by the app and should never appear in an issue.
