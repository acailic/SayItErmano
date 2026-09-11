"""Installation-path integration: .deb build/extract/import + one-shot
installer download. Heaviest tests in the repo (minutes)."""
import os
import subprocess

import pytest

from tests.integration.conftest import REPO

pytestmark = [pytest.mark.integration, pytest.mark.slow,
             pytest.mark.needs_network]

SKIP_NET = pytest.mark.skipif(
    os.environ.get("SAYITERMANO_SKIP_NET") == "1",
    reason="SAYITERMANO_SKIP_NET=1 (no network)")


class TestDebPackage:
    def test_build_extract_and_import(self, tmp_path):
        debs = list((REPO / "dist").glob("*.deb"))
        deb = max(debs, key=lambda p: p.stat().st_mtime) if debs else None
        if deb is None or os.environ.get("SAYITERMANO_REBUILD_DEB") == "1":
            subprocess.run([str(REPO / "packaging/build-deb.sh")], check=True,
                           timeout=900)
            deb = max((REPO / "dist").glob("*.deb"),
                      key=lambda p: p.stat().st_mtime)
        assert deb.stat().st_size > 10_000_000
        root = tmp_path / "root"
        subprocess.run(["dpkg-deb", "-x", str(deb), str(root)], check=True)
        # key integration points of the installed layout
        assert (root / "usr/bin/sayit-ermano").exists()
        assert (root / "etc/xdg/autostart/sayit-ermano.desktop").exists()
        assert (root / "usr/share/applications/sayit-ermano.desktop").exists()
        assert (root / "usr/share/icons/hicolor/128x128/apps/sayit-ermano.png").exists()
        assert (root / "usr/share/icons/hicolor/512x512/apps/sayit-ermano.png").exists()
        assert (root / "usr/lib/systemd/user/sayit-ermano.service").exists()
        # the bundled venv must import the app from the relocated location
        out = subprocess.run(
            [str(root / "opt/sayit-ermano/venv/bin/python"),
             "-m", "fluidvoice", "--version"],
            capture_output=True, text=True, timeout=120)
        assert out.returncode == 0 and out.stdout.strip()


class TestOneShotInstaller:
    @SKIP_NET
    def test_dry_run_downloads_real_release(self, tmp_path):
        env = {**os.environ, "DRY_RUN": "1"}
        out = subprocess.run(
            ["bash", str(REPO / "scripts/install-one-shot.sh")],
            capture_output=True, text=True, timeout=600, env=env, cwd=tmp_path)
        assert out.returncode == 0, out.stderr
        assert "DRY_RUN=1: package ready" in out.stdout
