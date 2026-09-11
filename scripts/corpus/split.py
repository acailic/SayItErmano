"""Held-out split sealer (corpus spec §6).

The split is **deterministic and hash-based**: within each
``(speaker, language, taxonomy)`` stratum, cases are ordered by
``sha256(case_id)`` and every *N*-th (N = ``round(1/fraction)``, 5 for
the spec's ~20%) goes to ``heldout``; speakers listed as whole held-out
speakers contribute every case. Nothing is random and nothing depends
on file order — the same input cases always seal the same split.

The result is written once to a sealed TOML file (``splits.toml``) and
never edited afterwards. Tuning tools call :func:`is_held_out` (or the
:class:`HeldOutGuard` wrapper) and
:func:`assert_no_held_out` before touching anything tuned on ``train``.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

try:  # TOML is stdlib for reading; writing goes through tomlout
    import tomllib
except ModuleNotFoundError:  # pragma: no cover (py>=3.11 everywhere)
    import tomli as tomllib  # type: ignore[no-redef]

from .model import Case, ManifestError
from .tomlout import dump_toml

SPLIT_METHOD = "hash-stratified-v1"
DEFAULT_FRACTION = 0.2
SEALED_FILENAME = "splits.toml"


class SplitError(ValueError):
    """A split is malformed or a held-out guard tripped; str(e) is the
    actionable message."""


@dataclass(frozen=True)
class SealedSplit:
    """An immutable, sealed held-out assignment."""

    method: str
    fraction_target: float
    sealed_date: str
    corpus: str                     # batch/manifest the ids came from
    whole_speakers: tuple[str, ...]
    heldout_ids: frozenset[str]
    heldout_count: int
    train_count: int
    ids_digest: str                 # sha256 over the sorted id list

    def is_held_out(self, case_id: str) -> bool:
        return case_id in self.heldout_ids


def _ids_digest(ids: frozenset[str]) -> str:
    return hashlib.sha256("\n".join(sorted(ids)).encode("utf-8")).hexdigest()


def _hash_key(case_id: str) -> str:
    return hashlib.sha256(case_id.encode("utf-8")).hexdigest()


def compute_heldout(
    cases: list[Case] | tuple[Case, ...],
    *,
    fraction: float = DEFAULT_FRACTION,
    whole_speakers: tuple[str, ...] | list[str] = (),
) -> frozenset[str]:
    """The deterministic held-out id set for these cases (pure function).

    Stratified by ``(speaker, language, taxonomy)``; within each stratum
    cases are ordered by ``sha256(id)`` and every *N*-th is held out.
    Whole held-out speakers contribute all of their cases first and are
    excluded from the stratified pass.
    """
    if not 0.0 < fraction <= 0.5:
        raise SplitError(f"fraction must be in (0, 0.5], got {fraction!r}")
    step = max(1, round(1 / fraction))

    whole = set(whole_speakers)
    heldout: set[str] = set()
    strata: dict[tuple[str, str, str], list[Case]] = {}
    for case in cases:
        if case.speaker in whole:
            heldout.add(case.id)
        else:
            strata.setdefault(
                (case.speaker, case.language, case.taxonomy),
                []).append(case)

    for _, group in sorted(strata.items()):
        ordered = sorted(group, key=lambda c: _hash_key(c.id))
        heldout.update(c.id for c in ordered[step - 1::step])
    return frozenset(heldout)


def seal(
    cases: list[Case] | tuple[Case, ...],
    *,
    corpus: str,
    fraction: float = DEFAULT_FRACTION,
    whole_speakers: tuple[str, ...] | list[str] = (),
    sealed_date: str | None = None,
    allow_under: bool = False,
) -> SealedSplit:
    """Compute and seal the split; the spec's ≥20% overall target is a
    hard error unless ``allow_under`` (recorded in the sealed file)."""
    if not cases:
        raise SplitError("cannot seal an empty corpus")
    known = {c.speaker for c in cases if c.speaker}
    unknown = [s for s in whole_speakers if s not in known]
    if unknown:
        raise SplitError(
            f"whole held-out speakers {unknown} have no cases in this "
            "corpus (typo? speakers with no cases cannot be sealed out)")

    heldout = compute_heldout(cases, fraction=fraction,
                              whole_speakers=whole_speakers)
    if not heldout:
        raise SplitError(
            "held-out split is empty — no stratum reached the every-"
            f"{max(1, round(1 / fraction))}-th case; add cases or hold out "
            "whole speakers")
    actual = len(heldout) / len(cases)
    if actual < fraction and not allow_under:
        raise SplitError(
            f"held-out fraction {actual:.1%} is below the {fraction:.0%} "
            f"target ({len(heldout)}/{len(cases)} cases) — hold out whole "
            "speakers (spec §6.2) or pass allow_under=True to record the "
            "shortfall deliberately")

    return SealedSplit(
        method=SPLIT_METHOD,
        fraction_target=fraction,
        sealed_date=sealed_date or date.today().isoformat(),
        corpus=corpus,
        whole_speakers=tuple(sorted(set(whole_speakers))),
        heldout_ids=heldout,
        heldout_count=len(heldout),
        train_count=len(cases) - len(heldout),
        ids_digest=_ids_digest(heldout),
    )


def write_sealed(split: SealedSplit, path: Path) -> Path:
    """Write the sealed split file (one-way door: see load_sealed)."""
    path = Path(path)
    data = {
        "method": split.method,
        "sealed_date": split.sealed_date,
        "corpus": split.corpus,
        "fraction_target": split.fraction_target,
        "fraction_actual": (split.heldout_count /
                            (split.heldout_count + split.train_count)),
        "whole_speakers_heldout": list(split.whole_speakers),
        "heldout_count": split.heldout_count,
        "train_count": split.train_count,
        "heldout_ids_sha256": split.ids_digest,
        "heldout": sorted(split.heldout_ids),
    }
    header = (
        "# Sealed held-out split (docs/eval/corpus-spec.md §6).\n"
        "# Written ONCE by `python -m scripts.corpus split`; never edit.\n"
        "# Tuning happens on train only; heldout runs are acceptance runs.\n"
    )
    path.write_text(header + dump_toml(data), encoding="utf-8")
    return path


def load_sealed(path: Path) -> SealedSplit:
    """Load and verify a sealed split (digest mismatch = tamper = error)."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise SplitError(f"cannot read sealed split {path}: {e}") from e
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise SplitError(f"{path}: invalid TOML: {e}") from e

    for key in ("method", "sealed_date", "corpus", "heldout",
                "heldout_ids_sha256"):
        if key not in data:
            raise SplitError(f"{path}: sealed split is missing '{key}' "
                             "(re-seal from the corpus manifest; do not "
                             "hand-edit)")
    ids = data["heldout"]
    if not isinstance(ids, list) or not ids or any(
            not isinstance(x, str) for x in ids):
        raise SplitError(f"{path}: 'heldout' must be a non-empty list of "
                         "case ids")
    digest = _ids_digest(frozenset(ids))
    if digest != data["heldout_ids_sha256"]:
        raise SplitError(
            f"{path}: held-out id list does not match its digest "
            f"({digest[:12]}… != {str(data['heldout_ids_sha256'])[:12]}…) —"
            " the sealed file was edited; re-seal from the corpus manifest "
            "instead")
    held = frozenset(ids)
    whole = data.get("whole_speakers_heldout", [])
    return SealedSplit(
        method=data["method"],
        fraction_target=float(data.get("fraction_target",
                                       DEFAULT_FRACTION)),
        sealed_date=data["sealed_date"],
        corpus=data["corpus"],
        whole_speakers=tuple(whole) if isinstance(whole, list) else (),
        heldout_ids=held,
        heldout_count=len(held),
        train_count=int(data.get("train_count", 0)),
        ids_digest=data["heldout_ids_sha256"],
    )


def is_held_out(case_id: str, sealed: SealedSplit) -> bool:
    """The guard any tuning tool calls before looking at a case."""
    return sealed.is_held_out(case_id)


@dataclass(frozen=True)
class HeldOutGuard:
    """Convenience wrapper: load once, ask per case."""

    split: SealedSplit

    @classmethod
    def load(cls, path: Path) -> "HeldOutGuard":
        return cls(load_sealed(path))

    def __call__(self, case_id: str) -> bool:
        return is_held_out(case_id, self.split)


def assert_no_held_out(
    case_ids: list[str] | tuple[str, ...],
    sealed: SealedSplit,
    *,
    source: str = "train manifest",
) -> None:
    """Raise :class:`SplitError` if any id is held out.

    The spec's invariant: a train manifest contains ZERO held-out ids —
    this is the check that keeps tuning data and acceptance data
    disjoint.
    """
    offenders = sorted(set(case_ids) & sealed.heldout_ids)
    if offenders:
        shown = ", ".join(offenders[:5])
        more = f" (+{len(offenders) - 5} more)" if len(offenders) > 5 else ""
        raise SplitError(
            f"{source} contains {len(offenders)} held-out case(s): "
            f"{shown}{more} — held-out cases must never appear in tuning "
            f"input (sealed split {sealed.corpus}, method {sealed.method})")


# ManifestError re-exported for callers that catch both loader families.
__all__ = [
    "DEFAULT_FRACTION",
    "HeldOutGuard",
    "SEALED_FILENAME",
    "SealedSplit",
    "SplitError",
    "ManifestError",
    "assert_no_held_out",
    "compute_heldout",
    "is_held_out",
    "load_sealed",
    "seal",
    "write_sealed",
]
