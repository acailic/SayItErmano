# Remote STT server (optional)

Part of the [documentation index](../README.md).

Nothing leaves your machine unless you point SayItErmano at a URL you
choose — the remote backend is **off by default** and LAN-friendly. When
`remote_url` is set, each dictation POSTs the recorded WAV (the same
16 kHz mono audio the local backends decode) as multipart to
`<url>/v1/audio/transcriptions` on **any OpenAI-compatible server** and
types back the returned text:

```toml
[model]
remote_url  = "http://192.168.1.50:8000"  # empty = local models only
remote_model = "whisper-large-v3"          # model name sent with the request
# remote_api_key = ""                      # optional bearer token (masked, never logged)
# remote_timeout_s = 30
```

Server examples:

- **vLLM**: `vllm serve openai/whisper-large-v3 --port 8000` →
  `remote_url = "http://<lan-host>:8000"`, `remote_model = "whisper-large-v3"`
- **whisper.cpp**: `whisper-server -m models/ggml-large-v3.bin --port 8080`
  (its OpenAI-compatible route)
- anything else speaking `POST /v1/audio/transcriptions` works: NVIDIA NIM,
  DGX Spark, Groq/OpenAI cloud (set `remote_api_key` there)

`sayit-ermano doctor` shows the endpoint and probes reachability. To go
back to local models, clear `remote_url` (Settings → Models → Remote
sends the empty value — that means "off") or click **Use** on a local
model, which clears it for you. Notes: remote takes have **no live
preview and no VAD auto-stop** (one batch decode per take); transient
failures (connection refused, 429/5xx) are retried once, other HTTP
errors are not; a saved API key is removed by editing `config.toml`
(same limitation as `ai.api_key`).
