"""Tests for scripts.corpus (recording manifest, consent flow, split
sealer, provenance validator) — corpus-spec phase 0 tooling."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fluidvoice.evalharness.manifest import load_manifest
from scripts.corpus import (
    ManifestError,
    SplitError,
    assert_no_held_out,
    compute_heldout,
    export_harness_manifest,
    is_held_out,
    load_batch_manifest,
    load_sealed,
    scaffold_batch_manifest,
    seal,
    validate_batch,
    write_sealed,
)
from scripts.corpus.model import cases_from_dicts
from scripts.corpus.split import HeldOutGuard
from scripts.corpus.template import TEMPLATE, TEMPLATE_PATH_REL

REPO_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# helpers: tiny JSON fixtures
# --------------------------------------------------------------------------

def _speaker(sid="S01", **over):
    doc = {
        "id": sid,
        "l1": "en",
        "accent": "en-US",
        "languages": ["en"],
        "mic_classes": ["laptop-array", "headset-usb"],
        "consent_ref": f"{sid} consent 2026-09-20",
    }
    doc.update(over)
    return doc


def _case(cid, *, speaker="S01", taxonomy="t1-short-commands",
          language="en", status="recorded", audio="audio/x.wav",
          reference="hello world", **over):
    doc = {
        "id": cid,
        "speaker": speaker,
        "language": language,
        "taxonomy": taxonomy,
        "mic_class": "headset-usb",
        "status": status,
        "audio": audio,
        "reference_text": reference,
        "tags": [],
        "expected_guard": "ok" if not taxonomy.startswith("negative")
        else "flag",
        "license": "private — do not redistribute",
        "snr_db": 14.2,
        "environment": "home-office",
        "session_date": "2026-09-20",
    }
    doc.update(over)
    for k, v in list(doc.items()):
        if v is ...:
            doc.pop(k)
    return doc


def _batch_doc(cases, speakers=None, **over):
    doc = {
        "manifest_schema": 1,
        "batch": "test-batch",
        "created": "2026-09-20",
        "license": "private — do not redistribute",
        "recorder": {
            "name": "pw-record",
            "version": "1.0",
            "backend": {"type": "pipewire", "version": "0.3"},
            "settings": {"rate_hz": 48000},
        },
        "speakers": speakers if speakers is not None
        else [_speaker()],
        "cases": cases,
    }
    doc.update(over)
    for k, v in list(doc.items()):
        if v is ...:
            doc.pop(k)
    return doc


def _write_batch(tmp_path, cases, speakers=None, name="manifest.json",
                 **over):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_batch_doc(cases, speakers, **over),
                               ensure_ascii=False), encoding="utf-8")
    return path


def _error_of(fn) -> str:
    with pytest.raises(ManifestError) as ei:
        fn()
    return str(ei.value)


# --------------------------------------------------------------------------
# template + init
# --------------------------------------------------------------------------

def test_docs_template_matches_canonical_copy():
    docs = json.loads((REPO_ROOT / TEMPLATE_PATH_REL).read_text("utf-8"))
    assert docs == TEMPLATE
    # targets pre-filled per spec §4
    assert TEMPLATE["taxonomy_targets"] == {
        "t1-short-commands": 40, "t2-chat-messages": 60, "t3-jargon": 45,
        "t4-names-numbers": 30, "t5-long-form": 20,
        "t6-developer-prompts": 45}
    assert TEMPLATE["negative_target"] == 30


def test_init_scaffolds_loadable_manifest_with_spec_targets(tmp_path):
    out = tmp_path / "eval-private" / "b01"
    path = scaffold_batch_manifest("b01", out, created="2026-09-20")
    assert path == out / "manifest.json"
    assert (out / "audio").is_dir()
    doc = json.loads(path.read_text("utf-8"))
    assert doc["batch"] == "b01"
    assert doc["created"] == "2026-09-20"
    assert doc["taxonomy_targets"]["t3-jargon"] == 45
    batch = load_batch_manifest(path)          # skeleton loads cleanly
    assert {c.taxonomy for c in batch.cases} == {
        "t1-short-commands", "t2-chat-messages", "t3-jargon",
        "t4-names-numbers", "t5-long-form", "t6-developer-prompts",
        "negative-silence", "negative-room-tone", "negative-noise",
        "negative-faint-speech", "negative-non-speech"}


def test_init_refuses_overwrite_and_bad_names(tmp_path):
    out = tmp_path / "b"
    scaffold_batch_manifest("b", out, created="2026-09-20")
    with pytest.raises(ManifestError, match="already exists"):
        scaffold_batch_manifest("b", out, created="2026-09-20")
    assert scaffold_batch_manifest("b", out, created="2026-09-20",
                                   force=True)
    with pytest.raises(ManifestError, match="path segment"):
        scaffold_batch_manifest("a b", tmp_path / "x")


# --------------------------------------------------------------------------
# loader: actionable validation errors
# --------------------------------------------------------------------------

def test_loader_rejects_missing_case_field(tmp_path):
    path = _write_batch(tmp_path, [_case("S01-T1-EN-001", mic_class=...)])
    msg = _error_of(lambda: load_batch_manifest(path))
    assert "cases[0] (id 'S01-T1-EN-001')" in msg
    assert "'mic_class'" in msg


def test_loader_rejects_bad_taxonomy_and_mic(tmp_path):
    path = _write_batch(tmp_path, [_case("S01-T1-EN-001",
                                         taxonomy="t9-made-up")])
    assert "'taxonomy'" in _error_of(lambda: load_batch_manifest(path))
    path = _write_batch(tmp_path, [_case("S01-T1-EN-001",
                                         mic_class="telepathy")])
    assert "'mic_class'" in _error_of(lambda: load_batch_manifest(path))


def test_loader_rejects_speech_case_without_speaker(tmp_path):
    path = _write_batch(tmp_path, [_case("X-T1-EN-001", speaker="")])
    assert "only be empty for negative" in _error_of(
        lambda: load_batch_manifest(path))


def test_loader_rejects_bad_snr_status_guard_tags(tmp_path):
    path = _write_batch(tmp_path, [_case("S01-T1-EN-001", snr_db="loud")])
    assert "'snr_db'" in _error_of(lambda: load_batch_manifest(path))
    path = _write_batch(tmp_path, [_case("S01-T1-EN-001", status="final")])
    assert "'status'" in _error_of(lambda: load_batch_manifest(path))
    path = _write_batch(tmp_path, [_case("S01-T1-EN-001",
                                         expected_guard="maybe")])
    assert "'expected_guard'" in _error_of(lambda: load_batch_manifest(path))
    path = _write_batch(tmp_path, [_case("S01-T1-EN-001", tags=[1])])
    assert "'tags'" in _error_of(lambda: load_batch_manifest(path))


def test_loader_rejects_duplicate_case_ids(tmp_path):
    path = _write_batch(tmp_path, [_case("S01-T1-EN-001"),
                                   _case("S01-T1-EN-001")])
    msg = _error_of(lambda: load_batch_manifest(path))
    assert "duplicate case id" in msg and "cases[1]" in msg


def test_loader_rejects_bad_schema_and_recorder(tmp_path):
    path = _write_batch(tmp_path, [_case("S01-T1-EN-001")],
                        manifest_schema=99)
    assert "manifest_schema" in _error_of(lambda: load_batch_manifest(path))
    path = _write_batch(tmp_path, [_case("S01-T1-EN-001")],
                        recorder={"name": "x"})
    assert "recorder" in _error_of(lambda: load_batch_manifest(path))


def test_loader_rejects_bad_speaker_rows(tmp_path):
    path = _write_batch(tmp_path, [_case("S01-T1-EN-001")],
                        speakers=[_speaker(id="Speaker-1")])
    assert "speakers[0]" in _error_of(lambda: load_batch_manifest(path))
    path = _write_batch(tmp_path, [_case("S01-T1-EN-001")],
                        speakers=[_speaker(consent_ref="")])
    assert "consent_ref" in _error_of(lambda: load_batch_manifest(path))


def test_loader_rejects_invalid_json(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text("{not json", encoding="utf-8")
    assert "invalid JSON" in _error_of(lambda: load_batch_manifest(path))


def test_loader_tolerates_unknown_keys_and_planned_empty_audio(tmp_path):
    path = _write_batch(tmp_path, [_case(
        "S01-T1-EN-001", status="planned", audio="",
        reference=None, future_key={"anything": True})])
    batch = load_batch_manifest(path)
    assert batch.cases[0].status == "planned"
    assert batch.cases[0].reference_text is None


# --------------------------------------------------------------------------
# split sealer: determinism, stratification, sealing, guards
# --------------------------------------------------------------------------

def _speech_cases(speaker="S01", taxonomy="t1-short-commands", n=10,
                  language="en"):
    tag = {"t1-short-commands": "T1", "t2-chat-messages": "T2",
           "t3-jargon": "T3"}.get(taxonomy, taxonomy)
    return cases_from_dicts([
        _case(f"{speaker}-{tag}-{language.upper()}-{i:03d}",
              speaker=speaker, taxonomy=taxonomy, language=language)
        for i in range(1, n + 1)])


def _hash_order(ids):
    return sorted(ids, key=lambda i: hashlib.sha256(i.encode()).hexdigest())


def test_split_is_deterministic_and_order_independent():
    cases = _speech_cases(n=10) + _speech_cases(taxonomy="t2-chat-messages",
                                                n=10)
    first = compute_heldout(cases, fraction=0.2)
    again = compute_heldout(list(reversed(cases)), fraction=0.2)
    assert first == again
    # every 5th of the hash-ordered stratum (10 cases -> exactly 2)
    for taxonomy in ("t1-short-commands", "t2-chat-messages"):
        ids = [c.id for c in cases if c.taxonomy == taxonomy]
        expect = set(_hash_order(ids)[4::5])
        assert {i for i in first if i in set(ids)} == expect
    assert len(first) == 4                      # ~20% overall


def test_split_sealed_file_roundtrip_and_tamper_detection(tmp_path):
    cases = _speech_cases(n=10)
    sealed = seal(cases, corpus="test-batch", fraction=0.2,
                  sealed_date="2026-09-20")
    assert sealed.heldout_count == 2 and sealed.train_count == 8
    path = write_sealed(sealed, tmp_path / "splits.toml")
    loaded = load_sealed(path)
    assert loaded.heldout_ids == sealed.heldout_ids
    assert loaded.ids_digest == sealed.ids_digest
    assert loaded.method == "hash-stratified-v1"
    # same input + date -> byte-identical sealed file
    twin = tmp_path / "splits2.toml"
    write_sealed(seal(cases, corpus="test-batch", fraction=0.2,
                      sealed_date="2026-09-20"), twin)
    assert twin.read_text() == path.read_text()

    # sealed file tamper: swapping an id breaks the recorded digest
    h0 = sorted(sealed.heldout_ids)[0]
    other = "S01-T1-EN-001" if h0 != "S01-T1-EN-001" else "S01-T1-EN-002"
    tampered = tmp_path / "tampered.toml"
    tampered.write_text(
        path.read_text().replace(f'"{h0}"', f'"{other}"', 1),
        encoding="utf-8")
    with pytest.raises(SplitError, match="digest"):
        load_sealed(tampered)


def test_split_whole_speakers_are_entirely_held_out():
    cases = (_speech_cases(speaker="S01", n=9)
             + _speech_cases(speaker="S02", n=6))
    held = compute_heldout(cases, fraction=0.2, whole_speakers=("S02",))
    s02 = {c.id for c in cases if c.speaker == "S02"}
    assert s02 <= held
    assert held - s02 == {_hash_order(
        [c.id for c in cases if c.speaker == "S01"])[4::5][0]}


def test_seal_enforces_overall_fraction_and_escapes():
    cases = _speech_cases(n=9)                  # 1/9 ≈ 11% < 20%
    with pytest.raises(SplitError, match="below the 20% target"):
        seal(cases, corpus="b", fraction=0.2)
    sealed = seal(cases, corpus="b", fraction=0.2, allow_under=True)
    assert sealed.heldout_count == 1
    with pytest.raises(SplitError, match="no cases in this corpus"):
        seal(cases, corpus="b", fraction=0.2, whole_speakers=("S09",))
    with pytest.raises(SplitError, match="empty"):
        seal([], corpus="b")


def test_heldout_guards():
    cases = _speech_cases(n=10)
    sealed = seal(cases, corpus="b", fraction=0.2, sealed_date="2026-09-20")
    assert all(is_held_out(i, sealed) for i in sealed.heldout_ids)
    assert not any(is_held_out(i, sealed)
                   for i in set(c.id for c in cases) - sealed.heldout_ids)
    guard = HeldOutGuard(sealed)                 # wrapper agrees either way
    assert guard("S01-T1-EN-001") == is_held_out("S01-T1-EN-001", sealed)

    train = [c.id for c in cases if c.id not in sealed.heldout_ids]
    assert_no_held_out(train, sealed)           # train is clean: passes
    with pytest.raises(SplitError, match="held-out case"):
        assert_no_held_out([c.id for c in cases], sealed,
                           source="tuning input")


# --------------------------------------------------------------------------
# validator: each error class + audio placement
# --------------------------------------------------------------------------

def _fake_repo(tmp_path, ignore="eval-private/\n", name="repo"):
    repo = tmp_path / name
    (repo / ".git").mkdir(parents=True)
    (repo / ".gitignore").write_text(ignore, encoding="utf-8")
    return repo


def test_validator_catches_each_error_class(tmp_path):
    path = _write_batch(tmp_path, [
        _case("S01-T1-EN-001"),                              # clean
        _case("S09-T1-EN-002", speaker="S09"),               # unknown spk
        _case("S02-T2-EN-003"),                              # id/speaker
        _case("S01-T3-ENX-004", taxonomy="t2-chat-messages"),  # stratum
        _case("N3-NOIS-005", speaker="", taxonomy="negative-noise",
              reference="stray words"),                      # neg + ref
        _case("S01-T4-EN-006", taxonomy="t4-names-numbers",
              audio="audio/missing.wav"),                    # no audio
        _case("S01-T5-EN-007", taxonomy="t5-long-form",
              reference=""),                                 # no transcript
    ])
    audio = tmp_path / "audio"
    audio.mkdir()
    (audio / "x.wav").touch()                  # existence only; no bytes
    msgs = "\n".join(f.message for f in validate_batch(
        load_batch_manifest(path)) if f.is_error)
    assert "speaker 'S09' is not in the speakers table" in msgs
    assert "id embeds speaker 'S02'" in msgs
    assert "does not match taxonomy 't2-chat-messages'" in msgs
    assert "empty reference_text" in msgs
    assert "audio not found" in msgs
    assert "no reference_text" in msgs


def test_validator_audio_outside_git(tmp_path):
    repo = _fake_repo(tmp_path)
    batch_dir = repo / "eval-private" / "corpus-v1"
    (batch_dir / "audio").mkdir(parents=True)
    (repo / "docs").mkdir()
    (batch_dir / "audio" / "ok.wav").touch()
    (repo / "docs" / "leak.wav").touch()
    path = _write_batch(batch_dir, [
        _case("S01-T1-EN-001", audio="audio/ok.wav"),
        _case("S01-T2-EN-002", taxonomy="t2-chat-messages",
              audio="../../docs/leak.wav"),
    ])
    errs = [f.message for f in validate_batch(load_batch_manifest(path))
            if f.is_error]
    assert not any("ok.wav" in m for m in errs)     # ignored dir: fine
    assert any("leak.wav" in m and "inside the git repo" in m
               for m in errs)
    # un-ignored repo with no eval-private rule: same audio now errors
    repo2 = _fake_repo(tmp_path, ignore="", name="repo2")
    batch2 = repo2 / "eval-private" / "corpus-v1"
    (batch2 / "audio").mkdir(parents=True)
    (batch2 / "audio" / "ok.wav").touch()
    path2 = _write_batch(batch2, [_case("S01-T1-EN-001",
                                         audio="audio/ok.wav")],
                         name="manifest2.json")
    assert any("inside the git repo" in f.message
               for f in validate_batch(load_batch_manifest(path2)))


def test_validator_warns_on_shape_and_metadata(tmp_path):
    audio = tmp_path / "audio"
    audio.mkdir()
    path = _write_batch(tmp_path, [
        _case("S01-T1-EN-001", snr_db=None),
        _case("S01-T2-EN-002", taxonomy="t2-chat-messages",
              session_date="yesterday-ish", license="CC0-1.0"),
        _case("S01-T1-EN-003", environment="rocket"),
    ])
    warns = [f.message for f in validate_batch(load_batch_manifest(path))
             if not f.is_error]
    joined = "\n".join(warns)
    assert "without snr_db" in joined
    assert "not YYYY-MM-DD" in joined
    assert "not the default private tier" in joined
    assert "is not one of" in joined               # environment label
    assert "t1-short-commands" in joined           # taxonomy under target
    assert "negatives 0" in joined
    assert "speakers — spec minimum" in joined


def test_validator_clean_full_shape_batch_has_no_errors(tmp_path):
    audio = tmp_path / "audio"
    audio.mkdir()
    (audio / "x.wav").touch()
    speakers = [_speaker(f"S{i:02d}",
                         mic_classes=["laptop-array", "headset-usb"])
                for i in range(1, 11)]
    cases = []
    for i in range(1, 11):
        cases += [
            _case(f"S{i:02d}-T1-EN-{k:03d}", speaker=f"S{i:02d}")
            for k in range(1, 5)]
    path = _write_batch(tmp_path, cases, speakers=speakers)
    findings = validate_batch(load_batch_manifest(path))
    assert not [f for f in findings if f.is_error]


# --------------------------------------------------------------------------
# exporter: bridge to the harness manifest loader (compatibility pin)
# --------------------------------------------------------------------------

def test_export_loads_through_harness_manifest_loader(tmp_path):
    audio = tmp_path / "audio"
    audio.mkdir()
    (audio / "a.wav").touch()
    (audio / "n.wav").touch()
    path = _write_batch(tmp_path, [
        _case("S01-T1-EN-001", audio="audio/a.wav", tags=["hello"],
              mic_model="Logitech H390"),
        _case("N1-SIL-001", speaker="", taxonomy="negative-silence",
              reference="", audio="audio/n.wav", snr_db=None),
        _case("S01-T2-EN-002", taxonomy="t2-chat-messages",
              status="planned", audio=""),        # must NOT export
    ])
    batch = load_batch_manifest(path)
    out = export_harness_manifest(batch)
    assert out == tmp_path / "manifest.toml"
    harness_cases = load_manifest(out)            # the real loader
    assert [c.id for c in harness_cases] == ["S01-T1-EN-001", "N1-SIL-001"]
    assert harness_cases[0].audio == (tmp_path / "audio" / "a.wav")
    assert harness_cases[0].tags == ("hello",)
    assert harness_cases[1].reference_text == ""
    text = out.read_text("utf-8")
    assert 'taxonomy = "negative-silence"' in text
    assert "mic_model" in text and "Logitech H390" in text
    assert "S01-T2-EN-002" not in text
    assert "pw-record" in text                    # source provenance line


def test_export_nothing_to_export(tmp_path):
    path = _write_batch(tmp_path, [_case("S01-T1-EN-001", status="planned",
                                         audio="")])
    with pytest.raises(ValueError, match="no recorded cases"):
        export_harness_manifest(load_batch_manifest(path))


def test_export_relativizes_to_custom_out_dir(tmp_path):
    audio = tmp_path / "audio"
    audio.mkdir()
    path = _write_batch(tmp_path, [_case("S01-T1-EN-001",
                                         audio="audio/a.wav")])
    out = tmp_path / "elsewhere" / "harness.toml"
    export_harness_manifest(load_batch_manifest(path), out)
    cases = load_manifest(out)
    assert cases[0].audio == (tmp_path / "audio" / "a.wav").resolve()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _run_cli(*argv, cwd=REPO_ROOT):
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    return subprocess.run(
        [sys.executable, "-m", "scripts.corpus", *argv],
        cwd=cwd, capture_output=True, text=True, env=env)


def test_cli_help_works():
    r = _run_cli("--help")
    assert r.returncode == 0
    assert "init" in r.stdout and "validate" in r.stdout \
        and "split" in r.stdout and "check-split" in r.stdout


def test_cli_init_validate_split_roundtrip(tmp_path):
    root = tmp_path / "eval-private"
    r = _run_cli("init", "b01", "--root", str(root), "--date", "2026-09-20",
                 cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    manifest = root / "b01" / "manifest.json"
    assert manifest.is_file()

    # a deliberately broken manifest: validate exits 1 with a message
    bad = _write_batch(root / "b01", [_case("S09-T1-EN-001",
                                           speaker="S09")],
                       name="bad.json")
    r = _run_cli("validate", str(bad), cwd=tmp_path)
    assert r.returncode == 1
    assert "not in the speakers table" in r.stdout + r.stderr

    # clean batch: warnings allowed, exit 0 (planned = not recorded yet)
    good = _write_batch(root / "b01", [
        _case("S01-T1-EN-001", status="planned", audio=""),
        _case("S01-T1-EN-002", status="planned", audio="")],
        name="good.json")
    r = _run_cli("validate", str(good), cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr

    # split + check-split: seal, then a train-only batch passes
    split_file = root / "b01" / "splits.toml"
    r = _run_cli("split", str(good), "--out", str(split_file),
                 "--whole-speaker", "S01", "--date", "2026-09-20",
                 cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    r = _run_cli("split", str(good), "--out", str(split_file),
                 cwd=tmp_path)
    assert r.returncode == 1 and "never edited" in r.stderr
    other = _write_batch(root / "b01", [
        _case("S02-T1-EN-001", speaker="S02", status="planned",
              audio="")],
        speakers=[_speaker("S02")], name="train.json")
    r = _run_cli("check-split", str(split_file), str(other), cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "zero held-out ids" in r.stdout
