# Network egress audit

Date: 2026-11-21. Method and evidence recorded below; re-run the two
commands in "How to re-verify" after any change that touches network
code. Feeds the store-page privacy claim (#24, PRICING.md).

## Static inventory — every network-capable call site

`grep -rn "urlopen|urllib.request|http.client|httpx|requests." fluidvoice/`
resolves to exactly four outbound modules. Nothing else in the package
opens sockets (the control server is a Unix-domain socket only —
ADR-0001; the test suite enforces the no-loopback-connect rule in
conftest's network guard).

| Path | Destination | When it fires | Off by default? | How to disable |
|---|---|---|---|---|
| `fluidvoice/model_download.py` | HuggingFace (model files) | First use of a model, or a model switch, when the file is not already in the local cache | **No** — but only until the model is cached; never during dictation | Pre-place models in the cache; downloads never repeat for a cached file |
| `fluidvoice/update.py` | GitHub releases API | Periodic update check | **No** — `updates.check` defaults to true | `[updates] check = false` |
| `fluidvoice/ai/client.py` | The AI-polish endpoint YOU configured | Only per dictation when `ai.enabled = true` | **Yes** | `[ai] enabled = false` (default) |
| `fluidvoice/backends/remote_stt.py` | The STT server YOU configured | Only when `model.remote_url` is set | **Yes** | Leave `model.remote_url` empty (default) |

No telemetry, analytics, crash reporting, or account network code
exists in the package — there is nothing to opt out of beyond the
table above.

## Runtime observation — default-config daemon, 2026-11-21

Observed on the developer machine against the live daemon (X11 session,
local `large-v3` model, `ai.enabled=false`, `model.remote_url` unset,
`updates.check=true`):

```
ss -tnp  | grep pid=<daemon>   # established/lingering TCP connections
ss -tlnp | grep pid=<daemon>   # TCP listeners
```

Results:

- **0 TCP listeners** (ADR-0001 held in production).
- **Exactly 1 lingering TCP connection**: `CLOSE-WAIT` to
  `18.172.242.45:443` (CloudFront) — the update check's completed
  fetch, socket awaiting cleanup. No other remote endpoints at rest.
- All other sockets are Unix-domain (PipeWire, D-Bus, journal, the
  control socket).
- During dictation on this configuration, no new connection appears:
  transcription is local (faster-whisper on the cached model) and AI
  polish is off.

## The claim this backs (store copy)

> Your recordings never leave this computer. No telemetry, no
> analytics, no account. The app makes exactly these network
> connections: model download (until cached), update check (you can
> switch it off), and the AI-polish endpoint — only if you configure
> one.

## How to re-verify (2 minutes)

1. Static: the grep above; any new hit must be added to the table or
   removed from the code.
2. Runtime: with the daemon running —
   `ss -tnp | grep "$(systemctl --user show -p MainPID --value sayit-ermano)"`.
   Expected on a default config: nothing established at idle beyond at
   most one lingering update-check socket; zero listeners.
