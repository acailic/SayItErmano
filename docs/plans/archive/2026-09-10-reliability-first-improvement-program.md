# FluidVoiceLinux Reliability-First Improvement Program

## Summary

The committed baseline is feature-rich and well tested, but release safety and runtime concurrency lag behind its pace of change.

- `HEAD` passed 1,784 tests in 98.5 seconds serial and 28.2 seconds parallel, but emitted unhandled thread exceptions.
- Confirmed defects:
  - A long control-socket transcription blocks status, UI, and other requests.
  - A concurrent history edit can overwrite and lose a daemon append.
  - MCP responses omit the mandatory `jsonrpc: "2.0"` member required by the [MCP base protocol](https://modelcontextprotocol.io/specification/2024-11-05/basic/messages) and [JSON-RPC 2.0](https://www.jsonrpc.org/specification).
  - The published deb embeds Python 3.12 packages while declaring compatibility with Python 3.11+, and the AUR recipe repackages that host-dependent venv.
- Repository friction:
  - Bare `pytest` collects agent-factory scripts and fails.
  - Ruff reports 1 production and 109 test/script issues.
  - Five releases through [v0.8.1](https://github.com/acailic/SayItErmano/releases/tag/v0.8.1) followed the [last successful CI run](https://github.com/acailic/SayItErmano/actions/runs/33825252644).
  - Configuration, roadmap, and protocol decisions are duplicated or contradictory.
- The architecture program follows the deletion test: deepen modules where deleting them would spread complexity across many callers, improving leverage, locality, and testability.

## Prioritized Implementation

### P0 — v0.8.2 reliability release: Strong recommendation

1. **Finish the active hallucination-guard work**
   - Leave the current owning session and its worktree untouched.
   - Resolve its preview symbol/import failure, backend segment compatibility, retry heuristic tests, and false-positive cases.
   - Do not begin the following refactors until that work is committed and the complete offline suite is green without warnings.

2. **Make the control socket responsive and bounded**
   - Replace the single connection loop with a `ControlServer` module using one accept thread and a fixed eight-worker pool.
   - Preserve every existing synchronous JSON request and response shape.
   - Allow status/read requests while a long transcription runs; retain the daemon's single-transcription `busy` guarantee.
   - Enforce a 1 MiB request limit, 16 MiB response limit, ten-second idle-read timeout, socket mode `0600`, and deterministic worker shutdown.
   - Reject non-object JSON, missing/invalid actions, oversized lines, and oversized history responses with structured errors.

3. **Make history operations transactional**
   - Introduce a deep `HistoryStore` interface while retaining the existing module-level functions as compatibility wrappers.
   - Keep JSONL; do not migrate to SQLite.
   - Use a sidecar `flock`: shared locks for reads and exclusive locks covering complete append/read-modify-replace transactions.
   - Write through unique same-directory temporary files, flush and `fsync`, atomically replace, then sync the directory.
   - Give retained audio collision-proof names, roll back copied audio if the history append fails, and prune orphaned audio safely.
   - Preserve the current row schema and timestamp-based callers in this release.

4. **Close lifecycle and protocol defects**
   - Track and cancel the first-PCM timer on stop, cancel, and shutdown; callbacks must validate recorder identity through a safe interface.
   - Make unhandled thread exceptions test failures and silence expected test-server disconnect noise.
   - Make every MCP response valid JSON-RPC 2.0, validate request/version/method/params, negotiate only supported MCP protocol versions, and add Inspector-compatible handshake tests.
   - Document that launching the MCP bridge grants the client access to local history, transcription files, and dictation control.

5. **Repair developer and release gates**
   - Set pytest's default `testpaths` to `tests`, so bare `pytest` never collects `adws/`.
   - Keep the factory, but add clearly named application `just` recipes for lint, unit tests, parallel tests, and the complete local gate; retain factory recipes under their own namespace.
   - Move Ruff configuration to the current `lint` section, clean all 110 findings, and add Ruff plus pytest-xdist to the development extra.
   - Keep GitHub Actions manual-only as requested. Split release handling into:
     - `prepare`: clean-tree check, version update, lint/tests, locked package build, commit, push.
     - `publish`: require a manually dispatched successful CI run for that exact commit before tagging or uploading.
   - Fail releases on warnings, dirty files, version disagreement, stale constraints, or a mismatched CI SHA.

6. **Correct packaging and documentation**
   - Define the deb as Ubuntu 24.04/x86_64/Python 3.12 only, with matching package dependencies and prominent install documentation.
   - Build it in a pinned Ubuntu 24.04 environment using a reviewed, hash-locked dependency set; pipx remains the any-distro Python 3.11+ route.
   - Replace the unpublished `-bin` AUR recipe with a native PEP 517 build against Arch's current Python. Its main speech dependency is available as [python-faster-whisper](https://aur.archlinux.org/packages/python-faster-whisper).
   - Fix the README's duplicate `[general]` table and misplaced `hotwords` example.
   - Remove the stale local-HTTP roadmap item and make the no-TCP decision canonical.

### P1 — v0.9 architecture and measurement: Strong recommendation

1. **Deepen the speech-backend seam**
   - Define `SpeechBackend`, `BackendCapabilities`, `Transcript`, and `TranscriptSegment`.
   - Replace duck-typed flags and result dictionaries with explicit capabilities for language selection/detection, hotwords, streaming, segments, confidence signals, and alternatives.
   - Adapt all five backends at the seam; serialize typed transcripts only at CLI/control/MCP edges.
   - Add one shared adapter-contract suite.

2. **Decompose daemon ownership**
   - `SpeechEngineManager`: load, warm, reload, idle-unload, select, delete, language resolution, and engine status.
   - `CaptureCoordinator`: recording state, preview, VAD, PCM/stall/max-duration timers, media pause, stop, and cancel.
   - `CommandCoordinator`: command conversation, proposal, confirmation, timeout, panel, and history.
   - `RuntimeTasks`: named threads/timers, cancellation, deadlines, exception reporting, and shutdown joins.
   - Keep `Daemon` as the composition root and control router; callers and tests use the same module interfaces.

3. **Create one configuration registry**
   - Introduce `SettingSpec(section, key, default, coercer, persistence, apply_mode, secret, description)`.
   - Derive defaults, allowed keys, save keys, restart/reload sets, masking, and the commented template from this registry.
   - Keep the TOML layout and public dictionary returned by `load_config`.
   - Keep hand-built GTK layouts; widgets query the registry for defaults and validation instead of duplicating policy.

4. **Add a local evaluation harness**
   - Manifest fields: case ID, audio, reference text, language, tags, expected guard outcome, and license/source.
   - Report WER, CER, hotword recall, real-time factor, first-preview latency, final latency, and guard false positives/negatives as JSON and Markdown.
   - Commit only redistributable fixtures; allow private corpora through an external path.
   - Unit-test metric math in CI and require a manual representative-model evaluation before speech-pipeline releases.
   - Integrate existing soak measurement: two hours before major releases and a documented 24-hour run for lifecycle changes.

5. **Consolidate project knowledge**
   - Add a domain glossary covering take, preview, transcript, polish, insertion, history entry, command proposal, and speech backend.
   - Record ADRs for no TCP, locked JSONL history, runtime task ownership, and the Ubuntu deb contract.
   - Standardize every request brief with `OPEN`, `SHIPPED`, or `SUPERSEDED` status and validate those headers automatically.
   - Reduce ROADMAP to future work; STATUS to shipped behavior; ADRs to decisions; research documents to evidence.

### P2 — v0.10 contextual dictation: Worth exploring after P0/P1

- Add a `ContextProvider` seam with existing X11 and new AT-SPI adapters returning app identity, accessible role, selection, and bounded preceding text.
- Read context only for the focused field immediately before insertion; never persist surrounding text.
- Introduce per-app behavior profiles covering prompt profile, insertion mode, formatting mode, and spoken-send policy.
- Migrate legacy per-app prompts and terminal lists into canonical profiles on the first settings save; continue reading legacy keys until v1.0.
- Use context for sentence capitalization, spacing, GAAV continuous dictation, terminal safety, and Wayland app hints. Preserve existing behavior whenever accessibility data is missing.
- Complete live smoke matrices on GNOME Wayland and sway before declaring Wayland parity.

### P3 — capability backlog

- **Chunked file transcription — next product priority:** convert once, process ten-minute chunks with overlap, reconcile timestamps, and retain the current CLI/socket output shape. This removes the 25 MB warning path and safely raises the daemon's practical file limit.
- **Diarization:** run a separate adapter/license/resource benchmark; ship only if an offline adapter meets accuracy, redistribution, and memory criteria. Add optional `speaker` fields to transcript segments without changing plain-text output.
- **True streaming:** add a streaming interface only after selecting a real Parakeet/Nemotron adapter; do not create a hypothetical seam around segmented batch preview.
- **n-best correction:** remain blocked until at least two backend adapters expose genuine alternatives; then add optional transcript alternatives and a lightweight history correction picker.
- **Model variants:** benchmark Parakeet fp16/fp32 against int8 through the evaluation harness before adding catalog choices.
- **Platform polish:** system/light overlay theme, wlroots layer-shell preview, native Nix flake, and clean-chroot AUR verification.
- **Explicitly deferred:** local HTTP/TCP API, telemetry, macOS support, and closed-source intelligence models.

## Public Interfaces and Migration

- The Unix-socket protocol remains synchronous and backward compatible; limits and structured validation errors are the only behavioral additions.
- MCP retains `transcribe_file`, `history`, `status`, and `toggle`, but responses become standards-compliant JSON-RPC 2.0.
- History remains JSONL in the same location and shape; locking requires no user migration.
- Backend result and capability types are internal until serialized at the existing CLI/socket/MCP edges.
- Existing TOML keys continue to work. Per-app behavior profiles are additive, with legacy reads retained through v1.0.
- The deb's public compatibility statement changes to Ubuntu 24.04/x86_64; pipx is the supported cross-distro installation path.

## Test and Release Plan

- **Control:** prove a two-second transcription does not delay `status` beyond 250 ms; verify concurrent transcriptions still yield one active job, limits are enforced, permissions are `0600`, and shutdown leaves no workers.
- **History:** exercise append-versus-edit, append-versus-trim, two-process mutation, crash residue, audio rollback, corrupt rows, and cap enforcement without lost entries.
- **Lifecycle:** fail on every unhandled thread exception; verify timers cannot fire after cancel/shutdown and every supervised task reaches a terminal state.
- **Backend seam:** run the same transcript/capability contract tests against all five adapters, including missing optional fields and serialization round trips.
- **MCP:** run official JSON-RPC examples, invalid requests, unsupported versions, notifications, and an MCP Inspector smoke test.
- **Configuration:** prove every registered setting derives its default, validation, persistence, masking, template entry, and apply mode from one specification.
- **Packaging:** build from the locked Ubuntu container; install and launch in a clean Ubuntu 24.04 VM/container; verify incompatible Python minors fail through package dependencies; build the AUR package in a clean Arch chroot; test pipx on Python 3.11–3.13.
- **Release gate:** clean worktree, Ruff clean, no pytest warnings, full offline suite, selected integration tests, package smoke, current evaluation report, and successful manually dispatched CI for the exact release SHA.

## Assumptions and Defaults

- Reliability precedes architecture, which precedes major features.
- Manual-only GitHub Actions and manual publishing remain project policy.
- The agent factory remains in this repository but is isolated from application tests and dependencies.
- JSONL, no TCP, privacy-first/no telemetry, and Linux-only decisions remain in force.
- The deb targets Ubuntu 24.04; pipx and a native source-built AUR package cover other distributions.
- Existing user configuration, history, CLI behavior, and control clients remain compatible throughout P0 and P1.
