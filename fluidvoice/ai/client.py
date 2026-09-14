"""Minimal OpenAI-compatible chat client for the AI polish step.

Mirrors FluidVoice's LLMClient behavior:
- Endpoint: <base_url>/chat/completions (appended unless the base URL already
  ends with /chat/completions, /api/chat or /api/generate).
- OpenAI Responses API (/responses) is used when the base URL contains
  "/responses", or the host is api.openai.com with a gpt-5*/o1/o3/o4 model.
- temperature is omitted for models that reject it (reasoning models,
  claude-opus-4-7/4-8, claude-sonnet-5, claude-fable, claude-mythos).
- Reasoning extras: reasoning_effort for gpt-5*/o1/o3/o4/gpt-oss, and
  enable_thinking for nemotron/nemo and deepseek-reasoner style models.
- Responses pass through the same <think>-tag stripping as upstream,
  including the opening-tag guard (a stray close tag after a real pair must
  not eat the answer).
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from .prompts import base_prompt_for, render_dictation_user_message

THINK_RE = re.compile(r"<think(?:ing)?>([\s\S]*?)</think(?:ing)?>")
ORPHAN_CLOSE_RE = re.compile(r"^([\s\S]*?)</think(?:ing)?>")
STRAY_TAG_RE = re.compile(r"</?think(?:ing)?>")

# Models that reject the temperature parameter (upstream isTemperatureUnsupported)
_CLAUDE_TEMP_UNSUPPORTED = {"claude-opus-4-7", "claude-opus-4-8", "claude-sonnet-5",
                            "claude-fable", "claude-mythos"}


def _normalize_model_name(model: str) -> str:
    # strip provider prefixes like "openai/" or "groq/" (upstream does the same)
    return model.rsplit("/", 1)[-1].strip().lower().replace(".", "-")


def is_reasoning_model(model: str) -> bool:
    name = _normalize_model_name(model)
    return (name.startswith(("gpt-5", "o1", "o3", "o4")) or "gpt-oss" in name
            or ("deepseek" in name and "reasoner" in name))


def is_temperature_unsupported(model: str) -> bool:
    name = _normalize_model_name(model)
    return is_reasoning_model(model) or name in _CLAUDE_TEMP_UNSUPPORTED


def _reasoning_extras(model: str) -> dict:
    """Upstream getReasoningConfig + ThinkingParserFactory extra parameters."""
    name = _normalize_model_name(model)
    extras: dict = {}
    if "nemotron" in name or "nemo" in name:
        extras["enable_thinking"] = True
    if "deepseek" in name and ("r1" in name or "reasoner" in name):
        extras["enable_thinking"] = True
    if name.startswith("gpt-5") or "gpt-oss" in name:
        extras["reasoning_effort"] = "low"
    elif name.startswith(("o1", "o3", "o4")):
        extras["reasoning_effort"] = "medium"
    return extras


def strip_thinking(text: str) -> str:
    """Port of FluidVoice's StripThinkingTags (incl. the opening-tag guard:
    the orphan-close pattern only applies when no opening tag was present)."""
    if not text:
        return text
    has_opening = ("<think>" in text or "<thinking>" in text)
    text = THINK_RE.sub("", text)
    if not has_opening and ("</think>" in text or "</thinking>" in text):
        # Nemotron-style: output begins with thinking and a bare close tag
        m = ORPHAN_CLOSE_RE.match(text)
        if m:
            text = text[m.end():]
    text = STRAY_TAG_RE.sub("", text)
    return text.strip()


class AIError(RuntimeError):
    pass


def _endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    for suffix in ("/chat/completions", "/api/chat", "/api/generate", "/responses"):
        if base.endswith(suffix):
            return base
    return base + "/chat/completions"


def _use_responses_api(base_url: str, model: str) -> bool:
    base = base_url.rstrip("/")
    if base.endswith("/responses"):
        return True
    host = urlparse(base).hostname or ""
    return host == "api.openai.com" and is_reasoning_model(model)


class AIClient:
    def __init__(self, cfg: dict):
        ai = cfg["ai"]
        self.base_url = ai["base_url"].rstrip("/")
        self.model = ai.get("model", "")
        self.temperature = float(ai.get("temperature", 0.2))
        self.timeout = float(ai.get("timeout_seconds", 120))
        self.retries = int(ai.get("max_retries", 3))
        self.api_key = ai.get("api_key", "") or os.environ.get(ai.get("api_key_env", ""), "")
        self.system_prompt = base_prompt_for(cfg)

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model)

    def polish(self, transcript: str, system_prompt: str | None = None) -> str:
        """Clean a raw transcript through the configured chat model."""
        if not self.configured:
            raise AIError("AI polish enabled but base_url/model not configured")
        prompt = (system_prompt or self.system_prompt).strip()
        user_message = render_dictation_user_message(prompt, transcript)
        content = self.chat(user_message)
        cleaned = strip_thinking(content)
        # Divergence (intentional): upstream falls back to the raw content when
        # cleaning empties it; we prefer the transcript over typed <think> junk.
        return cleaned or transcript

    # -- request building ------------------------------------------------------

    def _build_body(self, messages_or_text, responses_api: bool,
                    temperature: float | None = None) -> bytes:
        extras = _reasoning_extras(self.model)
        temp = self.temperature if temperature is None else temperature
        if responses_api:
            if isinstance(messages_or_text, str):
                prompt_text = messages_or_text
            else:  # flatten a message list for the Responses API
                prompt_text = "\n\n".join(
                    f"[{m['role']}]\n{m['content']}" for m in messages_or_text)
            body: dict = {"model": self.model, "input": prompt_text, "store": False}
            if extras.get("reasoning_effort"):
                body["reasoning"] = {"effort": extras["reasoning_effort"]}
            extras.pop("reasoning_effort", None)
            body.update(extras)
        else:
            messages = ([{"role": "user", "content": messages_or_text}]
                        if isinstance(messages_or_text, str) else messages_or_text)
            body = {"model": self.model, "messages": messages, "stream": False}
            if not is_temperature_unsupported(self.model):
                body["temperature"] = temp
            body.update(extras)
        return json.dumps(body).encode()

    def _parse_response(self, data: dict) -> str:
        if "choices" in data:  # chat/completions
            return data["choices"][0]["message"]["content"]
        # Responses API: collect text parts from the output items
        parts: list[str] = []
        for item in data.get("output", []) or []:
            for content in item.get("content", []) or []:
                if isinstance(content, dict) and content.get("text"):
                    parts.append(content["text"])
        return "".join(parts)

    def chat(self, user_message: str, temperature: float | None = None) -> str:
        return self.chat_messages([{"role": "user", "content": user_message}],
                                  temperature=temperature)

    def chat_messages(self, messages: list[dict],
                      temperature: float | None = None) -> str:
        """Full message list (system/user/assistant) through the same transport."""
        responses_api = _use_responses_api(self.base_url, self.model)
        payload = self._build_body(messages, responses_api, temperature=temperature)
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(_endpoint(self.base_url), data=payload,
                                     headers=headers, method="POST")
        last_err: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode())
                content = self._parse_response(data)
                if not content.strip():
                    raise AIError("empty response from model")
                return content
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode(errors="replace")[:300]
                except Exception:  # noqa: S110 — best-effort path; caller must proceed
                    pass
                last_err = AIError(f"HTTP {e.code}: {detail}")
                if e.code in (400, 401, 403, 404, 422):
                    raise last_err  # config errors won't fix themselves
                # Divergence (intentional): 429/5xx are retried, unlike upstream.
            except Exception as e:  # noqa: BLE001 - transport errors retry
                last_err = AIError(str(e))
            if attempt < self.retries:
                time.sleep(0.2 * attempt)
        raise last_err or AIError("request failed")
