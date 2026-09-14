# Scripting the daemon (unix socket) and MCP

Part of the [documentation index](../README.md). The unix control
socket and the MCP bridge, for scripts and on-device agents.

The control socket (JSON lines, filesystem-scoped to your runtime dir —
no TCP by design) is a scriptable API. Beyond `toggle`/`status`/
`set-config`/`select-model`, two routes exist for on-device agents:

```python
from fluidvoice import control
# transcribe a file through the daemon's WARM model (no reload)
r = control.request("transcribe", path="/tmp/note.wav", process=True)
print(r["text"])
# query stored dictations (chronological, newest last)
h = control.request("history", limit=5, since_ts=1788800000.0)
```

```bash
echo '{"action": "transcribe", "path": "/tmp/note.wav"}' \
  | socat - UNIX-CONNECT:/run/user/$(id -u)/sayit-ermano.sock
```

`transcribe` refuses while a dictation is running (the GPU stays
dedicated to your take); long inputs are chunked automatically
(ten-minute overlapping chunks, reconciled into one transcript — see
`fluidvoice/chunking.py`), with a hard ceiling of 6 h of audio;
`process: true` runs the standard filler/punctuation chain.

**MCP agents** get the same powers over stdio — run `sayit-ermano mcp`
(register it with any MCP client, e.g. Claude Desktop:

```json
{"mcpServers": {"sayit-ermano":
    {"command": "sayit-ermano", "args": ["mcp"]}}}
```

) and the tools `transcribe_file` / `history` / `status` / `toggle` are
forwarded to the running daemon — your warm model does the work.

**Security note:** launching the MCP bridge grants the connected client
the reach of the daemon itself — it can read your **local dictation
history**, run arbitrary local files through **transcription**, and
**start/stop dictation takes** (`toggle`). Register `sayit-ermano mcp`
only with agents you actually trust; any client configured with it
inherits that access for as long as the bridge runs.
