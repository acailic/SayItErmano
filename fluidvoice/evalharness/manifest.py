"""Evaluation manifest loading and validation (plan P1.4).

A manifest is a TOML file (repo convention: config is TOML) with one
``[[cases]]`` table per case. Fields per case:

=================  ==============================  =========================
field              type                           notes
=================  ==============================  =========================
``id``             non-empty string               unique across manifests
``audio``          path (str)                     relative to the manifest
``reference_text`` string (may be empty)          ground-truth transcript
``language``       string                         BCP-47/whisper-style code
``tags``           list of non-empty strings      double as the hotword
                                                  vocabulary (hotword recall
                                                  scores the tags the
                                                  reference contains)
``expected_guard`` ``ok`` / ``flag`` / ``none``   hallucination-guard
                                                  expectation (``none`` =
                                                  case not guard-scored)
``license``        non-empty string               e.g. ``CC0-1.0``
``source``         non-empty string               where the audio came from
``synth``          optional table                 regenerable fixture recipe
=================  ==============================  =========================

``synth`` (all optional keys shown): ``kind`` (``tone`` / ``chirp`` /
``silence``), ``seconds`` (> 0), ``rate`` (default 16000). See
fluidvoice/evalharness/synth.py.

Optional provenance keys (corpus-spec §1; used by subgroup aggregation
and the guard sweep): ``speaker`` / ``mic_class`` / ``taxonomy`` /
``split`` (non-empty strings when present) and ``snr_db`` (number when
present). ``split`` labels the case ``train`` / ``heldout`` for the
corpus-spec §6 rule — threshold sweeps only ever use ``train`` cases.

Validation fails loudly (ManifestError) with the manifest path, case
index and id in every message. Unknown extra keys are tolerated —
private corpora evolve ahead of this schema — but every required key
must be present and well-typed.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .metrics import GUARD_EXPECTED_VALUES

MANIFEST_FILENAME = "manifest.toml"
SYNTH_KINDS = ("tone", "chirp", "silence")


class ManifestError(ValueError):
    """A manifest is malformed; str(e) is the actionable message."""


@dataclass(frozen=True)
class SynthSpec:
    """Deterministic recipe for a regenerable synthetic fixture."""

    kind: str                      # one of SYNTH_KINDS
    seconds: float                 # > 0
    rate: int = 16_000


@dataclass(frozen=True)
class Case:
    """One evaluation case (a manifest row, audio path resolved).

    Provenance fields (speaker, mic_class, taxonomy, snr_db, split) are
    None when the manifest doesn't carry them — subgroups group those
    cases under ``(unset)`` instead of dropping them.
    """

    id: str
    audio: Path                    # absolute, resolved against the manifest
    reference_text: str
    language: str
    tags: tuple[str, ...]
    expected_guard: str            # ok | flag | none
    license: str
    source: str
    synth: SynthSpec | None = None
    speaker: str | None = None     # corpus-spec §1 provenance (S07-style id)
    mic_class: str | None = None   # e.g. "headset-usb" (§5)
    taxonomy: str | None = None    # stratum id, e.g. "t3-jargon" (§4)
    snr_db: float | None = None    # post-hoc estimate (§5)
    split: str | None = None       # "train" / "heldout" (§6)


def resolve_manifest_path(path: Path) -> Path:
    """Accept a manifest file or a directory containing ``manifest.toml``."""
    if path.is_dir():
        cand = path / MANIFEST_FILENAME
        if not cand.exists():
            raise ManifestError(
                f"{path}: directory has no {MANIFEST_FILENAME} "
                "(pass the manifest file directly?)")
        return cand
    return path


def default_manifest_path() -> Path:
    """The committed synthetic corpus shipped inside the package."""
    return Path(__file__).resolve().parent / "corpus" / MANIFEST_FILENAME


def _where(path: Path, index: int, cid: str | None) -> str:
    cid_part = f" (id {cid!r})" if cid else ""
    return f"{path}: cases[{index}]{cid_part}: "


def _synth_from(raw: object, where: str) -> SynthSpec:
    if not isinstance(raw, dict):
        raise ManifestError(f"{where}'synth' must be a table, got "
                            f"{type(raw).__name__}")
    kind = raw.get("kind")
    if kind not in SYNTH_KINDS:
        raise ManifestError(f"{where}'synth.kind' must be one of "
                            f"{SYNTH_KINDS}, got {kind!r}")
    seconds = raw.get("seconds")
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) \
            or not seconds > 0:
        raise ManifestError(f"{where}'synth.seconds' must be a number > 0, "
                            f"got {seconds!r}")
    rate = raw.get("rate", 16_000)
    if not isinstance(rate, int) or isinstance(rate, bool) or rate <= 0:
        raise ManifestError(f"{where}'synth.rate' must be an int > 0, "
                            f"got {rate!r}")
    return SynthSpec(kind=kind, seconds=float(seconds), rate=rate)


def load_manifest(path: Path) -> list[Case]:
    """Load and validate one manifest file; ManifestError on any defect."""
    path = resolve_manifest_path(Path(path))
    try:
        raw_bytes = path.read_bytes()
    except OSError as e:
        raise ManifestError(f"cannot read manifest {path}: {e}") from e
    try:
        data = tomllib.loads(raw_bytes.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise ManifestError(f"{path}: invalid TOML: {e}") from e

    cases_raw = data.get("cases")
    if not isinstance(cases_raw, list) or not cases_raw:
        raise ManifestError(f"{path}: no [[cases]] tables (a manifest needs "
                            "at least one case)")

    manifest_dir = path.resolve().parent
    cases: list[Case] = []
    seen: set[str] = set()
    for i, entry in enumerate(cases_raw):
        if not isinstance(entry, dict):
            raise ManifestError(f"{path}: cases[{i}] must be a table")
        where = _where(path, i, entry.get("id") if isinstance(
            entry.get("id"), str) else None)

        for field in ("id", "audio", "language", "license", "source"):
            v = entry.get(field)
            if not isinstance(v, str) or not v.strip():
                raise ManifestError(
                    f"{where}missing or empty required field '{field}' "
                    f"({v!r})")
        ref = entry.get("reference_text")
        if ref is None:
            raise ManifestError(f"{where}missing required field "
                                "'reference_text' (empty string is allowed)")
        if not isinstance(ref, str):
            raise ManifestError(f"{where}'reference_text' must be a string, "
                                f"got {type(ref).__name__}")
        guard = entry.get("expected_guard")
        if guard not in GUARD_EXPECTED_VALUES:
            raise ManifestError(
                f"{where}'expected_guard' must be one of "
                f"{list(GUARD_EXPECTED_VALUES)}, got {guard!r}")
        tags_raw = entry.get("tags")
        if not isinstance(tags_raw, list) or any(
                not isinstance(t, str) or not t.strip() for t in tags_raw):
            raise ManifestError(f"{where}'tags' must be a list of non-empty "
                                f"strings, got {tags_raw!r}")

        cid = entry["id"]
        if cid in seen:
            raise ManifestError(f"{where}duplicate case id {cid!r} in this "
                                "manifest")
        seen.add(cid)

        synth = None
        if "synth" in entry and entry["synth"] is not None:
            synth = _synth_from(entry["synth"], where)

        speaker = mic_class = taxonomy = split = None
        for key in ("speaker", "mic_class", "taxonomy", "split"):
            v = entry.get(key)
            if v is None:
                continue
            if not isinstance(v, str) or not v.strip():
                raise ManifestError(f"{where}'{key}' must be a non-empty "
                                    f"string when present, got {v!r}")
            if key == "speaker":
                speaker = v
            elif key == "mic_class":
                mic_class = v
            elif key == "taxonomy":
                taxonomy = v
            else:
                split = v
        snr_db = entry.get("snr_db")
        if snr_db is not None:
            if not isinstance(snr_db, (int, float)) or isinstance(snr_db, bool):
                raise ManifestError(f"{where}'snr_db' must be a number when "
                                    f"present, got {snr_db!r}")
            snr_db = float(snr_db)

        cases.append(Case(
            id=cid,
            audio=(manifest_dir / entry["audio"]).resolve(),
            reference_text=ref,
            language=entry["language"],
            tags=tuple(tags_raw),
            expected_guard=guard,
            license=entry["license"],
            source=entry["source"],
            synth=synth,
            speaker=speaker,
            mic_class=mic_class,
            taxonomy=taxonomy,
            snr_db=snr_db,
            split=split,
        ))
    return cases


def load_manifests(paths: list[Path]) -> list[Case]:
    """Load several manifests (committed + ``--external`` private) as one
    corpus; duplicate ids across manifests are an error."""
    merged: list[Case] = []
    seen: dict[str, Path] = {}
    for p in paths:
        for case in load_manifest(p):
            if case.id in seen:
                raise ManifestError(
                    f"duplicate case id {case.id!r}: defined in both "
                    f"{seen[case.id]} and {p}")
            seen[case.id] = p
            merged.append(case)
    return merged
