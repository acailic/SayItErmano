"""Recording batch manifest model (real-speech corpus, spec phase 0).

A *batch manifest* is the JSON file a recording session fills in as it
goes: one row per planned/recorded case, provenance per the corpus spec
(docs/eval/corpus-spec.md §1), speakers with consent references, and the
recorder/backend/settings capture provenance. It lives OUTSIDE git
(under ``eval-private/…``) next to the audio.

The finished product for the eval harness is a plain TOML manifest
loaded by :mod:`fluidvoice.evalharness.manifest` — see
:mod:`scripts.corpus.exporter` for the bridge. This module only loads
and structurally validates the JSON batch format; semantic checks
(taxonomy counts, consent refs, audio placement) live in
:mod:`scripts.corpus.validator`.

Field reference (case):

===================  ==========================  =========================
field                type                        notes
===================  ==========================  =========================
``id``               non-empty string            ``S07-T3-ENCS-012`` style
``speaker``          ``S\\d{2}`` or ``""``       ``""`` allowed for
                                                 negative cases only
``language``         non-empty string            BCP-47 style code
``taxonomy``         taxonomy id or negative id  see :data:`TAXONOMIES`
``mic_class``        mic class id                see :data:`MIC_CLASSES`
``status``           planned/recorded/rejected   default ``planned``
``audio``            path (str)                  relative to the manifest;
                                                 may be ``""`` while
                                                 ``planned``
``reference_text``   string or null              typed at review time
``snr_db``           number or null              post-hoc estimate (§5)
===================  ==========================  =========================

Optional: ``accent``, ``mic_model``, ``environment``, ``session_date``,
``consent_ref``, ``tags``, ``expected_guard``, ``license``, ``notes``.
Unknown keys are tolerated (private corpora evolve ahead of this
model); validation errors name the manifest path, case index and id,
mirroring the harness manifest loader's style.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from fluidvoice.evalharness.manifest import GUARD_EXPECTED_VALUES

#: Bump when the batch format changes incompatibly.
MODEL_VERSION = 1

#: Speech strata (spec §4) — manifest taxonomy ids.
TAXONOMIES: dict[str, str] = {
    "t1-short-commands": "T1 short commands",
    "t2-chat-messages": "T2 chat messages",
    "t3-jargon": "T3 jargon & code-switching",
    "t4-names-numbers": "T4 names & numbers",
    "t5-long-form": "T5 long-form",
    "t6-developer-prompts": "T6 developer prompts",
}

#: Negative strata (spec §4) — ``taxonomy = "negative-*"``.
NEGATIVE_TAXONOMIES: dict[str, str] = {
    "negative-silence": "N1 digital silence",
    "negative-room-tone": "N2 room tone",
    "negative-noise": "N3 noise-only",
    "negative-faint-speech": "N4 noise + faint speech",
    "negative-non-speech": "N5 non-speech voice",
}

ALL_TAXONOMIES = {**TAXONOMIES, **NEGATIVE_TAXONOMIES}

#: Mic classes (spec §5) — the class mix should be roughly even.
MIC_CLASSES = ("laptop-array", "headset-3.5mm", "headset-usb", "usb-desk",
               "bluetooth-hfp")

#: Environment labels (spec §5) — one per session.
ENVIRONMENTS = ("home-office", "open-office", "cafe")

#: Default licensing tier (spec §1): exact string the manifest carries.
PRIVATE_LICENSE = "private — do not redistribute"

#: Corpus shape targets (spec "Shape at a glance" + §4).
SPEC_TAXONOMY_TARGETS: dict[str, int] = {
    "t1-short-commands": 40,
    "t2-chat-messages": 60,
    "t3-jargon": 45,
    "t4-names-numbers": 30,
    "t5-long-form": 20,
    "t6-developer-prompts": 45,
}
SPEC_SPEECH_TOTAL_TARGET = 240
SPEC_SPEECH_TOTAL_RANGE = (150, 300)      # min … ceiling
SPEC_NEGATIVE_TARGET = 30
SPEC_NEGATIVE_MINIMUM = 20
SPEC_SPEAKERS_TARGET = (12, 15)
SPEC_SPEAKERS_MINIMUM = 10

STATUS_VALUES = ("planned", "recorded", "rejected")

SPEAKER_ID_RE = re.compile(r"S\d{2}$")
#: Speech ids embed speaker + stratum (spec §2); negatives may omit the
#: speaker prefix when collected/synthesized outside a session (spec §4).
CASE_ID_RE = re.compile(r"(S\d{2}-)?(T[1-6]|N[1-5])-[A-Z0-9]{2,8}-\d{3}$")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}$")


class ManifestError(ValueError):
    """A batch manifest is malformed; str(e) is the actionable message."""


@dataclass(frozen=True)
class Speaker:
    """One pseudonymous speaker (roster detail stays outside git)."""

    id: str
    l1: str                                  # first language, e.g. "de"
    accent: str = ""                         # self-described, free text
    consent_ref: str = ""                    # points at the signed form
    mic_classes: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()          # languages actually recorded


@dataclass(frozen=True)
class Case:
    """One planned or recorded utterance (a manifest row)."""

    id: str
    speaker: str                             # "" allowed for negatives
    language: str
    taxonomy: str
    mic_class: str
    status: str                              # planned | recorded | rejected
    audio: str                               # relative to the manifest dir
    reference_text: str | None = None        # typed at review time
    accent: str = ""
    mic_model: str = ""
    snr_db: float | None = None
    environment: str = ""
    session_date: str = ""
    consent_ref: str = ""
    tags: tuple[str, ...] = ()
    expected_guard: str = "ok"
    license: str = ""                        # defaults to batch-level
    notes: str = ""

    @property
    def is_negative(self) -> bool:
        return self.taxonomy in NEGATIVE_TAXONOMIES

    def effective_license(self, batch_license: str) -> str:
        return self.license or batch_license


@dataclass(frozen=True)
class Recorder:
    """Capture provenance (spec §1 provenance block)."""

    name: str
    version: str
    backend: dict[str, object]
    settings: dict[str, object]


@dataclass(frozen=True)
class BatchManifest:
    """A loaded recording batch manifest."""

    path: Path
    batch: str
    license: str
    created: str
    manifest_schema: int
    recorder: Recorder
    taxonomy_targets: dict[str, int] = field(default_factory=dict)
    negative_target: int = SPEC_NEGATIVE_TARGET
    speakers: tuple[Speaker, ...] = ()
    cases: tuple[Case, ...] = ()

    def speaker(self, sid: str) -> Speaker | None:
        for s in self.speakers:
            if s.id == sid:
                return s
        return None

    def resolve_audio(self, case: Case) -> Path:
        """Absolute audio path (existence NOT checked here)."""
        return (self.path.parent / case.audio).resolve()


def _require_str(entry: dict, key: str, where: str, *,
                 allow_empty: bool = False) -> str:
    v = entry.get(key)
    if not isinstance(v, str) or (not allow_empty and not v.strip()):
        raise ManifestError(
            f"{where}missing or empty required field '{key}' ({v!r})")
    return v


def _optional_str(entry: dict, key: str, where: str) -> str:
    v = entry.get(key, "")
    if v is None:
        return ""
    if not isinstance(v, str):
        raise ManifestError(f"{where}'{key}' must be a string, got "
                            f"{type(v).__name__} ({v!r})")
    return v


def _speaker_from(raw: object, path: Path, index: int) -> Speaker:
    if not isinstance(raw, dict):
        raise ManifestError(f"{path}: speakers[{index}] must be a table")
    sid = raw.get("id")
    where = f"{path}: speakers[{index}] (id {sid!r}): "
    sid = _require_str(raw, "id", where)
    if not SPEAKER_ID_RE.match(sid):
        raise ManifestError(
            f"{where}'id' must look like 'S07' (S + two digits), got {sid!r}")
    l1 = _require_str(raw, "l1", where)
    consent = _require_str(raw, "consent_ref", where)
    mic_classes_raw = raw.get("mic_classes")
    if not isinstance(mic_classes_raw, list) or not mic_classes_raw or any(
            not isinstance(m, str) or not m.strip() for m in mic_classes_raw):
        raise ManifestError(
            f"{where}'mic_classes' must be a non-empty list of mic class "
            f"ids {list(MIC_CLASSES)}, got {mic_classes_raw!r}")
    for m in mic_classes_raw:
        if m not in MIC_CLASSES:
            raise ManifestError(
                f"{where}'mic_classes' entry {m!r} is not a known mic "
                f"class (one of {list(MIC_CLASSES)})")
    langs_raw = raw.get("languages", [l1])
    if not isinstance(langs_raw, list) or any(
            not isinstance(x, str) or not x.strip() for x in langs_raw):
        raise ManifestError(f"{where}'languages' must be a list of language "
                            f"codes, got {langs_raw!r}")
    return Speaker(
        id=sid,
        l1=l1,
        accent=_optional_str(raw, "accent", where),
        consent_ref=consent,
        mic_classes=tuple(mic_classes_raw),
        languages=tuple(langs_raw),
    )


def _case_from(raw: object, path: Path, index: int) -> Case:
    if not isinstance(raw, dict):
        raise ManifestError(f"{path}: cases[{index}] must be a table")
    cid = raw.get("id")
    cid = cid if isinstance(cid, str) else None
    where = f"{path}: cases[{index}] (id {cid!r}): "

    cid = _require_str(raw, "id", where)
    taxonomy = _require_str(raw, "taxonomy", where)
    if taxonomy not in ALL_TAXONOMIES:
        raise ManifestError(
            f"{where}'taxonomy' must be one of {sorted(ALL_TAXONOMIES)}, "
            f"got {taxonomy!r}")
    speaker = _require_str(raw, "speaker", where, allow_empty=True)
    if not speaker and taxonomy not in NEGATIVE_TAXONOMIES:
        raise ManifestError(
            f"{where}'speaker' may only be empty for negative-* cases; "
            "speech cases must name their speaker (e.g. 'S07')")
    if speaker and not SPEAKER_ID_RE.match(speaker):
        raise ManifestError(
            f"{where}'speaker' must look like 'S07' or be empty for "
            f"negatives, got {speaker!r}")
    language = _require_str(raw, "language", where)
    mic_class = _require_str(raw, "mic_class", where)
    if mic_class not in MIC_CLASSES:
        raise ManifestError(
            f"{where}'mic_class' must be one of {list(MIC_CLASSES)}, got "
            f"{mic_class!r}")
    status = raw.get("status", "planned")
    if status not in STATUS_VALUES:
        raise ManifestError(f"{where}'status' must be one of "
                            f"{list(STATUS_VALUES)}, got {status!r}")
    audio = raw.get("audio", "")
    if not isinstance(audio, str):
        raise ManifestError(f"{where}'audio' must be a string path "
                            f"(relative to the manifest dir), got "
                            f"{audio!r}")
    if not audio and status in ("recorded", "rejected"):
        raise ManifestError(
            f"{where}'audio' path is required once status is {status!r}")

    ref = raw.get("reference_text")
    if ref is not None and not isinstance(ref, str):
        raise ManifestError(f"{where}'reference_text' must be a string or "
                            f"null, got {type(ref).__name__}")
    snr = raw.get("snr_db")
    if snr is not None and (not isinstance(snr, (int, float))
                            or isinstance(snr, bool)):
        raise ManifestError(f"{where}'snr_db' must be a number or null, "
                            f"got {snr!r}")
    guard = raw.get("expected_guard", "flag" if taxonomy in
                    NEGATIVE_TAXONOMIES else "ok")
    if guard not in GUARD_EXPECTED_VALUES:
        raise ManifestError(f"{where}'expected_guard' must be one of "
                            f"{list(GUARD_EXPECTED_VALUES)}, got {guard!r}")
    tags_raw = raw.get("tags", [])
    if not isinstance(tags_raw, list) or any(
            not isinstance(t, str) or not t.strip() for t in tags_raw):
        raise ManifestError(f"{where}'tags' must be a list of non-empty "
                            f"strings, got {tags_raw!r}")

    return Case(
        id=cid,
        speaker=speaker,
        language=language,
        taxonomy=taxonomy,
        mic_class=mic_class,
        status=status,
        audio=audio,
        reference_text=ref,
        accent=_optional_str(raw, "accent", where),
        mic_model=_optional_str(raw, "mic_model", where),
        snr_db=None if snr is None else float(snr),
        environment=_optional_str(raw, "environment", where),
        session_date=_optional_str(raw, "session_date", where),
        consent_ref=_optional_str(raw, "consent_ref", where),
        tags=tuple(tags_raw),
        expected_guard=guard,
        license=_optional_str(raw, "license", where),
        notes=_optional_str(raw, "notes", where),
    )


def case_from_dict(raw: object, path: Path | str = "<memory>",
                   index: int = 0) -> Case:
    """Validate one raw case dict into a :class:`Case` (same errors as
    :func:`load_batch_manifest`; handy for programmatic callers)."""
    return _case_from(raw, Path(path), index)


def cases_from_dicts(raw_cases: list[object],
                     path: Path | str = "<memory>") -> list[Case]:
    """Validate a list of raw case dicts into :class:`Case` objects,
    rejecting duplicate ids like the loader does."""
    cases: list[Case] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_cases):
        case = case_from_dict(raw, path, i)
        if case.id in seen:
            raise ManifestError(f"{path}: cases[{i}] (id {case.id!r}): "
                                "duplicate case id in this manifest")
        seen.add(case.id)
        cases.append(case)
    return cases


def _recorder_from(raw: object, path: Path) -> Recorder:
    if not isinstance(raw, dict):
        raise ManifestError(f"{path}: 'recorder' must be a table "
                            f"(capture provenance), got {type(raw).__name__}")
    where = f"{path}: recorder: "
    name = _require_str(raw, "name", where)
    version = _require_str(raw, "version", where)
    backend = raw.get("backend")
    if not isinstance(backend, dict) or not backend:
        raise ManifestError(f"{where}'backend' must be a non-empty table "
                            f"(capture backend provenance), got {backend!r}")
    settings = raw.get("settings")
    if not isinstance(settings, dict) or not settings:
        raise ManifestError(f"{where}'settings' must be a non-empty table "
                            f"(capture settings), got {settings!r}")
    return Recorder(name=name, version=version, backend=dict(backend),
                    settings=dict(settings))


def load_batch_manifest(path: Path) -> BatchManifest:
    """Load and structurally validate one JSON batch manifest.

    Raises :class:`ManifestError` with the path, index and id in every
    message (the harness loader's error style).
    """
    path = Path(path)
    try:
        raw_bytes = path.read_bytes()
    except OSError as e:
        raise ManifestError(f"cannot read batch manifest {path}: {e}") from e
    try:
        data = json.loads(raw_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ManifestError(f"{path}: invalid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ManifestError(f"{path}: batch manifest must be a JSON object")

    schema = data.get("manifest_schema")
    if schema != MODEL_VERSION:
        raise ManifestError(
            f"{path}: manifest_schema is {schema!r}; this tooling reads "
            f"version {MODEL_VERSION} (docs/eval/recording-manifest-"
            f"template.json documents the current format)")
    batch = _require_str(data, "batch", f"{path}: ")
    license_ = _require_str(data, "license", f"{path}: ")
    created = _require_str(data, "created", f"{path}: ")
    recorder = _recorder_from(data.get("recorder"), path)

    targets_raw = data.get("taxonomy_targets", SPEC_TAXONOMY_TARGETS)
    if not isinstance(targets_raw, dict) or any(
            not isinstance(k, str) or k not in TAXONOMIES
            or not isinstance(v, int) or isinstance(v, bool) or v < 0
            for k, v in targets_raw.items()):
        raise ManifestError(
            f"{path}: 'taxonomy_targets' must map taxonomy ids "
            f"{sorted(TAXONOMIES)} to non-negative ints, got {targets_raw!r}")
    negative_target = data.get("negative_target", SPEC_NEGATIVE_TARGET)
    if not isinstance(negative_target, int) or isinstance(negative_target, \
            bool) or negative_target < 0:
        raise ManifestError(f"{path}: 'negative_target' must be a "
                            f"non-negative int, got {negative_target!r}")

    speakers_raw = data.get("speakers")
    if not isinstance(speakers_raw, list):
        raise ManifestError(f"{path}: 'speakers' must be a list of tables")
    speakers: list[Speaker] = []
    seen_speakers: set[str] = set()
    for i, s in enumerate(speakers_raw):
        speaker = _speaker_from(s, path, i)
        if speaker.id in seen_speakers:
            raise ManifestError(f"{path}: speakers[{i}] (id {speaker.id!r}):"
                                " duplicate speaker id")
        seen_speakers.add(speaker.id)
        speakers.append(speaker)

    cases_raw = data.get("cases")
    if not isinstance(cases_raw, list):
        raise ManifestError(f"{path}: 'cases' must be a list of tables")
    cases: list[Case] = []
    seen: set[str] = set()
    for i, c in enumerate(cases_raw):
        case = _case_from(c, path, i)
        if case.id in seen:
            raise ManifestError(f"{path}: cases[{i}] (id {case.id!r}): "
                                "duplicate case id in this manifest")
        seen.add(case.id)
        cases.append(case)

    return BatchManifest(
        path=path,
        batch=batch,
        license=license_,
        created=created,
        manifest_schema=schema,
        recorder=recorder,
        taxonomy_targets=dict(targets_raw),
        negative_target=negative_target,
        speakers=tuple(speakers),
        cases=tuple(cases),
    )
