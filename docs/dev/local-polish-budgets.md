# Local polish resource budgets (#21)

The managed llama.cpp server (`ai.local_polish`) is a product feature
with an explicit resource contract. These budgets are **documented
here** and **enforced** where enforcement is automatable; anything
measured on real hardware is noted as such and re-measured per model
bump.

## The contract

| state | RAM | disk | processes | ports |
|---|---|---|---|---|
| toggle off | 0 | model file only (if downloaded) | 0 | 0 |
| toggle on, idle (post-timeout) | 0 | ~2.5 GB model | 0 | 0 |
| toggle on, polishing | ~2.6–3.5 GB RSS (q4 4B + 4k ctx, CPU) | ~2.5 GB model | 1 child + 1 watcher thread | 1 loopback, ephemeral |

- **Disk**: the pinned `qwen3-4b-instruct-2507-q4_k_m.gguf` is
  2,497,281,120 bytes (sha256-pinned in `CHAT_GGUF_CATALOG`;
  downloaded through the same `.part` + checksum flow as every other
  model, prunable in Settings → Models → Disk usage).
- **RAM while polishing**: Q4_K_M 4B weights (~2.5 GB) + KV/context
  overhead; measured 2.6–3.5 GB RSS for llama-server CPU builds on the
  2026-11 reference machine. GPU builds move the weights to VRAM
  automatically (no flags of ours involved).
- **Cold start**: paid ONCE per idle window — spawn + model load +
  `/health` readiness. Budget: 5–20 s on CPU (the manager's startup
  poll allows `DEFAULT_STARTUP_TIMEOUT_S = 90` for slow disks before
  failing the take honestly with the raw transcript). While the server
  is warm, per-take polish latency is the request round-trip only.
- **Idle shutdown**: `ai.local_idle_s` (default 300 s, range
  30..86400, 0 = keep until daemon exit). The idle watcher thread
  exists only after the first spawn and is reaped at stop/shutdown.
- **Toggle off = zero footprint**: no process, no listener, no watcher
  thread, no port; the polish path falls back to the stock
  `AIClient(cfg)` cloud endpoint. `apply_setting_change` stops a
  running child the instant the toggle flips.

## How each budget is enforced

- **Zero footprint (off)**:
  `tests/test_local_llm.py::TestManagerLogic::test_zero_footprint_when_disabled`
  and `...::TestManagerProcess::test_disabled_never_spawns_even_if_ready`
  assert no child and no watcher exist with the toggle off, and
  `...::test_apply_setting_change_off_stops_child` that a running child
  dies on flip-off.
- **Idle-down**: `test_idle_stop_and_respawn` (exact-threshold stop,
  FP-guard) and `test_in_flight_blocks_idle_stop` (never mid-request).
- **Cold-start bound**: `DEFAULT_STARTUP_TIMEOUT_S` caps the wait;
  `test_health_timeout_raises` proves the failure path surfaces the
  server log tail instead of hanging a take.
- **Loopback-only, ephemeral port**: the spawn args are asserted in
  `test_spawn_polish_and_flags` (`--host 127.0.0.1`, `--port
  <kernel-chosen>`); ADR-0007 records why this is not a daemon
  listener.
- **Memory while polishing** (human-measured, soak policy): use
  `scripts/soak.py` with the toggle on and dictation flowing; the
  release-notes soak for a model bump must cover a polishing session.

## What is intentionally NOT bounded here

- llama-server's own internal thread count (it autodetects cores; no
  `-t` flag is forced).
- GPU selection (a CUDA/Vulkan build advertises itself; CPU-only
  machines are the design target and the reference measurements).
