"""Icon assets & provider-logo mapping (macOS-parity brand icon set)."""
from __future__ import annotations

import os

import pytest

# Pillow >= 12 deprecates Image.getdata (removal in Pillow 14, 2027);
# production use lives in fluidvoice/tray.py:142 — migrate in a
# dedicated pass. Mark-level filter (NOT ini): with -W error on the
# CLI, ini filterwarnings entries lose and the gate stays red.
pytestmark = pytest.mark.filterwarnings(
    "ignore:Image.Image.getdata is deprecated:DeprecationWarning")


from fluidvoice.gtkui.logos import PROVIDERS, logo_path, provider_for


class TestBrandAssets:
    def test_bundled_app_icon_present(self):
        from importlib import resources
        base = resources.files("fluidvoice.assets")
        assert base.joinpath("icon.png").is_file()
        assert base.joinpath("icons/sayit-ermano.png").is_file()

    def test_hicolor_sizes_for_packaging(self):
        for size in (16, 32, 48, 64, 128, 256, 512):
            path = (f"packaging/icons/hicolor/{size}x{size}/apps/"
                    f"sayit-ermano.png")
            assert os.path.isfile(path), path

    def test_tray_pixmaps_use_new_icon(self):
        from fluidvoice.tray import render_pixmaps
        pm = render_pixmaps()
        assert set(pm) == {"idle", "recording"}


class TestProviderFor:
    def test_known_hosts(self):
        assert provider_for("https://api.openai.com/v1") == "openai"
        assert provider_for("https://api.groq.com/openai/v1") == "groq"
        assert provider_for("http://localhost:11434/v1") == "ollama"
        assert provider_for("http://127.0.0.1:1234/v1") == "lmstudio"
        assert provider_for("https://openrouter.ai/api/v1") == "openrouter"
        assert provider_for("https://api.x.ai/v1") == "xai"

    def test_unknown_and_empty(self):
        assert provider_for("") is None
        assert provider_for(None) is None
        assert provider_for("http://192.168.0.5:8000/v1") is None

    def test_every_provider_has_a_logo(self):
        for key, _needles in PROVIDERS:
            assert logo_path(key, dark=False), key
            assert logo_path(key, dark=True), key

    def test_dark_prefers_light_variant_when_bundled(self):
        # Ollama's mark is black-on-transparent: a white recolor is bundled
        # for dark panels and must be picked when dark=True.
        light = logo_path("ollama", dark=True)
        assert light and light.endswith("ollama-light.png")
        assert os.path.isfile(light)
        assert logo_path(None) is None
        assert logo_path("nope") is None
