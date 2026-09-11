"""Parakeet ONNX backend against the real downloaded model (integration).

Downloads `parakeet-tdt-0.6b-v2` through the production download path on
first run (~482 MB once, cached under models_dir()/parakeet/); skips when
onnxruntime is missing or the network is unavailable. The bundled fixture
is the sherpa-onnx release's own test wav (Apache-2.0), so the golden
sentence is a real end-to-end speech check."""
from __future__ import annotations

from pathlib import Path

import pytest

from fluidvoice import model_catalog

pytestmark = [pytest.mark.integration, pytest.mark.needs_model]

FIXTURE = Path(__file__).parent / "fixtures" / "parakeet_v2_0.wav"
GOLDEN = ("Well, I don't wish it any more, observed Phebe, turning away her "
          "eyes. It is certainly very like the old portrait.")


@pytest.fixture(scope="module")
def parakeet_v2():
    pytest.importorskip("onnxruntime")
    name = "parakeet-tdt-0.6b-v2"
    if not model_catalog.parakeet_downloaded(name):
        from fluidvoice import model_download
        try:
            model_download.download_parakeet(name)
        except OSError as e:  # network down / checksum trouble
            pytest.skip(f"cannot download {name}: {e}")
    from fluidvoice import backends
    from fluidvoice.config import DEFAULTS
    cfg = {"model": dict(DEFAULTS["model"], backend="parakeet", name=name),
           "general": DEFAULTS["general"]}
    be = backends.load_backend(cfg)
    be.warmup()
    return be


def test_golden_transcript(parakeet_v2):
    out = parakeet_v2.transcribe(FIXTURE)
    assert out["text"] == GOLDEN, out
    assert out["duration"] == pytest.approx(7.435, abs=0.01)
    assert out["segments"][0]["start"] == 0.0
    assert out["segments"][0]["end"] == pytest.approx(7.435, abs=0.01)
