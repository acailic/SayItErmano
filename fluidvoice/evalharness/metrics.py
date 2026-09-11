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
- **Omission rate**: deletion-only errors — reference words left absent
  by the alignment (:func:`omission_rate`) — over reference words;
  reported alongside WER everywhere. Empty reference: None (not
  scoreable, nothing could be omitted), excluded from aggregates.
- **Hallucination word rate** (negative cases): hypothesis tokens per
  audio second when the reference is empty
  (:func:`hallucination_word_rate`); None when the audio duration is
  unknown or non-positive.
- **Punctuation family** (:func:`terminal_punctuation_accuracy`,
  :func:`sentence_start_capital_accuracy`): computed on the RAW texts —
  terminal punctuation runs and sentence-start capitals, sentences
  aligned by index. A **separate metric family**: these scores never
  feed WER/CER normalization, and normalization never feeds them (both
  directions pinned by test).
- **Language match** (:func:`language_codes_match`): expected vs
  adapter-detected codes agree when their casefolded *primary* subtags
  (the part before ``-``/``_``) are equal — ``en`` vs ``en-US`` is a
  match, ``de`` vs ``en`` is a confusion.
- **SNR buckets** (:func:`snr_bucket`): ``<5`` / ``5-10`` / ``10-20`` /
  ``>=20`` dB (half-open upward: 10.0 lands in ``10-20``), mirroring
  the corpus-spec §5 recording targets; no ``snr_db`` key groups under
  ``"(unset)"``.
- **Hotword recall**: of a case's tags that appear in the *normalized
  reference*, the fraction that also appear in the hypothesis. Tags the
  reference doesn't contain cannot be recalled and are not scored; no
  scoreable hotwords -> None (the case is excluded from that aggregate).
- **Name/number recall convention** (corpus-spec §4, T4): every
  names-and-numbers case tags its name/number tokens in ``tags``, so
  hotword recall doubles as the cross-language name/number recall
  metric — normalization is case- and punctuation-insensitive, so
  ``192.168.0.1``, ``Anaïs``, ``Müllerstraße`` score identically in
  every language (a dropped dot or diacritic is a word error the
  punctuation/word metrics catch, not a localization artifact).
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

import math
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

OP_MATCH = "match"
OP_SUBSTITUTION = "substitution"
OP_DELETION = "deletion"             # a reference word with no hypothesis
OP_INSERTION = "insertion"           # a hypothesis word with no reference

# Punctuation family (G4): raw-text sentences, never WER-normalized.
SENTENCE_TERMINALS = ".!?…"

# Subgroup label for cases whose manifest lacks a provenance key (G1).
UNSET_LABEL = "(unset)"

# Subgroup dimensions (G1), in report order; case fields of the same
# name (None -> UNSET_LABEL), so per-dimension counts sum to the corpus.
SUBGROUP_DIMENSIONS = ("language", "speaker", "mic_class", "taxonomy",
                       "snr_bucket")

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


def _ratio(distance: int, ref_len: int, hyp: Sequence) -> float:
    """Distance over reference length, with the empty-reference rule.

    Emptiness is judged on the NORMALIZED hypothesis (found by the Q5
    property tests: punctuation-only text normalizes to zero words on
    both sides, but the old raw-string check scored identical input as
    WER 1.0)."""
    if ref_len == 0:
        return 0.0 if not hyp else 1.0
    return distance / ref_len


def levenshtein_alignment(a: Sequence[T], b: Sequence[T]) \
        -> list[tuple[str, T | None, T | None]]:
    """Alignment-returning Levenshtein: one ``(op, ref_elem, hyp_elem)``
    per edit, front-to-back. ``op`` is :data:`OP_MATCH` or
    :data:`OP_SUBSTITUTION` (both elements set), :data:`OP_DELETION`
    (reference element, hypothesis None) or :data:`OP_INSERTION`
    (reference None). Non-match ops always number exactly
    ``levenshtein(a, b)``; ties prefer the diagonal, so a given pair
    always aligns identically.
    """
    n, m = len(a), len(b)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        d[i][0] = i
    for j in range(1, m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = min(d[i - 1][j - 1] + (a[i - 1] != b[j - 1]),
                          d[i - 1][j] + 1,
                          d[i][j - 1] + 1)
    ops: list[tuple[str, T | None, T | None]] = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and \
                d[i][j] == d[i - 1][j - 1] + (a[i - 1] != b[j - 1]):
            ops.append((OP_MATCH if a[i - 1] == b[j - 1]
                        else OP_SUBSTITUTION, a[i - 1], b[j - 1]))
            i, j = i - 1, j - 1
        elif i > 0 and d[i][j] == d[i - 1][j] + 1:
            ops.append((OP_DELETION, a[i - 1], None))
            i -= 1
        else:
            ops.append((OP_INSERTION, None, b[j - 1]))
            j -= 1
    ops.reverse()
    return ops


def alignment_counts(ops: Iterable[tuple[str, T | None, T | None]]) \
        -> dict[str, int]:
    """Edit counts per op type from a :func:`levenshtein_alignment` list."""
    counts = {OP_MATCH: 0, OP_SUBSTITUTION: 0, OP_DELETION: 0,
              OP_INSERTION: 0}
    for op, _, _ in ops:
        counts[op] += 1
    return counts


def omission_rate(reference: str, hypothesis: str) -> float | None:
    """Reference words missing from the hypothesis / reference words.

    Deletion-only WER (``deletions / reference words``) from the
    normalized alignment — reported alongside WER everywhere (G2).
    Empty reference -> None (nothing scoreable); aggregates exclude it
    by the ``mean()`` convention.
    """
    ref = normalize_words(reference)
    if not ref:
        return None
    ops = levenshtein_alignment(ref, normalize_words(hypothesis))
    deletions = sum(1 for op, _, _ in ops if op == OP_DELETION)
    return deletions / len(ref)


def hallucination_word_rate(hypothesis: str,
                            audio_duration_s: float | None) -> float | None:
    """Hypothesis tokens per audio second on empty-reference cases (G3).

    The negative-case (N1–N5) hallucination measure: with an empty
    reference every produced word is hallucinated, so the rate is the
    normalized hypothesis token count over the audio's play time.
    None when the duration is unknown or non-positive (not scoreable).
    """
    if audio_duration_s is None or audio_duration_s <= 0:
        return None
    return len(normalize_words(hypothesis)) / audio_duration_s


def language_codes_match(expected: str, detected: str) -> bool:
    """True when expected and detected language codes agree (G5).

    Compares casefolded primary subtags (before ``-``/``_``): ``en`` vs
    ``en-US`` matches (region/script variants are not confusions);
    ``de`` vs ``en`` does not. Empty codes never match anything.
    """
    def primary(code: str | None) -> str:
        return (code or "").strip().casefold().replace("_", "-").split("-")[0]
    p = primary(detected)
    return bool(p) and p == primary(expected)


def snr_bucket(snr_db: float | None) -> str | None:
    """SNR bucket label for subgroup tables (corpus-spec §5 targets).

    ``<5`` / ``5-10`` / ``10-20`` / ``>=20`` dB, half-open upward
    (10.0 -> ``10-20``); None when ``snr_db`` is absent (the caller
    groups those cases under :data:`UNSET_LABEL`).
    """
    if snr_db is None:
        return None
    if snr_db < 5:
        return "<5"
    if snr_db < 10:
        return "5-10"
    if snr_db < 20:
        return "10-20"
    return ">=20"


_SENTENCE_RE = re.compile(r"[^.!?…]*[.!?…]+|[^.!?…]+", re.DOTALL)


def split_sentences(text: str) -> list[tuple[str, str]]:
    """Split raw text into ``(body, terminal_mark)`` pairs (G4).

    A sentence is everything up to and including a *run* of terminal
    punctuation (``.!?…`` — ``!!!`` and ``...`` are single marks,
    compared strictly); a trailing fragment without terminals has mark
    ``""``. Fragments with no word content are dropped. Raw text only —
    this must never see WER-normalized strings.
    """
    out: list[tuple[str, str]] = []
    for m in _SENTENCE_RE.finditer(text or ""):
        chunk = m.group(0)
        k = len(chunk)
        while k > 0 and chunk[k - 1] in SENTENCE_TERMINALS:
            k -= 1
        body, mark = chunk[:k].strip(), chunk[k:]
        if body:
            out.append((body, mark))
    return out


def _first_alpha(text: str) -> str | None:
    for ch in text:
        if unicodedata.category(ch).startswith("L"):
            return ch
    return None


def terminal_punctuation_accuracy(reference: str,
                                  hypothesis: str) -> float | None:
    """Fraction of reference sentences whose aligned hypothesis
    sentence ends with the same terminal punctuation run (G4).

    Reference sentences without a terminal mark are not scored; a
    missing or extra hypothesis sentence at that index counts as
    wrong. None when no reference sentence carries terminal
    punctuation (not scoreable — including empty references).
    """
    ref = split_sentences(reference)
    scored = [i for i, (_, mark) in enumerate(ref) if mark]
    if not scored:
        return None
    hyp = split_sentences(hypothesis)
    correct = sum(1 for i in scored
                  if i < len(hyp) and hyp[i][1] == ref[i][1])
    return correct / len(scored)


def sentence_start_capital_accuracy(reference: str,
                                    hypothesis: str) -> float | None:
    """Fraction of reference sentences whose aligned hypothesis
    sentence starts with the same letter case (G4).

    Agrees with the *reference's* sentence-start casing — uppercase
    starts stay uppercase, lowercase starts stay lowercase (German
    nouns included). A missing hypothesis sentence counts as wrong.
    None when no reference sentence contains a letter.
    """
    ref = split_sentences(reference)
    scored = [i for i, (body, _) in enumerate(ref) if _first_alpha(body)]
    if not scored:
        return None
    hyp = split_sentences(hypothesis)
    correct = 0
    for i in scored:
        want = _first_alpha(ref[i][0])
        got = _first_alpha(hyp[i][0]) if i < len(hyp) else None
        if got is not None and want.isupper() == got.isupper():
            correct += 1
    return correct / len(scored)


def wer(reference: str, hypothesis: str) -> float:
    """Word error rate (see module docstring for the convention)."""
    ref, hyp = normalize_words(reference), normalize_words(hypothesis)
    return _ratio(levenshtein(ref, hyp), len(ref), hyp)


def cer(reference: str, hypothesis: str) -> float:
    """Character error rate over the normalized character stream."""
    ref, hyp = normalize_chars(reference), normalize_chars(hypothesis)
    return _ratio(levenshtein(ref, hyp), len(ref), hyp)


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
    # scoreable only when BOTH inputs are finite and positive: NaN/inf
    # measurements are artifacts too (the Q5 property tests caught NaN
    # slipping through the falsy/<=0 checks and scoring nan)
    try:
        ok = (math.isfinite(audio_duration_s)
              and math.isfinite(processing_time_s)
              and audio_duration_s > 0 and processing_time_s > 0)
    except TypeError:  # non-numeric garbage
        return None
    if not ok:
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
