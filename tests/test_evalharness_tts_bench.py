"""tts_bench: TTS decode-baseline harness (wave 3, agent J).

Unit tests pin the pieces that must never need the model: fixture
integrity, wav generation/resampling, the in-memory backend config (no
daemon, no config-file I/O), manifest generation, and the summary
renderer working from a fake results dict. The REAL run happens once
per machine in the session that owns the brief (see the module
docstring's rerun command); its evidence lands in
docs/eval/decode-baseline-2026-09-11.md.
"""
from __future__ import annotations

import math
import wave
from pathlib import Path

import pytest

from fluidvoice.evalharness import tts_bench
from fluidvoice.evalharness.manifest import ManifestError, load_manifest
from fluidvoice.evalharness.tts_bench import (
    Negative,
    Utterance,
    auto_probe_ids,
    build_backend_config,
    generate_negatives,
    load_reference,
    noise_samples,
    piper_binary,
    probe_backend_env,
    read_wav,
    resample_to_16k,
    silence_samples,
    summarize,
    write_manifest,
    write_wav,
)

# ── fixture integrity ──────────────────────────────────────────────────────

def test_reference_fixture_parses_and_is_stratified():
    utts, negs = load_reference()
    # brief: 25-40 short English utterances, all taxonomies present
    assert 25 <= len(utts) <= 40
    taxonomies = {u.taxonomy for u in utts}
    assert taxonomies == set(tts_bench.SPEECH_TAXONOMIES)
    # negatives: silence AND noise, per the brief
    kinds = {n.kind for n in negs}
    assert kinds == {"silence", "noise"}
    assert all(n.seconds > 0 for n in negs)


def test_reference_fixture_ids_and_texts_unique():
    utts, _ = load_reference()
    ids = [u.id for u in utts]
    assert len(ids) == len(set(ids))
    texts = [u.text.casefold() for u in utts]
    assert len(texts) == len(set(texts))


def test_reference_fixture_t4_tags_its_names_and_numbers():
    utts, _ = load_reference()
    t4 = [u for u in utts if u.taxonomy == "t4-names-numbers"]
    assert t4, "T4 stratum must exist"
    assert all(u.tags for u in t4), "T4 items tag their names/numbers"


def test_reference_fixture_rejects_broken(tmp_path):
    def ref(text: str) -> Path:
        p = tmp_path / "ref.toml"
        p.write_text(text, encoding="utf-8")
        return p

    # duplicate id
    with pytest.raises(ManifestError, match="duplicate"):
        load_reference(ref(
            '[[utterances]]\nid = "a"\ntaxonomy = "t1-command"\n'
            'text = "One."\n\n[[utterances]]\nid = "a"\n'
            'taxonomy = "t2-message"\ntext = "Two."\n'))
    # missing taxonomy stratum -> count/stratification error
    single = ('[[utterances]]\nid = "a"\ntaxonomy = "t1-command"\n'
              'text = "One."\n\n[[negatives]]\nid = "n"\n'
              'kind = "silence"\nseconds = 1.0\n')
    with pytest.raises(ManifestError, match="stratification|taxonomy"):
        load_reference(ref(single))
    # no negatives at all
    many = "".join(
        f'[[utterances]]\nid = "u{i}"\ntaxonomy = "{t}"\n'
        f'text = "Text {i}."\n\n'
        for i, t in enumerate(tts_bench.SPEECH_TAXONOMIES * 5))
    with pytest.raises(ManifestError, match="negatives"):
        load_reference(ref(many + '[[utterances]]\nid = "zz"\n'
                            'taxonomy = "t1-command"\ntext = "Zz."\n'))


def test_reference_fixture_count_window_enforced(tmp_path):
    # 24 utterances (below the 25-40 window) with all taxonomies
    body = ""
    counts = {"t1-command": 6, "t2-message": 6, "t3-jargon": 4,
              "t4-names-numbers": 4, "t5-longform": 2, "t6-dev-prompt": 2}
    i = 0
    for tax, n in counts.items():
        for _ in range(n):
            body += (f'[[utterances]]\nid = "u{i:02d}"\n'
                     f'taxonomy = "{tax}"\ntext = "Say {i}."\n\n')
            i += 1
    assert i == 24
    body += ('[[negatives]]\nid = "n"\nkind = "silence"\n'
             'seconds = 1.0\n')
    p = tmp_path / "ref.toml"
    p.write_text(body, encoding="utf-8")
    with pytest.raises(ManifestError, match="25"):
        load_reference(p)


# ── negatives + wav math ───────────────────────────────────────────────────

def test_silence_and_noise_negatives_are_deterministic_16k():
    sil = silence_samples(0.25)
    assert len(sil) == 4000 and set(sil) == {0}
    n1, n2 = noise_samples(0.25), noise_samples(0.25)
    assert n1 == n2, "seeded noise must be identical across calls"
    assert max(abs(v) for v in n1) <= tts_bench.NOISE_PEAK
    assert any(v != 0 for v in n1), "noise must not degenerate to silence"


def test_generate_negatives_writes_valid_wavs(tmp_path):
    negs = [Negative(id="neg-silence-2s", kind="silence", seconds=0.5),
            Negative(id="neg-noise-1s", kind="noise", seconds=0.25)]
    out = generate_negatives(negs, tmp_path / "audio")
    for (neg, path) in out:
        with wave.open(str(path), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 16000
            expect = int(16000 * neg.seconds)
            assert abs(wf.getnframes() - expect) <= 1
    # regenerating is byte-identical
    out2 = generate_negatives(negs, tmp_path / "audio")
    assert [(p, p.read_bytes()) for _, p in out] == \
        [(p, p.read_bytes()) for _, p in out2]


def test_resample_to_16k_preserves_tone_and_length():
    rate = 22050
    seconds = 0.2
    hz = 1000.0
    src = [int(20000 * math.sin(2 * math.pi * hz * i / rate))
           for i in range(int(rate * seconds))]
    out = resample_to_16k(src, rate)
    assert abs(len(out) - int(16000 * seconds)) <= 1
    peak_src = max(abs(v) for v in src)
    peak_out = max(abs(v) for v in out)
    assert abs(peak_out - peak_src) <= 0.1 * peak_src
    # identity passthrough
    assert resample_to_16k([1, -2, 3], 16000) == [1, -2, 3]


def test_write_and_read_wav_roundtrip(tmp_path):
    p = write_wav(tmp_path / "x.wav", [0, 1, -1, 32767, -32768])
    rate, samples = read_wav(p)
    assert rate == 16000 and samples == [0, 1, -1, 32767, -32768]


# ── adapter construction ───────────────────────────────────────────────────

def test_build_backend_config_is_in_memory_daemon_mirror():
    cfg = build_backend_config()
    assert cfg["general"]["language"] == "en"
    m = cfg["model"]
    assert m["backend"] == "auto" and m["name"] == "large-v3"
    assert m["device"] == "auto" and m["compute"] == "auto"
    # the whisper.cpp path the brief named is constructible the same
    # way: the config carries no binary/model side effects of its own
    assert "whispercpp_model" not in m


def test_probe_backend_env_reports_without_instantiating():
    env = probe_backend_env()
    # cheap probes only: never a loaded model, but a resolution answer
    assert env["resolved_backend"] in (
        "faster-whisper", "whisper-torch", "whisper.cpp", "parakeet",
        "remote", None)
    assert isinstance(env["whispercpp_binary_on_path"], (str, type(None)))
    assert "no config file" in env["config"]


def test_piper_binary_resolution(tmp_path, monkeypatch):
    monkeypatch.setattr(tts_bench, "BENCH_VENV_PIPER",
                        tmp_path / "missing-piper")
    monkeypatch.delattr(tts_bench.shutil, "which", raising=False)
    fake = tmp_path / "venv" / "bin" / "piper"
    fake.parent.mkdir(parents=True)
    fake.write_text("#!/bin/sh\n")
    assert piper_binary(fake) == fake
    assert piper_binary(tmp_path / "nope") is None
    # explicit path wins over the bench-venv default
    monkeypatch.setattr(tts_bench, "BENCH_VENV_PIPER", fake)
    assert piper_binary(None) == fake


# ── manifest generation ────────────────────────────────────────────────────

def _fake_corpus(tmp_path):
    utts = [Utterance(id="t1-01", taxonomy="t1-command",
                      text='Open the "downloads" folder.', tags=[]),
            Utterance(id="t4-01", taxonomy="t4-names-numbers",
                      text="The server is 192.168.0.1.",
                      tags=["192.168.0.1"])]
    negs = [(Negative(id="neg-silence-2s", kind="silence", seconds=0.1),
             tmp_path / "audio" / "neg-silence-2s.wav")]
    audio_of = {u.id: tmp_path / "audio" / f"{u.id}.wav"
                for u in utts}
    for p in list(audio_of.values()) + [n[1] for n in negs]:
        write_wav(p, [0] * 160)
    return utts, negs, audio_of


def test_write_manifest_roundtrips_through_the_loader(tmp_path):
    utts, negs, audio_of = _fake_corpus(tmp_path)
    mpath = write_manifest(tmp_path / "forced-manifest.toml",
                           utts, negs, audio_of)
    cases = load_manifest(mpath)
    assert [c.id for c in cases] == ["t1-01", "t4-01", "neg-silence-2s"]
    by_id = {c.id: c for c in cases}
    speech, neg = by_id["t4-01"], by_id["neg-silence-2s"]
    assert speech.reference_text == "The server is 192.168.0.1."
    assert speech.tags == ("192.168.0.1",)
    assert speech.taxonomy == "t4-names-numbers"
    assert speech.expected_guard == "ok"
    assert speech.speaker == "piper-en_US-lessac-medium"
    assert speech.mic_class == "tts-synthetic"
    assert speech.audio.is_file()
    assert neg.reference_text == ""
    assert neg.expected_guard == "flag"
    assert neg.taxonomy == "negative-silence"
    assert neg.audio.is_file()
    # quotes survive the TOML escaping round trip
    assert by_id["t1-01"].reference_text == 'Open the "downloads" folder.'


def test_write_manifest_suffix_distinguishes_auto_probe(tmp_path):
    utts, negs, audio_of = _fake_corpus(tmp_path)
    mpath = write_manifest(tmp_path / "auto-manifest.toml", utts, negs,
                           audio_of, suffix="-auto")
    cases = load_manifest(mpath)
    assert {c.id for c in cases} == {"t1-01-auto", "t4-01-auto",
                                     "neg-silence-2s-auto"}


def test_auto_probe_subset_is_fixed_and_short_taxonomy_only():
    utts, _ = load_reference()
    probe = auto_probe_ids(utts)
    assert len(probe) == (len(tts_bench.AUTO_PROBE_TAXONOMIES)
                          * tts_bench.AUTO_PROBE_PER_TAXONOMY)
    probed = {u.id: u for u in utts if u.id in set(probe)}
    assert len(probed) == len(probe)
    assert all(u.taxonomy in tts_bench.AUTO_PROBE_TAXONOMIES
               for u in probed.values())
    assert not any(u.taxonomy == "t5-longform" for u in probed.values())


# ── summary renderer (pure; fake results dict, no model) ───────────────────

def _fake_report() -> dict:
    return {
        "summary": {"cases": 3, "errors": 1, "wer_mean": 0.12,
                    "cer_mean": 0.03, "omission_mean": 0.02,
                    "hotword_recall_mean": 0.5,
                    "real_time_factor_mean": 0.4,
                    "final_latency_mean_s": 2.5},
        "subgroups": [
            {"dimension": "taxonomy", "group": "t1-command", "cases": 2,
             "errors": 0, "wer_mean": 0.05, "cer_mean": 0.01,
             "omission_mean": 0.0, "hotword_recall_mean": None,
             "real_time_factor_mean": 0.5, "final_latency_mean_s": 1.2},
            {"dimension": "taxonomy", "group": "t5-longform", "cases": 1,
             "errors": 1, "wer_mean": None, "cer_mean": None,
             "omission_mean": None, "hotword_recall_mean": None,
             "real_time_factor_mean": None,
             "final_latency_mean_s": None},
        ],
        "negatives": {"cases": [
            {"id": "neg-silence-5s", "taxonomy": "negative-silence",
             "tokens": 3, "audio_duration_s": 5.0,
             "hallucination_word_rate": 0.6},
            {"id": "neg-noise-5s", "taxonomy": "negative-noise",
             "tokens": 0, "audio_duration_s": 5.0,
             "hallucination_word_rate": 0.0},
        ], "hallucination_word_rate_by_taxonomy": {}},
        "language_confusion": {"rows": [
            {"expected": "en", "detected": "en", "match": True,
             "cases": 2, "ids": ["t1-01", "t2-01"]},
            {"expected": "en", "detected": "de", "match": False,
             "cases": 1, "ids": ["t4-02"]},
        ]},
    }


def test_summarize_renders_from_a_fake_results_dict():
    md = summarize(_fake_report())
    # headline + direction-pinned RTF column header
    assert "RTF (>1=rt)" in md
    assert "| 3 | 1 | 0.120 |" in md
    # per-taxonomy rows, n/a for unscored cells
    assert "| t1-command | 2 | 0.050 |" in md
    assert "| t5-longform | 1 | n/a |" in md
    # negatives + hallucination rates
    assert "| neg-silence-5s | 3 | 5.000 | 0.600 |" in md
    assert "| neg-noise-5s | 0 | 5.000 | 0.000 |" in md
    # detection rows mark confusions loudly
    assert "| en | de | NO | 1 |" in md
    assert "| en | en | yes | 2 |" in md


def test_summarize_handles_an_empty_report():
    md = summarize({"summary": {"cases": 0, "errors": 0},
                    "subgroups": [], "negatives": {"cases": []},
                    "language_confusion": {"rows": []}})
    assert "| 0 | 0 | n/a |" in md
    assert "negative" not in md.lower()
