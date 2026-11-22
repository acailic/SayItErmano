"""Guided first-use setup: ``sayit-ermano setup`` (product-excellence
bet 1 — a complete, VERIFIED first-use path).

Headless (no GTK) interactive pass that reuses every existing seam
instead of duplicating them:

* hardware grounding — ``backends.cuda_available()`` + an nvidia-smi
  parse (name/VRAM) + ``os.cpu_count()`` + ``/proc/meminfo`` feed a PURE
  ``recommend_models()`` whose one-line "why" always cites its inputs;
* downloads — ``model_download.download_gguf`` / ``download_parakeet``
  with a progress callback that prints percent, size, rate and ETA
  (faster-whisper repos stream through huggingface_hub's own tqdm);
* insertion path — ``insertion.check_insertion_capability`` (exact
  missing-package line via ``install_hint``), per-desktop shortcut
  instructions and the evdev permission guidance doctor prints;
* verified test take — 2 s through the real ``Recorder``, transcribed
  by the real backend, then INSERTED via ``insertion.insert_text`` (or
  reported insertion-blocked with the reason);
* funnel — the same local ``FunnelCounters`` steps the GTK onboarding
  records, with ``source="cli-setup"``.

Exit codes: 0 on a full verified loop OR an explicit, actionable
blocker (missing tool + exact install line); 1 when the take was
inconclusive (silence, empty transcript, unknown errors).
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import backends, model_catalog, paths
from . import insertion as insertion_mod
from . import session as session_mod
from .audio_utils import duration_seconds, is_silent
from .config import save_config
from .gtkui.onboarding_funnel import FunnelCounters
from .model_download import download_gguf, download_parakeet, failure_hint
from .recorder import Recorder, RecorderError

STATUS_VERIFIED = "verified"
STATUS_BLOCKED = "blocked"   # explicit + actionable: the fix is printed
STATUS_FAILED = "failed"     # inconclusive: retry, nothing was verified

TAKE_SECONDS = 2.0


# ---------------------------------------------------------------------------
# Hardware profile (the grounding inputs for the model recommendation)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HardwareProfile:
    """What the machine actually has — every field is cited in the
    recommendation output (acceptance: the pick names its inputs)."""

    cuda: bool
    cpu_cores: int
    ram_mb: int | None       # /proc/meminfo MemTotal; None = unknown
    gpu_name: str | None     # nvidia-smi; None = not probed
    vram_mb: int | None      # nvidia-smi; None = unknown / no NVIDIA GPU

    def ram_text(self) -> str:
        if not self.ram_mb:
            return "RAM unknown"
        if self.ram_mb >= 1000:
            return f"{self.ram_mb / 1000:.0f} GB RAM"
        return f"{self.ram_mb} MB RAM"

    def vram_text(self) -> str:
        if not self.vram_mb:
            return "VRAM unknown"
        return f"{self.vram_mb} MB VRAM"

    def summary_lines(self) -> list[str]:
        if self.cuda:
            gpu = f"{self.gpu_name or 'CUDA GPU'} ({self.vram_text()}), CUDA available"
        else:
            gpu = "none detected (no CUDA)"
        return [
            f"  GPU: {gpu}",
            f"  CPU: {self.cpu_cores} cores · RAM: {self.ram_text()}",
        ]


def _meminfo_mb(path: str = "/proc/meminfo") -> int | None:
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def _nvidia_smi(run: Callable[..., "subprocess.CompletedProcess[str]"] | None
                = None) -> tuple[str | None, int | None]:
    """(gpu name, VRAM MB) from nvidia-smi, or (None, None). doctor
    prints this query raw; the setup flow parses it so the
    recommendation can cite real numbers."""
    run = run or subprocess.run
    try:
        out = run(["nvidia-smi", "--query-gpu=name,memory.total",
                   "--format=csv,noheader,nounits"],
                  capture_output=True, text=True, timeout=4).stdout
        name, _, vram = out.splitlines()[0].partition(",")
        vram_mb = int(vram.strip()) if vram.strip().isdigit() else None
        return name.strip() or None, vram_mb
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None, None


def probe_hardware(*, cuda_fn: Callable[[], bool] | None = None,
                   smi: Callable[..., "subprocess.CompletedProcess[str]"]
                   | None = None,
                   meminfo: Callable[[str], int | None] | None = None,
                   cpu_count: Callable[[], int | None] | None = None,
                   ) -> HardwareProfile:
    """All probes injectable; defaults touch the real system (nvidia-smi
    only when CUDA already resolved, so CPU machines stay fast)."""
    cuda = cuda_fn() if cuda_fn is not None else backends.cuda_available()
    gpu_name = vram_mb = None
    if cuda:
        gpu_name, vram_mb = _nvidia_smi(smi)
    return HardwareProfile(
        cuda=cuda,
        cpu_cores=(cpu_count() if cpu_count is not None
                   else os.cpu_count()) or 1,
        ram_mb=(meminfo("/proc/meminfo") if meminfo is not None
                else _meminfo_mb()),
        gpu_name=gpu_name, vram_mb=vram_mb)


# ---------------------------------------------------------------------------
# Model recommendation (pure: HardwareProfile + language -> ranked picks)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Recommendation:
    kind: str        # "faster-whisper" | "whisper.cpp" | "parakeet"
    name: str
    size: str        # catalog display size
    why: str         # one line, citing the inputs it derived from


def recommend_models(hw: HardwareProfile, language: str) -> list[Recommendation]:
    """Ranked candidates for THIS machine + language. First entry is the
    default. Mirrors backends.resolve_model_name's auto tiering
    (small on CUDA, base on CPU) so setup never disagrees with the
    daemon's own resolution, then adds grounded upgrades."""
    lang = (language or "").strip().lower()
    en = lang in ("", "auto", "en")
    lang_note = "" if en else f", covers your language '{lang}' (99 total)"
    out: list[Recommendation] = []
    if hw.cuda:
        gpu = hw.gpu_name or "CUDA GPU"
        out.append(Recommendation(
            "faster-whisper", "small", "~484 MB",
            f"CUDA is available ({gpu}, {hw.vram_text()}) — small "
            f"(~484 MB) is the balanced GPU default: comfortable "
            f"headroom{lang_note}"))
        if hw.vram_mb is None or hw.vram_mb >= 8192:
            out.append(Recommendation(
                "faster-whisper", "large-v3-turbo", "~1.6 GB",
                f"{hw.vram_text()} fits large-v3-turbo (~1.6 GB) with "
                "room for the CUDA runtime — near-best accuracy, faster "
                f"than large{lang_note}"))
        if hw.vram_mb is None or hw.vram_mb >= 6144:
            out.append(Recommendation(
                "faster-whisper", "medium", "~1.5 GB",
                f"{hw.vram_text()} fits medium (~1.5 GB) — a step up in "
                f"accuracy over small on GPU{lang_note}"))
    else:
        out.append(Recommendation(
            "faster-whisper", "base", "~145 MB",
            f"no CUDA GPU detected (CPU: {hw.cpu_cores} cores, "
            f"{hw.ram_text()}) — base (~145 MB) keeps transcription fast "
            f"on CPU{lang_note}"))
        if hw.cpu_cores >= 8 and (hw.ram_mb or 0) >= 7800:
            out.append(Recommendation(
                "faster-whisper", "small", "~484 MB",
                f"your {hw.cpu_cores} CPU cores and {hw.ram_text()} keep "
                "small (~484 MB) usable on CPU — better accuracy than "
                f"base{lang_note}"))
    if en:
        out.append(Recommendation(
            "parakeet", "parakeet-tdt-0.6b-v2", "~630 MB",
            f"English-only alternative: Parakeet TDT 0.6B v2 (~630 MB) "
            f"runs on CPU (onnxruntime int8) with {hw.cpu_cores} cores — "
            "built-in punctuation + true casing"))
    out.append(Recommendation(
        "faster-whisper", "tiny", "~75 MB",
        f"weakest machines only (CPU: {hw.cpu_cores} cores, "
        f"{hw.ram_text()}): tiny (~75 MB) — fastest, lowest accuracy"))
    return out


def _model_downloaded(kind: str, name: str) -> bool:
    if kind == "whisper.cpp":
        return model_catalog.gguf_downloaded(name)
    if kind == "parakeet":
        return model_catalog.parakeet_downloaded(name)
    return model_catalog.model_downloaded(name)


# ---------------------------------------------------------------------------
# Download progress: percent + size + rate + ETA (the callback seam of
# model_download.download_file; DownloadProgress itself has no ETA yet)
# ---------------------------------------------------------------------------

def _eta_text(remaining_bytes: float, rate_bps: float) -> str:
    if rate_bps <= 0:
        return "unknown"
    secs = remaining_bytes / rate_bps
    if secs >= 90:
        return f"{secs / 60:.0f} min"
    return f"{max(1, int(secs))} s"


class ProgressLine:
    """``model_download`` progress callback printing at most one line per
    10% (or per 50 MB when the total is unknown):
    "  37% of 466 MB · 1.2 MB/s · ETA 1 min"."""

    def __init__(self, sink: Callable[[str], None],
                 clock: Callable[[], float] = time.monotonic):
        self._sink = sink
        self._clock = clock
        self._start: float | None = None
        self._last_unit = -10**9

    def __call__(self, done: int, total: int | None) -> None:
        now = self._clock()
        if self._start is None:
            self._start = now
        if total:
            unit, unit_label, step = int(done * 100 / total), "%", 10
        else:
            unit, unit_label, step = done // 50_000_000, " MB", 1
        if unit - self._last_unit < step and not (total and unit >= 99):
            return
        self._last_unit = unit
        elapsed = max(1e-6, now - self._start)
        rate = done / elapsed
        if total:
            size = model_catalog.human_bytes(total)
            eta = _eta_text(total - done, rate)
        else:
            size = f"{model_catalog.human_bytes(done)} so far"
            eta = "unknown"
        self._sink(f"  {min(unit, 99):>3}{unit_label} of {size} · "
                   f"{model_catalog.human_bytes(int(rate))}/s · ETA {eta}")


# ---------------------------------------------------------------------------
# The flow
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SetupResult:
    status: str          # STATUS_* constant
    reason: str = ""


class SetupFlow:
    """``sayit-ermano setup`` — every external effect is an injectable
    seam (recorder, backend, download, insertion, input/print, clock) so
    the unit tier exercises every state offline, display-free."""

    def __init__(self, cfg: dict, *, language: str | None = None,
                 model: str | None = None, assume_yes: bool = False,
                 input_fn: Callable[[str], str] = input,
                 print_fn: Callable[[str], None] = print,
                 sleep_fn: Callable[[float], None] = time.sleep,
                 hardware: HardwareProfile | None = None,
                 recorder_factory: Callable[[], Recorder] | None = None,
                 load_backend_fn: Callable[[dict], object] | None = None,
                 download_fn: Callable[[str, str, Callable], Path] | None = None,
                 insert_fn: Callable[[str, dict], str] | None = None,
                 funnel: FunnelCounters | None = None):
        self.cfg = cfg
        self.language_arg = language
        self.model_arg = model
        self.assume_yes = assume_yes
        self._input = input_fn
        self._print = print_fn
        self._sleep = sleep_fn
        self._hardware = hardware
        self._recorder_factory = recorder_factory or self._make_recorder
        self._load_backend_fn = load_backend_fn or backends.load_backend
        self._download_fn = download_fn or self._download_model
        self._insert_fn = insert_fn or insertion_mod.insert_text
        self._funnel = funnel if funnel is not None else FunnelCounters()
        self._funnel.record("onboarding_started", source="cli-setup")

    # -- defaults for the seams ---------------------------------------------

    def _make_recorder(self) -> Recorder:
        rcfg = self.cfg.get("recording", {}) or {}
        return Recorder(command=str(rcfg.get("command", "auto")),
                        device=str(rcfg.get("device", "")),
                        sample_rate=int(rcfg.get("sample_rate", 16000)))

    @staticmethod
    def _download_model(kind: str, name: str,
                        progress: Callable | None) -> Path:
        if kind == "whisper.cpp":
            return download_gguf(name, progress=progress)
        if kind == "parakeet":
            return download_parakeet(name, progress=progress)
        raise ValueError(f"setup cannot pre-download {kind} models")

    # -- step 3: download ----------------------------------------------------

    def _download(self, kind: str, name: str) -> str | None:
        """Returns None on success, else the actionable blocker line."""
        if _model_downloaded(kind, name):
            self._print(f"  model {name}: already downloaded — skipping")
            return None
        self._print(f"  downloading {name} "
                    f"({self._size_of(kind, name)})…")
        try:
            if kind == "faster-whisper":
                # huggingface_hub's own tqdm shows size + rate + ETA while
                # WhisperModel fetches the repo into the shared cache
                cfg = dict(self.cfg)
                cfg["model"] = dict(self.cfg["model"], name=name)
                self._load_backend_fn(cfg).warmup()
            else:
                self._download_fn(kind, name, ProgressLine(self._print))
        except Exception as e:  # noqa: BLE001 — becomes the blocker line
            return f"model download failed: {e} — {failure_hint(e)}"
        self._funnel.record("model_downloaded", model=name,
                            source="cli-setup")
        return None

    def _size_of(self, kind: str, name: str) -> str:
        cat = {"faster-whisper": model_catalog.MODEL_CATALOG,
               "whisper.cpp": model_catalog.GGUF_CATALOG,
               "parakeet": model_catalog.PARAKEET_CATALOG}[kind]
        return cat.get(name, {}).get("size", "size unknown")

    # -- step 4: insertion path ---------------------------------------------

    def insertion_lines(self, report, info) -> list[str]:
        """Session/desktop insertion audit with the exact missing-package
        line and the permission guidance doctor prints."""
        lines = [f"  session: {info.type}"
                 + (f" (desktop: {info.desktop or 'unknown'})"
                    if info.is_wayland else "")]
        if info.is_wayland:
            caps = session_mod.capabilities(info, cfg=self.cfg)
            lines.append(f"  insertion path: {caps.get('insertion', '?')} "
                         f"(typing tool: {report.typing_tool or 'none'})")
            if report.missing_tools:
                lines.append(f"  missing packages: {report.install_hint}")
            lines.append("  desktop shortcut (Wayland has no global grabs):")
            lines.extend("    " + l for l in
                         session_mod.de_shortcut_instructions(
                             info.desktop_all, str(paths.toggle_script())))
            if (self.cfg.get("hotkey", {}) or {}).get("wayland_evdev"):
                lines.append("  evdev push-to-talk needs /dev/input access — "
                             "permission: sudo usermod -aG input $USER, "
                             "then re-login")
        else:
            lines.append(
                f"  typing tool: {report.typing_tool or 'xdotool MISSING'}"
                " · clipboard: "
                f"{'xclip OK' if 'xclip' not in report.missing_tools else 'xclip MISSING'}")
            if report.missing_tools:
                lines.append(f"  missing packages: {report.install_hint}")
        lines.append(f"  {report.detail}")
        return lines

    # -- step 5: the verified test take --------------------------------------

    def _test_take(self, report) -> SetupResult:
        try:
            recorder = self._recorder_factory()
        except RecorderError as e:
            return SetupResult(STATUS_BLOCKED, f"no microphone recorder — {e}")
        fd, tmp = tempfile.mkstemp(prefix="sayitermano-setup-", suffix=".wav")
        os.close(fd)
        wav = Path(tmp)
        try:
            self._print(f"  recording {TAKE_SECONDS:g} s — SPEAK NOW "
                        "(anything: 'testing one two three')…")
            try:
                recorder.start(wav)
            except RecorderError as e:
                return SetupResult(STATUS_BLOCKED,
                                   f"microphone failed to start — {e}")
            self._sleep(TAKE_SECONDS)
            recorder.stop()
            if not wav.exists() or wav.stat().st_size < 200:
                return SetupResult(
                    STATUS_FAILED,
                    "no audio captured — muted mic or wrong input source? "
                    "Check `pactl info` (Default Source), then re-run setup")
            if is_silent(wav):
                return SetupResult(
                    STATUS_FAILED,
                    "the take was digital silence — unmute the mic or run "
                    "`systemctl --user restart wireplumber`, then re-run "
                    "`sayit-ermano setup`")
            try:
                backend = self._load_backend_fn(self.cfg)
                result = backend.transcribe(wav, self._language or None)
            except Exception as e:  # noqa: BLE001 — surfaced as the reason
                return SetupResult(STATUS_BLOCKED,
                                   f"transcription failed — {e} "
                                   "(full report: sayit-ermano doctor)")
            text = (result.get("text", "") if isinstance(result, dict)
                    else getattr(result, "text", "")).strip()
            if not text:
                return SetupResult(
                    STATUS_FAILED,
                    f"the {duration_seconds(wav):.0f} s take transcribed as "
                    "EMPTY — no speech was detected. Re-run setup and speak "
                    "during the take (louder / closer to the mic)")
            self._funnel.record("tryout_ok", source="cli-setup",
                                text_chars=len(text))
            self._print(f"  heard: \"{text}\"")
            return self._insert_take(text, report)
        finally:
            wav.unlink(missing_ok=True)

    def _insert_take(self, text: str, report) -> SetupResult:
        if not report.ok:
            self._funnel.record("real_insertion_reported_failed",
                                source="cli-setup")
            return SetupResult(STATUS_BLOCKED,
                               "insertion-blocked: " + report.detail)
        if not self.assume_yes:
            self._ask("  press Enter, then click into any text field "
                      "(this terminal works) — insertion follows in 3 s…")
            self._sleep(3.0)
        try:
            strategy = self._insert_fn(text, self.cfg)
        except insertion_mod.InsertionFailure as e:
            self._funnel.record("real_insertion_reported_failed",
                                source="cli-setup")
            hint = getattr(e, "install_hint", "") or report.install_hint
            return SetupResult(
                STATUS_BLOCKED,
                f"insertion failed ({getattr(e, 'kind', 'unknown')}): {e}"
                + (f" — {hint}" if hint
                   else " — full report: sayit-ermano doctor"))
        except Exception as e:  # noqa: BLE001 — surfaced as the reason
            self._funnel.record("real_insertion_reported_failed",
                                source="cli-setup")
            return SetupResult(STATUS_BLOCKED, f"insertion failed — {e}")
        self._funnel.record("real_insertion_reported_ok",
                            source="cli-setup", strategy=strategy)
        self._print(f"  inserted via {strategy} — full loop verified")
        return SetupResult(STATUS_VERIFIED)

    # -- orchestration --------------------------------------------------------

    def run(self) -> int:
        self._print("SayItErmano first-use setup")
        self._print("")

        self._print("hardware:")
        hw = self._probe()
        for line in hw.summary_lines():
            self._print(line)

        self._print("")
        self._language = self._choose_language()
        recs = recommend_models(hw, self._language)
        pick = self._choose_model(recs)
        self._print(f"  chosen: {pick.name} ({pick.size})")

        # persist the choice (same discipline as engine_manager.warmup_model)
        self._apply_model(pick)
        if self._language and self._language != "auto":
            self.cfg.setdefault("general", {})["language"] = self._language
        try:
            save_config(self.cfg)
        except OSError as e:
            self._print(f"  note: could not save config ({e}) — the choice "
                        "applies to this run only")

        self._print("")
        self._print("model:")
        blocker = self._download(pick.kind, pick.name)
        if blocker is not None:
            return self._finish(SetupResult(STATUS_BLOCKED, blocker))

        self._print("")
        self._print("insertion:")
        info = session_mod.probe()
        report = insertion_mod.check_insertion_capability(self.cfg)
        for line in self.insertion_lines(report, info):
            self._print(line)

        self._print("")
        self._print("verification (a real 2 s take, transcribed and inserted):")
        return self._finish(self._test_take(report))

    def _finish(self, result: SetupResult) -> int:
        self._print("")
        if result.status == STATUS_VERIFIED:
            self._print("SETUP VERIFIED — record → transcribe → insert all "
                        "worked. Start dictating with your hotkey or "
                        "`sayit-ermano toggle`.")
        elif result.status == STATUS_BLOCKED:
            self._print(f"SETUP BLOCKED: {result.reason}")
            self._print("Fix the above and re-run `sayit-ermano setup`.")
        else:
            self._print(f"SETUP INCOMPLETE: {result.reason}")
        return 0 if result.status != STATUS_FAILED else 1

    def _probe(self) -> HardwareProfile:
        return self._hardware if self._hardware is not None \
            else probe_hardware()

    def _apply_model(self, pick: Recommendation) -> None:
        m = self.cfg.setdefault("model", {})
        m.pop("remote_url", None)  # a local pick must actually load locally
        if pick.kind == "whisper.cpp":
            m["backend"] = "whisper.cpp"
            m["whispercpp_model"] = str(model_catalog.gguf_path(pick.name))
        elif pick.kind == "parakeet":
            m["backend"] = "parakeet"
            m["name"] = pick.name
        else:
            m["backend"] = "auto"
            m["name"] = pick.name

    # -- interactive helpers ---------------------------------------------------

    def _ask(self, prompt: str) -> str:
        """input() that degrades to empty (default) on EOF/error — piped
        or closed stdin must never crash the flow."""
        try:
            return self._input(prompt)
        except (EOFError, OSError):
            return ""

    def _choose_language(self) -> str:
        current = str(self.cfg.get("general", {}).get("language") or "auto")
        default = "en" if current in ("", "auto") else current
        if self.language_arg:
            return self.language_arg.strip().lower() or default
        if self.assume_yes:
            return default
        raw = self._ask(f"  dictation language code (en, de, fr, …) "
                        f"[{default}]: ").strip().lower()
        return raw or default

    def _choose_model(self, recs: list[Recommendation]) -> Recommendation:
        if self.model_arg:
            pick = self._resolve_model_arg()
            self._print("  candidates for this machine:")
            for r in recs:
                marker = "*" if r.name == pick.name else " "
                self._print(f"  {marker} {r.name} ({r.size}) — {r.why}")
            return pick
        self._print("  recommended for this machine:")
        for i, r in enumerate(recs, 1):
            self._print(f"   {i}. {r.name} ({r.size}) — {r.why}")
        if self.assume_yes:
            return recs[0]
        raw = self._ask("  model [1]: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(recs):
            return recs[int(raw) - 1]
        return recs[0]

    def _resolve_model_arg(self) -> Recommendation:
        wanted = self.model_arg.strip().lower()
        wanted = backends.ALIASES.get(wanted, wanted)
        if wanted in model_catalog.PARAKEET_CATALOG:
            kind, cat = "parakeet", model_catalog.PARAKEET_CATALOG
        elif wanted in model_catalog.GGUF_CATALOG:
            kind, cat = "whisper.cpp", model_catalog.GGUF_CATALOG
        elif wanted in model_catalog.MODEL_CATALOG:
            kind, cat = "faster-whisper", model_catalog.MODEL_CATALOG
        else:
            raise ValueError(
                f"unknown model '{self.model_arg}' (choose from "
                f"{sorted(model_catalog.MODEL_CATALOG)})")
        return Recommendation(kind, wanted,
                              cat.get(wanted, {}).get("size", "?"),
                              "selected explicitly")
