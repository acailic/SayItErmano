"""AI refusal guardrail (D5): never type a refusing LLM's answer into the
user's document.

Upstream's closed model once pasted "I'm sorry, I can't assist with
that." into a document (GetVoibe review, research insight 9); a polish
reply that IS a refusal is a model failure, not dictation. Pure string
detection on the reply's first 240 chars — no extra LLM calls, no
network, English patterns only in v1 (a non-English refusal falls through
and is documented as a limitation).

False-positive posture: "I'm sorry I'm late" and "Sorry, I can't make it
today" are legitimate dictations — a bare apology NEVER matches; the
sorry-family requires a refuse-verb combo (can't/cannot/unable/won't +
assist/help with/comply with/fulfill/provide/support) nearby.
"""
from __future__ import annotations

import re

# examined window of the normalized reply (refusals lead)
_HEAD = 240
_SORRY_HEAD = 80

# alone-matchable refusal leads (first 40 chars of the normalized reply)
_LEADS = (
    "as an ai",
    "i must decline",
    "i will not assist",
    "i cannot assist",
    "i can't assist",
    "i cannot help with",
    "i can't help with",
    "i cannot comply",
    "i can't comply",
    "i'm unable to assist",
    "i am unable to assist",
    "i cannot fulfill",
    "i can't fulfill",
    "i'm not able to assist",
    "i am not able to assist",
    "that request goes against",
    "against my guidelines",
)

_SORRY_LEAD = re.compile(
    r"\b(i'?m sorry|i am sorry|sorry|unfortunately|i apologize)\b")
_REFUSE_VERB = re.compile(
    r"\b(can'?t|cannot|can not|unable to|won'?t|will not)\b"
    r"[^.!?]{0,40}\b(assist|help with|comply with|fulfill|provide that|"
    r"support that)\b")

_LEAD_WINDOW = 40


def _normalize(text: str) -> str:
    lowered = (text or "").strip().lower()
    # strip markdown/bullet/quote dressing refusals arrive in
    lowered = re.sub(r"^[\s>*#\-\"'`]+", "", lowered)
    return re.sub(r"\s+", " ", lowered)


def is_refusal(text: str) -> bool:
    """True when the reply reads as an assistant refusal, not dictation."""
    norm = _normalize(text)
    if not norm:
        return False
    if norm[:_LEAD_WINDOW].startswith(_LEADS):
        return True
    head = norm[:_SORRY_HEAD]
    full = norm[:_HEAD]
    return bool(_SORRY_LEAD.search(head) and _REFUSE_VERB.search(full))


def is_prompt_leak(reply: str, system_prompt: str, window: int = 8) -> bool:
    """True when the reply echoes a run of consecutive system-prompt words
    (upstream #910: the model pasted the entire system prompt into the
    user's document instead of the cleaned transcript). An 8-word
    consecutive shingle from the prompt appearing verbatim in the reply is
    a leak - natural dictation never reproduces instruction sentences."""
    prompt = _normalize(system_prompt)
    reply_n = _normalize(reply)
    if not prompt or not reply_n:
        return False
    words = prompt.split()
    if len(words) < window:
        return False
    for i in range(len(words) - window + 1):
        if " ".join(words[i:i + window]) in reply_n:
            return True
    return False
