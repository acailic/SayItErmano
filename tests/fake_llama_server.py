#!/usr/bin/env python3
"""Fake llama.cpp server (tests + live smoke for local polish, #21).

Stdlib-only threaded HTTP server speaking the subset of the
llama-server surface ``LocalPolishManager`` uses:

* ``GET /health``            -> 200 ``{"status": "ok"}``
* ``POST /v1/chat/completions`` -> OpenAI-shaped chat reply

Flag surface mirrors llama-server: ``--host H --port P -m MODEL
--alias NAME -c N`` (unknown flags are ignored, like the tolerant
arg parsing real servers do for this subset).

Modes (env ``FAKE_LLAMA_MODE``) change behavior for failure-path tests:

* ``ok``       normal; the reply deterministically "polishes" the last
               user message (drops um/uh fillers, capitalizes, adds the
               final period) so tests can assert real text flow
* ``echo``     reply = the exact last user message (no transformation)
* ``die``      exit(1) before binding (spawn-failure path)
* ``hang``     sleep without binding (health-poll timeout path)
* ``slowhealth`` serve /health only after ~1.5 s (startup polling)

Every request is appended (JSON line) to the file named by env
``FAKE_LLAMA_LOG`` — argv is recorded there at startup — so tests can
assert the exact flags and request bodies without any other channel.

Standalone live smoke (manual, mirrors fake_remote_stt_server):

    python3 tests/fake_llama_server.py --port 8417
    # then in another shell:
    #   [ai] local_polish via LocalPolishManager, or curl:
    curl -s http://127.0.0.1:8417/v1/chat/completions \\
      -d '{"model":"local-polish","messages":[{"role":"user","content":"um hello"}]}'
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FILLERS = {"um", "uh", "erm", "hmm"}


def transcript_part(message: str) -> str:
    """The transcript tail of a rendered polish user message (the dictation
    prompt is prepended, blank-line separated — see
    ai.prompts.render_dictation_user_message)."""
    return message.rsplit("\n\n", 1)[-1] if "\n\n" in message else message


def polish_like(text: str) -> str:
    """The deterministic 'polish' the fake model applies (ok mode):
    drop filler tokens, collapse spaces, sentence-case, terminal period."""
    words = [w for w in text.split() if w.strip().lower() not in FILLERS]
    out = " ".join(words).strip()
    if not out:
        return out
    out = out[0].upper() + out[1:]
    if not out.endswith((".", "!", "?")):
        out += "."
    return out


def parse_llama_flags(argv: list[str]) -> dict:
    """The llama-server flag subset: --host/--port/-m/--alias/-c values;
    anything unknown (with or without a value) is ignored."""
    out: dict = {}
    i = 0
    value_flags = {"--host": "host", "--port": "port", "-m": "model",
                   "--alias": "alias", "-c": "ctx", "--threads": "threads"}
    while i < len(argv):
        flag = argv[i]
        key = value_flags.get(flag)
        if key is not None and i + 1 < len(argv):
            out[key] = argv[i + 1]
            i += 2
            continue
        i += 1
    return out


def _record(entry: dict) -> None:
    path = os.environ.get("FAKE_LLAMA_LOG")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass  # a closed log must never kill the fake server


class _State:
    ready_at: float | None = None  # slowhealth: when /health starts serving


def make_handler(mode: str, alias: str, state: "_State"):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _maybe_delay_health(self) -> bool:
            if state.ready_at is not None and \
                    time.monotonic() < state.ready_at:
                return False  # not serving yet (slowhealth)
            return True

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            if self.path == "/health" and self._maybe_delay_health():
                body = json.dumps({"status": "ok"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802 - http.server API
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                req = json.loads(raw.decode())
            except ValueError:
                req = {}
            messages = req.get("messages") or []
            last_user = ""
            for m in reversed(messages):
                if isinstance(m, dict) and m.get("role") == "user":
                    last_user = str(m.get("content", ""))
                    break
            _record({"path": self.path, "body": req,
                     "authorization": self.headers.get("Authorization")})
            reply = last_user if mode == "echo" \
                else polish_like(transcript_part(last_user))
            payload = json.dumps({
                "id": "fake", "object": "chat.completion",
                "model": req.get("model") or alias,
                "choices": [{"index": 0,
                             "message": {"role": "assistant",
                                         "content": reply},
                             "finish_reason": "stop"}],
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, fmt, *args):  # keep test output quiet
            pass

    return Handler


class FakeLlamaServer:
    """Threaded fake with request recording; context-managed. Tests that
    need a real subprocess use the script entrypoint instead."""

    def __init__(self, mode: str = "ok", port: int = 0):
        self.mode = mode
        self.alias = "local-polish"
        self.state = _State()
        self._httpd = ThreadingHTTPServer(
            ("127.0.0.1", port), make_handler(mode, self.alias, self.state))
        self.port = self._httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        daemon=True)

    def start(self) -> "FakeLlamaServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> "FakeLlamaServer":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="fake llama-server for SayItErmano local-polish tests")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("-m", "--model", default="model.gguf")
    parser.add_argument("--alias", default="local-polish")
    parser.add_argument("-c", type=int, default=4096)
    args, _unknown = parser.parse_known_args(argv)

    mode = os.environ.get("FAKE_LLAMA_MODE", "ok")
    if mode == "die":
        print("fake llama-server: dying on start (FAKE_LLAMA_MODE=die)",
              file=sys.stderr, flush=True)
        return 1
    if mode == "hang":
        print("fake llama-server: hanging without binding "
              "(FAKE_LLAMA_MODE=hang)", file=sys.stderr, flush=True)
        time.sleep(3600)
        return 0

    state = _State()
    if mode == "slowhealth":
        state.ready_at = time.monotonic() + 1.5
    httpd = ThreadingHTTPServer((args.host, args.port),
                                make_handler(mode, args.alias, state))
    port = httpd.server_address[1]
    _record({"argv": sys.argv[1:], "mode": mode, "port": port,
             "pid": os.getpid()})
    print(f"fake llama-server listening on http://{args.host}:{port} "
          f"(mode {mode}, alias {args.alias}, model {args.model})",
          flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
