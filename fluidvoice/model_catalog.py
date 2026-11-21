"""Model catalog + download cache probe (shared by the native GTK app,
CLI, and daemon)."""
from __future__ import annotations

import os
from pathlib import Path

from . import backends, paths

# name -> (display size, languages, note)
MODEL_CATALOG: dict[str, dict[str, str]] = {
    "tiny": {"size": "~75 MB", "langs": "99", "note": "fastest, lowest accuracy"},
    "base": {"size": "~145 MB", "langs": "99", "note": "fast; CPU default"},
    "small": {"size": "~484 MB", "langs": "99", "note": "balanced; GPU default"},
    "medium": {"size": "~1.5 GB", "langs": "99", "note": "accurate, heavier"},
    "large-v3": {"size": "~2.9 GB", "langs": "99", "note": "best accuracy"},
    "large-v3-turbo": {"size": "~1.6 GB", "langs": "99",
                       "note": "near-large quality, faster"},
}


# whisper.cpp ggml models (huggingface.co/ggerganov/whisper.cpp) —
# key = file name as stored under models_dir()/whisper.cpp/
GGUF_CATALOG: dict[str, dict[str, str]] = {
    "ggml-base.bin": {
        "size": "~142 MB", "langs": "99", "note": "fast; CPU-friendly",
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin"},
    "ggml-base.en.bin": {
        "size": "~142 MB", "langs": "en", "note": "fast; English only",
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin"},
    "ggml-small.bin": {
        "size": "~466 MB", "langs": "99", "note": "balanced",
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin"},
    "ggml-small.en.bin": {
        "size": "~466 MB", "langs": "en", "note": "balanced; English only",
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin"},
    "ggml-medium.bin": {
        "size": "~1.5 GB", "langs": "99", "note": "accurate, heavier",
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.bin"},
    "ggml-medium.en.bin": {
        "size": "~1.5 GB", "langs": "en", "note": "accurate; English only",
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.en.bin"},
    "ggml-large-v3.bin": {
        "size": "~2.9 GB", "langs": "99", "note": "best accuracy (no .en upstream)",
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin"},
}

GGUF_DIR_NAME = "whisper.cpp"


# Local AI-polish chat model (llama.cpp GGUF, #21): the "small GGUF chat
# model (qwen3-4b-class)" the model manager downloads alongside the
# whisper GGUF flow. One pinned entry; key = file name as stored under
# models_dir()/chat/. The unsloth mirror is used because Qwen's own repo
# is gated (401 without a token) — same weights, ungated download.
CHAT_GGUF_DIR_NAME = "chat"
CHAT_MODEL_ALIAS = "local-polish"  # llama-server --alias (the model name
CHAT_GGUF_CATALOG: dict[str, dict] = {
    "qwen3-4b-instruct-2507-q4_k_m.gguf": {
        "size": "~2.5 GB", "langs": "multilingual",
        "note": "Qwen3 4B Instruct (2507) Q4_K_M — local AI polish, "
                "works offline",
        "url": "https://huggingface.co/unsloth/"
               "Qwen3-4B-Instruct-2507-GGUF/resolve/main/"
               "Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        "sha256": "3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c"
                  "6533bc4c67e597",
    },
}
CHAT_MODEL_DEFAULT = "qwen3-4b-instruct-2507-q4_k_m.gguf"


def chat_model_dir() -> Path:
    return paths.models_dir() / CHAT_GGUF_DIR_NAME


def chat_model_path(name: str) -> Path:
    return chat_model_dir() / name


def chat_model_downloaded(name: str) -> bool:
    """True when the catalog chat model's GGUF exists in the cache."""
    return name in CHAT_GGUF_CATALOG and chat_model_path(name).is_file()


# NVIDIA Parakeet TDT via the sherpa-onnx community exports (k2-fsa GitHub
# release, asr-models tag) — one tarball per model; `files` is the
# post-extract integrity + presence manifest (per-file sha256).
PARAKEET_TARBALL_BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models"
PARAKEET_CATALOG: dict[str, dict] = {
    "parakeet-tdt-0.6b-v2": {
        "size": "~630 MB", "langs": "en",
        "note": "NVIDIA Parakeet TDT 0.6B v2 — English, punctuation + true case",
        "url": PARAKEET_TARBALL_BASE
            + "/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8.tar.bz2",
        "tarball_sha256": "157c157bc51155e03e37d2466522a3a737dd9c72bb25f36eb18912964161e1ad",
        "files": {
            "encoder.int8.onnx": "a32b12d17bbbc309d0686fbbcc2987b5e9b8333a7da83fa6b089f0a2acd651ab",
            "decoder.int8.onnx": "b6bb64963457237b900e496ee9994b59294526439fbcc1fecf705b31a15c6b4e",
            "joiner.int8.onnx": "7946164367946e7f9f29a122407c3252b680dbae9a51343eb2488d057c3c43d2",
            "tokens.txt": "ec182b70dd42113aff6c5372c75cac58c952443eb22322f57bbd7f53977d497d",
        },
        "features": {"sample_rate": 16000, "n_mels": 128, "n_fft": 512,
                     "win": 400, "hop": 160, "fmin": 0.0, "fmax": 8000.0},
    },
    "parakeet-tdt-0.6b-v3": {
        "size": "~640 MB", "langs": "25 EU + ru/uk",
        "note": "NVIDIA Parakeet TDT 0.6B v3 — multilingual (25 European languages)",
        "url": PARAKEET_TARBALL_BASE
            + "/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2",
        "tarball_sha256": "5793d0fd397c5778d2cf2126994d58e9d56b1be7c04d13c7a15bb1b4eafb16bf",
        "files": {
            "encoder.int8.onnx": "acfc2b4456377e15d04f0243af540b7fe7c992f8d898d751cf134c3a55fd2247",
            "decoder.int8.onnx": "179e50c43d1a9de79c8a24149a2f9bac6eb5981823f2a2ed88d655b24248db4e",
            "joiner.int8.onnx": "3164c13fc2821009440d20fcb5fdc78bff28b4db2f8d0f0b329101719c0948b3",
            "tokens.txt": "d58544679ea4bc6ac563d1f545eb7d474bd6cfa467f0a6e2c1dc1c7d37e3c35d",
        },
        "features": {"sample_rate": 16000, "n_mels": 128, "n_fft": 512,
                     "win": 400, "hop": 160, "fmin": 0.0, "fmax": 8000.0},
    },
}
PARAKEET_DIR_NAME = "parakeet"
# explicit-backend default; upstream defaults to v3 — divergence noted in STATUS.md
PARAKEET_DEFAULT_MODEL = "parakeet-tdt-0.6b-v2"


def gguf_dir() -> Path:
    return paths.models_dir() / GGUF_DIR_NAME


def gguf_path(name: str) -> Path:
    return gguf_dir() / name


def gguf_downloaded(name: str) -> bool:
    """True when the catalog model's file exists in the managed cache."""
    return name in GGUF_CATALOG and gguf_path(name).is_file()


def parakeet_dir() -> Path:
    return paths.models_dir() / PARAKEET_DIR_NAME


def parakeet_model_dir(name: str) -> Path:
    return parakeet_dir() / name


def parakeet_downloaded(name: str) -> bool:
    """True when every catalog-listed file exists in parakeet/<name>/."""
    if name not in PARAKEET_CATALOG:
        return False
    return all((parakeet_model_dir(name) / f).is_file()
               for f in PARAKEET_CATALOG[name]["files"])


def model_downloaded(name: str) -> bool:
    """Best-effort check of the faster-whisper download cache (ignores
    in-progress .incomplete blobs)."""
    repo = backends.FW_MODEL_REPOS.get(backends.ALIASES.get(name, name), "")
    if not repo:
        return False
    for root in (paths.models_dir() / "faster-whisper",
                 paths.cache_dir().parent / "huggingface" / "hub"):
        candidate = root / ("models--" + repo.replace("/", "--"))
        if not candidate.exists():
            continue
        for blob in candidate.rglob("*"):
            if blob.is_file() and ".incomplete" not in blob.name:
                return True
    return False


# -- cached-model enumeration + deletion targets (Settings → Models pruning) --

def _dir_size(p: Path) -> int:
    """Sum of file sizes under a directory (symlinks not followed)."""
    total = 0
    for root, _dirs, files in os.walk(p, followlinks=False):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass  # a vanished temp file must not break the listing
    return total


def human_bytes(n: int) -> str:
    """Human-readable size for cache display (MB-style, 1e6 base)."""
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f} GB"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.0f} MB"
    return f"{n / 1_000:.0f} KB"


def _fw_repo_dir(repo: str) -> Path:
    return paths.models_dir() / "faster-whisper" / (
        "models--" + repo.replace("/", "--"))


def cached_models() -> list[dict]:
    """[{kind, name, path, bytes}] for every model cached under
    paths.models_dir() ONLY - the legacy huggingface/hub fallback stays
    unmanaged (see doctor)."""
    out: list[dict] = []
    fw_dir = paths.models_dir() / "faster-whisper"
    if fw_dir.is_dir():
        repos = {_fw_repo_dir(repo): name
                 for name, repo in backends.FW_MODEL_REPOS.items()}
        for d in sorted(fw_dir.iterdir()):
            if not d.is_dir() or not d.name.startswith("models--"):
                continue
            name = repos.get(d, d.name.removeprefix("models--").replace("--", "/"))
            out.append({"kind": "faster-whisper", "name": name,
                        "path": d, "bytes": _dir_size(d)})
    if gguf_dir().is_dir():
        for f in sorted(gguf_dir().iterdir()):
            if (f.is_file() and f.name.startswith("ggml-")
                    and f.suffix in (".bin", ".gguf")
                    and not f.name.endswith(".part")):
                out.append({"kind": "whisper.cpp", "name": f.name,
                            "path": f, "bytes": f.stat().st_size})
    if parakeet_dir().is_dir():
        for d in sorted(parakeet_dir().iterdir()):
            if d.is_dir() and not d.name.startswith("."):
                # dot-prefixed = staging/tarball leftovers, never a model
                out.append({"kind": "parakeet", "name": d.name,
                            "path": d, "bytes": _dir_size(d)})
    if chat_model_dir().is_dir():
        for f in sorted(chat_model_dir().iterdir()):
            if (f.is_file() and f.name in CHAT_GGUF_CATALOG
                    and not f.name.endswith(".part")):
                out.append({"kind": "chat", "name": f.name,
                            "path": f, "bytes": f.stat().st_size})
    return out


def cache_entry_path(kind: str, name: str) -> Path:
    """Resolve (kind, name) to its managed-cache path (the daemon deletes
    through this so a client path is never trusted). ValueError on an
    unknown kind."""
    if kind == "whisper.cpp":
        return gguf_dir() / name
    if kind == "parakeet":
        return parakeet_dir() / name
    if kind == "chat":
        if name not in CHAT_GGUF_CATALOG:
            raise ValueError(f"unknown chat model {name!r} "
                             f"(choose from {sorted(CHAT_GGUF_CATALOG)})")
        return chat_model_path(name)
    if kind == "faster-whisper":
        repo = backends.FW_MODEL_REPOS.get(name, name)
        return _fw_repo_dir(repo)
    raise ValueError(f"unknown model kind {kind!r} "
                     f"(faster-whisper, whisper.cpp, parakeet or chat)")
