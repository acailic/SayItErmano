"""``init``: scaffold a recording batch manifest from the template.

Creates ``<root>/<batch-name>/manifest.json`` plus an ``audio/``
directory, refusing to overwrite an existing manifest unless ``force``.
The scaffold's taxonomy targets are pre-filled from the corpus spec
(T1–T6 = 40/60/45/30/20/45, negatives = 30) so ``validate`` reports
progress against the real shape from the first take.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .model import ManifestError
from .template import find_template_document

MANIFEST_NAME = "manifest.json"
AUDIO_DIR = "audio"
DEFAULT_ROOT = Path("eval-private")


def scaffold_batch_manifest(
    batch_name: str,
    out_dir: Path,
    *,
    created: str | None = None,
    template: dict | None = None,
    force: bool = False,
) -> Path:
    """Write the batch manifest skeleton; returns its path.

    ``out_dir`` is created (with ``audio/``); an existing manifest is an
    error unless ``force``.
    """
    if not batch_name or any(ch in batch_name for ch in "/\\ "):
        raise ManifestError(
            f"batch name {batch_name!r} must be a non-empty path segment "
            "(no spaces or separators)")
    out_dir = Path(out_dir)
    manifest_path = out_dir / MANIFEST_NAME
    if manifest_path.exists() and not force:
        raise ManifestError(
            f"{manifest_path} already exists — recordings are appended to "
            "an existing batch; pass force=True (CLI: --force) only if you "
            "really mean to discard and re-scaffold")

    doc = dict(template if template is not None
               else find_template_document(out_dir))
    doc["batch"] = batch_name
    doc["created"] = created or date.today().isoformat()

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / AUDIO_DIR).mkdir(exist_ok=True)
    manifest_path.write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    return manifest_path
