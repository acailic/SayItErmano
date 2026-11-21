Bundled local AI polish: one-click llama.cpp server + GGUF chat model (#21)

STATUS: SHIPPED

<!-- shipped in 9c9fb71 (core: local_llm manager + catalog/download +
     config + daemon/pipeline/control wiring + UI + tests) 528dd50
     (docs, doctor, ADR-0007 links, budgets doc, integration live test,
     deb Recommends); brief follow-up: budgets datapoint + shipped refs. Live-verified twice on CPU-only runners with a real
     llama.cpp llama-server + the real pinned qwen3-4b GGUF (2026-11-21):
     download_chat_model sha256 pin, 2.5-2.8 s cold spawn with a warm
     page cache, 'um lets meet on um tuesday around like three no wait
     four pm ok' -> "Let's meet on Tuesday at 4 PM." with the cloud
     switch off and loopback-only traffic, guardrail fallback intact,
     idle stop at the exact threshold, toggle-off = child gone at once,
     and polish-compare rendering a real verdict (exit 1 on a
     demonstrably degraded variant; the smoke report renders per-variant
     means + gate block). Open: none for this brief — the eval-gate
     CLOUD baseline needs a real cloud key and is a release-time manual
     run per docs/eval/README.md. -->

One-click local AI polish: a bundled-managed llama.cpp server + a small
GGUF chat model (qwen3-4b-class), so polished output works with zero setup
and zero cloud key — closing the "no bundled local LLM yet" parity gap
end to end.
Design (2026-11-21):

1) Toggle: `[ai] local_polish` (default false) — "Local polish (works
offline)". While on, dictation polish is routed to a daemon-managed
llama.cpp server (`llama-server`) spawned on demand; the user's cloud
endpoint config is never rewritten (the override is resolved per take).
Toggle off = zero footprint: no subprocess, no watcher thread, no port.

2) Model: `CHAT_GGUF_CATALOG` in model_catalog.py — one pinned entry
(qwen3-4b-instruct-2507 Q4_K_M GGUF, unsloth mirror, sha256-pinned,
~2.5 GB) stored under models_dir()/chat/. Download goes through the
existing model_download.download_file plumbing (`.part` discipline,
progress registry, cancel) plus a sha256 verify; the Models settings
page gains an "AI polish model" group with Download/Use rows and the
cache is prunable through the existing model-delete route.

3) Server lifecycle: fluidvoice/local_llm.py LocalPolishManager —
binary discovery (ai.local_server_binary > PATH > distro package
helper), ephemeral loopback port, `--alias local-polish` model name,
/health readiness poll, `ai.local_idle_s` idle shutdown (default 300 s,
mirrors model.idle_unload_s grammar), terminate on daemon shutdown or
toggle-off. Not a daemon control interface: ADR-0007 records why this
does not violate ADR-0001 (loopback, ephemeral, user-owned child
process — same class as `ollama serve`, lifecycle-owned by us).

4) Auto-config: pipeline polish path gains an `_ai_factory` seam — the
daemon injects LocalPolishManager.client_factory, which patches
base_url/model/api_key onto the cfg the AIClient builds from. All
prompt-profile/per-app/refusal-guard branches are untouched.

5) Quality gate: evalharness `polish-compare` subcommand
(fluidvoice/evalharness/polish_gate.py) — runs the corpus through a
transcriber, then scores raw vs each polish variant (local/cloud)
against the reference with the existing WER/CER/omission/hotword
metrics; report.json/md gain a polish block with per-variant means and
a regression gate (local WER must not exceed the baseline by the
configured delta; exit 1 on failure). Documented in docs/eval/README.md.

6) Budgets (documented + enforced): disk ~2.5 GB model; RAM ~2.6-3.2 GB
RSS while polishing (q4 4B + ctx); cold start = spawn + model load
(5-20 s CPU, paid once per idle window); idle shutdown default 300 s;
toggle off = zero footprint (enforced by tests/test_local_llm.py).

Out of scope: GPU-specific flags, multi-model chat catalog, rewriting
the rewrite/command AI paths (they follow [ai] as before), shipping a
compiled llama-server binary inside the Python wheel (the deb Recommends
llama.cpp; discovery handles the rest).
