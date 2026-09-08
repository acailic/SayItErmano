"""Streaming model downloads for whisper.cpp GGUF + Parakeet ONNX
(stdlib only)."""
from __future__ import annotations

import errno
import hashlib
import os
import shutil
import tarfile
import urllib.request
from pathlib import Path
from typing import Callable

from . import (  # noqa: F401 - paths re-exported for callers
    __version__,
    model_catalog,
    paths,
)

Progress = Callable[[int, "int | None"], None]
CHUNK_BYTES = 64 * 1024
CONNECT_TIMEOUT_S = 30  # per-read socket timeout: fails stalled transfers


def download_gguf(name: str, progress: Progress | None = None) -> Path:
    """Fetch a GGUF_CATALOG model into models_dir()/whisper.cpp/.
    Returns the final path; no-op when the file already exists."""
    if name not in model_catalog.GGUF_CATALOG:
        raise ValueError(
            f"unknown gguf model '{name}' "
            f"(choose from {sorted(model_catalog.GGUF_CATALOG)})")
    dest = model_catalog.gguf_path(name)
    if dest.exists():
        return dest
    return download_file(model_catalog.GGUF_CATALOG[name]["url"], dest,
                         progress=progress)


def download_file(url: str, dest: Path, progress: Progress | None = None) -> Path:
    """Stream url -> dest via a sibling .part renamed on success.
    Any failure deletes the .part and re-raises; the final file is never
    left half-written."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(
        url, headers={"User-Agent": f"SayItErmano/{__version__}"})
    try:
        with urllib.request.urlopen(req, timeout=CONNECT_TIMEOUT_S) as resp:
            raw = resp.headers.get("Content-Length")
            total = int(raw) if raw and raw.isdigit() else None
            done = 0
            if progress:
                progress(0, total)
            with open(tmp, "wb") as fh:
                while chunk := resp.read(CHUNK_BYTES):
                    fh.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
        if total is not None and done != total:
            raise OSError(f"truncated download: {done}/{total} bytes")
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return dest


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _clean_stages(parent: Path, prefix: str) -> None:
    """Remove staging dirs left behind by crashed runs."""
    for stale in parent.glob(f"{prefix}.tmp-*"):
        if stale.is_dir():
            shutil.rmtree(stale, ignore_errors=True)


def download_parakeet(name: str, progress: Progress | None = None) -> Path:
    """Fetch a PARAKEET_CATALOG model into models_dir()/parakeet/<name>/.
    One checksummed tarball, extracted file-by-file (streamed, never
    extractall) into a staging dir that is renamed into place atomically:
    any failure or abort leaves NO model dir."""
    if name not in model_catalog.PARAKEET_CATALOG:
        raise ValueError(
            f"unknown parakeet model '{name}' "
            f"(choose from {sorted(model_catalog.PARAKEET_CATALOG)})")
    if model_catalog.parakeet_downloaded(name):
        return model_catalog.parakeet_model_dir(name)
    info = model_catalog.PARAKEET_CATALOG[name]
    pdir = model_catalog.parakeet_dir()
    pdir.mkdir(parents=True, exist_ok=True)
    _clean_stages(pdir, f".{name}")
    stage = pdir / f".{name}.tmp-{os.getpid()}"
    tarball = pdir / f".{name}.tar.bz2"
    try:
        download_file(info["url"], tarball, progress=progress)
        if sha256_file(tarball) != info["tarball_sha256"]:
            raise OSError(f"checksum mismatch: {name} tarball "
                          "— deleted, retry the download")
        stage.mkdir()
        wanted = set(info["files"])
        found: set[str] = set()
        with tarfile.open(tarball, "r|bz2") as tf:
            for member in tf:
                tail = member.name.rsplit("/", 1)[-1]
                if tail not in wanted or not member.isfile():
                    continue
                src = tf.extractfile(member)
                if src is None:
                    continue
                with open(stage / tail, "wb") as out:
                    shutil.copyfileobj(src, out)
                found.add(tail)
        missing = sorted(wanted - found)
        if missing:
            raise OSError(f"{name} tarball is missing: {', '.join(missing)}")
        for fname, want in info["files"].items():
            if sha256_file(stage / fname) != want:
                raise OSError(f"checksum mismatch: {name}/{fname} "
                              "— deleted, retry the download")
        try:
            os.rename(stage, model_catalog.parakeet_model_dir(name))
        except OSError as e:  # a concurrent download won the rename
            if e.errno not in (errno.EEXIST, errno.ENOTEMPTY):
                raise
            shutil.rmtree(stage, ignore_errors=True)
            return model_catalog.parakeet_model_dir(name)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        tarball.unlink(missing_ok=True)
        raise
    tarball.unlink(missing_ok=True)
    return model_catalog.parakeet_model_dir(name)


def download_files(entries: list[dict], dest_dir: Path,
                   progress: Progress | None = None) -> Path:
    """Generic multi-file fetch: entries = [{name, url, sha256, size}].
    Aggregate progress across files (completed bytes + current-file bytes
    over the summed sizes; None when any size is unknown); per-file
    download_file .part discipline + sha256 verification; everything goes
    into a staging dir atomically renamed at the end."""
    sizes = [int(e.get("size") or 0) for e in entries]
    total: int | None = sum(sizes) if all(sizes) else None
    dest_dir.parent.mkdir(parents=True, exist_ok=True)
    _clean_stages(dest_dir.parent, f".{dest_dir.name}")
    stage = dest_dir.parent / f".{dest_dir.name}.tmp-{os.getpid()}"
    stage.mkdir()
    try:
        done = 0
        for entry, size in zip(entries, sizes):
            base = done

            def agg(b: int, _t: "int | None", _base: int = base) -> None:
                if progress:
                    progress(_base + b, total)

            download_file(entry["url"], stage / entry["name"], progress=agg)
            if entry.get("sha256") and \
                    sha256_file(stage / entry["name"]) != entry["sha256"]:
                raise OSError(f"checksum mismatch: {entry['name']} "
                              "— deleted, retry the download")
            done += size
        try:
            os.rename(stage, dest_dir)
        except OSError as e:  # a concurrent fetch won the rename
            if e.errno not in (errno.EEXIST, errno.ENOTEMPTY):
                raise
            shutil.rmtree(stage, ignore_errors=True)
            return dest_dir
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return dest_dir
