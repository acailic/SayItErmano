"""Property-based tests at risky pure boundaries (Q5).

The quality plan asks for a SMALL set of properties over the pure
domains where example tests can't cover the input space: transcript
serialization, config round trips, Unicode text processing, metric
arithmetic, and chunk-boundary reconciliation. Invariants follow the
domain, not generic math:

* WER/CER are non-negative and UNBOUNDED above (they can exceed 1 —
  a hallucinating hypothesis against a short reference is exactly
  that); identical strings score exactly 0; the empty-reference rule
  is 0/1, never a division by zero;
* boundary reconciliation must never SILENTLY DELETE distinct speech —
  a visible double is the documented, preferred failure;
* serialization round trips must be stable and tolerant (garbage in,
  safe defaults out — remote endpoints send what they like).
"""
from __future__ import annotations

import copy
import sys

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from fluidvoice.backends.base import Transcript, TranscriptSegment
from fluidvoice.chunking import Chunk, merge_transcripts
from fluidvoice.config import DEFAULTS, load_config, save_config
from fluidvoice.evalharness import metrics as M
from fluidvoice.processing import post_process

# suites run under -n auto on a loaded box: no deadline flakiness
PROP = settings(deadline=None,
                suppress_health_check=[HealthCheck.too_slow],
                max_examples=50)

# ---------------------------------------------------------------------------
# transcript serialization
# ---------------------------------------------------------------------------

seg_strategy = st.fixed_dictionaries({
    "start": st.floats(min_value=0, max_value=1e6, allow_nan=False),
    "end": st.floats(min_value=0, max_value=1e6, allow_nan=False),
    "text": st.text(max_size=80),
    "avg_logprob": st.none() | st.floats(allow_nan=False),
    "no_speech_prob": st.none() | st.floats(allow_nan=False),
})


class TestTranscriptProperties:
    @PROP
    @given(seg=seg_strategy)
    def test_segment_dict_round_trip(self, seg):
        parsed = TranscriptSegment.from_dict(seg)
        again = TranscriptSegment.from_dict(parsed.to_dict())
        assert again == parsed

    @PROP
    @given(text=st.text(max_size=200),
           language=st.none() | st.text(max_size=8),
           segs=st.lists(seg_strategy, max_size=6))
    def test_transcript_dict_round_trip(self, text, language, segs):
        t = Transcript.from_dict({
            "text": text,
            "language": language,
            "duration": None,
            "segments": [dict(s) for s in segs],
        })
        assert Transcript.from_dict(t.to_dict()) == t
        # Transcript.of is the normalization point: idempotent
        assert Transcript.of(t) is t
        assert Transcript.of(t.to_dict()) == t

    @PROP
    @given(junk=st.recursive(
        st.none() | st.booleans() | st.integers() | st.floats(
            allow_nan=True, allow_infinity=True),
        lambda kids: st.lists(kids, max_size=3)
        | st.dictionaries(st.text(max_size=5), kids, max_size=3),
        max_leaves=8))
    def test_from_dict_never_raises_on_garbage(self, junk):
        # remote endpoints send what they like: tolerant parse, safe types
        t = Transcript.from_dict(junk)
        assert isinstance(t.text, str)
        assert t.language is None or isinstance(t.language, str)
        assert t.duration is None or isinstance(t.duration, float)
        assert all(isinstance(s, TranscriptSegment) for s in t.segments)
        # and the Mapping shim stays usable
        assert t["text"] == t.text


# ---------------------------------------------------------------------------
# metric arithmetic
# ---------------------------------------------------------------------------

class TestMetricProperties:
    @PROP
    @given(a=st.text(max_size=60), b=st.text(max_size=60))
    def test_levenshtein_is_a_metric_on_strings(self, a, b):
        # word-level inputs through normalize_words keep it discrete
        wa, wb = M.normalize_words(a), M.normalize_words(b)
        d_ab = M.levenshtein(wa, wb)
        assert d_ab >= 0
        assert d_ab == 0 if wa == wb else d_ab >= (0 if wa == wb else 1)
        assert d_ab == M.levenshtein(wb, wa)  # symmetry
        # triangle inequality via a midpoint
        for mid in (wa, wb, wa[:len(wa) // 2]):
            assert d_ab <= M.levenshtein(wa, mid) + M.levenshtein(mid, wb)

    @PROP
    @given(text=st.text(max_size=100))
    def test_identical_transcripts_score_zero(self, text):
        assert M.wer(text, text) == 0.0
        assert M.cer(text, text) == 0.0

    @PROP
    @given(ref=st.text(min_size=1, max_size=60),
           hyp=st.text(max_size=60))
    def test_wer_nonnegative_and_unbounded_above(self, ref, hyp):
        # the domain convention (module docstring): distance / reference
        # length — a hallucinating hypothesis against a short reference
        # scores ABOVE 1 and that is correct, not a bug
        score = M.wer(ref, hyp)
        assert score >= 0.0

    def test_wer_can_exceed_one(self):
        # 1 reference word, 5 inserted words -> 5/1 (the plan's explicit
        # invariant: WER must not be constrained to [0, 1])
        assert M.wer("hello", "hello a b c d e") == 5.0

    def test_empty_reference_rule(self):
        assert M.wer("", "") == 0.0
        assert M.wer("", "hallucination") == 1.0
        assert M.cer("", "x") == 1.0

    @PROP
    @given(hyp=st.text(max_size=100),
           hotwords=st.lists(st.text(max_size=12), max_size=6),
           ref=st.none() | st.text(max_size=100))
    def test_hotword_recall_bounds(self, hyp, hotwords, ref):
        r = M.hotword_recall(hyp, hotwords, reference=ref)
        assert r is None or 0.0 <= r <= 1.0

    @PROP
    @given(dur=st.floats(allow_nan=True, allow_infinity=True),
           proc=st.floats(allow_nan=True, allow_infinity=True))
    def test_rtf_positive_or_unscoreable(self, dur, proc):
        rtf = M.real_time_factor(dur, proc)
        if rtf is not None:
            assert rtf > 0.0
        # documented rule: non-positive or missing inputs never score
        if not dur or not proc or dur <= 0 or proc <= 0 \
                or dur != dur or proc != proc:
            assert rtf is None

    @PROP
    @given(values=st.lists(
        st.floats(min_value=-1e6, max_value=1e6, allow_nan=False),
        max_size=20))
    def test_mean_within_extremes(self, values):
        m = M.mean(values)
        if values:
            # sum/len can land one ULP outside the range at large
            # magnitudes (e.g. three identical 699050.7087230051s) —
            # allow exactly that floating error, no more
            lo, hi = min(values), max(values)
            ulps = (abs(lo) + abs(hi)) * sys.float_info.epsilon * len(values)
            assert lo - ulps <= m <= hi + ulps
        else:
            assert m is None


# ---------------------------------------------------------------------------
# config round trips
# ---------------------------------------------------------------------------

whitelisted_scalar_keys = [
    (section, key)
    for section, keys in {
        "general": ["language", "launch_on_login"],
        "hotkey": ["key", "cancel_key", "mode"],
        "recording": ["max_seconds", "first_pcm_timeout"],
        "sounds": ["enabled"],
    }.items()
    for key in keys
]

simple_values = st.one_of(
    st.booleans(),
    st.integers(min_value=0, max_value=10_000),
    st.floats(min_value=0, max_value=1000, allow_nan=False),
    # min_size=1: the empty string MEANS "unset, use the default" in the
    # save format (dropped on save, DEFAULTS restored on load) — that is
    # designed behavior, not a round-trip instability
    st.text(alphabet=st.characters(
        codec="utf-8", exclude_characters="\n\r\\"), min_size=1,
        max_size=24),
)


class TestConfigRoundTrip:
    @PROP
    @given(overrides=st.dictionaries(
        st.sampled_from(whitelisted_scalar_keys), simple_values,
        max_size=6))
    def test_save_load_save_is_a_fixpoint(self, tmp_path_factory, overrides):
        # not a fixture: hypothesis + function-scoped tmp_path interplay —
        # use a factory-made directory per example
        cfg = copy.deepcopy(DEFAULTS)
        for (section, key), value in overrides.items():
            cfg.setdefault(section, {})[key] = value
        f = tmp_path_factory.mktemp("cfg") / "config.toml"
        save_config(cfg, f)
        once = f.read_text()
        reloaded = load_config(f)
        save_config(reloaded, f)
        assert f.read_text() == once, (
            "save->load->save rewrote the config: the TOML round trip is "
            "unstable for values the settings UI can produce")


# ---------------------------------------------------------------------------
# unicode text processing
# ---------------------------------------------------------------------------

non_latin_text = st.text(
    alphabet=st.characters(
        min_codepoint=0x0400,  # Cyrillic onward: CJK, Arabic, symbols…
        exclude_categories=("Cs", "Cc")),
    max_size=120)


class TestUnicodeProcessing:
    @PROP
    @given(text=non_latin_text)
    def test_post_process_passes_non_latin_text_through(self, text):
        # filler/punctuation/dictionary rules are Latin-word based; a
        # dictation in another script must come out byte-identical
        cfg = copy.deepcopy(DEFAULTS)
        assert post_process(text, cfg) == text

    @PROP
    @given(text=st.text(max_size=200))
    def test_post_process_never_crashes_and_never_invents_text(self, text):
        out = post_process(text, copy.deepcopy(DEFAULTS))
        assert isinstance(out, str)
        # processing only REMOVES or reformats spoken tokens; it never
        # grows the transcript beyond punctuation expansion margins
        assert len(out) <= max(len(text), 8) + 4 * len(text) // 10 + 16


# ---------------------------------------------------------------------------
# chunk-boundary reconciliation
# ---------------------------------------------------------------------------

def chunk_pair(gap: float, overlap: float = 1.5) -> list[Chunk]:
    # two 60 s chunks stepped by (60 - overlap): the second starts where
    # the first has `overlap` seconds left
    first_end = 60.0
    second_start = 60.0 - overlap + gap
    return [Chunk(path=None, start=0.0, end=first_end),   # type: ignore[arg-type]
            Chunk(path=None, start=second_start, end=second_start + 60.0)]  # type: ignore[arg-type]


def tr(segments: list[tuple[float, float, str]]) -> Transcript:
    return Transcript(text=" ".join(t for _, _, t in segments),
                      segments=tuple(
                          TranscriptSegment(start=s, end=e, text=t)
                          for s, e, t in segments))


class TestMergeTranscriptsProperties:
    @PROP
    @given(distinct_a=st.text(min_size=1, max_size=30),
           distinct_b=st.text(min_size=1, max_size=30))
    def test_distinct_speech_at_boundary_is_never_silently_dropped(
            self, distinct_a, distinct_b):
        a, b = distinct_a.strip(), distinct_b.strip()
        if not a or not b or a.casefold() == b.casefold():
            return  # covered by the duplicate property below
        chunks = chunk_pair(0.0)
        results = [tr([(59.0, 59.9, a)]), tr([(0.2, 1.0, b)])]
        merged = merge_transcripts(chunks, results, 120.0)
        texts = [s.text for s in merged.segments]
        assert a in texts and b in texts, (
            f"boundary reconciliation deleted distinct speech: {texts}")

    @PROP
    @given(word=st.text(min_size=1, max_size=20))
    def test_exact_boundary_duplicate_is_dropped_exactly_once(self, word):
        w = word.strip()
        if not w:
            return
        chunks = chunk_pair(0.0)
        # both decoders transcribed the SAME utterance inside the
        # overlap window (same global start)
        results = [tr([(59.0, 59.8, w)]), tr([(0.3, 1.1, w)])]
        merged = merge_transcripts(chunks, results, 120.0)
        occurrences = [s.text for s in merged.segments].count(w)
        assert occurrences == 1

    def test_duplicate_outside_the_window_is_kept_visible(self):
        # same text, but far from the boundary: dropping it would be a
        # silent deletion of possibly-distinct speech
        chunks = chunk_pair(0.0)
        results = [tr([(10.0, 10.5, "meeting")]),
                   tr([(30.0, 30.5, "meeting")])]
        merged = merge_transcripts(chunks, results, 120.0)
        assert [s.text for s in merged.segments].count("meeting") == 2

    @PROP
    @given(segs_a=st.lists(
        st.tuples(st.floats(0, 59, allow_nan=False),
                  st.floats(0, 59, allow_nan=False),
                  st.text(min_size=1, max_size=20)), max_size=5),
       segs_b=st.lists(
        st.tuples(st.floats(0, 59, allow_nan=False),
                  st.floats(0, 59, allow_nan=False),
                  st.text(min_size=1, max_size=20)), max_size=5))
    def test_kept_segments_are_sorted_and_text_is_their_join(self, segs_a,
                                                             segs_b):
        chunks = chunk_pair(0.0)
        a = [(s, e, t) for s, e, t in segs_a if s <= e and t.strip()]
        b = [(s + chunks[1].start, e + chunks[1].start, t)
             for s, e, t in segs_b if s <= e and t.strip()]
        merged = merge_transcripts(chunks, [tr(a), tr(b)], 120.0)
        starts = [s.start for s in merged.segments]
        assert starts == sorted(starts)
        assert merged.text == " ".join(
            s.text.strip() for s in merged.segments if s.text.strip())
