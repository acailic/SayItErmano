"""The shipping Flatpak manifest, structurally pinned (issue #11).

flatpak-builder does not run in CI; what CAN run offline is everything
this suite checks on the committed manifest + payload:

* finish-args are exactly the ADR-0006 baseline — no --device=all, no
  fallback-x11, no undocumented permission can slip in silently
  (reviewer pushback precedent: Vocalinux flathub PR #9368);
* every python dep the manifest installs has a vendored wheel with a
  sha256 pin (the build sandbox has no network);
* metainfo / desktop file / icons exist as proper module sources and
  agree with each other (app id, license, OARS, keywords, launchable);
* the GPU scope decision is enforced: whisper.cpp Vulkan on the GL
  extension, never CUDA (ADR-0006 decision 3);
* sounds ride the pip-installed package (package-data), and the model
  store follows flatpak's redirected XDG dirs (~/.var/app/$APP_ID).
"""
from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
PACKAGING = REPO / "packaging"
MANIFEST = PACKAGING / "io.github.acailic.SayItErmano.yml"
APP_ID = "io.github.acailic.SayItErmano"

#: ADR-0006 decision 4 — the exact baseline, no more, no less
BASELINE_FINISH_ARGS = {
    "--socket=wayland", "--socket=pulseaudio",
    "--device=dri", "--share=network",
}


@pytest.fixture(scope="module")
def manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text())


@pytest.fixture(scope="module")
def modules_by_name(manifest) -> dict:
    return {m["name"]: m for m in manifest["modules"]}


class TestFinishArgsBaseline:
    def test_exactly_the_adr_baseline(self, manifest):
        args = {a.split()[0] for a in manifest["finish-args"]}
        assert args == BASELINE_FINISH_ARGS
        assert len(manifest["finish-args"]) == len(BASELINE_FINISH_ARGS)

    def test_no_device_all_anywhere(self, manifest):
        """--device=all (uinput/ydotool) is a documented opt-in
        OVERRIDE, never a shipped permission. Comments may mention it;
        actual args may not."""
        for line in MANIFEST.read_text().splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            assert "--device=all" not in line, \
                "--device=all must stay an override, not a default"

    def test_no_undocumented_sockets_or_talks(self, manifest):
        for arg in manifest["finish-args"]:
            flag = arg.split()[0]
            assert flag in BASELINE_FINISH_ARGS, f"undocumented: {flag}"

    def test_app_id_and_runtime(self, manifest):
        assert manifest["id"] == APP_ID
        assert manifest["runtime"] == "org.gnome.Platform"
        assert manifest["command"] == "sayit-ermano"


class TestPythonDepsWheels:
    def test_every_pip_dep_has_a_pinned_wheel(self, modules_by_name):
        deps = modules_by_name["python-deps"]
        install = " ".join(deps["build-commands"])
        wheels = set(re.findall(r"([\w.-]+\.whl)", install))
        assert wheels, "no wheels found in the deps build command"
        sources = {s["path"].split("/")[-1]: s for s in deps["sources"]}
        assert set(sources) == wheels
        for name, src in sources.items():
            assert src["type"] == "file"
            assert re.fullmatch(r"[0-9a-f]{64}", src.get("sha256", "")), \
                f"{name}: missing/short sha256 pin"
            wheel = PACKAGING / src["path"]
            assert wheel.is_file(), f"{name}: wheel not vendored"
            digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
            assert digest == src["sha256"], f"{name}: stale sha256"

    def test_core_and_portal_deps_are_covered(self, modules_by_name):
        """The wheels must keep covering pyproject's runtime deps that
        the sandbox install needs (python-xlib, pillow) plus the
        ADR-0006 portal pair the input stack rides on."""
        install = " ".join(modules_by_name["python-deps"]["build-commands"])
        for expected in ("python_xlib", "pillow", "dbus_next",
                         "global_shortcut_portal"):
            assert expected in install, f"{expected} wheel dropped"

    def test_app_module_installs_no_deps(self, modules_by_name):
        """--no-deps: nothing may resolve through the network-less
        build sandbox's pip."""
        cmd = " ".join(modules_by_name["sayitermano"]["build-commands"])
        assert "--no-deps" in cmd and "pip3 install" in cmd


class TestDesktopIntegration:
    def test_module_installs_metainfo_desktop_and_icons(self,
                                                        modules_by_name):
        di = modules_by_name["desktop-integration"]
        cmds = "\n".join(di["build-commands"])
        assert "/app/share/metainfo/" in cmds
        assert "/app/share/applications/" in cmds
        icon_installs = [c for c in di["build-commands"]
                         if "/app/share/icons/hicolor/" in c]
        assert len(icon_installs) >= 6, "hicolor icon set not installed"
        assert any(c.rstrip().endswith(f"apps/{APP_ID}.png")
                   for c in icon_installs), "icons not under the app id"
        assert any("mkdir -p /app/lib/GL" in c for c in di["build-commands"])

    def test_every_payload_source_exists_with_fresh_sha(self,
                                                        modules_by_name):
        for src in modules_by_name["desktop-integration"]["sources"]:
            assert src["type"] == "file"
            path = PACKAGING / src["path"]
            assert path.is_file(), f"missing payload file: {src['path']}"
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            assert digest == src["sha256"], f"stale sha256: {src['path']}"

    def test_icon_sizes_match_the_hicolor_tree(self, modules_by_name):
        di = modules_by_name["desktop-integration"]
        manifest_sizes = {re.search(r"(\d+x\d+)/apps", s["path"]).group(1)
                          for s in di["sources"] if "/apps/" in s["path"]}
        tree_sizes = {p.parent.parent.name
                      for p in (PACKAGING / "icons/hicolor").glob(
                          "*x*/apps/sayit-ermano.png")}
        assert manifest_sizes == tree_sizes


@pytest.fixture(scope="module")
def metainfo() -> ET.Element:
    path = (PACKAGING / "flatpak" / f"{APP_ID}.metainfo.xml")
    return ET.parse(path).getroot()


class TestMetainfo:
    def test_declares_gpl3_and_appstream_type(self, metainfo):
        assert metainfo.get("type") == "desktop-application"
        assert metainfo.findtext("project_license") == "GPL-3.0-or-later"
        assert metainfo.findtext("metadata_license") == "CC0-1.0"

    def test_launchable_matches_the_desktop_file(self, metainfo):
        launchable = metainfo.find("launchable")
        assert launchable.get("type") == "desktop-id"
        assert launchable.text == f"{APP_ID}.desktop"

    def test_oars_rating_present(self, metainfo):
        rating = metainfo.find("content_rating")
        assert rating is not None and rating.get("type") == "oars-1.1"

    def test_keywords_and_categories_match_the_desktop_file(self, metainfo):
        desktop = (PACKAGING / "flatpak" / f"{APP_ID}.desktop").read_text()

        def entries(block, lower):
            line = next(l for l in desktop.splitlines() if l.startswith(block))
            values = {v.strip().rstrip(";")
                      for v in line.split("=", 1)[1].split(";") if v.strip()}
            return {v.lower() for v in values} if lower else values

        kw = {k.text.strip().lower() for k in metainfo.iter("keyword")}
        assert kw == entries("Keywords", lower=True)
        cats = {c.text.strip() for c in metainfo.iter("category")}
        assert cats == entries("Categories", lower=False)

    def test_releases_have_versions_and_dates(self, metainfo):
        releases = metainfo.findall("releases/release")
        assert releases, "appstream needs at least one release entry"
        for rel in releases:
            assert rel.get("version") and rel.get("date")

    def test_desktop_file_conventions(self):
        text = (PACKAGING / "flatpak" / f"{APP_ID}.desktop").read_text()
        assert f"Icon={APP_ID}" in text
        assert "Type=Application" in text
        exec_line = next(l for l in text.splitlines()
                         if l.startswith("Exec="))
        assert exec_line.startswith("Exec=sayit-ermano")  # in-sandbox cmd
        assert "/usr/bin" not in exec_line  # no host prefix survives export


class TestGpuScope:
    def test_whisper_cpp_vulkan_not_cuda(self, modules_by_name):
        wc = modules_by_name["whisper-cpp"]
        assert wc["buildsystem"] in ("cmake", "cmake-ninja")
        opts = " ".join(wc["config-opts"])
        assert "-DGGML_VULKAN=ON" in opts
        assert "CUDA" not in opts.upper(), \
            "CUDA is a host-build-only path (ADR-0006 decision 3)"
        assert wc["sources"][0]["url"].endswith("whisper.cpp.git")
        assert wc["sources"][0].get("commit"), "deploys need pinned commits"

    def test_gl_extension_mount_is_declared(self, manifest):
        gl = manifest.get("add-extensions", {}).get(
            "org.freedesktop.Platform.GL")
        assert gl and gl.get("directory") == "lib/GL"


class TestSandboxPayload:
    def test_sounds_ride_the_package_data(self):
        """The start/stop sfx must land in the sandbox image via the
        pip-installed package — package-data is the single source."""
        pyproject = (REPO / "pyproject.toml").read_text()
        assert "assets/sfx/*" in pyproject
        sfx = sorted((REPO / "fluidvoice/assets/sfx").glob("*.m4a"))
        assert len(sfx) == 2, "start/stop sounds expected"

    def test_models_follow_the_flatpak_home(self, monkeypatch, tmp_path):
        """paths.py must follow flatpak's redirected XDG dirs, putting
        downloaded models under ~/.var/app/$APP_ID (issue #11 AC)."""
        var_app = tmp_path / ".var/app" / APP_ID
        monkeypatch.setenv("XDG_CACHE_HOME", str(var_app / "cache"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(var_app / "config"))
        monkeypatch.setenv("XDG_DATA_HOME", str(var_app / "data"))
        from fluidvoice import paths
        assert paths.models_dir().is_relative_to(var_app)
        assert paths.cache_dir().name == "sayit-ermano"

    def test_manifest_documents_the_lifecycle(self):
        """The no-systemd lifecycle (app-owned daemon + Background
        portal autostart) is stated where a reviewer will read it."""
        text = MANIFEST.read_text()
        assert "daemon_owner" in text and "portal_autostart" in text
