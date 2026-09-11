"""Canonical recording batch manifest template.

The checked-in copy lives at ``docs/eval/recording-manifest-template.json``
and is the file humans read; this module carries an identical in-code
copy so ``python -m scripts.corpus init`` works from any cwd (and keeps
working if a docs checkout is absent). ``tests/test_corpus_tooling.py``
pins the two together — edit both or neither.

``init`` substitutes ``batch`` and ``created``; everything else is
deliberately placeholder-shaped so ``validate`` nags until a human has
replaced the obvious ``<...>`` values.
"""
from __future__ import annotations

import json
from pathlib import Path

from .model import (
    NEGATIVE_TAXONOMIES,
    SPEC_NEGATIVE_TARGET,
    SPEC_TAXONOMY_TARGETS,
    TAXONOMIES,
)

TEMPLATE_PATH_REL = Path("docs/eval/recording-manifest-template.json")

_READ_ME = [
    "Recording batch manifest for the SayItErmano real-speech corpus",
    "(docs/eval/corpus-spec.md). Scaffold a copy with:",
    "  python -m scripts.corpus init <batch-name>",
    "This file, the audio and the signed consents live OUTSIDE git",
    "(eval-private/... is gitignored); never commit any of them.",
    "Validate as you record: python -m scripts.corpus validate <file>",
    "Export to the harness format once recorded:",
    "  python -m scripts.corpus export <file>",
    "Case status: planned (not recorded yet) / recorded (final take",
    "selected — one file per utterance) / rejected (do not use).",
]

#: One worked example per taxonomy (T1–T6 and N1–N5); the id embeds the
#: speaker and stratum per corpus-spec §2. Replace rows per take.
_EXAMPLE_SPEAKER = {
    "id": "S01",
    "l1": "en",
    "accent": "en-US (self-described)",
    "languages": ["en"],
    "mic_classes": ["laptop-array", "headset-usb"],
    "consent_ref": "S01 consent <YYYY-MM-DD> (signed form kept at "
                   "~/.local/share/sayit-ermano-eval/consent/)",
}

_EXAMPLE_CASES = [
    {
        "id": "S01-T1-EN-001",
        "speaker": "S01",
        "language": "en",
        "accent": "en-US (self-described)",
        "taxonomy": "t1-short-commands",
        "mic_class": "headset-usb",
        "mic_model": "<e.g. Logitech H390>",
        "snr_db": None,
        "environment": "home-office",
        "session_date": "<YYYY-MM-DD>",
        "consent_ref": "S01 consent <YYYY-MM-DD>",
        "status": "planned",
        "audio": "",
        "reference_text": None,
        "tags": [],
        "expected_guard": "ok",
        "license": "private — do not redistribute",
        "notes": "replace this row per take (spec §5: >=1 s silence "
                 "lead-in/out, retakes allowed, final selection recorded "
                 "here)",
    },
    {
        "id": "S01-T2-EN-001",
        "speaker": "S01",
        "language": "en",
        "taxonomy": "t2-chat-messages",
        "mic_class": "headset-usb",
        "status": "planned",
        "audio": "",
        "reference_text": None,
        "tags": [],
        "expected_guard": "ok",
        "license": "private — do not redistribute",
    },
    {
        "id": "S01-T3-ENCS-001",
        "speaker": "S01",
        "language": "en",
        "taxonomy": "t3-jargon",
        "mic_class": "headset-usb",
        "status": "planned",
        "audio": "",
        "reference_text": None,
        "tags": [],
        "expected_guard": "ok",
        "license": "private — do not redistribute",
    },
    {
        "id": "S01-T4-EN-001",
        "speaker": "S01",
        "language": "en",
        "taxonomy": "t4-names-numbers",
        "mic_class": "headset-usb",
        "status": "planned",
        "audio": "",
        "reference_text": None,
        "tags": [],
        "expected_guard": "ok",
        "license": "private — do not redistribute",
        "notes": "T4 items are read verbatim; tag the name/number tokens "
                 "(hotword recall convention G7)",
    },
    {
        "id": "S01-T5-EN-001",
        "speaker": "S01",
        "language": "en",
        "taxonomy": "t5-long-form",
        "mic_class": "headset-usb",
        "status": "planned",
        "audio": "",
        "reference_text": None,
        "tags": [],
        "expected_guard": "ok",
        "license": "private — do not redistribute",
    },
    {
        "id": "S01-T6-EN-001",
        "speaker": "S01",
        "language": "en",
        "taxonomy": "t6-developer-prompts",
        "mic_class": "headset-usb",
        "status": "planned",
        "audio": "",
        "reference_text": None,
        "tags": [],
        "expected_guard": "ok",
        "license": "private — do not redistribute",
    },
    {
        "id": "N1-SIL-001",
        "speaker": "",
        "language": "zxx",
        "taxonomy": "negative-silence",
        "mic_class": "headset-usb",
        "status": "planned",
        "audio": "",
        "reference_text": "",
        "tags": [],
        "expected_guard": "flag",
        "license": "private — do not redistribute",
    },
    {
        "id": "N2-ROOM-001",
        "speaker": "",
        "language": "zxx",
        "taxonomy": "negative-room-tone",
        "mic_class": "headset-usb",
        "status": "planned",
        "audio": "",
        "reference_text": "",
        "tags": [],
        "expected_guard": "flag",
        "license": "private — do not redistribute",
    },
    {
        "id": "N3-NOISE-001",
        "speaker": "",
        "language": "zxx",
        "taxonomy": "negative-noise",
        "mic_class": "headset-usb",
        "status": "planned",
        "audio": "",
        "reference_text": "",
        "tags": [],
        "expected_guard": "flag",
        "license": "private — do not redistribute",
        "notes": "keyboard/fan/street/cafe at 2 levels (spec §4 N3)",
    },
    {
        "id": "N4-FAINT-001",
        "speaker": "",
        "language": "zxx",
        "taxonomy": "negative-faint-speech",
        "mic_class": "headset-usb",
        "status": "planned",
        "audio": "",
        "reference_text": "",
        "tags": [],
        "expected_guard": "flag",
        "license": "private — do not redistribute",
    },
    {
        "id": "N5-COUGH-001",
        "speaker": "",
        "language": "zxx",
        "taxonomy": "negative-non-speech",
        "mic_class": "headset-usb",
        "status": "planned",
        "audio": "",
        "reference_text": "",
        "tags": [],
        "expected_guard": "flag",
        "license": "private — do not redistribute",
    },
]

#: The canonical template document (kept byte-compatible with
#: docs/eval/recording-manifest-template.json — pinned by test).
TEMPLATE: dict = {
    "_read_me": _READ_ME,
    "manifest_schema": 1,
    "batch": "<batch-name>",
    "created": "<YYYY-MM-DD>",
    "license": "private — do not redistribute",
    "recorder": {
        "name": "pw-record",
        "version": "<capture tool version>",
        "backend": {"type": "pipewire", "version": "<backend version>"},
        "settings": {
            "master_format": "48 kHz FLAC (lossless archive)",
            "eval_copy_format": "16 kHz mono s16 WAV",
            "lead_in_out_silence_s": 1.0,
        },
    },
    "taxonomy_targets": dict(SPEC_TAXONOMY_TARGETS),
    "negative_target": SPEC_NEGATIVE_TARGET,
    "speakers": [_EXAMPLE_SPEAKER],
    "cases": _EXAMPLE_CASES,
}

#: Sanity guard so the template never drifts from the model's vocab.
for _c in TEMPLATE["cases"]:
    assert _c["taxonomy"] in {**TAXONOMIES, **NEGATIVE_TAXONOMIES}, _c


def find_template_document(start: Path | None = None) -> dict:
    """Return the template dict: the docs copy if a checkout is found
    (searching upward from *start*/cwd), else the in-code TEMPLATE."""
    probe = (Path(start) if start else Path.cwd()).resolve()
    for cand_dir in (probe, *probe.parents):
        cand = cand_dir / TEMPLATE_PATH_REL
        if cand.is_file():
            try:
                data = json.loads(cand.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except (OSError, json.JSONDecodeError):
                pass  # fall back to the in-code copy
            break
    return TEMPLATE
