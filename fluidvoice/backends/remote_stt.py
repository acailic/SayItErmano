"""Remote OpenAI-compatible STT backend (config-gated, local-first).

POSTs the recorded WAV as multipart/form-data to an endpoint normalized
like ai/client.py (``<url>/v1/audio/transcriptions`` unless the URL
already ends with ``/audio/transcriptions``). Response is the plain
OpenAI JSON ``{"text": ...}``; ``verbose_json`` segments are concatenated
when ``text`` is absent. stdlib urllib only - no new dependency, the
retry table mirrors fluidvoice/ai/client.py (2 attempts total, retry only
transport errors and 429/5xx, 0.2s backoff, ≤300-char body excerpts in
error messages - never the API key).

Nothing leaves the machine unless model.remote_url is set; an empty URL
means this backend never constructs.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import effective_language

# mirrors AIClient: retried statuses only (config 4xx never self-heal)
_RETRYABLE_HTTP = {429, 500, 502, 503, 504}
_ATTEMPTS = 2  # one retry on transient failures, never more


class RemoteSttError(RuntimeError):
    """User-facing failure (message is key-free; same UX as local
    decode failures through the pipeline's exception path)."""


def _endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/audio/transcriptions"):
        return base
    return base + "/v1/audio/transcriptions"


def _host_of(url: str) -> str:
    return urlparse(url).netloc or url


class RemoteSttBackend:
    name = "remote"
    # wrong-language guard: the plain-JSON endpoint does not surface the
    # detected language, so the pipeline gate skips us by construction
    # (see backends.LANGUAGE_GUARD["remote"]).

    def __init__(self, cfg: dict):
        m = cfg["model"]
        url = str(m.get("remote_url") or "").strip()
        if not url:
            raise RuntimeError(
                "model.remote_url is required for the remote backend "
                '(set it under [model], or leave backend on "auto" for '
                "local models)")
        self.remote_url = url
        self.endpoint = _endpoint(url)
        self.remote_model = str(m.get("remote_model") or "").strip() \
            or "whisper-large-v3"
        self.api_key = str(m.get("remote_api_key") or "")
        try:
            self.timeout = float(m.get("remote_timeout_s", 30) or 30)
        except (TypeError, ValueError):
            self.timeout = 30.0
        # identity for model.languages overrides + doctor's active-model
        # line (backend_model_key reads model_name first)
        self.model_name = self.remote_model
        self.language = effective_language(cfg) or "auto"

    # -- wire format ----------------------------------------------------------

    @staticmethod
    def _multipart(fields: dict[str, tuple[str, str, bytes]]) \
            -> tuple[bytes, str]:
        """fields: name -> (filename, content_type, payload). Returns
        (body, Content-Type header). Hand-rolled - no dependency."""
        import uuid
        boundary = uuid.uuid4().hex
        parts: list[bytes] = []
        for name, (filename, ctype, payload) in fields.items():
            disp = f'form-data; name="{name}"'
            if filename is not None:
                disp += f'; filename="{filename}"'
            head = f"Content-Disposition: {disp}\r\n"
            if ctype:
                head += f"Content-Type: {ctype}\r\n"
            parts.append(f"--{boundary}\r\n{head}\r\n".encode() + payload
                         + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        return b"".join(parts), f"multipart/form-data; boundary={boundary}"

    def _request(self, body: bytes, content_type: str) -> dict:
        headers = {"Content-Type": content_type}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.endpoint, data=body,
                                     headers=headers, method="POST")
        host = _host_of(self.remote_url)
        last_err: Exception | None = None
        for attempt in range(1, _ATTEMPTS + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode()
                try:
                    return json.loads(raw)
                except ValueError:  # malformed JSON never retries
                    raise RemoteSttError(
                        f"remote STT {host}: malformed JSON response "
                        f"({raw[:300]!r})")
            except RemoteSttError:
                raise
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode(errors="replace")[:300]
                except Exception:
                    pass
                last_err = RemoteSttError(f"remote STT {host}: "
                                          f"HTTP {e.code}: {detail}")
                if e.code not in _RETRYABLE_HTTP:
                    raise last_err  # config errors won't fix themselves
            except Exception as e:  # noqa: BLE001 - transport errors retry
                last_err = RemoteSttError(f"remote STT {host}: {e}")
            if attempt < _ATTEMPTS:
                time.sleep(0.2 * attempt)
        raise last_err or RemoteSttError(f"remote STT {host}: request failed")

    @staticmethod
    def _extract_text(data: dict, host: str) -> str:
        text = str(data.get("text") or "").strip() if isinstance(data, dict) else ""
        if text:
            return text
        # verbose_json: concatenate per-segment text
        segments = data.get("segments") if isinstance(data, dict) else None
        if isinstance(segments, list) and segments:
            joined = " ".join(
                str(s.get("text") or "").strip()
                for s in segments if isinstance(s, dict))
            if joined.strip():
                return joined.strip()
        raise RemoteSttError(
            f"remote STT {host}: response has no 'text' field")

    # -- backend contract -----------------------------------------------------

    def transcribe(self, wav_path: Path,
                   language: str | None = None) -> dict[str, Any]:
        wav_bytes = Path(wav_path).read_bytes()
        lang = (language or self.language or "").strip()
        fields: dict[str, tuple[str, str, bytes]] = {
            "file": ("audio.wav", "audio/wav", wav_bytes),
            "model": ("", "", self.remote_model.encode()),
        }
        if lang and lang != "auto":
            fields["language"] = ("", "", lang.encode())
        body, content_type = self._multipart(fields)
        data = self._request(body, content_type)
        text = self._extract_text(data, _host_of(self.remote_url))
        segments = data.get("segments") if isinstance(data, dict) else None
        return {"text": text,
                "language": None if lang in ("", "auto") else lang,
                "duration": None,
                "segments": segments if isinstance(segments, list) else []}

    def warmup(self) -> None:
        pass  # reachability belongs to doctor + the real take
