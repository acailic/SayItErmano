"""Guided setup wizard (`sayit-ermano --setup`, excellence bet 1).

Offline, headless, model-free: every side-effecting seam (hardware
probe, download, recorder, transcriber, inserter) is faked per the
suite's established patterns. What is pinned here:

  * the model recommendation CITES ITS INPUTS (GPU/VRAM/CUDA/CPU/RAM
    and the chosen language) in the output text;
  * download progress lines carry percent + size + ETA;
  * every flow state (no mic, no model, no insertion path, full loop)
    lands on an actionable message with the documented exit code -
    green only on a verified loop or an explicit blocker;
  * the CLI dispatch (`--setup`) reaches the wizard.
"""
from __future__ import annotations

import subprocess

import pytest

from fluidvoice import setup_wizard as sw
from fluidvoice.recorder import RecorderError
from fluidvoice.setup_wizard import (
    HardwareProfile,
    ProgressPrinter,
    SetupDeps,
    SetupWizard,
    recommend_models,
)


def ok(args=None, stdout=b""):
    return subprocess.CompletedProcess(args or [], 0, stdout, b"")


# ---------------------------------------------------------------------------
# recommend_models: grounded in hardware + language, why cites its inputs
# ---------------------------------------------------------------------------

def test_gpu_with_big_vram_recommends_large_first_and_cites_it():
    hw = HardwareProfile(gpu="NVIDIA GeForce RTX 4070", vram_mib=12288,
                         cuda=True, cpu_cores=12, ram_gb=32.0)
    cands = recommend_models(hw, "en")
    assert cands[0].name == "ggml-large-v3.bin"
    why = cands[0].why
    assert "RTX 4070" in why and "12 GB VRAM" in why and "CUDA" in why
    assert any(c.name == "ggml-small.bin" for c in cands)
    assert any("Parakeet" in c.why for c in cands)


def test_cpu_only_recommendation_cites_cores_and_ram():
    hw = HardwareProfile(cpu_cores=8, ram_gb=16.0)
    cands = recommend_models(hw, "en")
    cpu = [c for c in cands if c.name.startswith("ggml-base")]
    assert len(cpu) == 1
    assert cpu[0].name == "ggml-base.en.bin"
    assert "8 CPU cores" in cpu[0].why and "16 GB RAM" in cpu[0].why
    assert "CUDA" in cpu[0].why  # names the absence, not silence


def test_non_english_excludes_english_only_models_and_cites_language():
    hw = HardwareProfile(cpu_cores=8, ram_gb=16.0)
    cands = recommend_models(hw, "de")
    assert not any(".en.bin" in c.name for c in cands)
    assert not any("v2" in c.name and c.kind == "parakeet" for c in cands)
    assert any("parakeet-tdt-0.6b-v3" in c.name for c in cands)
    assert any("'de'" in c.why for c in cands)


# ---------------------------------------------------------------------------
# hardware probe parsing (the existing probes, faked transports)
# ---------------------------------------------------------------------------

def test_probe_hardware_parses_nvidia_smi_and_meminfo():
    def runner(args, **kw):
        return ok(args, "NVIDIA GeForce RTX 3060, 6144 MiB\n")

    def meminfo(key):
        return 16_000_000 if key == "MemTotal" else None  # kB -> ~15 GB

    hw = sw.probe_hardware(runner=runner, meminfo_reader=meminfo,
                           cuda_fn=lambda: True, cpu_fn=lambda: 8,
                           which=lambda n: "/usr/bin/nvidia-smi")
    assert hw.gpu == "NVIDIA GeForce RTX 3060"
    assert hw.vram_mib == 6144
    assert hw.cuda and hw.cpu_cores == 8 and hw.ram_gb == pytest.approx(15.3, abs=0.2)
    assert "RTX 3060" in " ".join(hw.summary_lines())
    assert "6 GB VRAM" in " ".join(hw.summary_lines())


def test_probe_hardware_without_gpu_stays_cpu_path():
    def runner(args, **kw):
        raise FileNotFoundError("no nvidia-smi")

    hw = sw.probe_hardware(runner=runner, meminfo_reader=lambda k: None,
                           cuda_fn=lambda: False, cpu_fn=lambda: 4,
                           which=lambda n: "/usr/bin/nvidia-smi")
    assert hw.gpu == "" and hw.vram_mib is None and not hw.cuda


# ---------------------------------------------------------------------------
# download progress rendering: percent + size + ETA
# ---------------------------------------------------------------------------

def test_progress_printer_shows_percent_size_and_eta():
    lines: list[str] = []
    t = {"now": 0.0}

    def clock():
        return t["now"]

    p = ProgressPrinter(lines.append, "ggml-base.bin", clock=clock)
    t["now"] = 0.0
    p(0, 142_000_000)
    t["now"] = 10.0
    p(71_000_000, 142_000_000)
    t["now"] = 20.0
    p(142_000_000, 142_000_000)
    assert any("0% of 142 MB" in l for l in lines)
    mid = [l for l in lines if "50%" in l][0]
    assert "71 MB" in mid and "MB/s" in mid and "ETA" in mid
    assert any("100% of 142 MB" in l for l in lines)


def test_progress_printer_indeterminate_without_total():
    lines: list[str] = []
    p = ProgressPrinter(lines.append, "x", clock=lambda: 1.0)
    p(50_000_000, None)
    assert any("50 MB downloaded" in l for l in lines)


# ---------------------------------------------------------------------------
# flow states through fakes (offline: no network, no display, no model)
# ---------------------------------------------------------------------------

class Fakes:
    """The whole wizard surface, faked. Default: everything works."""

    def __init__(self, answers: list[str] | None = None):
        self.lines: list[str] = []
        self.answers = list(answers or [])
        self.downloads: list[tuple[str, str]] = []
        self.recorded: list[float] = []
        self.transcribed: list = []
        self.inserted: list[str] = []
        self.installed = {"xdotool", "xclip"}
        self.record_error: Exception | None = None
        self.download_error: Exception | None = None
        self.transcribe_error: Exception | None = None
        self.insert_error: Exception | None = None

    def deps(self) -> SetupDeps:
        return SetupDeps(
            out=self.lines.append,
            ask=self._ask,
            probe_hardware_fn=lambda: HardwareProfile(
                gpu="", cuda=False, cpu_cores=8, ram_gb=16.0),
            download_fn=self._download,
            record_fn=self._record,
            transcribe_fn=self._transcribe,
            insert_fn=self._insert,
            which=lambda n: f"/usr/bin/{n}" if n in self.installed else None,
        )

    def _ask(self, prompt: str) -> str:
        self.lines.append(prompt.strip())
        return self.answers.pop(0) if self.answers else ""

    def _download(self, kind: str, name: str, progress) -> None:
        self.downloads.append((kind, name))
        if self.download_error is not None:
            raise self.download_error
        progress(0, 142_000_000)
        progress(142_000_000, 142_000_000)

    def _record(self, seconds: float, out) -> object:
        self.recorded.append(seconds)
        if self.record_error is not None:
            raise self.record_error
        return "take.wav"

    def _transcribe(self, wav) -> str:
        self.transcribed.append(wav)
        if self.transcribe_error is not None:
            raise self.transcribe_error
        return "hello setup world"

    def _insert(self, text: str) -> None:
        self.inserted.append(text)
        if self.insert_error is not None:
            raise self.insert_error

    def text(self) -> str:
        return "\n".join(self.lines)


def test_full_loop_verifies_and_exits_green():
    f = Fakes(answers=["en", "1"])
    result = SetupWizard(deps=f.deps()).run()
    assert result.state == "verified" and result.exit_code == 0
    assert f.recorded == [sw.TEST_TAKE_SECONDS]
    assert f.transcribed and f.inserted == ["hello setup world"]
    assert "VERIFIED" in f.text() and "heard" in f.text()
    # the recommendation explains itself in the output text
    assert "why:" in f.text() and "CPU cores" in f.text()


def test_full_loop_saves_config_with_chosen_model():
    from fluidvoice import paths
    from fluidvoice.config import load_config
    f = Fakes(answers=["de", "1"])
    SetupWizard(deps=f.deps()).run()
    cfg = load_config(paths.config_file())
    assert cfg["model"]["backend"] == "whisper.cpp"
    assert cfg["model"]["whispercpp_model"]
    assert cfg["general"]["language"] == "de"


def test_no_microphone_is_an_actionable_blocker():
    f = Fakes(answers=["en", "1"])
    f.record_error = RecorderError(
        "no recorder found: install pipewire (pw-record) or "
        "pulseaudio-utils (parecord)")
    result = SetupWizard(deps=f.deps()).run()
    assert result.state == "blocked" and result.exit_code == 0
    assert "no microphone path" in result.summary
    assert "install pipewire" in result.summary  # actionable: names the fix


def test_no_model_download_is_an_actionable_blocker():
    import urllib.error
    f = Fakes(answers=["en", "1"])
    f.download_error = urllib.error.URLError("no route to host")
    result = SetupWizard(deps=f.deps()).run()
    assert result.state == "blocked" and result.exit_code == 0
    assert "download failed" in result.summary
    assert "check the connection" in result.summary  # failure_hint surfaced
    assert f.transcribed == []  # the flow stopped at the blocker


def test_no_insertion_path_reports_missing_package_line():
    f = Fakes(answers=["en", "1"])
    f.installed.clear()  # no xdotool, no xclip on this x11 session
    result = SetupWizard(deps=f.deps()).run()
    assert result.state == "blocked" and result.exit_code == 0
    assert "insertion path unavailable" in result.summary
    assert "xdotool" in f.text() and "install" in f.text().lower()
    # stopped BEFORE the test take: nothing recorded, nothing transcribed
    assert f.recorded == [] and f.transcribed == []
    # the exact install line for the detected distro is printed
    blocked = [l for l in f.lines if "fix on this machine" in l]
    assert blocked and "xdotool" in blocked[0]


def test_transcription_failure_is_an_actionable_blocker():
    f = Fakes(answers=["en", "1"])
    f.transcribe_error = RuntimeError("onnxruntime: no providers")
    result = SetupWizard(deps=f.deps()).run()
    assert result.state == "blocked" and result.exit_code == 0
    assert "transcription failed" in result.summary
    assert "doctor" in result.summary


def test_empty_transcript_is_an_actionable_blocker():
    f = Fakes(answers=["en", "1"])
    f._transcribe = lambda wav: ""  # instance attr wins over the method
    result = SetupWizard(deps=f.deps()).run()
    assert result.state == "blocked"
    assert "empty" in result.summary and "mic" in result.summary.lower()


def test_insert_failure_is_an_actionable_blocker():
    from fluidvoice.insertion import InsertError
    f = Fakes(answers=["en", "1"])
    f.insert_error = InsertError("xdotool not found")
    result = SetupWizard(deps=f.deps()).run()
    assert result.state == "blocked" and "insertion blocked" in result.summary


def test_already_downloaded_model_skips_download():
    from fluidvoice import model_catalog
    ggml = model_catalog.gguf_path("ggml-base.en.bin")
    ggml.parent.mkdir(parents=True, exist_ok=True)
    ggml.write_bytes(b"x")
    try:
        f = Fakes(answers=["en", "1"])
        SetupWizard(deps=f.deps()).run()
        assert f.downloads == []  # no network path taken
        assert "already downloaded" in f.text()
    finally:
        ggml.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# CLI dispatch
# ---------------------------------------------------------------------------

def test_cli_setup_flag_reaches_the_wizard(capsys, monkeypatch):
    import fluidvoice.cli as cli

    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    monkeypatch.setattr(
        sw, "probe_hardware",
        lambda **kw: HardwareProfile(cpu_cores=8, ram_gb=16.0))
    monkeypatch.setattr(sw, "_default_record",
                        lambda s, out: "take.wav")
    monkeypatch.setattr(sw, "_default_transcribe", lambda wav: "cli loop")
    inserted: list[str] = []
    monkeypatch.setattr(sw, "_default_insert", inserted.append)
    monkeypatch.setattr(sw, "_default_download",
                        lambda kind, name, progress: None)
    # x11 session pinned by conftest; give the CLI the tools it needs
    monkeypatch.setattr(sw.shutil, "which",
                        lambda n: f"/usr/bin/{n}")

    code = cli.main(["--setup"])
    out = capsys.readouterr()
    assert code == 0
    assert "VERIFIED" in out.out
    assert inserted == ["cli loop"]


def test_cli_setup_blocked_state_exits_documented_green(capsys, monkeypatch):
    import fluidvoice.cli as cli

    monkeypatch.setattr(
        sw, "probe_hardware",
        lambda **kw: HardwareProfile(cpu_cores=8, ram_gb=16.0))
    monkeypatch.setattr(sw, "_default_download",
                        lambda kind, name, progress: None)
    # nothing installed -> insertion path blocked, actionable
    monkeypatch.setattr(sw.shutil, "which", lambda n: None)
    monkeypatch.setattr("builtins.input", lambda prompt="": "en")

    code = cli.main(["--setup"])
    captured = capsys.readouterr()
    assert code == 0  # explicit, actionable blocker -> documented green
    assert "insertion path unavailable" in captured.err
