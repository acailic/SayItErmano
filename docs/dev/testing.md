# Development and testing

Part of the [documentation index](../README.md). How the repo is
checked locally: the tier model, the canonical recipes, and the
gate. Working rules (one tree per agent, merge-back) live in the
repo-root [AGENTS.md](../../AGENTS.md).

**Branch layout:** the port lives on `linux` — treat it as this fork's main
line (all commits, merges, and releases go there). The `main` branch mirrors
the upstream macOS repo for reference only and is **never** updated with port
work; to see what moved upstream, run `scripts/upstream-diff.sh`.

```bash
just test               # unit/contract tier (offline: the conftest network
                        # guard fails any non-loopback connect in-process;
                        # no model/display, tests deselected by DECLARED
                        # requirement — needs_model/needs_display/etc.)
just test-parallel      # same scope on pytest-xdist (~4-5x faster)
just test-ui            # display/GTK tier on a VIRTUAL display (Xvfb +
                        # cairo renderer + parallel workers): nothing flashes
                        # on your desktop; SAYIT_TEST_REAL_DISPLAY=1 for the
                        # live display. A skip FAILS in CI's gtk-x11 lane
just test-process       # model-free process lane: real daemon/socket/CLI
                        # subprocesses, works headless or under xvfb-run
just test-integration   # real model + mic + GPU (needs hardware + the
                        # shared venv; grabs hotkeys — coordinate first)
just gate               # clean tree + lint + brief validation + unit tier
                        # with -W error, strict markers, timeout, JUnit
just coverage           # branch coverage (baseline: docs/research/
                        # 2026-09-12-coverage-baseline.md)
```

Tier selection lives ONCE in
[scripts/test_tier.py](../../scripts/test_tier.py) (org plan 5.1): the
justfile recipes (`just test`, `just test-parallel`, `just test-ui`,
`just test-process`, `just test-integration`) and every CI lane exec
that script — run the same recipes locally that CI runs (capability
markers: `model`, `network`, `desktop`, `packaging`, `gtk`, plus
`slow` for duration only); `tests/test_tier_source.py` fails if the
expression ever gets re-inlined.

The test suite — run `just test-parallel` for the current count
(3480 offline unit/contract tests at 2026-09-14, ~20 s on this
machine; 154 display-tier and 40 integration tests behind their own
recipes):

| Layer | What it exercises |
|---|---|
| Unit + integration-style | processing engines, AI client (mocked transport), daemon + pipeline state machines (stubs), socket API, MCP server, remote-STT + refusal/countdown guards, insertion command construction, config validation + registration meta-tests, overlay/pill painting, GTK app offscreen smoke tests |
| E2E (slow) | real whisper model transcribing the JFK sample |
| Integration | real `pw-record` capture + raw→WAV, GPU transcription, streaming preview with the loaded model, a real daemon **subprocess** (socket control incl. get/set-config + select-model, toggle/cancel, clean shutdown), live X11 hotkey grab + overlay pixel proof, real CLI invocations (doctor/transcribe/history/config), live AI polish + rewrite against local Ollama (skipped when absent), .deb extract + relocated-venv import, one-shot installer DRY_RUN download |

Integration tests run against your real PipeWire/X11/CUDA environment and are
isolated through `SAYITERMANO_CONFIG` / `SAYITERMANO_SOCKET` / `XDG_DATA_HOME`
env overrides (the same overrides work for running multiple daemons).

Layout: `fluidvoice/backends/` (speech engines) · `processing/` (fillers,
dictionary, spoken punctuation) · `ai/` (prompts + OpenAI-compatible client) ·
`context/` (focused-field context seam) · coordinators (`daemon.py`,
`capture`/`command_coord`/`engine_manager`/`runtime_tasks`) · `insertion.py` +
`selection.py` (typed/paste insertion + clipboard ownership) · `hotkey.py`
(XGrabKey) · `control.py`/`control_server.py`/`mcp_server.py` (IPC) · `gtkui/`
(settings/history/onboarding windows) · `evalharness/` (corpus + eval
adapters) · `overlay.py` (pill).
