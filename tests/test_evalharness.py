"""Evaluation harness: metric math, manifest, synth, runner (plan P1.4).

All hand-computed cases — these tests pin the *math*, not the models:
the harness computes metrics without any backend (models only produce
the texts being scored). Wave-2 measurement adapters G1–G7 (corpus-spec
§7): subgroup aggregation, omission rate, hallucination word rate, the
separate punctuation family, language confusion, guard-score sweep,
name/number recall convention.
"""
from __future__ import annotations

import json
import wave
from pathlib import Path

import pytest

from fluidvoice.evalharness import metrics
from fluidvoice.evalharness import synth as synth_mod
from fluidvoice.evalharness.manifest import (
    Case,
    ManifestError,
    SynthSpec,
    default_manifest_path,
    load_manifest,
    load_manifests,
)
from fluidvoice.evalharness.metrics import (
    GUARD_EXCLUDED,
    GUARD_FALSE_NEGATIVE,
    GUARD_FALSE_POSITIVE,
    GUARD_NOT_SCORED,
    GUARD_TRUE_NEGATIVE,
    GUARD_TRUE_POSITIVE,
    alignment_counts,
    cer,
    guard_category,
    guard_counts,
    hallucination_word_rate,
    hotword_recall,
    language_codes_match,
    levenshtein,
    levenshtein_alignment,
    mean,
    normalize_words,
    omission_rate,
    real_time_factor,
    sentence_start_capital_accuracy,
    snr_bucket,
    split_sentences,
    terminal_punctuation_accuracy,
    wer,
)
from fluidvoice.evalharness.runner import (
    GUARD_SWEEP_THRESHOLDS,
    TranscriptionOutput,
    TranscriptionOutputError,
    run_eval,
)

# --------------------------------------------------------------- metrics

class TestNormalization:
    def test_case_and_punctuation_insensitive(self):
        assert normalize_words("Hello, WORLD!") == ["hello", "world"]

    def test_intra_word_apostrophe_kept(self):
        assert normalize_words("it's Jack-Jones'") == ["it's", "jack", "jones"]

    def test_whitespace_collapses(self):
        assert normalize_words("a\t b\n  c") == ["a", "b", "c"]


class TestLevenshtein:
    def test_identity_and_empty(self):
        assert levenshtein([], []) == 0
        assert levenshtein("abc", "abc") == 0
        assert levenshtein("", "abc") == 3
        assert levenshtein("abc", "") == 3

    def test_hand_computed(self):
        assert levenshtein("kitten", "sitting") == 3
        assert levenshtein(["a", "b"], ["b", "a"]) == 2


class TestWER:
    def test_identity(self):
        assert wer("the quick brown fox", "THE QUICK BROWN FOX.") == 0.0

    def test_single_substitution(self):
        # ref 4 words, hyp substitutes one -> 1/4
        assert wer("the quick brown fox", "the quick black fox") == 0.25

    def test_single_deletion(self):
        assert wer("the quick brown fox", "the quick fox") == 0.25

    def test_single_insertion(self):
        assert wer("the quick brown fox", "the quick brown red fox") == 0.25

    def test_hand_computed_mixed(self):
        # ref "a b c d e" (5) vs "a x c d" (4): 1 sub + 1 del = 2/5
        assert wer("a b c d e", "a x c d") == pytest.approx(0.4)

    def test_empty_reference_rules(self):
        assert wer("", "") == 0.0
        assert wer("", "something") == 1.0
        assert wer("something", "") == 1.0


class TestCER:
    def test_identity(self):
        assert cer("hello world", "Hello, WORLD!") == 0.0

    def test_single_char_substitution(self):
        # normalized "cat"/"cut": 1 sub over 3 chars
        assert cer("cat", "cut") == pytest.approx(1 / 3)

    def test_spaces_are_characters(self):
        # "abcd" vs "ab cd": one inserted space over 4 reference chars
        assert cer("abcd", "ab cd") == pytest.approx(1 / 4)

    def test_empty_reference_rules(self):
        assert cer("", "") == 0.0
        assert cer("", "x") == 1.0


class TestHotwordRecall:
    def test_full_recall(self):
        assert hotword_recall("the quick brown fox", ["quick", "fox"]) == 1.0

    def test_partial_recall(self):
        r = hotword_recall("the quick brown dog", ["quick", "fox"],
                           reference="the quick brown fox")
        assert r == 0.5

    def test_tags_absent_from_reference_not_scored(self):
        # "lazy" is a tag but not in the reference -> only quick/fox score
        r = hotword_recall("quick fox", ["quick", "fox", "lazy"],
                           reference="the quick brown fox")
        assert r == 1.0

    def test_no_scoreable_hotwords_is_none(self):
        assert hotword_recall("anything", [], "reference") is None
        assert hotword_recall("anything", ["zebra"],
                              reference="no such animal") is None

    def test_case_and_punctuation_tolerant(self):
        assert hotword_recall("It's the Quick, Brown Fox!", ["brown fox"]) \
            == 1.0


class TestRealTimeFactor:
    def test_trivial_faster_than_realtime(self):
        assert real_time_factor(10.0, 5.0) == 2.0

    def test_slower_than_realtime(self):
        assert real_time_factor(2.0, 4.0) == 0.5

    @pytest.mark.parametrize("dur,proc", [(0.0, 1.0), (None, 1.0),
                                          (1.0, 0.0), (1.0, None),
                                          (-1.0, 1.0), (1.0, -1.0)])
    def test_unscoreable(self, dur, proc):
        assert real_time_factor(dur, proc) is None

    def test_direction_pinned_greater_is_faster(self):
        """Pin the RTF direction (G-wave-2 / corpus-spec §7 restates it).

        RTF = audio / processing, so > 1 is FASTER than realtime — the
        deliberate existing definition; the sweep of adapter work must
        never flip it. A 4x-realtime decode scores HIGHER than a
        0.25x-realtime one.
        """
        fast = real_time_factor(10.0, 2.5)
        slow = real_time_factor(10.0, 40.0)
        assert fast == 4.0 and slow == 0.25
        assert fast > 1.0 > slow


class TestMean:
    def test_mean_and_empty(self):
        assert mean([1.0, 2.0, 3.0]) == 2.0
        assert mean([]) is None
        assert mean([None, 2.0]) == 2.0  # non-numeric skipped


class TestGuardConfusion:
    def test_all_categories(self):
        assert guard_category("flag", "flag") == GUARD_TRUE_POSITIVE
        assert guard_category("ok", "ok") == GUARD_TRUE_NEGATIVE
        assert guard_category("ok", "flag") == GUARD_FALSE_POSITIVE
        assert guard_category("flag", "ok") == GUARD_FALSE_NEGATIVE
        assert guard_category("none", "flag") == GUARD_EXCLUDED
        assert guard_category("ok", "none") == GUARD_NOT_SCORED

    def test_invalid_inputs_raise(self):
        with pytest.raises(ValueError, match="expected guard"):
            guard_category("maybe", "ok")
        with pytest.raises(ValueError, match="observed guard"):
            guard_category("ok", "maybe")

    def test_counts_hand_computed(self):
        cats = [guard_category(e, o) for e, o in
                [("ok", "ok"), ("ok", "flag"), ("ok", "flag"),
                 ("flag", "flag"), ("flag", "ok"), ("none", "ok")]]
        counts = guard_counts(cats)
        assert counts[GUARD_TRUE_NEGATIVE] == 1
        assert counts[GUARD_FALSE_POSITIVE] == 2
        assert counts[GUARD_TRUE_POSITIVE] == 1
        assert counts[GUARD_FALSE_NEGATIVE] == 1
        assert counts[GUARD_EXCLUDED] == 1
        assert counts[GUARD_NOT_SCORED] == 0



class TestLevenshteinAlignment:
    """G2: the alignment-returning Levenshtein behind omission rate."""

    @pytest.mark.parametrize("a,b", [("kitten", "sitting"),
                                     ("same", "same"),
                                     ("abc", ""),
                                     ("", "abc"),
                                     ("a b c d e", "a x c d")])
    def test_nonmatch_ops_equal_distance(self, a, b):
        ops = levenshtein_alignment(a.split() if " " in a else list(a),
                                    b.split() if " " in b else list(b))
        assert sum(1 for op, _, _ in ops if op != "match") \
            == levenshtein(a.split() if " " in a else list(a),
                           b.split() if " " in b else list(b))

    def test_alignment_reconstructs_both_sides(self):
        ops = levenshtein_alignment("kitten", "sitting")
        assert "".join(x for _, x, _ in ops if x is not None) == "kitten"
        assert "".join(y for _, _, y in ops if y is not None) == "sitting"

    def test_hand_computed_mixed(self):
        ops = levenshtein_alignment(["a", "b", "c", "d", "e"],
                                    ["a", "x", "c", "d"])
        assert alignment_counts(ops) == {"match": 3, "substitution": 1,
                                         "deletion": 1, "insertion": 0}

    def test_empty_sides(self):
        assert levenshtein_alignment([], []) == []
        assert levenshtein_alignment([], ["x"]) == [("insertion", None, "x")]
        assert levenshtein_alignment(["x"], []) == [("deletion", "x", None)]


class TestOmissionRate:
    def test_pure_deletion(self):
        assert omission_rate("the quick brown fox", "the quick fox") \
            == pytest.approx(0.25)

    def test_only_deletions_count_not_substitutions(self):
        # 1 sub + 1 del over 5 ref words: WER 0.4, omission 0.2
        assert wer("a b c d e", "a x c d") == pytest.approx(0.4)
        assert omission_rate("a b c d e", "a x c d") == pytest.approx(0.2)

    def test_insertions_are_not_omissions(self):
        assert omission_rate("a b", "a b extra words") == 0.0

    def test_everything_omitted(self):
        assert omission_rate("a b c", "") == 1.0

    def test_empty_reference_not_scoreable(self):
        assert omission_rate("", "hallucinated") is None
        assert omission_rate("", "") is None


class TestHallucinationWordRate:
    def test_tokens_per_second(self):
        assert hallucination_word_rate("thank you for watching", 2.0) \
            == pytest.approx(2.0)
        assert hallucination_word_rate("One, two!", 1.0) \
            == pytest.approx(2.0)

    def test_silence_scored_zero(self):
        assert hallucination_word_rate("", 1.0) == 0.0

    @pytest.mark.parametrize("dur", [None, 0.0, -1.0])
    def test_unscoreable_durations(self, dur):
        assert hallucination_word_rate("words", dur) is None


class TestSplitSentences:
    def test_bodies_and_marks(self):
        assert split_sentences("Hello there. How are you?") \
            == [("Hello there", "."), ("How are you", "?")]

    def test_trailing_unterminated_fragment(self):
        assert split_sentences("One. two three") \
            == [("One", "."), ("two three", "")]

    def test_punctuation_runs_are_single_strict_marks(self):
        assert split_sentences("Wait... what?!") \
            == [("Wait", "..."), ("what", "?!")]

    def test_contentless_fragments_dropped(self):
        assert split_sentences(". a") == [("a", "")]
        assert split_sentences("") == []


class TestTerminalPunctuation:
    def test_perfect(self):
        assert terminal_punctuation_accuracy("One. Two?", "One. Two?") == 1.0

    def test_missing_final_mark(self):
        assert terminal_punctuation_accuracy("One. Two?", "One. Two") \
            == pytest.approx(0.5)

    def test_missing_hypothesis_sentence(self):
        assert terminal_punctuation_accuracy("Yes. No.", "Yes.") \
            == pytest.approx(0.5)

    def test_wrong_mark(self):
        assert terminal_punctuation_accuracy("Ready?", "Ready.") == 0.0

    def test_runs_compared_strictly(self):
        assert terminal_punctuation_accuracy("Wait...", "Wait.") == 0.0

    def test_no_terminal_marks_not_scoreable(self):
        assert terminal_punctuation_accuracy("no punctuation", "any") is None
        assert terminal_punctuation_accuracy("", "") is None


class TestSentenceStartCapitals:
    def test_perfect(self):
        assert sentence_start_capital_accuracy(
            "One two. Three four.", "One two. Three four.") == 1.0

    def test_lowercased_hypothesis(self):
        assert sentence_start_capital_accuracy("One. Two.", "one. two.") \
            == 0.0

    def test_mixed(self):
        assert sentence_start_capital_accuracy("One. Two.", "one. Two.") \
            == pytest.approx(0.5)

    def test_agrees_with_reference_not_blind_uppercase(self):
        # a lowercase-start reference must STAY lowercase
        assert sentence_start_capital_accuracy(
            "chat style. no caps.", "chat style. No caps.") \
            == pytest.approx(0.5)

    def test_german_sentence_starts(self):
        assert sentence_start_capital_accuracy(
            "Der Hund schläft. Er schnarcht.",
            "der Hund schläft. Er schnarcht.") == pytest.approx(0.5)

    def test_missing_hypothesis_sentence_counts_wrong(self):
        assert sentence_start_capital_accuracy("A. B.", "A.") \
            == pytest.approx(0.5)

    def test_no_letters_not_scoreable(self):
        assert sentence_start_capital_accuracy("123 456.", "789.") is None


class TestPunctuationFamilyIsSeparate:
    """G4 pin: punctuation/case never feed WER normalization, and the
    punctuation family never sees normalized text — a punctuation-only
    difference is WER-invisible and punctuation-visible."""

    def test_punctuation_only_difference(self):
        ref, hyp = "Hello there. It's me!", "hello there it's me"
        assert wer(ref, hyp) == 0.0
        assert cer(ref, hyp) == 0.0
        assert terminal_punctuation_accuracy(ref, hyp) == 0.0
        assert sentence_start_capital_accuracy(ref, hyp) == 0.0

    def test_word_errors_do_not_confuse_the_family(self):
        # same words, same punctuation -> family perfect even mid-WER-noise
        ref, hyp = "Send it now. Please.", "Send it now. Please."
        assert terminal_punctuation_accuracy(ref, hyp) == 1.0
        assert sentence_start_capital_accuracy(ref, hyp) == 1.0


class TestLanguageCodesMatch:
    def test_primary_subtag_casefolded(self):
        assert language_codes_match("en", "en") is True
        assert language_codes_match("en-US", "en") is True
        assert language_codes_match("en", "EN_us") is True
        assert language_codes_match("sl", "sl") is True

    def test_confusions(self):
        assert language_codes_match("de", "en") is False
        assert language_codes_match("pt", "es") is False

    def test_empty_never_matches(self):
        assert language_codes_match("", "") is False
        assert language_codes_match("en", "") is False


class TestSnrBucket:
    @pytest.mark.parametrize("snr,expected", [
        (-3.0, "<5"), (4.9, "<5"), (5.0, "5-10"), (9.9, "5-10"),
        (10.0, "10-20"), (19.9, "10-20"), (20.0, ">=20"),
        (42.0, ">=20"), (None, None)])
    def test_half_open_upward(self, snr, expected):
        assert snr_bucket(snr) == expected


# --------------------------------------------------------------- manifest

VALID_CASE = """
[[cases]]
id = "c1"
audio = "a.wav"
reference_text = "hello world"
language = "en"
tags = ["hello"]
expected_guard = "ok"
license = "CC0-1.0"
source = "synthetic"
"""


def write_manifest(tmp_path: Path, body: str,
                   name: str = "manifest.toml") -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


class TestManifestLoad:
    def test_valid_round_trip(self, tmp_path):
        p = write_manifest(tmp_path, VALID_CASE)
        cases = load_manifest(p)
        assert len(cases) == 1
        c = cases[0]
        assert c.id == "c1"
        assert c.audio == (tmp_path / "a.wav").resolve()
        assert c.reference_text == "hello world"
        assert c.language == "en"
        assert c.tags == ("hello",)
        assert c.expected_guard == "ok"
        assert c.license == "CC0-1.0"
        assert c.source == "synthetic"

    def test_directory_resolves_manifest_toml(self, tmp_path):
        write_manifest(tmp_path, VALID_CASE)
        cases = load_manifest(tmp_path)
        assert [c.id for c in cases] == ["c1"]

    def test_directory_without_manifest_is_an_error(self, tmp_path):
        with pytest.raises(ManifestError, match="no manifest.toml"):
            load_manifest(tmp_path)

    @pytest.mark.parametrize("field", ["id", "audio", "reference_text",
                                       "language", "tags", "expected_guard",
                                       "license", "source"])
    def test_missing_required_field_names_it(self, tmp_path, field):
        body = "\n".join(line for line in VALID_CASE.strip().splitlines()
                         if not line.startswith(f"{field} ="))
        p = write_manifest(tmp_path, body)
        with pytest.raises(ManifestError, match=f"'{field}'"):
            load_manifest(p)

    def test_bad_expected_guard_value(self, tmp_path):
        body = VALID_CASE.replace('expected_guard = "ok"',
                                  'expected_guard = "maybe"')
        p = write_manifest(tmp_path, body)
        with pytest.raises(ManifestError, match="expected_guard"):
            load_manifest(p)

    def test_bad_tags_type(self, tmp_path):
        body = VALID_CASE.replace('tags = ["hello"]', "tags = 3")
        p = write_manifest(tmp_path, body)
        with pytest.raises(ManifestError, match="tags"):
            load_manifest(p)

    def test_duplicate_id_within_manifest(self, tmp_path):
        p = write_manifest(tmp_path, VALID_CASE + VALID_CASE)
        with pytest.raises(ManifestError, match="duplicate case id"):
            load_manifest(p)

    def test_invalid_toml(self, tmp_path):
        p = write_manifest(tmp_path, "cases = [")
        with pytest.raises(ManifestError, match="invalid TOML"):
            load_manifest(p)

    def test_no_cases(self, tmp_path):
        p = write_manifest(tmp_path, "[meta]\nversion = 1\n")
        with pytest.raises(ManifestError, match="no \\[\\[cases\\]\\]"):
            load_manifest(p)

    def test_error_message_carries_case_id(self, tmp_path):
        body = VALID_CASE.replace('license = "CC0-1.0"', "")
        p = write_manifest(tmp_path, body)
        with pytest.raises(ManifestError, match="id 'c1'"):
            load_manifest(p)

    def test_synth_spec_parsed(self, tmp_path):
        body = VALID_CASE + \
            'synth = { kind = "tone", seconds = 1.5, rate = 8000 }\n'
        cases = load_manifest(write_manifest(tmp_path, body))
        assert cases[0].synth == SynthSpec("tone", 1.5, 8000)

    def test_bad_synth_kind(self, tmp_path):
        body = VALID_CASE + 'synth = { kind = "music", seconds = 1.0 }\n'
        p = write_manifest(tmp_path, body)
        with pytest.raises(ManifestError, match="synth.kind"):
            load_manifest(p)

    def test_extra_keys_tolerated(self, tmp_path):
        body = VALID_CASE + 'owner = "someone"\n'
        cases = load_manifest(write_manifest(tmp_path, body))
        assert cases[0].id == "c1"

    def test_empty_reference_text_allowed(self, tmp_path):
        body = VALID_CASE.replace('reference_text = "hello world"',
                                  'reference_text = ""')
        cases = load_manifest(write_manifest(tmp_path, body))
        assert cases[0].reference_text == ""


class TestManifestProvenance:
    """Corpus-spec §1 provenance keys: optional, typed, promoted to
    Case fields (G1 subgroups / G6 sweep depend on them)."""

    def test_provenance_round_trip(self, tmp_path):
        body = VALID_CASE + (
            'speaker = "S07"\n'
            'mic_class = "headset-usb"\n'
            'taxonomy = "t3-jargon"\n'
            'snr_db = 14.2\n'
            'split = "train"\n')
        c = load_manifest(write_manifest(tmp_path, body))[0]
        assert c.speaker == "S07"
        assert c.mic_class == "headset-usb"
        assert c.taxonomy == "t3-jargon"
        assert c.snr_db == pytest.approx(14.2)
        assert c.split == "train"

    def test_defaults_are_none(self, tmp_path):
        c = load_manifest(write_manifest(tmp_path, VALID_CASE))[0]
        assert c.speaker is None and c.mic_class is None
        assert c.taxonomy is None and c.snr_db is None and c.split is None

    @pytest.mark.parametrize("key, value", [
        ("speaker", '""'), ("mic_class", "3"),
        ("taxonomy", "' '"), ("split", '""')])
    def test_bad_string_provenance_rejected(self, tmp_path, key, value):
        p = write_manifest(tmp_path, VALID_CASE + f"{key} = {value}\n")
        with pytest.raises(ManifestError, match=key):
            load_manifest(p)

    def test_bad_snr_rejected(self, tmp_path):
        p = write_manifest(tmp_path, VALID_CASE + 'snr_db = "loud"\n')
        with pytest.raises(ManifestError, match="snr_db"):
            load_manifest(p)

    def test_snr_int_accepted(self, tmp_path):
        c = load_manifest(write_manifest(
            tmp_path, VALID_CASE + "snr_db = 18\n"))[0]
        assert c.snr_db == 18.0


class TestManifestMerge:
    def test_external_merge(self, tmp_path):
        p1 = write_manifest(tmp_path, VALID_CASE)
        ext = tmp_path / "private"
        ext.mkdir()
        write_manifest(ext, VALID_CASE.replace('"c1"', '"c2"'), "manifest.toml")
        merged = load_manifests([p1, ext / "manifest.toml"])
        assert [c.id for c in merged] == ["c1", "c2"]

    def test_duplicate_id_across_manifests(self, tmp_path):
        p1 = write_manifest(tmp_path, VALID_CASE)
        p2 = write_manifest(tmp_path, VALID_CASE, "manifest2.toml")
        with pytest.raises(ManifestError, match="duplicate case id"):
            load_manifests([p1, p2])


class TestPackagedCorpus:
    def test_default_manifest_loads(self):
        cases = load_manifest(default_manifest_path())
        assert len(cases) >= 3
        assert len({c.id for c in cases}) == len(cases)
        assert all(c.license for c in cases)
        assert all(c.synth is not None for c in cases)

    def test_ids_stable(self):
        cases = load_manifest(default_manifest_path())
        assert [c.id for c in cases] == ["tone-en-basic", "chirp-en-hotwords",
                                         "silence-guard"]


# ------------------------------------------------------------------ synth

class TestSynth:
    def test_deterministic_bytes(self, tmp_path):
        spec = SynthSpec("tone", 0.5)
        a = synth_mod.synth_wav(tmp_path / "a.wav", spec)
        b = synth_mod.synth_wav(tmp_path / "b.wav", spec)
        assert a.read_bytes() == b.read_bytes()

    @pytest.mark.parametrize("kind,seconds", [("tone", 1.0),
                                              ("chirp", 2.0),
                                              ("silence", 1.0)])
    def test_header_and_duration(self, tmp_path, kind, seconds):
        spec = SynthSpec(kind, seconds)
        path = synth_mod.synth_wav(tmp_path / "x.wav", spec)
        with wave.open(str(path), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getnframes() == int(spec.rate * seconds)
        assert synth_mod.wav_duration_seconds(path) == pytest.approx(seconds)

    def test_silence_is_silent_tone_is_loud(self, tmp_path):
        quiet = synth_mod.synth_wav(tmp_path / "q.wav", SynthSpec("silence", 0.3))
        loud = synth_mod.synth_wav(tmp_path / "l.wav", SynthSpec("tone", 0.3))
        with wave.open(str(quiet), "rb") as wf:
            assert set(wf.readframes(wf.getnframes())) == {0}
        with wave.open(str(loud), "rb") as wf:
            assert max(wf.readframes(wf.getnframes())) > 0

    def test_duration_probe_non_wav_is_none(self, tmp_path):
        junk = tmp_path / "junk.wav"
        junk.write_bytes(b"not a wav")
        assert synth_mod.wav_duration_seconds(junk) is None

    def test_ensure_case_audio_synthesizes(self, tmp_path):
        case = Case(id="x", audio=tmp_path / "s.wav", reference_text="",
                    language="en", tags=(), expected_guard="none",
                    license="CC0-1.0", source="s",
                    synth=SynthSpec("tone", 0.4))
        path = synth_mod.ensure_case_audio(case)
        assert path.exists() and path == case.audio

    def test_ensure_case_audio_missing_without_recipe(self, tmp_path):
        case = Case(id="x", audio=tmp_path / "gone.wav", reference_text="",
                    language="en", tags=(), expected_guard="none",
                    license="CC0-1.0", source="s")
        with pytest.raises(FileNotFoundError, match="no synth recipe"):
            synth_mod.ensure_case_audio(case)

    def test_ensure_case_audio_respects_no_synth(self, tmp_path):
        case = Case(id="x", audio=tmp_path / "s.wav", reference_text="",
                    language="en", tags=(), expected_guard="none",
                    license="CC0-1.0", source="s",
                    synth=SynthSpec("tone", 0.4))
        with pytest.raises(FileNotFoundError, match="--no-synth"):
            synth_mod.ensure_case_audio(case, allow_synth=False)


# ----------------------------------------------------------------- runner

RUNNER_MANIFEST = """
[[cases]]
id = "ok-case"
audio = "ok.wav"
reference_text = "the quick brown fox"
language = "en"
tags = ["quick", "fox"]
expected_guard = "ok"
license = "CC0-1.0"
source = "synthetic"
synth = { kind = "tone", seconds = 1.0 }

[[cases]]
id = "guard-case"
audio = "guard.wav"
reference_text = ""
language = "en"
tags = []
expected_guard = "flag"
license = "CC0-1.0"
source = "synthetic"
synth = { kind = "silence", seconds = 1.0 }

[[cases]]
id = "unrelated-case"
audio = "unrelated.wav"
reference_text = "unrelated words"
language = "en"
tags = []
expected_guard = "none"
license = "CC0-1.0"
source = "synthetic"
synth = { kind = "chirp", seconds = 1.0 }
"""

# fake transcriber outputs, hand-picked per case id above
FAKE_OUTPUTS = {
    "ok-case": {"text": "the quick black dog",          # 2 subs / 4 -> WER .5
                "guard": "flag",                        # expected ok -> FP
                "first_preview_latency_s": 0.3,
                "final_latency_s": 1.2,
                "processing_time_s": 2.0,               # RTF 1.0s/2.0s = 0.5
                "segments": [{"start": 0.0, "end": 1.0, "text": "raw"}]},
    "guard-case": {"text": "", "guard": "flag",        # expected flag -> TP
                    "processing_time_s": 4.0},          # RTF 1.0/4.0 = 0.25
    "unrelated-case": {"text": "UNRELATED words!",      # normalization -> 0
                        "processing_time_s": 5.0,       # RTF 1.0/5.0 = 0.2
                        "extra": [1, 2]},
}


def fake_transcriber(audio_path: Path, case: Case) -> dict:
    return dict(FAKE_OUTPUTS[case.id])


class TestTranscriptionOutput:
    def test_from_mapping_minimal(self):
        out = TranscriptionOutput.from_mapping({"text": "hi"})
        assert out.text == "hi"
        assert out.guard == metrics.GUARD_NONE
        assert out.raw == {}

    def test_from_mapping_full_and_raw_kept(self):
        out = TranscriptionOutput.from_mapping(FAKE_OUTPUTS["ok-case"])
        assert out.guard == "flag"
        assert out.first_preview_latency_s == 0.3
        assert out.final_latency_s == 1.2
        assert out.processing_time_s == 2.0
        assert out.raw == {"segments": [{"start": 0.0, "end": 1.0,
                                         "text": "raw"}]}

    def test_missing_text_raises(self):
        with pytest.raises(TranscriptionOutputError, match="text"):
            TranscriptionOutput.from_mapping({"guard": "ok"})

    def test_bad_guard_raises(self):
        with pytest.raises(TranscriptionOutputError, match="guard"):
            TranscriptionOutput.from_mapping({"text": "x", "guard": "?"})

    def test_negative_latency_raises(self):
        with pytest.raises(TranscriptionOutputError, match="latency"):
            TranscriptionOutput.from_mapping(
                {"text": "x", "first_preview_latency_s": -1})


class TestRunEvalRoundTrip:
    @pytest.fixture()
    def run_dir(self, tmp_path):
        m = write_manifest(tmp_path, RUNNER_MANIFEST)
        report = run_eval(load_manifest(m), fake_transcriber,
                          out_dir=tmp_path / "out", manifests=[m])
        return tmp_path, report

    def test_reports_written_and_parse(self, run_dir):
        tmp_path, report = run_dir
        rj = json.loads((tmp_path / "out" / "report.json").read_text())
        assert rj == report
        md = (tmp_path / "out" / "report.md").read_text()
        assert "# Speech evaluation report" in md
        assert "## Cases" in md
        for cid in FAKE_OUTPUTS:
            assert cid in md

    def test_metrics_sane_hand_computed(self, run_dir):
        _, report = run_dir
        by_id = {c["id"]: c for c in report["cases"]}
        ok = by_id["ok-case"]["metrics"]
        assert ok["wer"] == pytest.approx(0.5)
        # 'brown'->'black' (4 subs) + 'fox'->'dog' (2 subs) over 19 chars
        assert ok["cer"] == pytest.approx(6 / 19)
        assert ok["hotword_recall"] == 0.5         # quick hit, fox missed
        assert ok["real_time_factor"] == pytest.approx(0.5)
        assert ok["first_preview_latency_s"] == 0.3
        assert ok["final_latency_s"] == 1.2
        assert ok["audio_duration_s"] == pytest.approx(1.0)

        # normalized identity case
        assert by_id["unrelated-case"]["metrics"]["wer"] == 0.0

    def test_guard_confusion_counts(self, run_dir):
        _, report = run_dir
        by_id = {c["id"]: c for c in report["cases"]}
        g = report["summary"]["guard"]
        assert g[GUARD_FALSE_POSITIVE] == 1   # ok-case expected ok, flagged
        assert g[GUARD_TRUE_POSITIVE] == 1    # guard-case expected flag, flagged
        assert g[GUARD_FALSE_NEGATIVE] == 0
        assert g[GUARD_EXCLUDED] == 1         # unrelated-case expected none
        assert by_id["ok-case"]["guard_category"] == GUARD_FALSE_POSITIVE

    def test_raw_outputs_recorded(self, run_dir):
        _, report = run_dir
        by_id = {c["id"]: c for c in report["cases"]}
        assert by_id["ok-case"]["raw"] == {
            "segments": [{"start": 0.0, "end": 1.0, "text": "raw"}]}
        assert by_id["unrelated-case"]["raw"] == {"extra": [1, 2]}

    def test_summary_aggregates(self, run_dir):
        _, report = run_dir
        s = report["summary"]
        assert s["cases"] == 3
        assert s["errors"] == 0
        # wer over {0.5, 0.0 (empty ref, empty hyp), 0.0 (identity)}
        assert s["wer_mean"] == pytest.approx(0.5 / 3)
        assert s["first_preview_latency_mean_s"] == pytest.approx(0.3)
        # RTF over {1.0/2.0, 1.0/4.0, 1.0/5.0} (all 1-second fixtures)
        assert s["real_time_factor_mean"] == pytest.approx(
            (0.5 + 0.25 + 0.2) / 3)
        assert report["harness_version"]

    def test_case_error_recorded_not_fatal(self, tmp_path):
        m = write_manifest(tmp_path, RUNNER_MANIFEST)

        def flaky(audio_path: Path, case: Case) -> dict:
            if case.id == "guard-case":
                raise RuntimeError("backend exploded")
            return {"text": case.reference_text}  # perfect transcripts

        report = run_eval(load_manifest(m), flaky,
                          out_dir=tmp_path / "out2", manifests=[m])
        by_id = {c["id"]: c for c in report["cases"]}
        assert "backend exploded" in by_id["guard-case"]["error"]
        assert report["summary"]["errors"] == 1
        # healthy cases still scored (and excluded the errored one)
        assert by_id["ok-case"]["metrics"]["wer"] == 0.0
        assert by_id["guard-case"]["metrics"] is None
        assert report["summary"]["wer_mean"] == 0.0

    def test_missing_audio_without_synth_is_case_error(self, tmp_path):
        body = RUNNER_MANIFEST.replace(
            'synth = { kind = "tone", seconds = 1.0 }', "")
        m = write_manifest(tmp_path, body)

        def tr(audio_path: Path, case: Case) -> dict:
            return {"text": "x"}

        report = run_eval(load_manifest(m), tr, out_dir=tmp_path / "out3",
                          manifests=[m])
        assert report["summary"]["errors"] == 1
        assert "no synth recipe" in \
            [c["error"] for c in report["cases"] if c["error"]][0]

    def test_soak_section_embedded(self, tmp_path):
        m = write_manifest(tmp_path, RUNNER_MANIFEST)
        soak = {"source": "soak.csv", "samples": 5, "duration_h": 2.0,
                "rss_drift_mb": 12.0, "rss_start_mb": 900.0,
                "rss_end_mb": 912.0, "rss_max_mb": 915.0,
                "cpu_total_s": 30.0, "cpu_s_per_hour": 15.0,
                "duration_s": 7200.0}
        report = run_eval(load_manifest(m), fake_transcriber,
                          out_dir=tmp_path / "out4", manifests=[m], soak=soak)
        assert report["soak"]["rss_drift_mb"] == 12.0
        md = (tmp_path / "out4" / "report.md").read_text()
        assert "## Soak" in md and "soak.csv" in md

    def test_rtf_wall_clock_fallback(self, tmp_path):
        m = write_manifest(tmp_path, RUNNER_MANIFEST)

        def tr(audio_path: Path, case: Case) -> dict:
            return {"text": case.reference_text}  # no processing_time_s

        report = run_eval(load_manifest(m), tr, out_dir=tmp_path / "out5",
                          manifests=[m])
        ok = [c for c in report["cases"] if c["id"] == "ok-case"][0]
        proc = ok["metrics"]["processing_time_s"]
        assert proc is not None and proc >= 0.0
        rtf = ok["metrics"]["real_time_factor"]
        assert rtf is not None and rtf > 0


# ------------------------------------------- wave-2 adapters (G1-G7 report)

PROVENANCE_MANIFEST = """
[[cases]]
id = "S01-T1-EN-001"
audio = "a1.wav"
reference_text = "the quick brown fox"
language = "en"
tags = ["quick", "fox"]
expected_guard = "ok"
license = "CC0-1.0"
source = "synthetic"
synth = { kind = "tone", seconds = 1.0 }
speaker = "S01"
mic_class = "headset-usb"
taxonomy = "t1-commands"
snr_db = 22.0
split = "train"

[[cases]]
id = "S01-T4-EN-002"
audio = "a2.wav"
reference_text = "call ana\u00efs m\u00fcller at 192.168.0.1"
language = "en"
tags = ["ana\u00efs m\u00fcller", "192.168.0.1"]
expected_guard = "ok"
license = "CC0-1.0"
source = "synthetic"
synth = { kind = "tone", seconds = 1.0 }
speaker = "S01"
mic_class = "headset-usb"
taxonomy = "t4-names-numbers"
snr_db = 22.0
split = "train"

[[cases]]
id = "S02-T2-DE-001"
audio = "a3.wav"
reference_text = "Guten Morgen. Wie sp\u00e4t ist es?"
language = "de"
tags = ["sp\u00e4t"]
expected_guard = "ok"
license = "CC0-1.0"
source = "synthetic"
synth = { kind = "chirp", seconds = 2.0 }
speaker = "S02"
mic_class = "laptop-array"
taxonomy = "t2-chat"
snr_db = 12.0
split = "heldout"

[[cases]]
id = "NEG-SILENCE-001"
audio = "a4.wav"
reference_text = ""
language = "en"
tags = []
expected_guard = "flag"
license = "CC0-1.0"
source = "synthetic"
synth = { kind = "silence", seconds = 2.0 }
taxonomy = "negative-silence"
split = "train"
"""

PROVENANCE_OUTPUTS = {
    # WER 2 subs/4 = 0.5, omission 0, hotwords .5; RTF 1.0s/2.0s = 0.5;
    # no terminal marks in ref -> punctuation None; capitals 't'/'t' -> 1
    "S01-T1-EN-001": {"text": "the quick black dog", "guard": "ok",
                      "processing_time_s": 2.0, "guard_score": 0.2,
                      "detected_language": "en-US"},
    # WER 3 subs/8 = 0.375 (ana\u00efs, m\u00fcller, 1->2), omission 0;
    # both name/number tags missed -> recall 0.0 (G7-style miss);
    # ref "192.168.0.1" = three '.' marks, hyp has none -> terminal 0/3
    "S01-T4-EN-002": {"text": "call anais muller at 192 168 0 2",
                      "guard": "ok", "processing_time_s": 2.0,
                      "guard_score": 0.5, "detected_language": "en"},
    # 1 deletion over 6 -> WER 1/6, omission 1/6; terminal: ref marks
    # [., ?] vs hyp [., ""] -> .5; capitals 'G','W' vs 'g','w' -> 0;
    # detected 'en' for expected 'de' -> one confusion row
    "S02-T2-DE-001": {"text": "guten morgen. wie sp\u00e4t ist",
                      "guard": "ok", "processing_time_s": 4.0,
                      "guard_score": 0.1, "detected_language": "en"},
    # empty ref, 4 tokens / 2.0 s = 2.0 words/s; guard TP
    "NEG-SILENCE-001": {"text": "thank you for watching", "guard": "flag",
                        "processing_time_s": 4.0, "guard_score": 0.9},
}


def provenance_transcriber(audio_path: Path, case: Case) -> dict:
    return dict(PROVENANCE_OUTPUTS[case.id])


@pytest.fixture()
def provenance_report(tmp_path):
    m = write_manifest(tmp_path, PROVENANCE_MANIFEST)
    report = run_eval(load_manifest(m), provenance_transcriber,
                      out_dir=tmp_path / "out", manifests=[m])
    return tmp_path, report


class TestProvenanceCaseMetrics:
    """Per-case G2/G3/G4/G5 values on the hand-computed fixture."""

    def by_id(self, report):
        return {c["id"]: c for c in report["cases"]}

    def test_omission_reported_with_wer(self, provenance_report):
        _, report = provenance_report
        m = self.by_id(report)["S02-T2-DE-001"]["metrics"]
        assert m["wer"] == pytest.approx(1 / 6)
        assert m["omission"] == pytest.approx(1 / 6)
        m = self.by_id(report)["S01-T1-EN-001"]["metrics"]
        assert m["omission"] == 0.0

    def test_omission_none_on_negative_cases(self, provenance_report):
        _, report = provenance_report
        m = self.by_id(report)["NEG-SILENCE-001"]["metrics"]
        assert m["omission"] is None

    def test_hallucination_rate_only_on_empty_reference(self,
                                                        provenance_report):
        _, report = provenance_report
        by_id = self.by_id(report)
        neg = by_id["NEG-SILENCE-001"]["metrics"]
        assert neg["hallucination_word_rate"] == pytest.approx(2.0)
        assert neg["audio_duration_s"] == pytest.approx(2.0)
        assert by_id["S01-T1-EN-001"]["metrics"][
            "hallucination_word_rate"] is None

    def test_punctuation_family_per_case(self, provenance_report):
        _, report = provenance_report
        by_id = self.by_id(report)
        t1 = by_id["S01-T1-EN-001"]["metrics"]
        assert t1["terminal_punctuation_accuracy"] is None   # no marks
        assert t1["sentence_start_capital_accuracy"] == 1.0
        t4 = by_id["S01-T4-EN-002"]["metrics"]
        assert t4["terminal_punctuation_accuracy"] == 0.0    # 0/3 marks
        assert t4["sentence_start_capital_accuracy"] == 1.0
        de = by_id["S02-T2-DE-001"]["metrics"]
        assert de["terminal_punctuation_accuracy"] == pytest.approx(0.5)
        assert de["sentence_start_capital_accuracy"] == 0.0

    def test_name_number_recall_cross_language(self, provenance_report):
        """G7: T4 tags score through the shared hotword recall —
        diacritics/formatting misses are recall misses in any language."""
        _, report = provenance_report
        m = self.by_id(report)["S01-T4-EN-002"]["metrics"]
        assert m["hotword_recall"] == 0.0
        de = self.by_id(report)["S02-T2-DE-001"]["metrics"]
        assert de["hotword_recall"] == 1.0

    def test_language_detected_per_case(self, provenance_report):
        _, report = provenance_report
        by_id = self.by_id(report)
        assert by_id["S01-T1-EN-001"]["language_detected"] == "en-US"
        assert by_id["S01-T1-EN-001"]["language_match"] is True
        assert by_id["S02-T2-DE-001"]["language_match"] is False
        assert by_id["NEG-SILENCE-001"]["language_detected"] is None
        # raw-key contract: preserved verbatim under raw
        assert by_id["S01-T1-EN-001"]["raw"]["detected_language"] == "en-US"
        assert by_id["NEG-SILENCE-001"]["raw"]["guard_score"] == 0.9

    def test_guard_score_per_case(self, provenance_report):
        _, report = provenance_report
        by_id = self.by_id(report)
        assert by_id["S01-T1-EN-001"]["guard_score"] == 0.2
        assert by_id["NEG-SILENCE-001"]["guard_score"] == 0.9

    def test_summary_means_hand_computed(self, provenance_report):
        _, report = provenance_report
        s = report["summary"]
        assert s["cases"] == 4 and s["errors"] == 0
        assert s["wer_mean"] == pytest.approx((0.5 + 0.375 + 1 / 6
                                               + 1.0) / 4)
        # omission mean skips the None negative case: (0+0+1/6)/3
        assert s["omission_mean"] == pytest.approx((1 / 6) / 3)
        assert s["hallucination_word_rate_mean"] == pytest.approx(2.0)
        assert s["terminal_punctuation_accuracy_mean"] == \
            pytest.approx(0.25)
        assert s["sentence_start_capital_accuracy_mean"] == \
            pytest.approx(2 / 3)


class TestSubgroups:
    """G1: group-by tables alongside corpus means, (unset) handling."""

    def groups(self, report):
        return {(r["dimension"], r["group"]): r
                for r in report["subgroups"]}

    def test_language_groups(self, provenance_report):
        _, report = provenance_report
        g = self.groups(report)
        en = g[("language", "en")]
        assert en["cases"] == 3
        assert en["wer_mean"] == pytest.approx((0.5 + 0.375 + 1.0) / 3)
        de = g[("language", "de")]
        assert de["cases"] == 1
        assert de["wer_mean"] == pytest.approx(1 / 6)
        assert de["omission_mean"] == pytest.approx(1 / 6)

    def test_unset_rows_kept_not_dropped(self, provenance_report):
        _, report = provenance_report
        g = self.groups(report)
        unset = g[("speaker", "(unset)")]
        assert unset["cases"] == 1            # the negative case
        assert g[("snr_bucket", "(unset)")]["cases"] == 1
        assert g[("taxonomy", "negative-silence")]["cases"] == 1

    def test_snr_buckets(self, provenance_report):
        _, report = provenance_report
        g = self.groups(report)
        assert g[("snr_bucket", ">=20")]["cases"] == 2    # both S01
        assert g[("snr_bucket", "10-20")]["cases"] == 1   # S02 at 12 dB

    def test_counts_sum_to_corpus_per_dimension(self, provenance_report):
        _, report = provenance_report
        dims = {r["dimension"] for r in report["subgroups"]}
        assert dims == {"language", "speaker", "mic_class", "taxonomy",
                        "snr_bucket"}
        for dim in dims:
            rows = [r for r in report["subgroups"]
                    if r["dimension"] == dim]
            assert sum(r["cases"] for r in rows) == 4

    def test_guard_counts_per_group(self, provenance_report):
        _, report = provenance_report
        g = self.groups(report)
        neg = g[("taxonomy", "negative-silence")]
        assert neg["guard"][GUARD_TRUE_POSITIVE] == 1
        assert neg["guard"][GUARD_FALSE_POSITIVE] == 0
        de = g[("language", "de")]
        assert de["guard"][GUARD_TRUE_NEGATIVE] == 1

    def test_no_empty_group_rows(self, provenance_report):
        _, report = provenance_report
        assert all(r["cases"] > 0 for r in report["subgroups"])

    def test_means_none_when_metric_unscoreable_in_group(self, tmp_path):
        # a subgroup where no case scores a metric reports n/a (None),
        # not 0 — here: hotword recall over the (unset)-taxonomy group
        m = write_manifest(tmp_path, PROVENANCE_MANIFEST)
        report = run_eval(load_manifest(m), provenance_transcriber,
                          out_dir=tmp_path / "o", manifests=[m])
        g = {(r["dimension"], r["group"]): r
             for r in report["subgroups"]}
        assert g[("taxonomy", "t1-commands")]["hotword_recall_mean"] \
            == 0.5
        assert g[("taxonomy", "negative-silence")]["hotword_recall_mean"] \
            is None

    def test_markdown_subgroup_tables(self, provenance_report):
        tmp_path, report = provenance_report
        md = (tmp_path / "out" / "report.md").read_text()
        assert "## Subgroups" in md
        for dim in ("language", "speaker", "mic_class", "taxonomy",
                    "snr_bucket"):
            assert f"### By {dim}" in md
        assert "(unset)" in md
        assert "n/a" in md            # unscored cells never blank


class TestLanguageConfusionReport:
    """G5: expected-vs-detected table per case + summary."""

    def test_confusion_table(self, provenance_report):
        _, report = provenance_report
        conf = report["language_confusion"]
        assert conf["reported"] == 3
        assert conf["matches"] == 2
        assert conf["unreported"] == ["NEG-SILENCE-001"]
        rows = {(r["expected"], r["detected"]): r for r in conf["rows"]}
        assert rows[("de", "en")]["match"] is False
        assert rows[("de", "en")]["ids"] == ["S02-T2-DE-001"]
        assert rows[("en", "en-US")]["match"] is True

    def test_markdown_confusion_section(self, provenance_report):
        tmp_path, _ = provenance_report
        md = (tmp_path / "out" / "report.md").read_text()
        assert "## Language confusion" in md
        assert "| de | en | NO | 1 | S02-T2-DE-001 |" in md
        assert "(not reported)" in md


class TestGuardSweep:
    """G6: per-case scores under raw, train-only sweep convention."""

    def test_default_threshold_grid(self):
        assert GUARD_SWEEP_THRESHOLDS[0] == 0.05
        assert GUARD_SWEEP_THRESHOLDS[-1] == 0.95
        assert len(GUARD_SWEEP_THRESHOLDS) == 19

    def test_sweep_rows_hand_computed(self, provenance_report):
        # train pool: scores {0.2 ok, 0.5 ok, 0.9 flag}; S02 held out
        _, report = provenance_report
        sweep = report["guard_sweep"]
        assert sweep["cases"] == 3
        assert "held-out cases never enter a sweep" \
            in sweep["convention"].lower()
        rows = {round(r["threshold"], 2): r for r in sweep["rows"]}
        assert rows[0.05] == {"threshold": 0.05, "flagged": 3, "tp": 1,
                              "tn": 0, "fp": 2, "fn": 0}
        assert rows[0.5]["fp"] == 1 and rows[0.5]["tn"] == 1
        assert rows[0.6] == {"threshold": 0.6, "flagged": 1, "tp": 1,
                             "tn": 2, "fp": 0, "fn": 0}

    def test_case_split_labels_win_over_run_label(self, tmp_path):
        # same labeled corpus, run WITHOUT a split label: the labeled
        # train cases still (and only) form the sweep pool
        m = write_manifest(tmp_path, PROVENANCE_MANIFEST)
        report = run_eval(load_manifest(m), provenance_transcriber,
                          out_dir=tmp_path / "o1", manifests=[m])
        assert report["split"] == "full"
        assert report["guard_sweep"]["cases"] == 3

    def test_heldout_run_never_sweeps(self, tmp_path):
        # unlabeled cases + run labeled heldout -> no train-effective
        # cases -> no sweep, and the report says why
        body = PROVENANCE_MANIFEST.replace('split = "train"', "") \
                                  .replace('split = "heldout"', "")
        m = write_manifest(tmp_path, body)
        report = run_eval(load_manifest(m), provenance_transcriber,
                          out_dir=tmp_path / "o2", manifests=[m],
                          split="heldout")
        assert report["split"] == "heldout"
        assert report["guard_sweep"] is None
        assert "held-out" in report["conventions"]["guard_sweep"]

    def test_train_run_sweeps_unlabeled_cases(self, tmp_path):
        body = PROVENANCE_MANIFEST.replace('split = "train"', "") \
                                  .replace('split = "heldout"', "")
        m = write_manifest(tmp_path, body)
        report = run_eval(load_manifest(m), provenance_transcriber,
                          out_dir=tmp_path / "o3", manifests=[m],
                          split="train", guard_thresholds=(0.6,))
        assert report["guard_sweep"]["cases"] == 4   # all train now
        assert [r["threshold"] for r in
                report["guard_sweep"]["rows"]] == [0.6]

    def test_no_scores_no_sweep(self, tmp_path):
        # train run, but the adapter reports no guard scores at all
        m = write_manifest(tmp_path, RUNNER_MANIFEST)

        def tr(audio_path, case):
            return {"text": case.reference_text}

        report = run_eval(load_manifest(m), tr, out_dir=tmp_path / "o4",
                          manifests=[m], split="train")
        assert report["guard_sweep"] is None

    def test_markdown_sweep_section(self, provenance_report):
        tmp_path, _ = provenance_report
        md = (tmp_path / "out" / "report.md").read_text()
        assert "## Guard sweep" in md
        assert "| 0.60 | 1 | 1 | 2 | 0 | 0 |" in md
        assert "Train-only convention" in md

    def test_markdown_sweep_absence_note(self, tmp_path):
        m = write_manifest(tmp_path, RUNNER_MANIFEST)
        report = run_eval(load_manifest(m), fake_transcriber,
                          out_dir=tmp_path / "o5", manifests=[m],
                          split="heldout")
        md = (tmp_path / "o5" / "report.md").read_text()
        assert "## Guard sweep" in md
        assert "Not computed" in md


class TestNegativesReport:
    """G3: negative-case table + per-noise-type hallucination summary."""

    def test_negative_rows(self, provenance_report):
        _, report = provenance_report
        neg = report["negatives"]
        assert [r["id"] for r in neg["cases"]] == ["NEG-SILENCE-001"]
        row = neg["cases"][0]
        assert row["tokens"] == 4
        assert row["audio_duration_s"] == pytest.approx(2.0)
        assert row["hallucination_word_rate"] == pytest.approx(2.0)
        assert row["taxonomy"] == "negative-silence"
        assert row["guard_category"] == GUARD_TRUE_POSITIVE

    def test_by_noise_type(self, provenance_report):
        _, report = provenance_report
        assert report["negatives"]["hallucination_word_rate_by_taxonomy"] \
            == {"negative-silence": 2.0}

    def test_markdown_negative_section(self, provenance_report):
        tmp_path, _ = provenance_report
        md = (tmp_path / "out" / "report.md").read_text()
        assert "## Negative cases" in md
        assert "| NEG-SILENCE-001 | negative-silence | 4 | 2.0 |" in md
        assert "negative-silence 2.000" in md


class TestWorstCases:
    def test_ordered_and_capped(self, provenance_report):
        _, report = provenance_report
        worst = report["worst_cases"]
        assert [w["id"] for w in worst] == [
            "NEG-SILENCE-001", "S01-T1-EN-001", "S01-T4-EN-002",
            "S02-T2-DE-001"]
        assert worst[0] == {"id": "NEG-SILENCE-001", "speaker": None,
                            "language": "en",
                            "taxonomy": "negative-silence", "wer": 1.0}

    def test_capped_at_ten(self, tmp_path):
        ids = "\n".join(
            f'[[cases]]\nid = "c{i:02d}"\naudio = "c{i:02d}.wav"\n'
            f'reference_text = "a b c"\nlanguage = "en"\ntags = []\n'
            f'expected_guard = "none"\nlicense = "CC0-1.0"\n'
            f'source = "s"\nsynth = {{ kind = "tone", seconds = 0.1 }}\n'
            for i in range(12))
        m = write_manifest(tmp_path, ids)

        def tr(audio_path, case):
            return {"text": ""}      # WER 1.0 everywhere

        report = run_eval(load_manifest(m), tr, out_dir=tmp_path / "o",
                          manifests=[m])
        assert len(report["worst_cases"]) == 10

    def test_markdown_worst_section(self, provenance_report):
        tmp_path, _ = provenance_report
        md = (tmp_path / "out" / "report.md").read_text()
        assert "## Worst cases" in md
        assert "| NEG-SILENCE-001 | (unset) | en | " in md


class TestTranscriptionOutputRawContracts:
    def test_guard_score_and_language_parsed_and_kept_in_raw(self):
        out = TranscriptionOutput.from_mapping(
            {"text": "x", "guard_score": 0.4, "detected_language": "de"})
        assert out.guard_score == 0.4
        assert out.detected_language == "de"
        assert out.raw["guard_score"] == 0.4        # preserved under raw
        assert out.raw["detected_language"] == "de"

    @pytest.mark.parametrize("bad", [1.5, -0.1, "0.4", True])
    def test_bad_guard_score_raises(self, bad):
        with pytest.raises(TranscriptionOutputError,
                           match="guard_score"):
            TranscriptionOutput.from_mapping({"text": "x",
                                              "guard_score": bad})

    def test_bad_detected_language_raises(self):
        with pytest.raises(TranscriptionOutputError,
                           match="detected_language"):
            TranscriptionOutput.from_mapping({"text": "x",
                                              "detected_language": 7})
