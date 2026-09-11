"""scripts.corpus — recording-corpus tooling (spec phase 0, wave 2).

Turnkey tooling around the real-speech eval corpus
(docs/eval/corpus-spec.md): recording batch manifests (JSON, template
in docs/eval/), the consent text, a deterministic sealed held-out
split with an ``is_held_out`` guard, a provenance validator, and the
bridge to the harness manifest format. CLI: ``python -m
scripts.corpus --help``.
"""
from __future__ import annotations

from .exporter import export_harness_manifest
from .model import (
    ALL_TAXONOMIES,
    MIC_CLASSES,
    NEGATIVE_TAXONOMIES,
    PRIVATE_LICENSE,
    SPEC_NEGATIVE_TARGET,
    SPEC_TAXONOMY_TARGETS,
    TAXONOMIES,
    BatchManifest,
    Case,
    ManifestError,
    Recorder,
    Speaker,
    case_from_dict,
    cases_from_dicts,
    load_batch_manifest,
)
from .scaffold import scaffold_batch_manifest
from .split import (
    SealedSplit,
    SplitError,
    assert_no_held_out,
    compute_heldout,
    is_held_out,
    load_sealed,
    seal,
    write_sealed,
)
from .validator import Finding, validate_batch, validate_no_heldout

__all__ = [
    "ALL_TAXONOMIES",
    "BatchManifest",
    "Case",
    "Finding",
    "ManifestError",
    "MIC_CLASSES",
    "NEGATIVE_TAXONOMIES",
    "PRIVATE_LICENSE",
    "Recorder",
    "SPEC_NEGATIVE_TARGET",
    "SPEC_TAXONOMY_TARGETS",
    "SealedSplit",
    "Speaker",
    "SplitError",
    "TAXONOMIES",
    "assert_no_held_out",
    "case_from_dict",
    "cases_from_dicts",
    "compute_heldout",
    "export_harness_manifest",
    "is_held_out",
    "load_batch_manifest",
    "load_sealed",
    "scaffold_batch_manifest",
    "seal",
    "validate_batch",
    "validate_no_heldout",
    "write_sealed",
]
