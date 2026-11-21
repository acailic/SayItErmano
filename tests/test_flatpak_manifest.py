"""Structural gates for the SHIPPING Flatpak manifest (#11, ADR-0006).

flatpak-builder does not run here (no flatpak on this runner, and the
Flathub build happens in CI) — so these tests pin the manifest's
REVIEWER-FACING contract instead:

* finish-args carry exactly the ADR-0006 permission baseline
  (--socket=wayland, --socket=pulseaudio, --device=dri,
  --share=network) — no --device=all, no extra sockets/names;
* desktop integration ships as proper module sources (desktop file
  under the app-id name, appstream metainfo, hicolor icons);
* every python dependency is accounted for: sha256-pinned vendored
  wheels for the pure-Python set, with engines explicitly listed as
  the documented not-vendored remainder;
* metainfo: license matches the source, OARS rating, keywords, and a
  release equal to the shipped version.

The manifest is parsed with a minimal YAML-subset parser below (the
dev environment has no PyYAML; flatpak-builder manifests are a small,
regular subset — nested mappings, lists, string scalars). The parser
fails loudly on syntax it does not understand, so manifest drift away
from the supported shape breaks tests rather than silently passing.
"""
from __future__ import annotations

import configparser
import hashlib
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PACKAGING = REPO / "packaging"
MANIFEST_PATH = PACKAGING / "io.github.acailic.SayItErmano.yml"
FLATPAK_DIR = PACKAGING / "flatpak"

APP_ID = "io.github.acailic.SayItErmano"
DESKTOP_NAME = f"{APP_ID}.desktop"
METAINFO_NAME = f"{APP_ID}.metainfo.xml"

# ADR-0006 decision 4 — the reviewed baseline, and nothing else.
BASELINE = {
    "--socket=wayland",
    "--socket=pulseaudio",
    "--device=dri",
    "--share=network",
}
FORBIDDEN_PREFIXES = (
    "--device=all",
    "--talk-name",
    "--own-name",
    "--filesystem",
    "--socket=system-bus",
    "--socket=fallback-x11",
    "--share=ipc",
)


# -- minimal YAML-subset parser -------------------------------------------


def _strip_comment(line: str) -> str:
    out, quote = [], ""
    for ch in line:
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            break
        out.append(ch)
    return "".join(out)


def _scalar(text: str):
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _parse_block(lines: list[tuple[int, str]], i: int, indent: int):
    """Parse the block starting at lines[i] whose items sit at `indent`."""
    if i < len(lines) and lines[i][1].startswith("- "):
        items = []
        while i < len(lines) and lines[i][0] == indent \
                and lines[i][1].startswith("- "):
            content = lines[i][1][2:]
            if ":" in content:
                # "- key: value" opens a nested mapping at indent + 2;
                # the dash becomes the item's first key line
                sub = [(indent + 2, content)]
                i += 1
                while i < len(lines) and lines[i][0] > indent:
                    sub.append(lines[i])
                    i += 1
                value, _end = _parse_block(sub, 0, indent + 2)
                items.append(value)
            else:
                items.append(_scalar(content))
                i += 1
        return items, i
    mapping = {}
    while i < len(lines) and lines[i][0] == indent \
            and not lines[i][1].startswith("- "):
        key, sep, rest = lines[i][1].partition(":")
        assert sep, f"manifest: not a key line: {lines[i][1]!r}"
        key, rest = key.strip(), rest.strip()
        if rest:
            mapping[key] = _scalar(rest)
            i += 1
        elif i + 1 < len(lines) and lines[i + 1][0] > indent:
            mapping[key], i = _parse_block(lines, i + 1, lines[i + 1][0])
        else:
            mapping[key] = None
            i += 1
    return mapping, i


def parse_manifest(text: str) -> dict:
    lines = []
    for raw in text.splitlines():
        line = _strip_comment(raw).rstrip()
        if not line.strip():
            continue
        lines.append((len(line) - len(line.lstrip(" ")), line.strip()))
    assert lines, "manifest is empty"
    assert lines[0][0] == 0, "manifest must start at zero indentation"
    value, i = _parse_block(lines, 0, 0)
    assert i == len(lines), "manifest has unbalanced indentation"
    return value


@pytest.fixture(scope="module")
def manifest() -> dict:
    return parse_manifest(MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def modules(manifest) -> dict[str, dict]:
    mods = manifest.get("modules") or []
    out = {}
    for mod in mods:
        assert isinstance(mod, dict) and "name" in mod, mod
        assert mod["name"] not in out, f"duplicate module {mod['name']}"
        out[mod["name"]] = mod
    return out


# -- finish-args: the reviewed baseline, exactly ---------------------------


def test_finish_args_are_exactly_the_adr_baseline(manifest):
    finish = manifest["finish-args"]
    assert isinstance(finish, list) and finish
    permissions = {a for a in finish if not a.startswith("--env=")}
    assert permissions == BASELINE


def test_no_permission_families_beyond_the_baseline(manifest):
    for arg in manifest["finish-args"]:
        assert not arg.startswith(FORBIDDEN_PREFIXES), arg
        if arg.startswith("--socket="):
            assert arg in ("--socket=wayland", "--socket=pulseaudio"), arg


def test_only_extra_finish_arg_is_the_flatpak_marker(manifest):
    envs = [a for a in manifest["finish-args"] if a.startswith("--env=")]
    assert envs == ["--env=SAYITERMANO_FLATPAK=1"]


def test_identity_and_runtime(manifest):
    assert manifest["id"] == APP_ID  # com.github.* ids are rejected
    assert manifest["runtime"] == "org.gnome.Platform"
    assert manifest["sdk"] == "org.gnome.Sdk"
    assert manifest["runtime-version"] == "48"
    # command must resolve inside /app — the pip console script
    assert manifest["command"] == "sayit-ermano"
    pyproject = tomllib.loads(
        (REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["scripts"]["sayit-ermano"]


# -- desktop-integration module -------------------------------------------


def test_data_module_installs_metainfo_desktop_and_icons(modules):
    data = modules["sayitermano-data"]
    sources = data["sources"]
    paths = [s["path"] for s in sources]
    assert f"flatpak/{DESKTOP_NAME}" in paths
    assert f"flatpak/{METAINFO_NAME}" in paths
    assert any(s.get("type") == "dir" and s["path"] == "icons"
               for s in sources), "icons must ship as a dir source"
    cmds = "\n".join(data["build-commands"])
    assert "/app/share/applications" in cmds
    assert "/app/share/metainfo" in cmds
    for size in (16, 32, 48, 64, 128, 256, 512):
        target = f"/app/share/icons/hicolor/{size}x{size}/apps/sayit-ermano.png"
        assert target in cmds, f"icon install missing: {target}"
        assert (PACKAGING / "icons" / "hicolor" / f"{size}x{size}"
                / "apps" / "sayit-ermano.png").is_file()


def test_every_pinned_source_exists_and_matches_its_sha256(modules):
    for mod in modules.values():
        for src in mod.get("sources") or []:
            if "path" not in src:  # remote sources (git) have url/branch
                continue
            path = PACKAGING / src["path"]
            assert path.is_file() or path.is_dir(), \
                f"source missing on disk: {src['path']}"
            if src.get("type") == "file" and "sha256" in src:
                assert src["sha256"] == hashlib.sha256(
                    path.read_bytes()).hexdigest(), \
                    f"sha256 drift for {src['path']} — re-pin after editing"


def test_app_module_builds_offline_from_the_git_checkout(modules):
    app = modules["sayitermano"]
    cmds = "\n".join(app["build-commands"])
    assert "pip3 install" in cmds
    assert "--no-build-isolation" in cmds  # build sandbox has no network
    assert "--no-deps" in cmds             # deps are the vendored wheels
    srcs = app["sources"]
    assert len(srcs) == 1 and srcs[0]["type"] == "git"
    assert srcs[0]["url"] == "https://github.com/acailic/SayItErmano.git"


# -- python dependency accounting ------------------------------------------


VENDORED_WHEELS = {
    "python-xlib": "python_xlib-0.33-py2.py3-none-any.whl",
    "pillow": "pillow-13.0.0-cp312-cp312-manylinux_2_27_x86_64"
              ".manylinux_2_28_x86_64.whl",
    "dbus-next": "dbus_next-0.2.3-py3-none-any.whl",
    "global-shortcut-portal": "global_shortcut_portal-0.1.0-py3-none-any.whl",
}
# The engine chain (ADR-0006: whisper.cpp Vulkan / faster-whisper) is
# deliberately not vendored in this slice — documented in
# docs/packaging/flatpak-permissions.md ("Engine scope").
NOT_VENDORED = {"faster-whisper"}


def _normalize(name: str) -> str:
    return name.lower().replace("_", "-")


def _dep_name(requirement: str) -> str:
    """`python-xlib>=0.33` -> `python-xlib`."""
    import re
    return re.split(r"[<>=!~; \[]", requirement, maxsplit=1)[0].strip()


def test_every_python_dep_is_vendored_or_explicitly_deferred(modules):
    pyproject = tomllib.loads(
        (REPO / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]
    deps = {_dep_name(d) for d in project["dependencies"]}
    deps |= {_dep_name(d)
             for d in project["optional-dependencies"]["portal"]}
    assert deps == set(VENDORED_WHEELS) | NOT_VENDORED, (
        "new python dep: vendor a pinned wheel or extend NOT_VENDORED "
        "with the permissions-doc note")

    deps_module = modules["deps"]
    wheels = {s["path"].split("/")[-1]
              for s in deps_module["sources"] if s.get("type") == "file"}
    assert wheels == set(VENDORED_WHEELS.values())
    # the pip install line consumes exactly the vendored wheels
    for wheel in VENDORED_WHEELS.values():
        assert wheel in " ".join(deps_module["build-commands"])


def test_wheels_are_hash_pinned(modules):
    for src in modules["deps"]["sources"]:
        assert "sha256" in src, f"unpinned wheel: {src['path']}"


def test_sounds_ship_as_package_data():
    """The start/stop sounds are NOT /app/share/sounds installs: they
    ride the wheel as package data (same as the deb)."""
    pyproject = tomllib.loads(
        (REPO / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = pyproject["tool"]["setuptools"]["package-data"]["fluidvoice"]
    assert "assets/sfx/*" in package_data
    sfx = list((REPO / "fluidvoice" / "assets" / "sfx").glob("*.m4a"))
    assert sfx, "the sfx package-data glob matches nothing"


# -- metainfo + desktop file -----------------------------------------------


def _metainfo_root() -> ET.Element:
    return ET.fromstring(
        (FLATPAK_DIR / METAINFO_NAME).read_text(encoding="utf-8"))


def test_metainfo_declares_the_source_license():
    root = _metainfo_root()
    assert root.tag == "component"
    assert root.findtext("metadata_license")
    pyproject = tomllib.loads(
        (REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert root.findtext("project_license") \
        == pyproject["project"]["license"]["text"]


def test_metainfo_has_oars_keywords_and_current_release():
    root = _metainfo_root()
    rating = root.find("content_rating")
    assert rating is not None and rating.get("type") == "oars-1.1"
    keywords = {k.text for k in root.findall("keywords/keyword")}
    assert {"voice", "dictation", "speech", "whisper"} <= keywords
    pyproject = tomllib.loads(
        (REPO / "pyproject.toml").read_text(encoding="utf-8"))
    releases = root.findall("releases/release")
    assert releases, "no <release> — software centers need at least one"
    assert releases[0].get("version") == pyproject["project"]["version"]


def test_metainfo_launchable_matches_the_exported_desktop_id():
    root = _metainfo_root()
    launchable = root.find("launchable")
    assert launchable is not None \
        and launchable.get("type") == "desktop-id" \
        and launchable.text == DESKTOP_NAME
    assert (FLATPAK_DIR / DESKTOP_NAME).is_file()


def test_desktop_file_uses_the_in_sandbox_command():
    parser = configparser.ConfigParser(interpolation=None)
    read = parser.read(FLATPAK_DIR / DESKTOP_NAME)
    assert read, f"unparseable desktop file: {DESKTOP_NAME}"
    entry = parser["Desktop Entry"]
    assert entry["Type"] == "Application"
    # runs the /app/bin console script — no host paths, no flatpak-run
    # indirection inside the sandbox
    assert entry["Exec"] == "sayit-ermano settings"
    assert entry["Icon"] == "sayit-ermano"
    assert entry["Terminal"] == "false"
    categories = entry["Categories"].split(";")
    assert "Utility" in categories and "Accessibility" in categories


# -- cross-file wiring the manifest depends on ------------------------------


def test_flatpak_marker_gates_the_app_owned_daemon():
    """The --env marker exists because the GTK app owns the daemon in
    the sandbox — the wiring must stay (settings/history reach the
    daemon over the in-sandbox control socket)."""
    app_src = (REPO / "fluidvoice" / "gtkui" / "application.py").read_text()
    assert "_ensure_sandbox_daemon" in app_src
    assert "ensure_daemon" in app_src
    owner_src = (REPO / "fluidvoice" / "daemon_owner.py").read_text()
    assert "SAYITERMANO_FLATPAK" in owner_src


def test_permissions_doc_justifies_every_baseline_arg():
    doc = (REPO / "docs" / "packaging" / "flatpak-permissions.md")
    text = doc.read_text(encoding="utf-8")
    for arg in BASELINE:
        assert arg.split("=", 1)[1] in text, \
            f"{arg}: justification missing from {doc}"
    assert "--device=all" in text  # documented as the opt-in override
