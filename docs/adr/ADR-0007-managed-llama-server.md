# ADR-0007: Managed llama.cpp child server for local polish

Date: 2026-11-21
Status: Accepted

## Context

The README parity table carried one open gap against upstream
FluidVoice: "no bundled local LLM yet". Users without a cloud key had
to install and configure Ollama/LM Studio by hand to get AI polish —
breaking the local-only privacy promise at its final step. The local
polish feature (#21) needs an OpenAI-compatible chat endpoint that
exists with zero user setup.

ADR-0001 ("no TCP — the Unix control socket is the only external
interface") forbids the *daemon* opening a network listener. A managed
llama.cpp server is a loopback HTTP server, so this decision needs an
explicit relationship to that ADR.

## Decision

The daemon may spawn, supervise and stop a **llama.cpp `llama-server`
child process** for AI polish while `[ai] local_polish` is enabled:

- **Loopback-only, ephemeral port**: bound to 127.0.0.1 on a
  kernel-assigned free port; the port is never persisted and changes
  per spawn.
- **On-demand lifecycle**: spawned only when a polish call is actually
  imminent (first dictation after idle), stopped after `ai.local_idle_s`
  without polish traffic (default 300 s), stopped at daemon shutdown
  and the instant the toggle turns off (zero footprint when disabled).
- **Not a control interface**: the child serves only the chat
  completion endpoint consumed by the polish client. It does not
  expose daemon state, history, dictation control, or the MCP surface.
  It is the same class of dependency as the external recorders
  (`pw-record`) and inserters (`xdotool`) the daemon already execs —
  except lifecycle-owned by us — and the same trust boundary as a user
  running `ollama serve`, which [ai] base_url has always supported
  pointing at.

This is not a supersession of ADR-0001: the daemon's own external
interfaces remain the Unix control socket and the stdio MCP bridge.
No HTTP/TCP API of the daemon itself is introduced or re-proposed.

## Consequences

- Local polish works offline with zero setup: the only setup artifact
  is the GGUF chat model download (~2.5 GB) through the existing model
  manager.
- The child's port must be treated as private runtime state (never
  logged to user-visible surfaces beyond diagnostics, never written to
  config).
- A crash or hang of the child is contained: the manager health-polls
  at spawn, fails the single take gracefully (raw transcript typed,
  same as any AI error), and re-spawns on the next take.
- Distro packaging should Recommend/depend on an llama.cpp package
  providing `llama-server`; binary discovery also honors
  `ai.local_server_binary` for manual installs.
