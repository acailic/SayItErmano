"""ControlServer: responsive, bounded Unix-socket control channel (P0.2).

The previous single-connection loop served ONE client at a time, so a long
`transcribe` blocked every `status`/read behind it. This module keeps the
wire protocol byte-identical (one JSON object per line in, one JSON object
per line out, then close) while serving clients from a fixed pool:

- one accept thread: accept + enqueue only (never blocks on a handler)
- ``WORKERS`` daemon threads: read/dispatch/respond, one connection each
- hard bounds: 1 MiB request line, 16 MiB response, ten-second idle read,
  socket mode 0600, and a deterministic ``shutdown()`` that joins every
  thread (no leftovers).

The daemon's single-transcription ``busy`` guarantee is untouched: busy
arbitration stays in the handler; the server merely stops serializing
unrelated actions behind a long one.
"""
from __future__ import annotations

import errno
import json
import os
import queue
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Callable

MAX_REQUEST_BYTES = 1024 * 1024        # request line cap: 1 MiB
MAX_RESPONSE_BYTES = 16 * 1024 * 1024  # response cap: 16 MiB
IDLE_TIMEOUT_S = 10.0                  # per-client first-line read deadline
SOCKET_MODE = 0o600                    # owner-only control channel
WORKERS = 8
_BACKLOG = 128                         # kernel accept queue (was 8)
_DRAIN_CAP = 4 * 1024 * 1024           # discard cap after an oversized line
_DRAIN_TIMEOUT_S = 2.0                 # bound the discard loop
_CHUNK = 65536
_ACCEPT_RETRY_S = 0.05                 # backoff after a transient accept error
_QUEUE_PER_WORKER = 4                  # accept-queue bound (F8: 8 workers -> 32)


def _log(msg: str) -> None:
    """House log idiom (see pipeline.log): stderr, timestamped, quiet -
    only exceptional control-server paths ever log (F11)."""
    print(f"[sayit-ermano] {time.strftime('%H:%M:%S')} {msg}",
          file=sys.stderr, flush=True)


class ControlError(RuntimeError):
    pass


class _LineTooLarge(Exception):
    """The request line exceeded the limit before a newline arrived."""


class _RequestTimeout(Exception):
    """The TOTAL first-line read budget expired (F8: the idle deadline is
    per REQUEST, not per recv - a client dripping one byte every 9 s beat
    the old per-recv 10 s timeout forever). `partial` distinguishes a
    client that never sent anything (clean close, like the old idle
    timeout) from one that dribbled partial data (structured error)."""

    def __init__(self, partial: bool):
        super().__init__(f"request read budget expired (partial={partial})")
        self.partial = partial


def probe_live(path: Path, timeout: float = 1.0) -> bool:
    """True when a daemon ANSWERS at path (read-only status probe)."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(path))
            s.sendall(b'{"action": "status"}\n')
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(_CHUNK)
                if not chunk:
                    break
                buf += chunk
        return buf.strip().startswith(b"{")
    except OSError:
        return False


class ControlServer:
    """One accept thread plus a fixed worker pool over a Unix socket."""

    def __init__(self, handler: Callable[[dict], dict], path: Path,
                 *, workers: int = WORKERS,
                 request_limit: int = MAX_REQUEST_BYTES,
                 response_limit: int = MAX_RESPONSE_BYTES,
                 idle_timeout: float = IDLE_TIMEOUT_S) -> None:
        self._handler = handler
        self.path = Path(path)
        self._worker_count = max(1, int(workers))
        self._request_limit = int(request_limit)
        self._response_limit = int(response_limit)
        self._idle_timeout = float(idle_timeout)
        self._stopping = threading.Event()
        # Bounded (F8): an unbounded queue let misbehaving same-user
        # clients pile up connections (each holding an fd) until EMFILE
        # took out the accept thread outright.
        self._connections: queue.Queue = queue.Queue(
            maxsize=self._worker_count * _QUEUE_PER_WORKER)
        self._accept_thread: threading.Thread | None = None
        self._workers: list[threading.Thread] = []
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and probe_live(self.path):
            # never steal a socket a LIVE daemon is answering: sandboxed
            # second instances (isolated XDG_CONFIG_HOME, shared runtime
            # dir) would otherwise unlink the production daemon's control
            # channel
            raise ControlError(
                f"another sayit-ermano daemon is answering at {self.path} - "
                "refusing to steal its control socket (point "
                "SAYITERMANO_SOCKET elsewhere for a second instance)")
        self.path.unlink(missing_ok=True)
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(str(self.path))
        os.chmod(self.path, SOCKET_MODE)
        self._srv.listen(_BACKLOG)
        # identity of OUR socket file: shutdown only unlinks a path that
        # still points at this server (a replacement may have rebound it)
        self._sock_ino = os.stat(self.path).st_ino
        # unlink happens at most once: a REPEATED shutdown must never
        # touch the path again - a replacement binding the freed path can
        # even reuse our just-freed inode number, defeating the inode
        # check (F5)
        self._unlinked = False

    # -- lifecycle -----------------------------------------------------------

    def start(self, ready: threading.Event | None = None) -> ControlServer:
        """Spawn the accept thread and the worker pool."""
        self._accept_thread = threading.Thread(
            target=self._accept_loop, name="fluidvoice-control-accept",
            daemon=True)
        self._accept_thread.start()
        self._workers = [
            threading.Thread(target=self._worker_loop,
                             name=f"fluidvoice-control-worker-{i}",
                             daemon=True)
            for i in range(self._worker_count)]
        for t in self._workers:
            t.start()
        if ready is not None:
            ready.set()
        return self

    def shutdown(self, timeout: float = 10.0) -> None:
        """Deterministic shutdown: stop accepting, drop queued connections,
        let in-flight requests finish, and join every thread. Idempotent."""
        self._stopping.set()
        try:
            # wake a thread blocked in accept(): close() alone does NOT
            # unblock it on Linux; shutdown() does
            self._srv.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._srv.close()
        except OSError:
            pass
        self._unlink_owned_path()
        # queued-but-unserved connections close immediately; workers wake
        # from their blocking get() via one sentinel each
        while True:
            try:
                conn = self._connections.get_nowait()
            except queue.Empty:
                break
            if conn is not None:
                _close_quietly(conn)
        for _ in self._workers:
            self._connections.put(None)
        current = threading.current_thread()
        if self._accept_thread is not None \
                and self._accept_thread is not current:
            self._accept_thread.join(timeout=timeout)
        for t in self._workers:
            if t is not current:
                t.join(timeout=timeout)
        self._workers = []
        self._accept_thread = None

    def close(self) -> None:
        """Socket-compatible alias: the old serve() returned a raw socket
        whose close() callers expect to end the server."""
        self.shutdown()

    def _unlink_owned_path(self) -> None:
        if self._unlinked:
            return  # ours is already gone: the path belongs to someone else
        self._unlinked = True
        try:
            if os.stat(self.path).st_ino == self._sock_ino:
                self.path.unlink()
        except OSError:
            pass

    # -- threads -------------------------------------------------------------

    def _accept_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                conn, _ = self._srv.accept()
            except OSError as e:
                if self._stopping.is_set():
                    break  # our shutdown() closed the listening socket
                if e.errno in (errno.EBADF, errno.EINVAL):
                    # listening socket gone while we were NOT stopping:
                    # unrecoverable - exit loudly, never silently
                    _log(f"control accept: listening socket closed "
                         f"({e!r}) - accept thread exiting")
                    break
                # transient (EMFILE, ECONNABORTED, ENOMEM, ...): the
                # thread must survive it or the control plane is bricked
                # with the socket still bound and nobody accepting (F4).
                # Back off briefly so EMFILE/ENOMEM cannot hot-loop.
                _log(f"control accept: transient {e!r} - retrying in "
                     f"{_ACCEPT_RETRY_S:g}s")
                time.sleep(_ACCEPT_RETRY_S)
                continue
            try:
                conn.settimeout(self._idle_timeout)
            except OSError:
                _close_quietly(conn)
                continue
            try:
                self._connections.put_nowait(conn)
            except queue.Full:
                # saturated (F8): shed load with a structured error
                # instead of queueing without bound
                _log("control accept: queue saturated - rejecting client")
                self._send(conn, {"ok": False,
                                  "error": "server busy - retry shortly"})
                _close_quietly(conn)

    def _worker_loop(self) -> None:
        while True:
            conn = self._connections.get()
            if conn is None or self._stopping.is_set():
                if conn is not None:
                    _close_quietly(conn)
                break
            try:
                self._serve_connection(conn)
            except Exception:  # noqa: BLE001 - a dead worker is a capacity leak
                _close_quietly(conn)

    # -- one connection ------------------------------------------------------

    def _serve_connection(self, conn: socket.socket) -> None:
        with conn:
            try:
                line = self._read_request_line(conn)
            except _LineTooLarge:
                self._send(conn, {
                    "ok": False,
                    "error": f"request too large (limit is "
                             f"{self._request_limit} bytes)"})
                self._drain(conn)  # discard the rest so close() cannot RST
                return             # the error reply out from under the client
            except _RequestTimeout as e:
                if e.partial:
                    # a real request dribbled past the total read budget:
                    # answer with the structured timeout error (F8)
                    self._send(conn, {
                        "ok": False,
                        "error": f"request read timed out "
                                 f"(budget is {self._idle_timeout:g}s)"})
                return  # nothing received at all: idle client, clean close
            except OSError:
                return
            if line is None:
                return  # connected and left (or blank line): clean close
            conn.settimeout(self._idle_timeout)  # sane budget for the reply
            self._send(conn, self._dispatch(line))

    def _read_request_line(self, conn: socket.socket) -> bytes | None:
        """Read up to the first newline, bounded by the request limit and
        by an OVERALL deadline: idle_timeout is the budget for the whole
        request line, not for each individual recv (F8).

        Returns the raw line (possibly empty trailing bytes past the
        newline are ignored, as before), or None on EOF/blank input.
        Raises _LineTooLarge once the line passes the limit with no
        newline, and _RequestTimeout when the budget expires."""
        deadline = time.monotonic() + self._idle_timeout
        buf = b""
        overflow = False
        expired = False
        while b"\n" not in buf and not overflow:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                expired = True
                break
            conn.settimeout(remaining)
            try:
                chunk = conn.recv(_CHUNK)
            except socket.timeout:
                expired = True
                break
            if not chunk:
                break
            buf += chunk
            # bytes before the first newline - the request line - must
            # stay under the limit no matter where chunk boundaries fall
            if len(buf.split(b"\n", 1)[0]) > self._request_limit:
                overflow = True
        if overflow:
            raise _LineTooLarge
        if expired:
            raise _RequestTimeout(partial=bool(buf))
        return buf if buf.strip() else None

    def _dispatch(self, raw: bytes) -> dict:
        try:
            req = json.loads(raw.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"ok": False, "error": "invalid JSON"}
        if not isinstance(req, dict):
            return {"ok": False,
                    "error": "request must be a JSON object"}
        if not isinstance(req.get("action"), str) or not req["action"]:
            return {"ok": False,
                    "error": "missing or invalid action"}
        try:
            return dict(self._handler(req))
        except Exception as e:  # noqa: BLE001 - handler errors are payloads
            return {"ok": False, "error": str(e)}

    def _send(self, conn: socket.socket, resp: dict) -> None:
        try:
            conn.sendall(self._encode_response(resp))
        except OSError:
            pass  # client hung up mid-reply: nothing left to do

    def _encode_response(self, resp: dict) -> bytes:
        try:
            data = json.dumps(resp).encode() + b"\n"
        except (TypeError, ValueError):
            data = json.dumps({"ok": False,
                               "error": "response was not serializable"}
                              ).encode() + b"\n"
        if len(data) > self._response_limit:
            data = json.dumps({"ok": False,
                               "error": f"response too large ({len(data)} "
                                        f"bytes; limit is "
                                        f"{self._response_limit}) - narrow "
                                        "the request (e.g. a smaller "
                                        "history limit)"}).encode() + b"\n"
        return data

    def _drain(self, conn: socket.socket) -> None:
        """Discard the tail of an oversized request so closing the socket
        cannot reset the connection before the client reads our error."""
        deadline = time.monotonic() + _DRAIN_TIMEOUT_S
        discarded = 0
        while discarded < _DRAIN_CAP and time.monotonic() < deadline:
            try:
                chunk = conn.recv(_CHUNK)
            except OSError:  # includes the idle timeout
                return
            if not chunk or b"\n" in chunk:
                return
            discarded += len(chunk)


def _close_quietly(conn: socket.socket) -> None:
    try:
        conn.close()
    except OSError:
        pass
