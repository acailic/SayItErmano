"""`sayit-ermano setup` (first-use guided verification flow): state
coverage with fakes at every seam — no network, no display, no mic.

Acceptance mapping:
* every flow state (no mic / no model / no insertion path / silent /
  empty transcript / full loop) gets a CLI-level test asserting the
  actionable message;
* the model recommendation cites its inputs in the output text;
* download progress prints size + ETA through the model_download
  callback seam.
"""
from __future__ import annotations

import copy

import pytest

from fluidvoice import backends, cli, insertion as insertion_mod, setup_flow
from fluidvoice.config import DEFAULTS
from fluidvoice.gtkui.onboarding_funnel import FunnelCounters
from fluidvoice.recorder import RecorderError
from fluidvoice.setup_flow import (
    HardwareProfile,
    ProgressLine,
    SetupFlow,
    recommend_models,
)
from tests.test_daemon import make_wav

CPU_HW = HardwareProfile(cuda=False, cpu_cores=4, ram_mb=4000,
                         gpu_name=None, vram_mb=None)
BIG_GPU_HW = HardwareProfile(cuda=True, cpu_cores=16, ram_mb=32000,
                             gpu_name="NVIDIA GeForce RTX 4070",
                             vram_mb=8192)
SMALL_GPU_HW = HardwareProfile(cuda=True, cpu_cores=8, ram_mb=16000,
                               gpu_name="NVIDIA GeForce MX450",
                               vram_mb=2048)


class FakeRecorder:
    """Writes a real (non-silent) WAV at start(); no subprocesses."""

    started = stopped = 0

    def __init__(self, loud=True, fail_start=False, **kw):
        self.loud = loud
        self.fail_start = fail_start

    def start(self, path):
        if self.fail_start:
            raise RecorderError(
                "no recorder found: install pipewire (pw-record) or "
                "pulseaudio-utils (parecord)")
        make_wav(path, seconds=2.0, loud=self.loud)
        type(self).started += 1

    def stop(self):
        type(self).stopped += 1
        return None

    def cancel(self):
        pass


class FakeBackend:
    name = "fake"

    def __init__(self, text="testing one two three", error=None,
                 warmup_error=None):
        self.text = text
        self.error = error
        self.warmup_error = warmup_error
        self.transcriptions: list = []
        self.warmups = 0

    def warmup(self):
        self.warmups += 1
        if self.warmup_error is not None:
            raise self.warmup_error

    def transcribe(self, wav, language=None):
        self.transcriptions.append((str(wav), language))
        if self.error is not None:
            raise self.error
        return {"text": self.text, "language": language or "en",
                "duration": 2.0}


@pytest.fixture()
def seams(monkeypatch, tmp_path):
    """Wire every external effect to fakes; returns handles to assert on."""
    FakeRecorder.started = FakeRecorder.stopped = 0
    backend = FakeBackend()
    inserted: list[str] = []
    # headless runners have none of the desktop tools — pretend a fully
    # provisioned X11 box so the happy path is deterministic (the
    # no-insertion tests override which() with a selective fake)
    monkeypatch.setattr("shutil.which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(backends, "load_backend", lambda cfg: backend)
    monkeypatch.setattr(setup_flow, "Recorder", FakeRecorder)
    monkeypatch.setattr(insertion_mod, "insert_text",
                        lambda text, cfg, **kw: inserted.append(text)
                        or "typed")
    monkeypatch.setattr(setup_flow, "probe_hardware",
                        lambda **kw: CPU_HW)
    monkeypatch.setattr(cli, "load_config",
                        lambda p=None: copy.deepcopy(DEFAULTS))
    funnel_path = tmp_path / "funnel.json"
    monkeypatch.setattr(setup_flow, "FunnelCounters",
                        lambda: FunnelCounters(funnel_path))
    return type("Seams", (), {"backend": backend, "inserted": inserted,
                              "funnel": FunnelCounters(funnel_path),
                              "funnel_path": funnel_path})()


class TestRecommendations:
    """Pure ranking: the why-line must cite the inputs it used."""

    def test_cpu_profile_cites_cores_and_ram(self):
        recs = recommend_models(CPU_HW, "en")
        assert recs[0].name == "base"  # mirrors auto resolution on CPU
        why = recs[0].why
        assert "no CUDA GPU detected" in why
        assert "4 cores" in why and "4 GB RAM" in why
        assert all(r.name != "small" for r in recs)  # 4 cores: no upgrade

    def test_gpu_profile_cites_gpu_name_and_vram(self):
        recs = recommend_models(BIG_GPU_HW, "en")
        assert recs[0].name == "small"  # mirrors auto resolution on CUDA
        assert "NVIDIA GeForce RTX 4070" in recs[0].why
        assert "8192 MB VRAM" in recs[0].why
        names = [r.name for r in recs]
        assert "large-v3-turbo" in names and "medium" in names

    def test_small_vram_gpu_gets_no_heavy_models(self):
        names = [r.name for r in recommend_models(SMALL_GPU_HW, "en")]
        assert names[0] == "small"
        assert "large-v3-turbo" not in names and "medium" not in names

    def test_english_adds_parakeet_cpu_candidate(self):
        names = [r.name for r in recommend_models(CPU_HW, "en")]
        assert "parakeet-tdt-0.6b-v2" in names
        names_de = [r.name for r in recommend_models(CPU_HW, "de")]
        assert "parakeet-tdt-0.6b-v2" not in names_de
        assert any("covers your language 'de'" in r.why for r in
                   recommend_models(CPU_HW, "de"))

    def test_many_cores_ram_upgrade_cites_inputs(self):
        hw = HardwareProfile(cuda=False, cpu_cores=12, ram_mb=16000,
                             gpu_name=None, vram_mb=None)
        recs = recommend_models(hw, "en")
        assert recs[1].name == "small"
        assert "12 CPU cores" in recs[1].why and "16 GB RAM" in recs[1].why


class TestProgressLine:
    def test_percent_size_and_eta_printed(self):
        lines: list[str] = []
        ticks = iter([0.0, 10.0])
        cb = ProgressLine(lines.append, clock=lambda: next(ticks))
        cb(0, 142_000_000)            # first callback: 0%
        cb(71_000_000, 142_000_000)   # +50%: 71 MB in 10 s
        assert len(lines) == 2
        assert "50% of 142 MB" in lines[1]
        assert "ETA 10 s" in lines[1]  # 71 MB left at 7.1 MB/s

    def test_throttled_to_ten_percent_steps(self):
        lines: list[str] = []
        tick = {"t": 0.0}

        def fake_clock():
            tick["t"] += 1.0
            return tick["t"]

        cb = ProgressLine(lines.append, clock=fake_clock)
        for pct in range(0, 101, 5):
            cb(142_000_000 * pct // 100, 142_000_000)
        assert len(lines) <= 12  # 0,10,20,…,90,99-final


class TestFullLoop:
    def test_verified_exit_zero_and_funnel(self, seams, capsys):
        rc = cli.main(["setup", "--yes"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "SETUP VERIFIED" in out
        assert "CPU: 4 cores" in out          # hardware cited
        assert "no CUDA GPU detected" in out  # recommendation cites inputs
        assert "heard: \"testing one two three\"" in out
        assert seams.inserted == ["testing one two three"]
        assert FakeRecorder.started == 1
        steps = seams.funnel.steps()
        assert set(steps) == {"onboarding_started", "model_downloaded",
                              "tryout_ok", "real_insertion_reported_ok"}
        assert steps["onboarding_started"]["detail"]["source"] == "cli-setup"

    def test_config_persists_choice(self, seams, capsys):
        from fluidvoice import paths
        cli.main(["setup", "--yes", "--language", "de"])
        capsys.readouterr()
        text = paths.config_file().read_text()
        assert 'language = "de"' in text

    def test_whispercpp_download_shows_eta(self, seams, monkeypatch, capsys):
        calls: list = []

        def fake_download(name, progress=None, cancel=None):
            calls.append(name)
            progress(0, 142_000_000)
            progress(71_000_000, 142_000_000)
            progress(142_000_000, 142_000_000)
            return setup_flow.model_catalog.gguf_path(name)

        monkeypatch.setattr(setup_flow, "download_gguf", fake_download)
        rc = cli.main(["setup", "--yes", "--model", "ggml-base.bin"])
        out = capsys.readouterr().out
        assert rc == 0 and calls == ["ggml-base.bin"]
        assert "downloading ggml-base.bin" in out
        assert "ETA" in out and "50% of 142 MB" in out
        assert "SETUP VERIFIED" in out

    def test_explicit_unknown_model_fails_clean(self, seams, capsys):
        rc = cli.main(["setup", "--yes", "--model", "nope"])
        out = capsys.readouterr()
        assert rc == 1
        assert "unknown model 'nope'" in out.err


class TestBlockers:
    """Explicit, actionable blockers: the fix is named, exit stays 0."""

    def test_no_mic(self, seams, monkeypatch, capsys):
        monkeypatch.setattr(setup_flow, "Recorder",
                            lambda **kw: FakeRecorder(fail_start=True))
        rc = cli.main(["setup", "--yes"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "SETUP BLOCKED" in out
        assert "install pipewire (pw-record) or pulseaudio-utils (parecord)" \
            in out
        assert "tryout_ok" not in seams.funnel.steps()

    def test_no_model_download_fails_with_hint(self, seams, monkeypatch,
                                               capsys):
        seams.backend.warmup_error = OSError("network is unreachable")
        rc = cli.main(["setup", "--yes"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "SETUP BLOCKED" in out
        assert "model download failed" in out
        assert "network problem or stalled transfer" in out  # failure_hint

    def test_no_insertion_path_x11(self, seams, monkeypatch, capsys):
        monkeypatch.setattr("shutil.which",
                            lambda n: None if n == "xdotool"
                            else f"/usr/bin/{n}")
        rc = cli.main(["setup", "--yes"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "SETUP BLOCKED" in out
        assert "xdotool MISSING" in out
        # the exact missing-package line for THIS distro (apt on debian
        # runners, dnf/pacman elsewhere — same source doctor uses)
        expected = insertion_mod.install_hint(("xdotool",))
        assert expected and expected in out
        # the take still verified record + transcribe; only insert was
        # blocked, and nothing was inserted
        assert seams.backend.transcriptions
        assert seams.inserted == []
        steps = seams.funnel.steps()
        assert "tryout_ok" in steps
        assert "real_insertion_reported_failed" in steps


class TestInconclusive:
    """Nothing was verified and no system fix exists: exit 1."""

    def test_silent_take(self, seams, monkeypatch, capsys):
        monkeypatch.setattr(setup_flow, "Recorder",
                            lambda **kw: FakeRecorder(loud=False))
        rc = cli.main(["setup", "--yes"])
        out = capsys.readouterr().out
        assert rc == 1
        assert "SETUP INCOMPLETE" in out
        assert "digital silence" in out

    def test_empty_transcript(self, seams, monkeypatch, capsys):
        seams.backend.text = "   "
        rc = cli.main(["setup", "--yes"])
        out = capsys.readouterr().out
        assert rc == 1
        assert "SETUP INCOMPLETE" in out
        assert "EMPTY" in out
        assert seams.inserted == []


class TestInsertionAudit:
    def test_wayland_lines_carry_install_and_permission_guidance(self):
        from fluidvoice.insertion import CapabilityReport
        from fluidvoice import session as session_mod
        report = CapabilityReport(
            ok=False, fallback_ok=False, session_type="wayland",
            typing_tool=None,
            missing_tools=("wtype", "ydotool", "wl-copy", "wl-paste"),
            install_hint="sudo apt install wtype ydotool wl-clipboard",
            detail="insertion impossible: missing wtype, ydotool, wl-copy, "
                   "wl-paste")
        info = session_mod.probe({"XDG_SESSION_TYPE": "wayland",
                                  "XDG_CURRENT_DESKTOP": "ubuntu:GNOME"})
        cfg = copy.deepcopy(DEFAULTS)
        cfg.setdefault("hotkey", {})["wayland_evdev"] = True
        lines = SetupFlow(cfg).insertion_lines(report, info)
        joined = "\n".join(lines)
        assert "sudo apt install wtype ydotool wl-clipboard" in joined
        assert "GNOME" in joined              # per-desktop shortcut lines
        assert "shortcut" in joined
        assert "sudo usermod -aG input $USER" in joined  # doctor's guidance

    def test_x11_ready_lines(self):
        from fluidvoice.insertion import CapabilityReport
        from fluidvoice import session as session_mod
        report = CapabilityReport(
            ok=True, fallback_ok=True, session_type="x11",
            typing_tool="xdotool", missing_tools=(), install_hint="",
            detail="insertion ready (xdotool + clipboard)")
        info = session_mod.probe({"XDG_SESSION_TYPE": "x11"})
        lines = SetupFlow(copy.deepcopy(DEFAULTS)).insertion_lines(report, info)
        joined = "\n".join(lines)
        assert "typing tool: xdotool" in joined
        assert "xclip OK" in joined
        assert "missing packages" not in joined
