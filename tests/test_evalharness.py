"""Evaluation harness: metric math, manifest, synth, runner (plan P1.4).

All hand-computed cases — these tests pin the *math*, not the models:
the harness computes metrics without any backend (models only produce
the texts being scored).
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
    cer,
    guard_category,
    guard_counts,
    hotword_recall,
    levenshtein,
    mean,
    normalize_words,
    real_time_factor,
    wer,
)
from fluidvoice.evalharness.runner import (
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
