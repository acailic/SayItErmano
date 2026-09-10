"""Pure metric functions for the local evaluation harness (plan P1.4).

No I/O and no models here: every function is arithmetic over strings and
numbers, so the math is unit-testable in CI (the harness never needs a
model to compute metrics — models only produce the texts and latencies
being scored).

Conventions (documented once here; reports repeat the short form):

- **Text normalization**: NFKC, then casefold, then tokenize to runs of
  word characters plus intra-word apostrophes (``[\\w']+``), joined by
  single spaces. Case and punctuation therefore never count as errors;
  collapsed whitespace doesn't either.
- **WER**: word-level Levenshtein distance between the normalized
  reference and hypothesis, divided by the reference word count. Empty
  reference: 0.0 when the hypothesis is also empty, else 1.0.
- **CER**: the same Levenshtein distance over the normalized character
  stream (single spaces included — the model must produce word breaks),
  divided by the reference character count. Empty reference follows the
  WER rule.
- **Hotword recall**: of a case's tags that appear in the *normalized
  reference*, the fraction that also appear in the hypothesis. Tags the
  reference doesn't contain cannot be recalled and are not scored; no
  scoreable hotwords -> None (the case is excluded from that aggregate).
- **Real-time factor**: audio duration / processing time — **greater
  than 1 means faster than realtime**. Note this is the *reciprocal* of
  the ratio some tools call RTF (processing/audio); reports state the
  direction so numbers are never ambiguous.
- **Guard outcome**: expected ok/flag vs observed ok/flag from the
  transcriber. ``none`` expected means the case is excluded from guard
  scoring; ``none`` observed (guard didn't run) is reported but not
  scored as right or wrong.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from typing import TypeVar

# ---------------------------------------------------------------- text norm

_TOKEN_RE = re.compile(r"[\w']+", re.UNICODE)

GUARD_OK = "ok"
GUARD_FLAG = "flag"
GUARD_NONE = "none"
GUARD_EXPECTED_VALUES = (GUARD_OK, GUARD_FLAG, GUARD_NONE)
GUARD_OBSERVED_VALUES = (GUARD_OK, GUARD_FLAG, GUARD_NONE)

GUARD_TRUE_POSITIVE = "true_positive"
GUARD_TRUE_NEGATIVE = "true_negative"
GUARD_FALSE_POSITIVE = "false_positive"
GUARD_FALSE_NEGATIVE = "false_negative"
GUARD_EXCLUDED = "excluded"          # expected_guard = none
GUARD_NOT_SCORED = "not_scored"      # expected ok/flag but no guard ran
GUARD_CATEGORIES = (GUARD_TRUE_POSITIVE, GUARD_TRUE_NEGATIVE,
                    GUARD_FALSE_POSITIVE, GUARD_FALSE_NEGATIVE,
                    GUARD_EXCLUDED, GUARD_NOT_SCORED)

T = TypeVar("T")


def normalize_words(text: str) -> list[str]:
    """Normalized word sequence: NFKC + casefold + [\\w']+ tokens.

    Apostrophes survive only inside a word (``it's``); leading/trailing
    ones (quotes, possessive tails) are stripped (``jones'`` -> ``jones``).
    """
    folded = unicodedata.normalize("NFKC", text or "").casefold()
    return [t for t in (tok.strip("'") for tok in _TOKEN_RE.findall(folded))
            if t]


def normalize_chars(text: str) -> str:
    """Normalized character stream: normalized words joined by spaces."""
    return " ".join(normalize_words(text))


def levenshtein(a: Sequence[T], b: Sequence[T]) -> int:
    """Insert/substitute/delete edit distance between two sequences."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        append = cur.append
        for j, cb in enumerate(b, 1):
            append(min(prev[j] + 1,          # deletion
                       cur[j - 1] + 1,       # insertion
                       prev[j - 1] + (ca != cb)))  # substitution
        prev = cur
    return prev[-1]


def _ratio(distance: int, ref_len: int, ref: str, hyp: str) -> float:
    """Distance over reference length, with the empty-reference rule."""
    if ref_len == 0:
        return 0.0 if not hyp else 1.0
    return distance / ref_len


def wer(reference: str, hypothesis: str) -> float:
    """Word error rate (see module docstring for the convention)."""
    ref, hyp = normalize_words(reference), normalize_words(hypothesis)
    return _ratio(levenshtein(ref, hyp), len(ref), reference, hypothesis)


def cer(reference: str, hypothesis: str) -> float:
    """Character error rate over the normalized character stream."""
    ref, hyp = normalize_chars(reference), normalize_chars(hypothesis)
    return _ratio(levenshtein(ref, hyp), len(ref), reference, hypothesis)


def hotword_recall(hypothesis: str, hotwords: Iterable[str],
                   reference: str | None = None) -> float | None:
    """Fraction of scoreable hotwords present in the hypothesis.

    A hotword is scoreable when it appears in the normalized hypothesis's
    *reference* (when given) — tags the reference never contained cannot
    be missed. No scoreable hotwords -> None.
    """
    scored = [h for h in hotwords if h and h.strip()]
    if reference is not None:
        ref_text = normalize_chars(reference)
        scored = [h for h in scored if normalize_chars(h) in ref_text]
    if not scored:
        return None
    hyp_text = normalize_chars(hypothesis)
    hits = sum(1 for h in scored if normalize_chars(h) in hyp_text)
    return hits / len(scored)


def real_time_factor(audio_duration_s: float | None,
                     processing_time_s: float | None) -> float | None:
    """Audio duration / processing time; > 1 is faster than realtime.

    None (not scoreable) when either input is missing or non-positive —
    a zero-second "processing time" is a measurement artifact, not
    infinite speed.
    """
    if not audio_duration_s or not processing_time_s:
        return None
    if audio_duration_s <= 0 or processing_time_s <= 0:
        return None
    return audio_duration_s / processing_time_s


def mean(values: Iterable[float]) -> float | None:
    """Mean of the values, None when there are none (aggregate convention)."""
    vals = [v for v in values if isinstance(v, (int, float))]
    if not vals:
        return None
    return sum(vals) / len(vals)


def guard_category(expected: str, observed: str) -> str:
    """Confusion-matrix cell for one case (see GUARD_* constants)."""
    if expected not in GUARD_EXPECTED_VALUES:
        raise ValueError(f"invalid expected guard outcome: {expected!r}")
    if observed not in GUARD_OBSERVED_VALUES:
        raise ValueError(f"invalid observed guard outcome: {observed!r}")
    if expected == GUARD_NONE:
        return GUARD_EXCLUDED
    if observed == GUARD_NONE:
        return GUARD_NOT_SCORED
    if expected == GUARD_FLAG:
        return (GUARD_TRUE_POSITIVE if observed == GUARD_FLAG
                else GUARD_FALSE_NEGATIVE)
    return (GUARD_FALSE_POSITIVE if observed == GUARD_FLAG
            else GUARD_TRUE_NEGATIVE)


def guard_counts(categories: Iterable[str]) -> dict[str, int]:
    """Counts per guard category, all categories always present."""
    counts = {c: 0 for c in GUARD_CATEGORIES}
    for c in categories:
        if c not in counts:
            raise ValueError(f"invalid guard category: {c!r}")
        counts[c] += 1
    return counts
