"""Fake OpenAI-compatible /v1/audio/transcriptions server (tests + live smoke).

Stdlib-only threaded HTTP server on an ephemeral (or given) port. Used by
tests/test_remote_stt.py AND runnable standalone for the manual live smoke
(phase 6 of the remote-STT spec):

    .venv/bin/python tests/fake_remote_stt_server.py 8399 --verbose
    .venv/bin/python tests/fake_remote_stt_server.py --mode verbose_json

Every request is recorded on the server object (server.requests /
server.count) so tests can assert the exact multipart shape and attempt
counts. Modes change the response; the handler never sends anything back
except the transcription JSON / error it is configured for.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RESPONSES = {
    # mode -> (status, body-bytes, content-type)
    "default": (200, b'{"text": "hello remote world"}'),
    "verbose_json": (200, json.dumps({
        "text": "a b",
        "segments": [{"start": 0.0, "end": 1.0, "text": "a"},
                     {"start": 1.0, "end": 2.0, "text": "b"}],
    }).encode()),
    "missing_text": (200, b"{}"),
    "bad_json": (200, b"<html>not json</html>"),
    "http500": (500, b"upstream exploded"),
    "http401": (401, b"unauthorized"),
}


def parse_multipart(body: bytes, content_type: str) -> dict[str, dict]:
    """Minimal multipart/form-data parser for the assert-on shape tests.

    Returns {field_name: {"value": bytes, "filename": str|None}} - enough
    to check field presence, the model/language strings and the RIFF magic
    without dragging in a dependency.
    """
    boundary = None
    for part in content_type.split(";"):
        part = part.strip()
        if part.startswith("boundary="):
            boundary = part[len("boundary="):].strip('"')
    fields: dict[str, dict] = {}
    if not boundary:
        return fields
    delim = b"--" + boundary.encode()
    for chunk in body.split(delim):
        chunk = chunk.strip(b"\r\n")
        if not chunk or chunk == b"--":
            continue
        if b"\r\n\r\n" not in chunk:
            continue
        raw_headers, value = chunk.split(b"\r\n\r\n", 1)
        name = None
        filename = None
        for line in raw_headers.decode("latin-1").split("\r\n"):
            if line.lower().startswith("content-disposition:"):
                for token in line.split(";"):
                    token = token.strip()
                    if token.startswith("name="):
                        name = token[len("name="):].strip('"')
                    elif token.startswith("filename="):
                        filename = token[len("filename="):].strip('"')
        if name is not None:
            fields[name] = {"value": value, "filename": filename}
    return fields


def make_handler(mode: str, server_ref: list, verbose: bool = False):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _record(self, fields: dict) -> None:
            f = fields.get("file") or {}
            entry = {
                "path": self.path,
                "fields": sorted(fields),
                "model": (fields.get("model") or {}).get("value", b"").decode(
                    "latin-1"),
                "language": None,
                "file_len": len(f.get("value", b"")),
                "file_head": (f.get("value", b"") or b"")[:4],
                "filename": f.get("filename"),
                "authorization": self.headers.get("Authorization"),
                "content_type": self.headers.get("Content-Type", ""),
            }
            lang = fields.get("language")
            if lang is not None:
                entry["language"] = lang["value"].decode("latin-1")
            with server_ref[0].lock:
                server_ref[0].requests.append(entry)
                server_ref[0].count += 1
            if verbose:
                print(f"[fake-stt] POST {self.path} model={entry['model']!r} "
                      f"lang={entry['language']!r} file={entry['file_len']}B "
                      f"head={entry['file_head']!r} "
                      f"auth={'yes' if entry['authorization'] else 'no'}",
                      flush=True)

        def do_POST(self) -> None:  # noqa: N802 - http.server API
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            fields = parse_multipart(body, self.headers.get("Content-Type", ""))
            self._record(fields)
            if mode == "slow":
                import time
                time.sleep(5.0)
            status, payload = RESPONSES.get(mode, RESPONSES["default"])
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802 - doctor probe target
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, fmt, *args):  # keep test output quiet
            pass

    return Handler


class FakeRemoteSttServer:
    """Threaded fake with a per-request record; context-managed."""

    def __init__(self, mode: str = "default", verbose: bool = False,
                 port: int = 0):
        self.mode = mode
        self.requests: list[dict] = []
        self.count = 0
        self.lock = threading.Lock()
        ref = [self]
        self._httpd = ThreadingHTTPServer(("127.0.0.1", port),
                                          make_handler(mode, ref, verbose))
        self.port = self._httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        daemon=True)

    def set_mode(self, mode: str) -> None:
        """Swap the response mode live (error-then-success tests)."""
        self._httpd.RequestHandlerClass = make_handler(mode, [self])

    def start(self) -> "FakeRemoteSttServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> "FakeRemoteSttServer":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("port", nargs="?", type=int, default=0,
                        help="port to listen on (0 = ephemeral)")
    parser.add_argument("--mode", default="default",
                        choices=sorted(RESPONSES) + ["slow"],
                        help="response mode (default: %(default)s)")
    parser.add_argument("--verbose", action="store_true",
                        help="print one line per request")
    args = parser.parse_args(argv)

    srv = FakeRemoteSttServer(mode=args.mode, verbose=args.verbose,
                             port=args.port).start()
    print(f"fake STT server listening on {srv.url} (mode {args.mode})",
          flush=True)
    body = RESPONSES.get(args.mode, RESPONSES["default"])[1]
    try:
        print(f'transcribes to: {json.loads(body)["text"]!r}', flush=True)
    except ValueError:  # http500/http401/bad_json: no JSON payload to quote
        print(f"responds with: {body!r}", flush=True)
    print("ready-to-paste config:", flush=True)
    print(f'  [model]')
    print(f'  remote_url = "{srv.url}"')
    print("  remote_model = \"whisper-large-v3\"   # any string; echoed in the "
          "request log")
    print("Ctrl-C to stop", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        srv.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
