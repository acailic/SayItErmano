"""One dictation utterance: wav -> transcribe -> post-process -> optional
AI polish -> insert -> history.

DictationPipeline lives here (extracted from fluidvoice/daemon.py, audit
C5a). Every step is injectable so the whole flow is unit-testable without
audio, X11 or GPU.
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from typing import Any, Callable

from . import backends, insertion, ui
from . import history as history_mod
from .ai.client import AIError
from .ai.prompts import base_prompt_for
from .audio_utils import (duration_seconds, is_digital_silence, is_silent)
from .processing import post_process
from .processing.per_app import match_app_prompt, system_prompt_for
from .processing.refusal import (is_overcorrection, is_prompt_leak,
                                 is_refusal)
from .processing.slash import squeeze_slash_mentions


def log(msg: str) -> None:
    print(f"[sayit-ermano] {time.strftime('%H:%M:%S')} {msg}", file=sys.stderr, flush=True)


Inserter = Callable[[str, dict], str]

# Ordinal confidence bands from whisper segment log-probs (research §5:
# display honest, coarse confidence - 2 good / 1 mixed / 0 shaky; None when
# the backend cannot say). Thresholds follow whisper.cpp quality filters.
CONF_LOGPROB_MIXED = -0.35
CONF_LOGPROB_LOW = -0.90


def confidence_band(result: dict) -> int | None:
    """0-2 ordinal recognition confidence from a transcribe result."""
    segs = result.get("segments")
    if not segs:
        return None
    vals = [s.get("avg_logprob") for s in segs if isinstance(s, dict)]
    vals = [v for v in vals if isinstance(v, (int, float))]
    if not vals:
        return None
    mean = sum(vals) / len(vals)
    if mean >= CONF_LOGPROB_MIXED:
        return 2
    if mean >= CONF_LOGPROB_LOW:
        return 1
    return 0


# Hallucination tells (2026-09-10 incident): whisper emits CONFIDENT fluent
# garbage on audio it cannot parse - a bad mic (bluetooth-HFP loops like
# "you you you") or speech in another language under a pinned language
# ("Frenchman is overshadowed" over Slovenian). Logprobs cannot separate
# those from healthy takes; the two reliable tells are a short periodic
# token run dominating the words, and text over segments the model itself
# scored as probably-not-speech (no_speech_prob).
REPEAT_PERIODS = ((1, 4), (2, 6), (3, 9))  # (period words, min token count)
REPEAT_COVERAGE = 0.8                # periodic token comparisons satisfied
NO_SPEECH_MEAN = 0.6                 # mean segment no_speech_prob


def _word_tokens(text: str) -> list[str]:
    return re.findall(r"[\w']+", text.lower())


def is_repeat_hallucination(text: str) -> bool:
    """True when the words are a short periodic run ("you you you you",
    "Thank you. Thank you. Thank you."): period-p comparisons hold for
    >= 80% of the token stream."""
    toks = _word_tokens(text)
    if len(toks) < 4:
        return False
    for period, min_len in REPEAT_PERIODS:
        if len(toks) < min_len:
            continue
        comps = len(toks) - period
        matches = sum(1 for i in range(comps)
                      if toks[i] == toks[i + period])
        if matches / comps >= REPEAT_COVERAGE:
            return True
    return False


def looks_like_hallucination(result: dict) -> bool:
    """Repetition loop or text-over-no-speech segments: whisper's two
    reliable garbage tells. Backends without segment tells (whisper.cpp,
    parakeet, most fakes) degrade to repetition-only detection."""
    if is_repeat_hallucination(str(result.get("text") or "")):
        return True
    segs = [s for s in (result.get("segments") or [])
            if isinstance(s, dict)]
    vals = [s["no_speech_prob"] for s in segs
            if isinstance(s.get("no_speech_prob"), (int, float))]
    return bool(str(result.get("text") or "").strip() and vals
                and sum(vals) / len(vals) >= NO_SPEECH_MEAN)


class DictationPipeline:
    """Processes one finished recording into typed text (fully injectable)."""

    def __init__(self, cfg: dict, backend: Any, *,
                 inserter: Inserter = insertion.insert_text,
                 polisher: Callable[[str], str] | None = None,
                 history_writer: Callable[[dict, Path | None], None] | None = None,
                 key_presser: Callable[[str], None] | None = None,
                 rewriter: Callable[[str, str | None], str] | None = None,
                 logger: Callable[[str], None] = log):
        self.cfg = cfg
        self.backend = backend
        self.polisher = polisher  # None -> build AIClient lazily when enabled
        self.history_writer = history_writer  # None -> history_mod.append
        self.key_presser = key_presser or self._press_send_key
        self.rewriter = rewriter
        self.log = logger
        self._pending_send_key: str | None = None
        self._pending_send_skipped_terminal = False
        self.notify = lambda title, body="": ui.notify(title, body, enabled=cfg["notifications"]["enabled"])
        if inserter is insertion.insert_text:
            # default path: surface paste-fallback / clipboard-restore
            # warnings from insertion through the existing notification path
            # (no pill/UI change; stub inserters in tests stay untouched)
            notify = self.notify

            def _inserter(text: str, c: dict) -> str:
                return insertion.insert_text(text, c, on_notice=notify)

            self.inserter = _inserter
        else:
            self.inserter = inserter

    # -- steps -------------------------------------------------------------

    def _should_skip(self, wav: Path, duration: float) -> bool:
        if not self.cfg["recording"].get("skip_silent"):
            return False
        return duration <= 4.0 and is_silent(str(wav))

    def _transcribe(self, wav: Path) -> dict:
        # runtime cycle override (set by Daemon._process via attribute
        # injection, mirroring _profile_override) > effective_language
        override = getattr(self, "_language_override", None)
        lang = override if override else backends.effective_language(
            self.cfg, self.backend)
        result = self.backend.transcribe(wav, language=lang)
        result = self._whitelist_retry(wav, result, lang)
        # hallucination guard (final decode only - preview partials never
        # re-decode): a FORCED language decoding audio it cannot parse
        # (Slovenian speech under pinned en, or a broken mic feed) returns
        # confident fluent garbage. Measured on the 2026-09-10 incident:
        # forced-en garbage lands in confidence band 0 (logprob ~ -1.2)
        # while the same audio under auto is band 1+. When the forced
        # decode shows hallucination tells OR band 0, re-decode once with
        # auto detection and keep the retry only when it comes back clean
        # and confident (the whitelist guard above already applied to the
        # retry's detected language). Backends without language selection
        # (parakeet, English-only): silent skip.
        if (lang and lang != "auto"
                and getattr(self.backend, "selects_language", False)
                and (looks_like_hallucination(result)
                     or confidence_band(result) == 0)):
            retry = self._whitelist_retry(
                wav, self.backend.transcribe(wav, language="auto"), "auto")
            if (str(retry.get("text") or "").strip()
                    and not looks_like_hallucination(retry)
                    and confidence_band(retry) != 0):
                self.log(f"hallucination guard: forced={lang} decode "
                         f"{'looked hallucinated' if looks_like_hallucination(result) else 'was low-confidence'}; "
                         f"auto retry detected={retry.get('language')}")
                result = retry
            else:
                self.log(f"hallucination guard: forced={lang} decode "
                         f"{'looked hallucinated' if looks_like_hallucination(result) else 'was low-confidence'}; "
                         f"retry not confidently better - keeping original")
        return result

    def _whitelist_retry(self, wav: Path, result: dict, lang: str) -> dict:
        """Wrong-language guard (final decode only): when the resolved
        language for the take is "auto", the backend surfaces its detected
        language, and detection landed outside general.language_whitelist,
        re-decode ONCE with the first whitelist entry and log it. Detected
        language unavailable (whisper.cpp under auto) or a backend without
        language selection (parakeet): silent skip."""
        whitelist = list(((self.cfg.get("general", {}) or {})
                          .get("language_whitelist")) or []) \
            if isinstance(self.cfg, dict) else []
        detected = str(result.get("language") or "").strip().lower() or None
        if (whitelist and lang == "auto" and detected
                and getattr(self.backend, "surfaces_detected_language", False)
                and detected.split("-")[0] not in
                    {w.split("-")[0] for w in whitelist}):
            retry = whitelist[0]
            self.log(f"language guard: detected={detected} outside "
                     f"whitelist [{', '.join(whitelist)}]; "
                     f"re-decoding as {retry}")
            result = self.backend.transcribe(wav, language=retry)
        return result

    def _polish(self, text: str, app_hint: str | None = None) -> tuple[str, bool]:
        """Returns (text, ai_used) - falls back to the raw text on AI errors.
        Per-app prompt rules (upstream per-app prompt sets) extend the
        dictation prompt when the target app matches."""
        if not self.cfg["ai"].get("enabled"):
            return text, False
        # late import (no module-level cycle): AIClient is resolved through
        # the daemon module at call time so patches of daemon.AIClient keep
        # steering this construction, exactly as before the split
        from .daemon import AIClient
        polisher = self.polisher or AIClient(self.cfg).polish
        instructions = match_app_prompt(
            self.cfg["ai"].get("per_app_prompts", []), app_hint)
        override_prompt = None
        if getattr(self, "_profile_override", None):
            _profile = self._profile_override
            from .ai.profiles import load_profiles
            override_prompt = load_profiles().get(_profile)
            if override_prompt:
                log(f"polish uses profile '{_profile}'")
            else:
                log(f"WARN shortcut profile '{_profile}' not "
                    "found; using the base prompt")
        try:
            if instructions and self.polisher is None:
                prompt = system_prompt_for(
                    override_prompt or base_prompt_for(self.cfg), instructions)
                polished = polisher(text, system_prompt=prompt)
                prompt_used = prompt
            elif override_prompt:
                # new path (profiled shortcuts): the override always rides
                # along, injected polishers included
                polished = polisher(text, system_prompt=override_prompt)
                prompt_used = override_prompt
            else:
                polished = polisher(text)
                prompt_used = base_prompt_for(self.cfg)
        except AIError as e:
            self.log(f"AI polish failed ({e}); using raw transcription")
            return text, False
        # refusal guardrail (D5): a reply that reads as an LLM refusal
        # never reaches the doc - the raw transcript is typed instead
        if self.cfg["ai"].get("refusal_guard", True):
            if is_refusal(polished):
                self.log("AI polish refused (guardrail); "
                         "using raw transcription")
                self.notify("SayItErmano",
                            "AI polish refused — typed the raw transcript")
                return text, False
            if is_prompt_leak(polished, prompt_used or ""):
                # upstream #910: the model pasted its system prompt into
                # the document - same fallback, same guarantee
                self.log("AI polish leaked its system prompt (guardrail); "
                         "using raw transcription")
                self.notify("SayItErmano",
                            "AI polish echoed its prompt — typed the raw "
                            "transcript")
                return text, False
            if is_overcorrection(polished, text):
                # Ma et al. 2024: unconstrained LLM correction swaps
                # correct words for synonyms - keep the raw transcript
                self.log("AI polish over-corrected (guardrail); "
                         "using raw transcription")
                self.notify("SayItErmano",
                            "AI polish rewrote your words — typed the raw "
                            "transcript")
                return text, False
        return polished, True

    def _rewrite(self, instruction: str, context: str | None, raw: str,
                 duration: float, wav: Path,
                 conf: int | None = None) -> dict | None:
        from . import rewrite as rewrite_mod
        try:
            rewriter = self.rewriter or rewrite_mod.run_rewrite
            rewritten = rewriter(instruction, context)
            if is_refusal(rewritten):
                # same failure path as a transport error: nothing typed,
                # the user is told why
                raise rewrite_mod.RewriteError("the model refused")
        except rewrite_mod.RewriteError as e:
            self.log(f"rewrite failed: {e}")
            self.notify("SayItErmano", f"Rewrite failed: {e}")
            return None
        strategy = self._insert(rewritten)
        out = {"raw": raw, "text": rewritten, "ai": True,
               "strategy": strategy, "mode": "rewrite", "confidence": conf}
        self.log(f"rewrote ({strategy}, {len(rewritten)} chars): {rewritten[:120]}")
        entry = {"ts": time.time(), "duration_s": round(duration, 2),
                 "raw": raw, "text": rewritten, "ai": True,
                 "backend": self.backend.name, "app": None, "mode": "rewrite"}
        if conf is not None:
            entry["confidence"] = conf
        self._write_history(entry, wav)
        return out

    def _command(self, instruction: str, raw: str,
                 duration: float, wav: Path) -> dict:
        """Command mode turn 0: hand the instruction back to the daemon (the
        CommandSession inserts nothing, polishes nothing and writes history
        only for EXECUTED commands, later)."""
        self.log(f"command instruction ({len(instruction)} chars): {instruction[:120]}")
        return {"mode": "command", "text": instruction, "raw": raw,
                "duration_s": round(duration, 2)}

    def _log_hotword_hits(self, final_text: str) -> None:
        """Per-take hotword hit-rate (BWER spirit, app-scale): how many
        configured bias words actually landed in the transcript. Lets a
        user see the list earning its keep - and notice when it is dead
        weight (over-biasing risk grows with useless entries)."""
        import re
        hot = ((self.cfg.get("model", {}) or {}).get("hotwords")) or []
        if not hot:
            return
        low = final_text.lower()
        hits = sum(1 for w in hot if re.search(r"\b" + re.escape(w.lower())
                                               + r"\b", low))
        self.log(f"hotwords: {hits}/{len(hot)} in transcript")

    def _after_ai_formatting(self, text: str, app_hint: str | None = None) -> str:
        """Slash/mention squeeze + GAAV + spoken-send stripping (upstream
        post-AI steps; the literal squeeze runs after AI cleanup and before
        GAAV — upstream ContentView.swift:2658-2661)."""
        from .processing.extra_formats import apply_gaav, parse_spoken_send
        p = self.cfg.get("processing", {})
        if p.get("slash_mention_squeeze", True):
            text = squeeze_slash_mentions(text)
        if p.get("gaav_enabled"):
            text = apply_gaav(text,
                             lowercase_first=p.get("gaav_lowercase_first", True),
                             remove_trailing_period=p.get("gaav_remove_trailing_period", True))
        if self.cfg["recording"].get("spoken_send_enabled"):
            result = parse_spoken_send(text,
                                       self.cfg["recording"].get("spoken_send_phrase", "send it"))
            self._pending_send_key = None
            self._pending_send_skipped_terminal = False
            if result.should_send:
                # terminal blocklist (upstream ContentView.swift:1928-1937,
                # :2786-2798): the phrase still strips and the text still
                # inserts, but Enter is never pressed — executing a
                # half-typed shell line is worse than not sending
                if app_hint and insertion.is_terminal_app(app_hint, self.cfg):
                    self._pending_send_skipped_terminal = True
                    self.log("spoken-send: Enter suppressed (terminal app)")
                else:
                    self._pending_send_key = self.cfg["recording"].get(
                        "spoken_send_key", "enter")
            return result.text
        return text

    def _press_send_key(self, spec: str) -> None:
        try:
            insertion.press_key(spec)
        except insertion.InsertError as e:
            self.log(f"send-key failed: {e}")

    def _set_pill_badge(self, text: str) -> None:
        """Spoken-send indicator on the processing pill (upstream
        SpokenSendIndicator, simplified to a status chip)."""
        display = getattr(self, "_closing_display", None)
        if display is not None:
            try:
                display.set_badge(text)
            except Exception:
                pass

    def _insert(self, text: str) -> str:
        try:
            strategy = self.inserter(text, self.cfg)
        except insertion.InsertError as e:
            self.log(f"insertion failed: {e}")
            self.notify("SayItErmano", f"Could not type text: {e}\n(copied to clipboard instead)")
            insertion.clipboard_fallback(text)
            strategy = "clipboard-fallback"
        if self.cfg.get("general", {}).get("copy_to_clipboard"):
            insertion.copy_to_clipboard(text)  # upstream copyTranscriptionToClipboard
        return strategy

    def _write_history(self, entry: dict, wav: Path) -> None:
        if not self.cfg["history"].get("save"):
            return
        hcfg = self.cfg["history"]
        if self.history_writer is not None:
            self.history_writer(entry, wav if hcfg.get("save_audio") else None)
        else:
            history_mod.append(entry,
                               audio_src=wav if hcfg.get("save_audio") else None,
                               keep_audio=hcfg.get("save_audio", False),
                               budget_gb=hcfg.get("audio_budget_gb", 4.0))

    # -- orchestration ------------------------------------------------------

    def run(self, wav: Path, app_hint: str | None,
            mode: str = "dictate", rewrite_context: str | None = None) -> dict | None:
        """Returns the result dict (raw/text/ai/strategy) or None if nothing was typed."""
        from .audio_utils import pad_wav
        started = time.monotonic()
        self._pending_send_skipped_terminal = False
        try:
            duration = duration_seconds(str(wav))
            if self._should_skip(wav, duration):
                self.log("silent recording skipped")
                return None
            pad_wav(wav)  # whisper.cpp requires >= 1s (16000 samples) of audio
            try:
                result = self._transcribe(wav)
            except Exception as e:
                self.log(f"transcription failed: {e}")
                self.notify("SayItErmano", f"Transcription failed: {e}")
                return None
            raw = result.get("text", "")
            if not raw.strip():
                if is_digital_silence(str(wav)):
                    # the mic streamed zeros: not "user said nothing" but
                    # a dead/wedged input path - say so, it is actionable
                    self.log("empty transcription: mic delivered digital "
                             "silence (dead or wedged input path)")
                    self.notify("SayItErmano",
                                "No audio from the mic — check that it is "
                                "connected and selected")
                else:
                    self.log("empty transcription")
                return None
            if is_repeat_hallucination(raw):
                # a pure repetition loop is never the intended speech
                # (bad-mic whisper artifact); typing it is worse than
                # nothing - tell the user what to check instead
                self.log(f"hallucination guard: suppressed repetition "
                         f"output ({len(raw)} chars): {raw[:60]!r}")
                self.notify("SayItErmano",
                            "No usable speech caught — check mic and language")
                return None
            conf = confidence_band(result)
            text = post_process(raw, self.cfg, app_hint=app_hint)
            if mode == "rewrite":
                return self._rewrite(text, rewrite_context, raw, duration,
                                     wav, conf)
            if mode == "command":
                return self._command(text, raw, duration, wav)
            polished, ai_used = self._polish(text, app_hint=app_hint)
            polished = self._after_ai_formatting(polished, app_hint=app_hint)
            self._log_hotword_hits(polished)
            strategy = self._insert(polished)
            if self._pending_send_key:
                spec, self._pending_send_key = self._pending_send_key, None
                self._set_pill_badge("⏎ sending…")
                self.key_presser(spec)
                self._set_pill_badge("⏎ sent")
                strategy += f"+{spec}"
            elif self._pending_send_skipped_terminal:
                self._pending_send_skipped_terminal = False
                self._set_pill_badge("⏎ skipped (terminal)")
            out = {"raw": raw, "text": polished, "ai": ai_used,
                   "strategy": strategy, "confidence": conf}
            self.log(f"typed ({strategy}, {len(polished)} chars, {duration:.1f}s audio, "
                     f"{time.monotonic() - started:.1f}s total): {polished[:120]}")
            entry = {"ts": time.time(), "duration_s": round(duration, 2),
                     "raw": raw, "text": polished, "ai": ai_used,
                     "backend": self.backend.name, "app": app_hint}
            if conf is not None:
                entry["confidence"] = conf
            self._write_history(entry, wav)
            return out
        finally:
            wav.unlink(missing_ok=True)
