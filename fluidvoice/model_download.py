"""Streaming model downloads for whisper.cpp GGUF + Parakeet ONNX
(stdlib only).

Progress surfaces (first-use funnel F-02): downloads are observable
while they run - `observe_downloads()` scans the shared models cache so
EVEN ANOTHER PROCESS can render progress (the daemon downloads a
faster-whisper model through huggingface_hub's `.incomplete` blobs; the
onboarding window and the overlay pill watch the same directory),
`active_downloads()` reports in-process downloads with exact totals, and
`request_cancel()` aborts a running `download_file` cleanly (the `.part`
is removed - never a half-written state).
"""
from __future__ import annotations

import errno
import hashlib
import os
import re
import shutil
import tarfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
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


class DownloadCancelled(Exception):
    """A download aborted via request_cancel(). The `.part` is gone -
    retrying later starts clean (no half-written state)."""


@dataclass(frozen=True)
class DownloadProgress:
    """One observable in-flight download (registry or cache scan)."""

    label: str            # model identity: "base", "ggml-base.bin", ...
    kind: str             # "faster-whisper" | "whisper.cpp" | "parakeet" | "file"
    done_bytes: int
    total_bytes: int | None  # None = indeterminate (show MB + elapsed)
    elapsed_s: float

    @property
    def percent(self) -> int | None:
        if not self.total_bytes:
            return None
        return min(99, int(self.done_bytes * 100 / self.total_bytes))

    def describe(self) -> str:
        """Human line for UI surfaces: percent when known, MB + elapsed
        otherwise ("downloading base… 37% of ~145 MB, 12 s")."""
        size = model_catalog.human_bytes(self.done_bytes)
        pct = f"{self.percent}% of " if self.percent is not None else ""
        total = (model_catalog.human_bytes(self.total_bytes)
                 if self.total_bytes else "")
        return (f"downloading {self.label}… {pct}{total or size}"
                f" ({int(self.elapsed_s)} s)")


# in-process registry: str(dest) -> state (exact totals via the
# progress callback; cross-process observers fall back to estimates)
_ACTIVE_LOCK = threading.Lock()
_ACTIVE: dict[str, dict] = {}
_CANCELS: dict[str, threading.Event] = {}


def active_downloads() -> list[DownloadProgress]:
    """Snapshot of downloads running in THIS process (exact totals)."""
    now = time.monotonic()
    with _ACTIVE_LOCK:
        return [DownloadProgress(
            label=st["label"], kind=st["kind"], done_bytes=st["done"],
            total_bytes=st["total"],
            elapsed_s=now - st["started"])
            for st in _ACTIVE.values()]


def request_cancel(dest: Path | str) -> bool:
    """Ask the in-process download writing `dest` to abort. True when a
    download was found (it raises DownloadCancelled at the next chunk)."""
    with _ACTIVE_LOCK:
        ev = _CANCELS.get(str(dest))
    if ev is not None:
        ev.set()
    return ev is not None


def _register(dest: Path, label: str, kind: str) -> threading.Event:
    ev = threading.Event()
    with _ACTIVE_LOCK:
        _ACTIVE[str(dest)] = {"label": label, "kind": kind, "done": 0,
                              "total": None,
                              "started": time.monotonic()}
        _CANCELS[str(dest)] = ev
    return ev


def _update(dest: Path, done: int, total: int | None) -> None:
    with _ACTIVE_LOCK:
        st = _ACTIVE.get(str(dest))
        if st is not None:
            st["done"], st["total"] = done, total


def _unregister(dest: Path) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE.pop(str(dest), None)
        _CANCELS.pop(str(dest), None)


def download_gguf(name: str, progress: Progress | None = None,
                  cancel: threading.Event | None = None) -> Path:
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
                         progress=progress, cancel=cancel, label=name,
                         kind="whisper.cpp")


def download_file(url: str, dest: Path, progress: Progress | None = None,
                  cancel: threading.Event | None = None,
                  label: str | None = None,
                  kind: str = "file") -> Path:
    """Stream url -> dest via a sibling .part renamed on success.
    Any failure deletes the .part and re-raises; the final file is never
    left half-written. Registers in the in-process download registry
    (progress surfaces) and honors request_cancel() / the `cancel` event
    between chunks (DownloadCancelled; same clean .part removal)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    auto_ev = _register(dest, label or dest.name, kind)
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
                    if ((cancel is not None and cancel.is_set())
                            or auto_ev.is_set()):
                        raise DownloadCancelled(
                            f"download of {label or dest.name} cancelled")
                    fh.write(chunk)
                    done += len(chunk)
                    _update(dest, done, total)
                    if progress:
                        progress(done, total)
        if total is not None and done != total:
            raise OSError(f"truncated download: {done}/{total} bytes")
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        _unregister(dest)
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


def download_parakeet(name: str, progress: Progress | None = None,
                      cancel: threading.Event | None = None) -> Path:
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
        download_file(info["url"], tarball, progress=progress, cancel=cancel,
                      label=name, kind="parakeet")
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


# ---------------------------------------------------------------------------
# Download observation (F-02 progress surfaces). The daemon downloads the
# default faster-whisper model through huggingface_hub (blobs/*.incomplete
# under the models cache) while this module's own GGUF/Parakeet fetches
# stream into sibling .part files - both shapes are visible on disk, so a
# DIFFERENT process (onboarding window, overlay pill) can render progress
# without any daemon changes.
# ---------------------------------------------------------------------------

# a .part/.incomplete untouched for this long is a stalled/crashed run,
# not a download (real transfers write at least every CONNECT_TIMEOUT_S)
STALE_PART_S = 120.0

_SIZE_RE = re.compile(r"~?\s*([\d.]+)\s*(KB|MB|GB)", re.IGNORECASE)


def _approx_bytes(size_note: str | None) -> int | None:
    '''Parse a catalog size note ("~145 MB", "~1.5 GB") into bytes.'''
    if not size_note:
        return None
    m = _SIZE_RE.match(str(size_note))
    if not m:
        return None
    mult = {"kb": 1e3, "mb": 1e6, "gb": 1e9}[m.group(2).lower()]
    try:
        return int(float(m.group(1)) * mult)
    except ValueError:
        return None


def _fw_name_from_repo_dir(repo_dir_name: str) -> str:
    '''models--Systran--faster-whisper-base -> "base" (repo tail when
    the repo is not in FW_MODEL_REPOS).'''
    from . import backends
    repo = repo_dir_name.removeprefix("models--").replace("--", "/")
    for name, r in backends.FW_MODEL_REPOS.items():
        if r == repo:
            return name
    return repo.rsplit("/", 1)[-1] or repo


def _age_s(p: Path) -> float:
    try:
        return max(0.0, time.time() - p.stat().st_ctime)
    except OSError:
        return 0.0


def _fresh(p: Path) -> bool:
    """A part file still being written (or created moments ago)."""
    try:
        return time.time() - p.stat().st_mtime < STALE_PART_S
    except OSError:
        return False


def observe_downloads() -> list[DownloadProgress]:
    """Every in-flight model download visible in the shared cache:
    in-process registry entries (exact totals) plus on-disk .part /
    huggingface .incomplete blobs (estimates from the catalog sizes).
    Cross-process by design - the onboarding window watches the daemon's
    eager-warmup download through the filesystem."""
    out: dict[str, DownloadProgress] = {}
    for d in active_downloads():
        out[(d.kind, d.label)] = d

    def _estimated(kind: str, label: str, done: int, elapsed: float,
                   total: int | None) -> None:
        """Insert a scanned entry, or enrich an in-process registry entry
        whose Content-Length was unknown with the catalog estimate."""
        prev = out.get((kind, label))
        if prev is not None:
            if prev.total_bytes is None and total is not None:
                out[(kind, label)] = DownloadProgress(
                    label=label, kind=kind, done_bytes=prev.done_bytes,
                    total_bytes=total, elapsed_s=prev.elapsed_s)
            return
        out[(kind, label)] = DownloadProgress(
            label=label, kind=kind, done_bytes=done, total_bytes=total,
            elapsed_s=elapsed)
    root = paths.models_dir()
    gguf_names = set(model_catalog.GGUF_CATALOG)
    parakeet_names = set(model_catalog.PARAKEET_CATALOG)
    fw_dir = root / "faster-whisper"
    scans: list[tuple[str, Path]] = [
        ("whisper.cpp", root / model_catalog.GGUF_DIR_NAME),
        ("parakeet", root / model_catalog.PARAKEET_DIR_NAME),
        ("faster-whisper", fw_dir),
    ]
    for kind, base in scans:
        try:
            parts = list(base.rglob("*.part")) if base.is_dir() else []
        except OSError:
            parts = []
        for p in parts:
            if not p.is_file() or not _fresh(p):
                continue
            label = p.name[:-len(".part")]
            total = None
            if kind == "whisper.cpp" and label in gguf_names:
                total = _approx_bytes(
                    model_catalog.GGUF_CATALOG[label]["size"])
            elif kind == "parakeet":
                label = label.lstrip(".").removesuffix(".tar.bz2")
                if label in parakeet_names:
                    total = _approx_bytes(
                        model_catalog.PARAKEET_CATALOG[label]["size"])
            try:
                done = p.stat().st_size
            except OSError:
                continue
            _estimated(kind, label, done, _age_s(p), total)
    if fw_dir.is_dir():
        try:
            blobs = list(fw_dir.glob("models--*/blobs/*.incomplete"))
        except OSError:
            blobs = []
        for blob in blobs:
            if not blob.is_file() or not _fresh(blob):
                continue
            name = _fw_name_from_repo_dir(blob.parent.parent.name)
            total = _approx_bytes(
                model_catalog.MODEL_CATALOG.get(name, {}).get("size"))
            try:
                done = blob.stat().st_size
            except OSError:
                continue
            prev = out.get(("faster-whisper", name))
            if prev is None or prev.total_bytes is None:
                _estimated("faster-whisper", name, done, _age_s(blob), total)
    return sorted(out.values(), key=lambda d: (-d.done_bytes, d.label))


def model_readiness(cfg: dict) -> dict:
    """{kind, name, downloaded, progress} for the model the daemon would
    actually use - the onboarding engine row's honest state (F-02: the
    name alone says nothing; a model can be resolving-but-absent while a
    ~145 MB download runs invisibly)."""
    from . import backends
    m = (cfg.get("model", {}) or {}) if isinstance(cfg, dict) else {}
    if backends.remote_url_set(cfg):
        return {"kind": "remote", "name": str(m.get("remote_model") or ""),
                "downloaded": True, "progress": None}
    backend = str(m.get("backend", "auto"))
    if backend in ("whisper.cpp", "whispercpp"):
        raw = str(m.get("whispercpp_model", "") or "").strip()
        name = raw.rsplit("/", 1)[-1] if raw else ""
        if "/" in raw or raw.startswith("~"):
            downloaded = Path(raw).expanduser().is_file()  # explicit path
        else:
            downloaded = model_catalog.gguf_downloaded(name)
        progress = _progress_for("whisper.cpp", name)
        return {"kind": "whisper.cpp", "name": name or "none",
                "downloaded": downloaded, "progress": progress}
    if backend in ("parakeet", "parakeet-onnx"):
        name = str(m.get("name", "") or "").strip() \
            or model_catalog.PARAKEET_DEFAULT_MODEL
        return {"kind": "parakeet", "name": name,
                "downloaded": model_catalog.parakeet_downloaded(name),
                "progress": _progress_for("parakeet", name)}
    try:
        name = backends.resolve_model_name(str(m.get("name", "auto")))
    except ValueError:
        name = str(m.get("name", "auto"))
    return {"kind": "faster-whisper", "name": name,
            "downloaded": model_catalog.model_downloaded(name),
            "progress": _progress_for("faster-whisper", name)}


def _progress_for(kind: str, name: str) -> DownloadProgress | None:
    for d in observe_downloads():
        if d.kind == kind and d.label == name:
            return d
    return None


def failure_hint(exc: BaseException) -> str:
    """Actionable one-liner for a failed model download (onboarding
    surfaces it next to the raw error; `doctor` has the full report)."""
    if isinstance(exc, DownloadCancelled):
        return "cancelled - nothing was half-written; retry any time"
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (429, 503):
            return ("the model host is rate-limiting or busy - wait a few "
                    "minutes and retry")
        return f"the host answered HTTP {exc.code} - retry, or pick another model in Settings → Models"
    if isinstance(exc, (urllib.error.URLError, TimeoutError, OSError)):
        return ("network problem or stalled transfer - check the connection "
                "and retry (partial data was discarded, retry is safe)")
    return (f"{type(exc).__name__}: {exc} - retry the download; "
            "`sayit-ermano doctor` helps if it keeps failing")
