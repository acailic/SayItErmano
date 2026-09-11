"""GGUF + Parakeet download mechanics — urlopen fakes, never any network."""
from __future__ import annotations

import copy
import hashlib
import io
import os
import tarfile
import time
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

from fluidvoice import model_catalog, model_download


class FakeResp:
    def __init__(self, chunks, length=None):
        self._chunks = list(chunks)
        self.headers = {}
        if length is not None:
            self.headers["Content-Length"] = str(length)

    def read(self, n):
        if not self._chunks:
            return b""
        first = self._chunks[0]
        if isinstance(first, Exception):
            raise self._chunks.pop(0)
        return self._chunks.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture()
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    return tmp_path


def test_happy_path_streams_and_renames(cache, monkeypatch):
    chunks = [b"ab", b"cd", b"ef"]
    seen_req = {}

    def fake_urlopen(req, timeout=None):
        seen_req["req"] = req
        return FakeResp(chunks, length=6)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    events: list[tuple[int, int | None]] = []
    dest = model_download.download_gguf(
        "ggml-base.bin", progress=lambda b, t: events.append((b, t)))
    assert dest == model_catalog.gguf_path("ggml-base.bin")
    assert dest.read_bytes() == b"abcdef"
    assert list(dest.parent.iterdir()) == [dest]  # no .part left
    assert events[0] == (0, 6)
    assert events[-1] == (6, 6)
    assert [b for b, _ in events] == sorted(b for b, _ in events)  # monotonic
    # URL fidelity + UA
    assert seen_req["req"].full_url == \
        model_catalog.GGUF_CATALOG["ggml-base.bin"]["url"]
    assert "SayItErmano" in seen_req["req"].headers["User-agent"]


def test_no_content_length_still_succeeds(cache, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=None: FakeResp([b"xyz"]))
    events: list[tuple[int, int | None]] = []
    dest = model_download.download_gguf(
        "ggml-base.en.bin", progress=lambda b, t: events.append((b, t)))
    assert dest.read_bytes() == b"xyz"
    assert events and all(t is None for _, t in events)
    assert not dest.with_name(dest.name + ".part").exists()


def test_midstream_failure_cleans_up(cache, monkeypatch):
    def fake_urlopen(req, timeout=None):
        return FakeResp([b"par", OSError("socket reset"), b"tial"])

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(OSError, match="socket reset"):
        model_download.download_gguf("ggml-small.bin")
    assert list(model_catalog.gguf_dir().iterdir()) == []  # nothing at all


def test_http_error_propagates(cache, monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(urllib.error.HTTPError):
        model_download.download_gguf("ggml-medium.bin")
    assert not model_catalog.gguf_path("ggml-medium.bin").exists()
    assert model_catalog.gguf_dir().is_dir()  # parent still created


def test_truncated_download_raises(cache, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=None: FakeResp([b"only"], length=99))
    with pytest.raises(OSError, match="truncated"):
        model_download.download_gguf("ggml-large-v3.bin")
    assert not model_catalog.gguf_path("ggml-large-v3.bin").exists()
    assert not model_catalog.gguf_dir().joinpath(
        "ggml-large-v3.bin.part").exists()


def test_unknown_gguf_name_rejected(cache):
    with pytest.raises(ValueError, match="unknown gguf model"):
        model_download.download_gguf("nope.bin")


def test_existing_file_is_noop(cache, monkeypatch):
    model_catalog.gguf_dir().mkdir(parents=True)
    dest = model_catalog.gguf_path("ggml-base.bin")
    dest.write_bytes(b"already here")
    called = []
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: called.append(a))
    out = model_download.download_gguf("ggml-base.bin")
    assert out == dest and dest.read_bytes() == b"already here"
    assert called == []  # urlopen never touched


# -- parakeet tarball downloads -------------------------------------------------

PK_FILES = {"encoder.int8.onnx": b"ENC", "decoder.int8.onnx": b"DEC",
            "joiner.int8.onnx": b"JOIN", "tokens.txt": b"<unk> 0\n"}


def make_tarball(path: Path, files: dict[str, bytes] | None = None,
                 top: str = "sherpa-onnx-nemo-parakeet-x-int8") -> bytes:
    data = files if files is not None else PK_FILES
    with tarfile.open(path, "w:bz2") as tf:
        for name, blob in data.items():
            info = tarfile.TarInfo(f"{top}/{name}")
            info.size = len(blob)
            tf.addfile(info, io.BytesIO(blob))
    return path.read_bytes()


def pk_entry(tar_path: Path, files: dict[str, bytes] | None = None,
             tarball_sha: str | None = None) -> dict:
    data = files if files is not None else PK_FILES
    return {
        "size": "~tiny", "langs": "en", "note": "fixture",
        "url": "http://fake/parakeet.tar.bz2",
        "tarball_sha256": tarball_sha or hashlib.sha256(tar_path.read_bytes()).hexdigest(),
        "files": {n: hashlib.sha256(b).hexdigest() for n, b in data.items()},
        "features": {"sample_rate": 16000, "n_mels": 128, "n_fft": 512,
                     "win": 400, "hop": 160, "fmin": 0.0, "fmax": 8000.0},
    }


class TestDownloadParakeet:
    NAME = "pk-fixture"

    @pytest.fixture()
    def pk(self, cache, monkeypatch, tmp_path):
        """A fixture catalog entry + its matching tarball on a fake server."""
        tar = tmp_path / "t.tar.bz2"
        blob = make_tarball(tar)
        entry = pk_entry(tar)
        monkeypatch.setattr(model_catalog, "PARAKEET_CATALOG",
                            {self.NAME: entry})
        serve = {"blob": blob}

        def fake_urlopen(req, timeout=None):
            return FakeResp([serve["blob"]], length=len(serve["blob"]))

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        return SimpleNamespace(entry=entry, serve=serve, fake=fake_urlopen)

    def test_happy_path(self, cache, monkeypatch, pk):
        events: list[tuple[int, int | None]] = []
        d = model_download.download_parakeet(
            self.NAME, progress=lambda b, t: events.append((b, t)))
        assert d == model_catalog.parakeet_model_dir(self.NAME)
        assert sorted(p.name for p in d.iterdir()) == sorted(PK_FILES)
        for name, want in PK_FILES.items():
            assert (d / name).read_bytes() == want
        total = len(pk.serve["blob"])
        assert events[0] == (0, total)
        assert events[-1] == (total, total)
        assert [b for b, _ in events] == sorted(b for b, _ in events)
        # nothing left behind: no tarball, no stage, no .part
        leftovers = [p.name for p in model_catalog.parakeet_dir().iterdir()]
        assert leftovers == [self.NAME]

    def test_tarball_sha_mismatch_cleans_up(self, cache, monkeypatch, pk, tmp_path):
        tar = tmp_path / "bad.tar.bz2"
        make_tarball(tar, {**PK_FILES, "tokens.txt": b"tampered\n"})
        pk.serve["blob"] = tar.read_bytes()  # valid bz2, wrong bytes
        with pytest.raises(OSError, match="checksum mismatch.*tarball"):
            model_download.download_parakeet(self.NAME)
        assert list(model_catalog.parakeet_dir().iterdir()) == []

    def test_midstream_failure_leaves_nothing(self, cache, monkeypatch, pk):
        blob = pk.serve["blob"]
        half = len(blob) // 2

        def flaky(req, timeout=None):
            return FakeResp([blob[:half], OSError("socket reset"), blob[half:]])

        monkeypatch.setattr(urllib.request, "urlopen", flaky)
        with pytest.raises(OSError, match="socket reset"):
            model_download.download_parakeet(self.NAME)
        assert list(model_catalog.parakeet_dir().iterdir()) == []

    def test_inner_file_sha_mismatch_cleans_up(self, cache, monkeypatch, pk, tmp_path):
        tampered = {**PK_FILES, "joiner.int8.onnx": b"EVIL"}
        tar = tmp_path / "t2.tar.bz2"
        blob = make_tarball(tar, tampered)
        entry = pk_entry(tar, files=PK_FILES, tarball_sha=hashlib.sha256(blob).hexdigest())
        monkeypatch.setattr(model_catalog, "PARAKEET_CATALOG",
                            {self.NAME: entry})
        monkeypatch.setattr(urllib.request, "urlopen",
                            lambda req, timeout=None: FakeResp([blob], length=len(blob)))
        with pytest.raises(OSError, match="checksum mismatch.*joiner"):
            model_download.download_parakeet(self.NAME)
        assert list(model_catalog.parakeet_dir().iterdir()) == []

    def test_missing_member_raises(self, cache, monkeypatch, pk, tmp_path):
        partial = {k: v for k, v in PK_FILES.items() if k != "tokens.txt"}
        tar = tmp_path / "t3.tar.bz2"
        blob = make_tarball(tar, partial)
        entry = pk_entry(tar, files=PK_FILES,
                         tarball_sha=hashlib.sha256(blob).hexdigest())
        monkeypatch.setattr(model_catalog, "PARAKEET_CATALOG",
                            {self.NAME: entry})
        monkeypatch.setattr(urllib.request, "urlopen",
                            lambda req, timeout=None: FakeResp([blob], length=len(blob)))
        with pytest.raises(OSError, match="missing: tokens.txt"):
            model_download.download_parakeet(self.NAME)
        assert list(model_catalog.parakeet_dir().iterdir()) == []

    def test_already_downloaded_is_noop(self, cache, monkeypatch, pk):
        d = model_catalog.parakeet_model_dir(self.NAME)
        d.mkdir(parents=True)
        for name, want in PK_FILES.items():
            (d / name).write_bytes(want)
        called = []
        monkeypatch.setattr(urllib.request, "urlopen",
                            lambda *a, **k: called.append(a))
        out = model_download.download_parakeet(self.NAME)
        assert out == d and called == []

    def test_unknown_name_rejected(self, cache):
        with pytest.raises(ValueError, match="unknown parakeet model"):
            model_download.download_parakeet("parakeet-nope")

    def test_stale_stage_removed_on_fresh_run(self, cache, monkeypatch, pk):
        stale = model_catalog.parakeet_dir() / f".{self.NAME}.tmp-1"
        stale.mkdir(parents=True)
        (stale / "encoder.int8.onnx").write_bytes(b"junk")
        d = model_download.download_parakeet(self.NAME)
        assert (d / "tokens.txt").read_bytes() == PK_FILES["tokens.txt"]
        assert not stale.exists()


class TestDownloadFiles:
    def test_aggregate_progress_across_files(self, cache, monkeypatch, tmp_path):
        a, b = b"aaa", b"bb"
        urls = {"http://x/a": a, "http://x/b": b}

        def fake_urlopen(req, timeout=None):
            blob = urls[req.full_url]
            return FakeResp([blob], length=len(blob))

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        events: list[tuple[int, int | None]] = []
        dest = tmp_path / "multi"
        out = model_download.download_files(
            [{"name": "a.bin", "url": "http://x/a",
              "sha256": hashlib.sha256(a).hexdigest(), "size": len(a)},
             {"name": "b.bin", "url": "http://x/b",
              "sha256": hashlib.sha256(b).hexdigest(), "size": len(b)}],
            dest, progress=lambda done, t: events.append((done, t)))
        assert out == dest
        assert (dest / "a.bin").read_bytes() == a
        assert (dest / "b.bin").read_bytes() == b
        assert events[0] == (0, 5)
        assert events[-1] == (5, 5)
        assert [d for d, _ in events] == sorted(d for d, _ in events)

    def test_bad_sha_aborts_and_leaves_no_dest(self, cache, monkeypatch, tmp_path):
        a = b"aaa"
        monkeypatch.setattr(
            urllib.request, "urlopen",
            lambda req, timeout=None: FakeResp([a], length=len(a)))
        dest = tmp_path / "multi"
        with pytest.raises(OSError, match="checksum mismatch"):
            model_download.download_files(
                [{"name": "a.bin", "url": "http://x/a",
                  "sha256": "0" * 64, "size": len(a)}], dest)
        assert not dest.exists()
        assert list(tmp_path.iterdir()) == []  # staging dir gone too


# -- F-02 progress surfaces: registry, observation, cancel, hints -------------

FIRST_CHUNK = b"a" * 100_000  # > the stdio write buffer: lands on disk


class _GatedResp(FakeResp):
    """Serves one chunk immediately, then holds the stream open until
    released - a slow link we can observe mid-flight (bytes on disk,
    transfer not finished)."""

    def __init__(self, gate):
        super().__init__([FIRST_CHUNK, b"chunk-two"], length=None)
        self.gate = gate
        self.served = False

    def read(self, n):
        if not self.served:
            self.served = True
            return super().read(n)
        self.gate.wait(5.0)
        self.gate.clear()
        return super().read(n)


class TestCancelMidDownload:
    """request_cancel() / an explicit cancel event abort between chunks;
    the .part is removed - never a half-written file, never a stuck
    registry entry."""

    def test_request_cancel_aborts_and_cleans(self, cache, monkeypatch):
        import threading
        gate = threading.Event()
        monkeypatch.setattr(urllib.request, "urlopen",
                            lambda req, timeout=None: _GatedResp(gate))
        dest = model_catalog.gguf_path("ggml-base.bin")
        err: list[BaseException] = []

        def run():
            try:
                model_download.download_gguf("ggml-base.bin")
            except BaseException as e:  # noqa: BLE001 - captured for assert
                err.append(e)

        t = threading.Thread(target=run)
        t.start()
        # wait until the first chunk landed (registry + .part with bytes)
        deadline = time.monotonic() + 5
        part = dest.with_name(dest.name + ".part")
        try:
            while time.monotonic() < deadline:
                if part.exists() and part.stat().st_size == len(FIRST_CHUNK) \
                        and model_download.active_downloads():
                    break
                time.sleep(0.02)
            assert part.exists() and part.stat().st_size == len(FIRST_CHUNK)
            assert model_download.request_cancel(dest) is True
        finally:
            # never strand a blocked download thread past the test
            model_download.request_cancel(dest)
            gate.set()
            t.join(timeout=5)
        assert not t.is_alive()
        # clean abort: no file, no .part, no registry residue
        assert not dest.exists()
        assert not part.exists()
        assert model_download.active_downloads() == []
        assert isinstance(err[0], model_download.DownloadCancelled)

    def test_explicit_cancel_event_param(self, cache, monkeypatch):
        import threading
        ev = threading.Event()

        def fake_urlopen(req, timeout=None):
            ev.set()  # cancelled before the first read lands
            return FakeResp([b"data"], length=4)

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(model_download.DownloadCancelled):
            model_download.download_gguf("ggml-small.bin", cancel=ev)
        assert list(model_catalog.gguf_dir().iterdir()) == []
        assert model_download.active_downloads() == []

    def test_cancel_unknown_dest_is_false(self, cache):
        assert model_download.request_cancel("/nowhere/x.bin") is False


class TestObserveDownloads:
    """The cross-process progress surface: in-process registry entries
    (exact totals) + on-disk .part / huggingface .incomplete blobs
    (catalog-size estimates) - what the onboarding window and the pill
    read while the daemon downloads."""

    def test_registry_entry_visible_while_slow_download_runs(
            self, cache, monkeypatch):
        import threading
        gate = threading.Event()
        monkeypatch.setattr(urllib.request, "urlopen",
                            lambda req, timeout=None: _GatedResp(gate))
        dest = model_catalog.gguf_path("ggml-base.bin")
        err = []

        def run():
            try:
                model_download.download_gguf(
                    "ggml-base.bin",
                    progress=lambda b, tot: None)
            except BaseException as e:  # noqa: BLE001
                err.append(e)

        t = threading.Thread(target=run)
        t.start()
        deadline = time.monotonic() + 5
        try:
            while time.monotonic() < deadline:
                if model_download.active_downloads():
                    break
                time.sleep(0.02)
            obs = model_download.observe_downloads()
            assert len(obs) == 1
            d = obs[0]
            assert d.label == "ggml-base.bin" and d.kind == "whisper.cpp"
            assert d.done_bytes == len(FIRST_CHUNK)
            assert d.total_bytes is not None  # estimated from the catalog
            assert 0 <= d.percent < 100
            assert "downloading ggml-base.bin" in d.describe()
        finally:
            model_download.request_cancel(dest)
            gate.set()
            t.join(timeout=5)
        assert err  # DownloadCancelled; nothing half-written
        assert model_download.observe_downloads() == []

    def test_fw_incomplete_blob_observed_with_estimate(self, cache):
        repo = model_catalog._fw_repo_dir("Systran/faster-whisper-base")
        blobs = repo / "blobs"
        blobs.mkdir(parents=True)
        (blobs / "deadbeef.incomplete").write_bytes(b"x" * (145_000_000 // 4))
        obs = model_download.observe_downloads()
        assert [(d.label, d.kind) for d in obs] == [("base", "faster-whisper")]
        assert obs[0].total_bytes == 145_000_000
        assert obs[0].percent == 25
        assert "% of 145 MB" in obs[0].describe()

    def test_stale_part_not_observed(self, cache):
        model_catalog.gguf_dir().mkdir(parents=True)
        part = model_catalog.gguf_path("ggml-base.bin") \
            .with_name("ggml-base.bin.part")
        part.write_bytes(b"old")
        old = time.time() - model_download.STALE_PART_S - 60
        os.utime(part, (old, old))
        assert model_download.observe_downloads() == []

    def test_download_failure_leaves_no_observation(self, cache, monkeypatch):
        def fake_urlopen(req, timeout=None):
            return FakeResp([b"par", OSError("socket reset")])

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(OSError, match="socket reset"):
            model_download.download_gguf("ggml-small.bin")
        assert model_download.observe_downloads() == []
        assert model_download.active_downloads() == []


class TestModelReadiness:
    """The onboarding engine row's honest state (F-02): name resolution
    alone used to show OK with zero models on disk."""

    def _cfg(self, **model):
        from fluidvoice.config import DEFAULTS
        cfg = copy.deepcopy(DEFAULTS)
        cfg["model"].update(model)
        return cfg

    def test_fw_missing_then_downloaded(self, cache):
        cfg = self._cfg(name="base")
        r = model_download.model_readiness(cfg)
        assert (r["kind"], r["name"], r["downloaded"]) == \
            ("faster-whisper", "base", False)
        assert r["progress"] is None
        repo = model_catalog._fw_repo_dir("Systran/faster-whisper-base")
        (repo / "snapshots" / "rev").mkdir(parents=True)
        (repo / "snapshots" / "rev" / "model.bin").write_bytes(b"m")
        r = model_download.model_readiness(cfg)
        assert r["downloaded"] is True

    def test_fw_downloading_progress_attached(self, cache):
        repo = model_catalog._fw_repo_dir("Systran/faster-whisper-base")
        (repo / "blobs").mkdir(parents=True)
        (repo / "blobs" / "x.incomplete").write_bytes(b"x" * 14_500_000)
        r = model_download.model_readiness(self._cfg(name="base"))
        assert r["downloaded"] is False
        assert r["progress"] is not None and r["progress"].percent == 10

    def test_gguf_and_parakeet_and_remote(self, cache):
        r = model_download.model_readiness(
            self._cfg(backend="whisper.cpp", whispercpp_model="ggml-base.bin"))
        assert (r["kind"], r["downloaded"]) == ("whisper.cpp", False)
        model_catalog.gguf_dir().mkdir(parents=True)
        model_catalog.gguf_path("ggml-base.bin").write_bytes(b"g")
        assert model_download.model_readiness(
            self._cfg(backend="whisper.cpp",
                      whispercpp_model="ggml-base.bin"))["downloaded"]

        r = model_download.model_readiness(self._cfg(backend="parakeet"))
        assert r["kind"] == "parakeet" and r["downloaded"] is False

        r = model_download.model_readiness(
            self._cfg(remote_url="http://stt.local/v1",
                      remote_model="whisper-1"))
        assert r["kind"] == "remote" and r["downloaded"] is True


class TestFailureHint:
    """Download failure -> an actionable message (retry-safe, doctor)."""

    def test_rate_limited(self):
        err = urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None)
        hint = model_download.failure_hint(err)
        assert "rate-limit" in hint and "retry" in hint

    def test_network_problem(self):
        hint = model_download.failure_hint(
            urllib.error.URLError(OSError("unreachable")))
        assert "network" in hint and "retry is safe" in hint

    def test_cancelled(self):
        hint = model_download.failure_hint(
            model_download.DownloadCancelled("x"))
        assert "cancelled" in hint and "half-written" in hint

    def test_unknown_error_names_doctor(self):
        hint = model_download.failure_hint(ValueError("weird"))
        assert "doctor" in hint


class TestPillDownloadHook:
    """overlay.download_hint + the pill's downloading render (F-02): the
    processing pill says \"Downloading\" with a percent badge instead of
    shimmering \"Transcribing\" through a minutes-long first download."""

    def test_hint_maps_progress_to_badge(self, monkeypatch):
        import fluidvoice.model_download as md
        import fluidvoice.overlay as ov
        prog = md.DownloadProgress(label="base", kind="faster-whisper",
                                   done_bytes=50, total_bytes=100,
                                   elapsed_s=9.0)
        monkeypatch.setattr(md, "observe_downloads", lambda: [prog])
        ov._DL_HINT = (0.0, None)  # bypass the throttle cache
        assert ov.download_hint(100.0) == ("\u2193 50%", 0.5)
        # throttled: no rescan inside the window
        monkeypatch.setattr(md, "observe_downloads",
                            lambda: pytest.fail("must not rescan"))
        assert ov.download_hint(100.2) == ("\u2193 50%", 0.5)
        # and clears when the download finishes
        ov._DL_HINT = (0.0, None)
        monkeypatch.setattr(md, "observe_downloads", lambda: [])
        assert ov.download_hint(101.0) is None

    def test_hint_indeterminate_without_total(self, monkeypatch):
        import fluidvoice.model_download as md
        import fluidvoice.overlay as ov
        prog = md.DownloadProgress(label="ggml-tiny.bin",
                                   kind="whisper.cpp", done_bytes=12_000_000,
                                   total_bytes=None, elapsed_s=3.0)
        monkeypatch.setattr(md, "observe_downloads", lambda: [prog])
        ov._DL_HINT = (0.0, None)
        badge, frac = ov.download_hint(10.0)
        assert badge == "\u2193 12 MB" and frac == 0.0

    def test_downloading_renders_wider_pill_with_badge(self):
        from fluidvoice.overlay import DOWNLOADING_LABEL, PillRenderer
        r = PillRenderer(size="pill")
        w0, _, _ = r.inner_size(None, "Transcribing")
        w1, _, _ = r.inner_size(None, DOWNLOADING_LABEL)
        assert w1 > w0  # the label swap is visible in geometry alone
        img, _ = r.render([4.0] * 8, None, mode="dictate",
                          state="processing", downloading=True,
                          badge="\u2193 50%", elapsed=6.0)
        # the badge paints bright text somewhere right of center
        strip = img.crop((img.width // 2, 0, img.width, img.height))
        bright = sum(1 for x in range(0, strip.width, 2)
                     for y in range(0, strip.height, 2)
                     if strip.convert("L").getpixel((x, y)) > 150)
        assert bright > 5

    def test_downloading_requires_processing_state(self):
        from fluidvoice.overlay import PillRenderer
        r = PillRenderer(size="pill")
        # recording + downloading flag: no effect (downloads only gate the
        # processing window)
        a, _ = r.render([20.0] * 8, None, state="recording", downloading=True)
        b, _ = r.render([20.0] * 8, None, state="recording")
        assert a.tobytes() == b.tobytes()
