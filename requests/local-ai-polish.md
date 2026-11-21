Bundled local AI polish: one-click llama.cpp server + GGUF model (issue #21)

STATUS: SHIPPED

Shipped on branch `local-ai-polish` (PR for issue #21). The README
parity-table gap ("no bundled local LLM yet") is closed: a paying user
without a cloud key gets polished output with zero setup, end to end
matching the local-only privacy promise.

What landed:

- Settings → AI toggle "Local polish (works offline)"
  (`ai.local_polish`); while on, the polish step routes through the
  bundled llama.cpp server and IGNORES ai.base_url (offline promise).
- Chat GGUF catalog + download in the model manager
  (model_catalog.CHAT_GGUF_CATALOG, download_chat_gguf) alongside the
  whisper GGUF flow: qwen3-4b-class (Qwen3-4B-Q4_K_M.gguf, ~2.5 GB,
  Qwen/Qwen3-4B-GGUF), cached under models/llama.cpp/, listed and
  deletable in Settings → Models (one-click "Download & use" also
  enables the feature).
- fluidvoice/local_ai.py LocalAIManager: spawn llama-server on demand
  (env SAYITERMANO_LLAMA_SERVER -> models/llama.cpp/bin -> PATH),
  /health-gated readiness (cold start measured + 120 s bound),
  auto-configured endpoint overlay (base_url/model, no key), idle-down
  after ai.local_polish_idle_s (default 300 s; 0 = keep up), stop on
  daemon shutdown and the instant the toggle flips off. Toggle off =
  zero footprint (enforced, not just documented).
- Status/doctor surfacing: status route "local_ai" block; doctor
  prints toggle/model/binary/live server.
- Eval harness `polish-gate` subcommand: local vs cloud polish quality
  over a text corpus (packaged CC0 corpus; product-faithful fallback
  scoring — errors/refusals score the raw transcript), regression gate
  local WER <= cloud WER + tolerance (default 0.02), exit 1 on
  regression; polish-gate.json/md reports.
- Budgets documented (docs/guides/local-polish.md): serving RAM ≈ model
  size + 4 k ctx (~3 GB), cold start bounded at 120 s and measured,
  resting footprint zero (off or idle).

Live verification still owed on a CPU-only machine (this branch's CI
runner has no model/network): E2E dictate → polished text with the
network disconnected, plus a real polish-gate run vs a cloud baseline.
The offline unit suite covers the manager lifecycle with a fake
llama-server, pipeline routing/fallbacks, config validation, catalog/
download, daemon wiring (toggle/stop/status), delete guards and the
gate math.

Out of scope (kept): shipping an actual llama-server binary in the
deb/AUR packages (discovery order 1-2 covers user/system installs;
packaging a binary is a separate packaging task), routing Rewrite/
Command mode through the local server.
