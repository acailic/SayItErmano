"""Real perf-backend adapters + worker entry (quality plan Q8).

The perf harness (``fluidvoice.evalharness.perf``) is model-free; this
module plugs the REAL backends into its ``measure_corpus`` seam, in the
daemon process shape:

- ``faster-whisper`` (the default backend): one in-process model load
  (CPU int8, tiny) held for every take — exactly how the daemon holds
  it. Peak RSS is the worker process itself.
- ``whisper.cpp``: the app's shape for this backend is one
  ``whisper-cli`` subprocess PER transcription, so each take is a full
  subprocess and the peak RSS is the largest worker child. Model load
  is not a one-time cost here; the adapter parses whisper.cpp's own
  ``load time`` line per take and reports the mean.

Backend preference matches the app's auto-selection (faster-whisper
first), with the whisper.cpp tiny model that ships baked into the
reference runner. Selection is explicit in every evidence file — a
baseline recorded on one backend never silently gates another.

Nothing here is imported by the offline unit tier's tests: they use
fake backends against the harness seam. The timing PARSER is pure and
unit-tested (``tests/test_evalharness_perf.py``) since it feeds the
recorded ``model_load_s``.
"""
from __future__ import annotations

import re
import resource
import shutil
import subprocess
from pathlib import Path
from statistics import fmean
from typing import Any, Sequence

from .manifest import Case
from .perf import measure_corpus

# whisper.cpp binary names, in the same order the app's backend probes
# them (fluidvoice/backends/__init__._whispercpp_binary)
_WHISPERCPP_BINARIES = ("whisper-cli", "whisper-cpp", "whisper.cpp",
                        "whisper-main")

# where a distro (or this runner) bakes the tiny ggml model
_SYSTEM_WHISPERCPP_MODELS = (
    Path("/usr/local/share/whisper"),
    Path("/usr/share/whisper"),
)
_TINY_GGML = "ggml-tiny.bin"

_LOAD_TIME_RE = re.compile(
    r"whisper_print_timings:\s+load time\s*=\s*([0-9.]+)\s*ms")


def parse_whispercpp_load_ms(text: str) -> float | None:
    """whisper.cpp's own load-time line, in milliseconds (or None)."""
    m = _LOAD_TIME_RE.search(text)
    return float(m.group(1)) if m else None


def _whispercpp_binary() -> str | None:
    for name in _WHISPERCPP_BINARIES:
        found = shutil.which(name)
        if found:
            return found
    return None


def _whispercpp_tiny_model() -> Path | None:
    from .. import model_catalog
    managed = model_catalog.gguf_path(_TINY_GGML)
    if managed.is_file():
        return managed
    for d in _SYSTEM_WHISPERCPP_MODELS:
        cand = d / _TINY_GGML
        if cand.is_file():
            return cand
    return None


def _linux_rss_mb(who: int) -> float:
    """ru_maxrss (KiB on Linux) of RUSAGE_SELF/CHILDREN, as MiB."""
    return resource.getrusage(who).ru_maxrss / 1024.0


class WhisperCppPerfBackend:
    """Perf shape of the app's whisper.cpp backend: one subprocess per
    take, model reloaded by each — the load-time mean is the honest
    ``model_load_s`` and the max child RSS is the worker footprint."""

    name = "whisper.cpp"

    def __init__(self, *, binary: str | None = None,
                 model: Path | None = None):
        self.binary = binary or _whispercpp_binary()
        if not self.binary:
            raise RuntimeError(
                "whisper.cpp binary not found (looked for: "
                + ", ".join(_WHISPERCPP_BINARIES) + ")")
        self.model = model or _whispercpp_tiny_model()
        if not self.model:
            raise RuntimeError(
                f"no whisper.cpp tiny model ({_TINY_GGML}) — checked the "
                "managed model cache and "
                + ", ".join(str(d) for d in _SYSTEM_WHISPERCPP_MODELS))
        self.model = Path(self.model)
        self.model_load_s: float | None = None
        self._load_ms: list[float] = []
        self._takes = 0

    def load(self) -> None:
        """No persistent model: each take's subprocess reloads it."""

    def transcribe(self, wav: Path, case: Case) -> None:
        lang = case.language or "auto"
        args = [self.binary, "-m", str(self.model), "-f", str(wav),
                "-nt"]
        if lang != "auto":
            args += ["-l", lang]
        proc = subprocess.run(args, capture_output=True, text=True,
                              timeout=300)
        if proc.returncode != 0:
            raise RuntimeError(
                f"whisper-cli failed ({proc.returncode}): "
                f"{(proc.stderr or proc.stdout).strip()[:300]}")
        ms = parse_whispercpp_load_ms(proc.stderr + "\n" + proc.stdout)
        if ms is not None:
            self._load_ms.append(ms)
            self.model_load_s = fmean(self._load_ms) / 1000.0
        self._takes += 1

    def peak_rss_mb(self) -> float:
        """Largest whisper-cli child (the process holding the model)."""
        return _linux_rss_mb(resource.RUSAGE_CHILDREN)

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "model": "tiny",
            "model_path": str(self.model),
            "binary": self.binary,
            "compute": "cpu",
            "language": "per-case (corpus languages)",
            "process_shape": "per-take whisper-cli subprocess "
                             "(the app's whisper.cpp shape); peak RSS = "
                             "largest child, model_load_s = mean parsed "
                             "whisper.cpp load time",
            "takes": self._takes,
        }


class FasterWhisperPerfBackend:
    """Perf shape of the default backend: one CPU-int8 in-process tiny
    model, loaded once and held for every take — the daemon's shape."""

    name = "faster-whisper"

    def __init__(self, *, model_name: str = "tiny", device: str = "cpu",
                 compute_type: str = "int8"):
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.model_load_s: float | None = None
        self._model: Any = None

    def load(self) -> None:
        from faster_whisper import WhisperModel  # deferred: optional dep

        from .. import paths
        self._model = WhisperModel(
            self.model_name, device=self.device,
            compute_type=self.compute_type,
            download_root=str(paths.models_dir() / "faster-whisper"))

    def transcribe(self, wav: Path, case: Case) -> None:
        segments, _info = self._model.transcribe(
            str(wav), language=case.language or None,
            without_timestamps=True)
        for _ in segments:  # generators decode lazily — drain to done
            pass

    def peak_rss_mb(self) -> float:
        """The worker process itself holds the model."""
        return _linux_rss_mb(resource.RUSAGE_SELF)

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "model": self.model_name,
            "compute": f"{self.device}/{self.compute_type}",
            "process_shape": "in-process resident model (the daemon "
                             "shape); peak RSS = worker process",
        }


def pick_backend(name: str | None = None) -> Any:
    """Resolve a perf backend by name (or auto: app preference order).

    Raises RuntimeError naming every option when nothing is usable.
    """
    errors: list[str] = []
    order = [name] if name else ["faster-whisper", "whisper.cpp"]
    for candidate in order:
        if candidate == "faster-whisper":
            try:
                import faster_whisper  # noqa: F401
            except ImportError as e:
                errors.append(f"faster-whisper: {e}")
                continue
            return FasterWhisperPerfBackend()
        if candidate == "whisper.cpp":
            try:
                return WhisperCppPerfBackend()
            except RuntimeError as e:
                errors.append(f"whisper.cpp: {e}")
                continue
        errors.append(f"unknown backend {candidate!r}")
    raise RuntimeError(
        "no usable perf backend:\n  " + "\n  ".join(errors)
        + "\n(record one on a machine with faster-whisper installed, or "
        "the baked whisper.cpp tiny model)")


def _recorded_path(p: Path) -> str:
    """Repo-relative when possible: evidence files stay portable."""
    try:
        return str(p.resolve().relative_to(Path(__file__).resolve()
                                           .parents[2]))
    except ValueError:
        return str(p)


def run_perf_worker(manifests: Sequence[Path],
                    backend_name: str | None = None) -> dict[str, Any]:
    """Load the corpus, measure it, return the fresh baseline dict.

    This runs IN the worker subprocess (see scripts/perf_baseline.py
    ``--worker``): one process loads the model, runs every take and
    reports its own peak RSS — the daemon process shape.
    """
    from .manifest import load_manifests

    cases = load_manifests(list(manifests))
    backend = pick_backend(backend_name)
    return measure_corpus(
        cases, backend,
        manifests=[Path(_recorded_path(p)) for p in manifests])
