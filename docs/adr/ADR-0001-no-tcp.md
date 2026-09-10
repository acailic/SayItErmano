# ADR-0001: No TCP — the Unix control socket is the only external interface

Date: 2026-09-10
Status: Accepted

## Context

The daemon needs a programmatic surface for its own CLI, the GTK app,
scripting, and agent clients. Upstream FluidVoice ships an OpenAI-style
loopback HTTP server (discussion #715), and early releases of this port
carried a localhost HTTP web UI — a CSRF/DNS-rebinding surface that had
to be treated as an attack path. Users also ask for agent access to the
STT engine (upstream #927). The reliability-first program (P0.6)
required the no-TCP decision to become canonical instead of a roadmap
footnote.

## Decision

The daemon never opens a network listener. Its only external interfaces
are:

1. the **user-owned Unix control socket** (mode 0600, one JSON object
   per line) served by `fluidvoice/control_server.py` /
   `fluidvoice/control.py` — trust boundary = filesystem permissions; and
2. the **stdio MCP bridge** (`fluidvoice/mcp_server.py`), which forwards
   tool calls to the running daemon over that same socket.

Evidence: the web UI and its `[server]` section were deleted when the
native GTK app replaced it (1ce8734); the plan's Assumptions list
"JSONL, no TCP, privacy-first/no telemetry, Linux-only" as in force;
ROADMAP carries the decision under Non-goals ("decided, not backlog");
commit d3b0c58 made it canonical in docs. The scriptable socket routes
(`transcribe`, `history`) deliver the loopback-API parity without the
listener (25fd209). The remote STT backend (`model.remote_url`) is an
outbound HTTP client, not a listener, and is explicitly exempt.

## Consequences

- No remote clients: cross-machine use means SSH/socket forwarding or
  the outbound remote-STT backend — acceptable for a personal,
  privacy-first desktop tool.
- The CSRF/DNS-rebinding surface is gone by construction (recorded in
  STATUS.md when the web UI retired).
- The socket must therefore be robust on its own: bounded, responsive,
  0600 — hardened by P0.2 (e9a5364: worker pool, size/idle limits).
- Launching the MCP bridge grants its client local-history and
  dictation control; that trust is documented in mcp_server.py and the
  README (0f1ff64) rather than weakened with a network ACL.
- Do not re-propose local HTTP/TCP APIs; changing this decision requires
  superseding this ADR.
