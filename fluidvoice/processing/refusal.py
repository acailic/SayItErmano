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


def is_overcorrection(polished: str, raw: str,
                      max_bad_subs: int = 3) -> bool:
    """True when the polish pass REPLACED spoken words with different
    words (near-synonyms) instead of cleaning them - the over-correction
    failure mode of LLM ASR error correction (Ma et al. 2024: unconstrained
    LLM correction swaps correct words; gains on strong ASR are ~1%).
    Substitutions count as bad only when BOTH words are alphabetic and
    far apart (edit distance > 2), so filler removal (deletions), casing,
    punctuation and number normalization ("five thirty" -> "5:30") pass.
    Deletions/insertions are the cleaner's legitimate business."""
    import difflib

    def words(t: str) -> list[str]:
        return [w.strip(".,!?;:()`'\"").lower()
                for w in t.split() if w.strip()]

    a, b = words(polished), words(raw)
    if not a or not b:
        return False
    if len(a) - len(b) > max(4, len(b) // 2):
        # hallucination-side blowup (much LONGER); a shorter result is
        # legitimate cleaning (filler removal deletes words)
        return True
    bad = 0
    for op, _i1, _i2, _j1, _j2 in difflib.SequenceMatcher(
            None, b, a, autojunk=False).get_opcodes():
        if op != "replace":
            continue
        for old, new in zip(_get(b, _i1, _i2), _get(a, _j1, _j2)):
            if (old.isalpha() and new.isalpha() and len(old) > 2
                    and len(new) > 2
                    and _edit_distance(old, new) > 2):
                bad += 1
    limit = max(1, min(max_bad_subs, len(b) // 6))
    return bad > limit


def _get(seq, i1, i2):
    return seq[i1:i2]


def _edit_distance(x: str, y: str) -> int:
    if abs(len(x) - len(y)) > 4:
        return 99
    prev = list(range(len(y) + 1))
    for i, cx in enumerate(x, 1):
        cur = [i]
        for j, cy in enumerate(y, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1,
                           prev[j - 1] + (cx != cy)))
        prev = cur
    return prev[-1]
