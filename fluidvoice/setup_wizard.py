"""Guided first-use setup: `sayit-ermano --setup` (excellence bet 1).

One interactive terminal pass that VERIFIES the first take end-to-end:

  1. hardware probe (GPU/VRAM/CPU/RAM via the existing probes) ->
     model recommendation grounded in the machine AND the chosen
     language, each candidate with a one-line WHY that cites its inputs;
  2. download through the existing model_download machinery with real
     progress (percent, size, ETA);
  3. insertion-path check (X11 tools vs Wayland portal/tools) with the
     exact missing-package line per desktop (the doctor's hint source);
  4. a VERIFIED test take: record 2 s, transcribe, insert into a scratch
     window - or report insertion-blocked with the reason.

Exit code: 0 only on a full verified loop or an explicit, actionable
blocker (the state names what to install/fix); 1 on anything unexpected.

The flow is built on injectable seams (out/ask/probe/download/record/
transcribe/insert) so the unit tier drives every state offline with
fakes - no network, no display, no model.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

Line = Callable[[str], None]
Ask = Callable[[str], str]

# VRAM thresholds (MiB) grounding the GPU candidate order: small needs
# ~2 GB of VRAM for comfortable GPU decode, large-v3 ~8 GB.
VRAM_LARGE_MIB = 8192
TEST_TAKE_SECONDS = 2.0


# ---------------------------------------------------------------------------
# Hardware probe (GPU/VRAM/CPU/RAM via the existing probes)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HardwareProfile:
    """What the model recommendation is grounded in. Every field is
    quoted back to the user in the WHY lines."""

    gpu: str = ""                 # NVIDIA query name, "" when absent
    vram_mib: int | None = None   # nvidia-smi memory.total, None when unknown
    cuda: bool = False            # backends.cuda_available()
    cpu_cores: int | None = None  # os.cpu_count()
    ram_gb: float | None = None   # /proc/meminfo MemTotal, None off-Linux

    def summary_lines(self) -> list[str]:
        if self.gpu:
            vram = (f", {self.vram_mib / 1024:.0f} GB VRAM"
                    if self.vram_mib else ", VRAM unknown")
            gpu = (f"  GPU: {self.gpu}{vram} - "
                   f"CUDA {'ready' if self.cuda else 'NOT usable'}")
        else:
            gpu = ("  GPU: none detected - CPU path"
                   + (f" (CUDA libs present but no device: {self.cuda})"
                      if self.cuda else ""))
        cpu = f"  CPU: {self.cpu_cores or '?'} cores"
        ram = (f", RAM {self.ram_gb:.0f} GB" if self.ram_gb is not None
               else ", RAM unknown")
        return [gpu, cpu + ram]


def _probe_gpu(runner=subprocess.run) -> tuple[str, int | None]:
    """(name, vram_mib) via nvidia-smi - the same query doctor prints."""
    if not shutil.which("nvidia-smi"):
        return "", None
    try:
        out = runner(["nvidia-smi", "--query-gpu=name,memory.total",
                      "--format=csv,noheader"],
                     capture_output=True, text=True, timeout=5).stdout
    except Exception:  # noqa: BLE001 - a probe must never raise
        return "", None
    first = out.splitlines()[0].strip() if out.strip() else ""
    if not first:
        return "", None
    name, _, vram = first.partition(",")
    vram_mib: int | None = None
    digits = "".join(c for c in vram if c.isdigit())
    if digits:
        vram_mib = int(digits)
    return name.strip(), vram_mib


def _probe_ram(reader: Callable[[str], str] | None = None) -> float | None:
    read = reader or _read_meminfo
    total_kb = read("MemTotal")
    return total_kb / 1024 / 1024 if total_kb is not None else None


def _read_meminfo(key: str) -> int | None:
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith(key + ":"):
                    digits = "".join(c for c in line if c.isdigit())
                    return int(digits) if digits else None
    except OSError:
        pass
    return None


def probe_hardware(runner=subprocess.run,
                   meminfo_reader: Callable[[str], str] | None = None,
                   cuda_fn: Callable[[], bool] | None = None,
                   cpu_fn: Callable[[], int | None] | None = None,
                   ) -> HardwareProfile:
    """The existing probes, gathered: backends.cuda_available(),
    nvidia-smi (doctor's query), os.cpu_count(), /proc/meminfo."""
    from . import backends
    if cuda_fn is None:
        cuda_fn = backends.cuda_available
    if cpu_fn is None:
        cpu_fn = os.cpu_count
    gpu, vram = _probe_gpu(runner)
    return HardwareProfile(gpu=gpu, vram_mib=vram, cuda=cuda_fn(),
                           cpu_cores=cpu_fn(), ram_gb=_probe_ram(meminfo_reader))


# ---------------------------------------------------------------------------
# Model recommendation (pure: hardware + language -> ranked candidates)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ModelCandidate:
    kind: str        # "whisper.cpp" | "parakeet" - both downloadable via
    name: str        # model_download with verified progress + checksums
    size: str        # catalog size note ("~142 MB")
    why: str         # one line, MUST cite its inputs

    @property
    def label(self) -> str:
        return f"{self.kind}:{self.name}"


def recommend_models(hw: HardwareProfile, language: str) -> list[ModelCandidate]:
    """Ranked candidates grounded in `hw` + `language`. Every WHY line
    names the hardware facts (GPU/VRAM/CUDA or CPU/RAM) and the language
    constraint that produced it. English-only models are excluded for
    non-English choices; parakeet v2 is English, v3 covers 25 EU
    languages + ru/uk."""
    lang = (language or "auto").strip().lower()
    english = lang in ("", "auto", "en")
    cands: list[ModelCandidate] = []
    if hw.cuda and hw.gpu:
        vram_txt = (f"{hw.vram_mib / 1024:.0f} GB VRAM"
                    if hw.vram_mib else "unknown VRAM")
        base_why = (f"CUDA ready on {hw.gpu} ({vram_txt}) - GPU decode "
                    f"keeps even long takes fast")
        if hw.vram_mib is None or hw.vram_mib >= VRAM_LARGE_MIB:
            cands.append(ModelCandidate(
                "whisper.cpp", "ggml-large-v3.bin", "~2.9 GB",
                f"CUDA ready on {hw.gpu} ({vram_txt}) - fits large-v3, "
                "best accuracy, GPU-accelerated"))
        cands.append(ModelCandidate(
            "whisper.cpp", "ggml-small.bin", "~466 MB",
            f"{base_why}; balanced accuracy"
            + ("" if english else
               f"; multilingual (99 languages) for '{lang}'")))
    if not hw.cuda:
        ram = f"{hw.ram_gb:.0f} GB RAM" if hw.ram_gb is not None else "RAM unknown"
        cores = hw.cpu_cores or "?"
        name = "ggml-base.en.bin" if english else "ggml-base.bin"
        size = "~142 MB"
        why = (f"no usable CUDA runtime ({hw.gpu or 'no GPU detected'}, "
               f"{cores} CPU cores, {ram}) - base decodes realtime on CPU")
        if english:
            why += "; English-only build is smaller per word of accuracy"
        else:
            why += f"; multilingual build for '{lang}'"
        cands.append(ModelCandidate("whisper.cpp", name, size, why))
    if english:
        cands.append(ModelCandidate(
            "parakeet", "parakeet-tdt-0.6b-v2", "~630 MB",
            "you chose English: Parakeet TDT v2 is the fastest English "
            "model and adds punctuation + true casing (ONNX, CPU-friendly)"))
    elif _parakeet_v3_covers(lang):
        cands.append(ModelCandidate(
            "parakeet", "parakeet-tdt-0.6b-v3", "~640 MB",
            f"'{lang}' is in Parakeet v3's 25 European languages - "
            "fast ONNX decode, CPU-friendly"))
    return cands


def _parakeet_v3_covers(lang: str) -> bool:
    v3 = ("de es fr it nl pl pt ru uk sl sr hr bs cs sk sv da fi no "
          "hu ro bg el tr").split()
    return lang in v3


# ---------------------------------------------------------------------------
# Download progress rendering (size + ETA from the existing callbacks)
# ---------------------------------------------------------------------------

class ProgressPrinter:
    """Formats model_download's (done, total) callbacks into single
    progress lines with percent, transferred/total size and ETA
    (from the observed rate); renders through `out`."""

    def __init__(self, out: Line, label: str,
                 clock: Callable[[], float] = time.monotonic):
        self.out = out
        self.label = label
        self.clock = clock
        self._t0: float | None = None
        self._last_line_at = float("-inf")  # the first line always renders

    def __call__(self, done: int, total: int | None) -> None:
        from . import model_catalog
        now = self.clock()
        if self._t0 is None:
            self._t0 = now
        if now - self._last_line_at < 0.25 and total and done < total:
            return  # throttle: the callback fires per 64 KB chunk
        self._last_line_at = now
        size = model_catalog.human_bytes(done)
        if not total:
            self.out(f"  {self.label}: {size} downloaded…")
            return
        pct = min(100, int(done * 100 / total))
        rate = done / max(now - self._t0, 1e-6)
        eta_s = (total - done) / rate if rate > 1 else None
        eta = (f"ETA {int(eta_s // 60)}:{int(eta_s % 60):02d}"
               if eta_s is not None else "ETA -")
        self.out(f"  {self.label}: {pct}% of "
                 f"{model_catalog.human_bytes(total)} "
                 f"({size}, {rate / 1e6:.1f} MB/s, {eta})")


# ---------------------------------------------------------------------------
# Result states
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SetupResult:
    state: str          # "verified" | "blocked" | "error"
    exit_code: int      # green on verified OR an actionable blocker only
    summary: str        # the actionable one-liner ("" when verified)
    blocker: str = ""   # the raw reason when blocked


# ---------------------------------------------------------------------------
# The wizard
# ---------------------------------------------------------------------------

@dataclass
class SetupDeps:
    """Every side-effecting seam the flow uses; the unit tier fakes all
    of them (offline, no display, no model). None fields resolve to the
    production function at wizard construction (module attribute lookup
    at that moment - monkeypatching wins for CLI-level tests)."""

    out: Line | None = None
    ask: Ask | None = None
    probe_hardware_fn: Callable[[], HardwareProfile] | None = None
    session_probe_fn: Callable | None = None      # session.probe
    capability_fn: Callable | None = None         # check_insertion_capability
    download_fn: Callable[[str, str, ProgressPrinter], Path] | None = None
    record_fn: Callable[[float, Line], Path] | None = None
    transcribe_fn: Callable[[Path], str] | None = None
    insert_fn: Callable[[str], None] | None = None
    sleep: Callable[[float], None] | None = None
    which: Callable[[str], str | None] | None = None

    def resolve(self) -> "SetupDeps":
        """Fill None fields from this module (import-time-lazy so the
        heavy imports stay out of collection and patches win)."""
        from . import insertion, session
        return SetupDeps(
            out=self.out or print,
            ask=self.ask or input,
            probe_hardware_fn=self.probe_hardware_fn or probe_hardware,
            session_probe_fn=self.session_probe_fn or session.probe,
            capability_fn=self.capability_fn
            or insertion.check_insertion_capability,
            download_fn=self.download_fn or _default_download,
            record_fn=self.record_fn or _default_record,
            transcribe_fn=self.transcribe_fn or _default_transcribe,
            insert_fn=self.insert_fn or _default_insert,
            sleep=self.sleep or time.sleep,
            which=self.which or shutil.which)


def _default_download(kind: str, name: str, progress: ProgressPrinter) -> Path:
    from . import model_download
    if kind == "whisper.cpp":
        return model_download.download_gguf(name, progress=progress)
    if kind == "parakeet":
        return model_download.download_parakeet(name, progress=progress)
    raise ValueError(f"setup cannot download kind {kind!r}")


def _default_record(seconds: float, out: Line) -> Path:
    """Record a fixed `seconds` take via the production Recorder."""
    import tempfile
    from .recorder import Recorder
    rec = Recorder()
    wav = Path(tempfile.mkdtemp(prefix="sayit-setup-")) / "test-take.wav"
    rec.start(wav)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        out(f"  recording… {remaining:.0f} s left (say something!)")
        time.sleep(min(1.0, max(remaining, 0.0)))
    rec.stop()
    return wav


def _default_transcribe(wav: Path) -> str:
    from . import backends
    from .config import load_config
    cfg = load_config()
    backend = backends.load_backend(cfg)
    result = backend.transcribe(wav, language=backends.effective_language(cfg))
    return result.to_plain_text()


def _default_insert(text: str) -> None:
    from .insertion import insert_typed
    insert_typed(text, delay_ms=12)


class SetupWizard:
    """The interactive first-run pass. Construct with fakes in tests; run
    through CLI (`sayit-ermano --setup`) in production."""

    def __init__(self, cfg: dict | None = None, deps: SetupDeps | None = None):
        from .config import DEFAULTS
        import copy
        self.cfg = copy.deepcopy(cfg) if cfg is not None else copy.deepcopy(DEFAULTS)
        self.deps = (deps or SetupDeps()).resolve()
        self._last_blocker: SetupResult | None = None

    # -- steps ---------------------------------------------------------------

    def run(self) -> SetupResult:
        d = self.deps
        d.out("SayItErmano guided setup")
        d.out("This pass verifies a complete dictation loop before it "
              "declares you set up.\n")

        hw = d.probe_hardware_fn()
        d.out("Detected hardware:")
        for line in hw.summary_lines():
            d.out(line)

        language = self._choose_language()
        d.out("")

        cands = recommend_models(hw, language)
        chosen = self._choose_model(cands)
        if chosen is None:
            return SetupResult("error", 1, "no model chosen")
        if not self._ensure_model(chosen):
            return self._last_blocker or SetupResult("error", 1, "download failed")
        if not self._apply_config(chosen, language):
            return SetupResult("error", 1, "could not write the config file")
        report = self._insertion_report()
        if report is not None:
            return report
        return self._verified_take()

    # -- language ------------------------------------------------------------

    def _choose_language(self) -> str:
        d = self.deps
        current = str(self.cfg.get("general", {}).get("language") or "auto")
        answer = d.ask(f"\nDictation language (ISO code like en/de/es, "
                       f"or 'auto'; Enter = {current}): ").strip().lower()
        if not answer:
            answer = current
        self.cfg.setdefault("general", {})["language"] = answer
        d.out(f"  language: {answer}")
        return answer

    # -- model ---------------------------------------------------------------

    def _choose_model(self, cands: list[ModelCandidate]) -> ModelCandidate | None:
        d = self.deps
        d.out("Recommended models for this machine (why each fits):")
        for i, c in enumerate(cands, 1):
            have = self._already_downloaded(c)
            mark = " [downloaded]" if have else f" [{c.size} download]"
            d.out(f"  {i}. {c.name} ({c.kind}){mark}")
            d.out(f"     why: {c.why}")
        default = "1"
        answer = d.ask(f"Choose a model [1-{len(cands)}, Enter=1]: ").strip() \
            or default
        if not answer.isdigit() or not 1 <= int(answer) <= len(cands):
            d.out(f"  '{answer}' is not a choice - using the recommendation")
            return cands[0]
        return cands[int(answer) - 1]

    def _already_downloaded(self, c: ModelCandidate) -> bool:
        from . import model_catalog
        if c.kind == "whisper.cpp":
            return model_catalog.gguf_downloaded(c.name)
        if c.kind == "parakeet":
            return model_catalog.parakeet_downloaded(c.name)
        return False

    def _ensure_model(self, c: ModelCandidate) -> bool:
        """Download (with real progress) unless already cached. Sets
        _last_blocker with an actionable reason on failure."""
        d = self.deps
        if self._already_downloaded(c):
            d.out(f"\nModel {c.name}: already downloaded - skipping the "
                  "download step")
            return True
        d.out(f"\nDownloading {c.name} ({c.size}):")
        download = d.download_fn or _default_download
        progress = ProgressPrinter(d.out, c.name)
        try:
            download(c.kind, c.name, progress)
        except Exception as e:  # noqa: BLE001 - reported via failure_hint
            from .model_download import failure_hint
            hint = failure_hint(e)
            self._last_blocker = SetupResult(
                "blocked", 0,
                f"model download failed ({e}) - {hint}", blocker=str(e))
            return False
        d.out(f"  {c.name}: download verified")
        return True

    def _apply_config(self, c: ModelCandidate, language: str) -> bool:
        from .config import save_config
        d = self.deps
        m = self.cfg.setdefault("model", {})
        m["backend"] = "whisper.cpp" if c.kind == "whisper.cpp" else "parakeet"
        if c.kind == "whisper.cpp":
            m["whispercpp_model"] = c.name
        else:
            m["name"] = c.name
        try:
            save_config(self.cfg)
        except OSError as e:
            d.out(f"error writing config: {e}")
            return False
        d.out(f"\nConfig saved: backend {m['backend']}, model {c.name}, "
              f"language {language}")
        return True

    # -- insertion path ------------------------------------------------------

    def _insertion_report(self):
        """Check the desktop insertion path; None when it works, else a
        blocked SetupResult carrying the exact missing-package line."""
        from . import insertion, session
        d = self.deps
        probe = d.session_probe_fn or session.probe
        caps_fn = d.capability_fn or insertion.check_insertion_capability
        info = probe()
        caps = session.capabilities(info, which=d.which, cfg=self.cfg)
        report = caps_fn(self.cfg, which=d.which, info=info)
        d.out(f"\nDesktop insertion path ({info.type}"
              + (f"/{info.desktop}" if info.desktop else "") + "):")
        d.out(f"  typing tool: {caps['insertion']}")
        if report.ok:
            d.out(f"  {report.detail}")
            return None
        d.out(f"  BLOCKED: {report.detail}")
        if report.install_hint:
            d.out(f"  fix on this machine: {report.install_hint}")
        else:
            d.out("  fix: install the missing tools above "
                  "(see `sayit-ermano doctor`)")
        if info.is_wayland:
            for line in session.de_shortcut_instructions(
                    info.desktop_all, "sayit-ermano toggle"):
                d.out(f"  shortcut: {line}")
        return SetupResult(
            "blocked", 0,
            "insertion path unavailable - install the missing tools, "
            f"then re-run: {report.install_hint or 'see doctor'}",
            blocker=",".join(report.missing_tools))

    # -- the verified test take ----------------------------------------------

    def _verified_take(self) -> SetupResult:
        """Record 2 s, transcribe, insert into a scratch window (focus any
        text field - the terminal running this works). Exit green only on
        the full loop or an explicit, actionable blocker."""
        from .recorder import RecorderError
        d = self.deps
        d.out("\nFinal step: a VERIFIED test take.")
        d.ask("Focus a scratch window or this terminal, press Enter and "
              "say something for 2 seconds…")
        record = d.record_fn or _default_record
        transcribe = d.transcribe_fn or _default_transcribe
        insert = d.insert_fn or _default_insert
        try:
            wav = record(TEST_TAKE_SECONDS, d.out)
        except RecorderError as e:
            return SetupResult(
                "blocked", 0,
                f"no microphone path - {e}", blocker=str(e))
        try:
            text = transcribe(wav).strip()
        except Exception as e:  # noqa: BLE001 - engine failures are blockers
            return SetupResult(
                "blocked", 0,
                f"transcription failed - {e} (`sayit-ermano doctor` "
                "names the missing runtime)", blocker=str(e))
        if not text:
            return SetupResult(
                "blocked", 0,
                "the take came back empty - check the mic is selected "
                "and not muted (pactl info | grep Source)",
                blocker="empty transcript")
        d.out(f"  heard: \"{text}\"")
        from .insertion import InsertError
        try:
            insert(text)
        except InsertError as e:
            return SetupResult(
                "blocked", 0,
                f"insertion blocked - {e} (fix above, then re-run setup)",
                blocker=str(e))
        d.out("\nVERIFIED: recorded -> transcribed -> inserted. "
              "Setup complete - the hotkey/shortcut starts dictation.")
        return SetupResult("verified", 0, "")


def run_setup(cfg: dict | None = None, deps: SetupDeps | None = None,
              ) -> SetupResult:
    """Entry for the CLI (`--setup`): run the wizard, print the summary,
    return the exit code."""
    wizard = SetupWizard(cfg, deps)
    result = wizard.run()
    if result.summary:
        import sys
        print(f"\nsetup {result.state}: {result.summary}", file=sys.stderr)
    return result
