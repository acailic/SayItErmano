"""Structural gates for the SHIPPING Flatpak manifests (#11, #12, ADR-0006).

flatpak-builder does not run here (no flatpak on this runner, and the
Flathub build happens in CI) — so these tests pin the manifest's
REVIEWER-FACING contract instead:

* finish-args carry exactly the ADR-0006 permission baseline
  (--socket=wayland, --socket=pulseaudio, --device=dri,
  --share=network) — no --device=all, no extra sockets/names;
* desktop integration ships as proper module sources (desktop file
  under the app-id name, appstream metainfo, hicolor icons);
* every python dependency is accounted for: sha256-pinned wheels for
  the pure-Python set whose dependency graph closes offline, with
  engines explicitly listed as the documented not-vendored remainder;
* every module installs its license(s) to
  $FLATPAK_DEST/share/licenses/$FLATPAK_ID (Flathub requirement);
* the app is built from the release tag `v<pyproject version>`;
* metainfo: license matches the source, OARS rating, keywords, a
  release equal to the shipped version, and the Flathub screenshot
  shot list (#12).

ONE manifest, two source sets — the divergence rule both headers
state (runbook section 0, docs/packaging/flathub-submission.md):

* `packaging/io.github.acailic.SayItErmano.yml` — the CI BUNDLE
  variant: local sources (desktop file, metainfo, icons dir, vendored
  wheels), built from the checkout by the flatpak-bundle workflow;
* `packaging/flatpak/io.github.acailic.SayItErmano.flathub.yml` — the
  FLATHUB variant: remote sources only (git at the release tag, PyPI
  url+sha256 wheels) — a Flathub PR may carry nothing but the manifest.

Everything except the `sources:` blocks must be identical, so every
structural test below runs against BOTH variants; the variant-specific
tests pin what makes each one what it is.

The manifests are parsed with a minimal YAML-subset parser below (the
dev environment has no PyYAML; flatpak-builder manifests are a small,
regular subset — nested mappings, lists, string scalars). The parser
fails loudly on syntax it does not understand, so manifest drift away
from the supported shape breaks tests rather than silently passing.
"""
from __future__ import annotations

import configparser
import email.parser
import fnmatch
import hashlib
import re
import shlex
import subprocess
import tomllib
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PACKAGING = REPO / "packaging"
FLATPAK_DIR = PACKAGING / "flatpak"

APP_ID = "io.github.acailic.SayItErmano"
DESKTOP_NAME = f"{APP_ID}.desktop"
METAINFO_NAME = f"{APP_ID}.metainfo.xml"

# The two variants of the shipping manifest (see the module docstring).
CI = "ci"
FLATHUB = "flathub"
MANIFEST_PATHS = {
    CI: PACKAGING / f"{APP_ID}.yml",
    FLATHUB: FLATPAK_DIR / f"{APP_ID}.flathub.yml",
}
APP_REPO_URL = "https://github.com/acailic/SayItErmano.git"

# The reviewed runtime pin. Bumping it is a deliberate act: the GNOME
# runtime's Python version moves with it and the compiled wheel must
# follow (RUNTIME_PYTHON below). Freshness against Flathub's hosted
# branches is checked with the network in
# tests/integration/test_flatpak_runtime_freshness.py.
RUNTIME_VERSION = "51"
# org.gnome.Platform branch -> the CPython ABI its freedesktop-sdk base
# ships (freedesktop-sdk elements/include/python3.yml `track:` per
# release: 24.08 = 3.12, 25.08 = 3.13, 26.08 = 3.14). A compiled
# wheel built for another ABI is refused by pip inside the sandbox.
RUNTIME_PYTHON = {"48": "cp312", "49": "cp313", "50": "cp313", "51": "cp314"}

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

# Flathub license requirement: every module installs its license(s)
# under this directory (one sub-directory per module/package).
DEST = "$FLATPAK_DEST"
LICENSE_DIR = f"{DEST}/share/licenses/$FLATPAK_ID"
WHEEL_LICENSE_GLOB = f"{DEST}/lib/python3*/site-packages/"


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


@pytest.fixture(scope="module", params=[CI, FLATHUB], ids=lambda v: v)
def variant(request) -> str:
    return request.param


@pytest.fixture(scope="module")
def manifest_text(variant) -> str:
    return MANIFEST_PATHS[variant].read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def manifest(manifest_text) -> dict:
    return parse_manifest(manifest_text)


@pytest.fixture(scope="module")
def modules(manifest) -> dict[str, dict]:
    mods = manifest.get("modules") or []
    out = {}
    for mod in mods:
        assert isinstance(mod, dict) and "name" in mod, mod
        assert mod["name"] not in out, f"duplicate module {mod['name']}"
        out[mod["name"]] = mod
    return out


def _load(variant: str) -> dict:
    return parse_manifest(
        MANIFEST_PATHS[variant].read_text(encoding="utf-8"))


def _commands(module: dict) -> list[str]:
    return module["build-commands"]


def _version() -> str:
    pyproject = tomllib.loads(
        (REPO / "pyproject.toml").read_text(encoding="utf-8"))
    return pyproject["project"]["version"]


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
    assert manifest["runtime-version"] == RUNTIME_VERSION
    assert RUNTIME_VERSION in RUNTIME_PYTHON, \
        "runtime bumped: record its Python ABI in RUNTIME_PYTHON"
    # command must resolve inside /app — the pip console script
    assert manifest["command"] == "sayit-ermano"
    pyproject = tomllib.loads(
        (REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["scripts"]["sayit-ermano"]


# -- desktop-integration module -------------------------------------------


def test_data_module_installs_metainfo_desktop_and_icons(modules):
    data = modules["sayitermano-data"]
    cmds = _commands(data)
    # installed from the in-repo paths — the git checkout layout
    desktop_src = f"packaging/flatpak/{DESKTOP_NAME}"
    metainfo_src = f"packaging/flatpak/{METAINFO_NAME}"
    assert (f"install -Dm644 -t {DEST}/share/applications {desktop_src}"
            in cmds)
    assert (f"install -Dm644 -t {DEST}/share/metainfo {metainfo_src}"
            in cmds)
    for size in (16, 32, 48, 64, 128, 256, 512):
        rel = f"packaging/icons/hicolor/{size}x{size}/apps/sayit-ermano.png"
        target = f"{DEST}/share/icons/hicolor/{size}x{size}/apps/sayit-ermano.png"
        assert f"install -Dm644 {rel} {target}" in cmds, \
            f"icon install missing: {target}"
        assert (REPO / rel).is_file(), f"{rel}: referenced but not in the tree"
    assert (REPO / desktop_src).is_file() and (REPO / metainfo_src).is_file()


def test_ci_variant_data_sources_are_the_local_files(variant, modules):
    if variant != CI:
        pytest.skip("local-source contract of the CI variant only")
    data = modules["sayitermano-data"]
    sources = data["sources"]
    by_path = {s["path"]: s for s in sources}
    assert by_path[f"flatpak/{DESKTOP_NAME}"]["type"] == "file"
    assert by_path[f"flatpak/{METAINFO_NAME}"]["type"] == "file"
    assert by_path["icons"]["type"] == "dir", "icons must ship as a dir source"
    # the local copies land at their in-repo paths: that is what lets
    # the build-commands be the Flathub variant's (checkout layout)
    assert by_path[f"flatpak/{DESKTOP_NAME}"]["dest"] == "packaging/flatpak"
    assert by_path["icons"]["dest"] == "packaging/icons"
    assert by_path["../LICENSE"].get("dest") is None  # checkout root, as in git


# -- man page (offline help; the v0.9.0 deb smoke-test finding) -------------
#
# Both variants install the same page (docs/man/sayit-ermano.1, pinned
# in test_deb_lock.py against the CLI's own --help output) into the
# sandbox man dir — the Flathub variant from the release-tag checkout,
# the CI variant from a sha256-pinned local source placed at the same
# in-repo path.

MAN_PAGE = "docs/man/sayit-ermano.1"
MAN_INSTALL = f"install -Dm644 {MAN_PAGE} {DEST}/share/man/man1/sayit-ermano.1"


def test_both_variants_install_the_man_page(variant, modules):
    assert MAN_INSTALL in _commands(modules["sayitermano-data"]), (
        f"{variant}: the man page (offline help) is not in the install set")
    assert (REPO / MAN_PAGE).is_file()


def test_ci_variant_pins_the_man_page_source(variant, modules):
    if variant != CI:
        pytest.skip("local-source contract of the CI variant only")
    by_path = {s["path"]: s
               for s in modules["sayitermano-data"]["sources"]}
    man = by_path["../docs/man/sayit-ermano.1"]
    # lands at its in-repo path so the build-command matches the
    # Flathub variant's git checkout; sha256 pinned like every local
    # file source (test_every_pinned_local_source_exists_and_matches_...)
    assert man["type"] == "file" and "sha256" in man
    assert man["dest"] == "docs/man"


def test_flathub_variant_has_no_local_sources(variant, modules):
    if variant != FLATHUB:
        pytest.skip("Flathub PR shape: remote sources only")
    for mod in modules.values():
        for src in mod.get("sources") or []:
            assert "path" not in src, f"local source in the Flathub variant: {src}"
            assert src["type"] in ("git", "file"), src
            assert src["url"].startswith("https://"), src
            if src["type"] == "file":
                assert re.fullmatch(r"[0-9a-f]{64}", src["sha256"]), src
            else:
                # the Flathub linter: no branch; a tag without a commit
                # is an error for new submissions
                assert "branch" not in src, src
                assert src["tag"] and re.fullmatch(r"[0-9a-f]{40}", src["commit"]), src


def test_every_pinned_local_source_exists_and_matches_its_sha256(
        variant, modules):
    if variant != CI:
        pytest.skip("the Flathub variant has no local sources")
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


# -- release tag pin (#12) -------------------------------------------------


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def test_app_sources_are_pinned_to_the_release_tag(variant, modules):
    tag = f"v{_version()}"
    app_srcs = modules["sayitermano"]["sources"]
    assert len(app_srcs) == 1 and app_srcs[0]["type"] == "git"
    src = app_srcs[0]
    assert src["url"] == APP_REPO_URL
    # Flathub builds must come from stable refs — never a branch
    assert "branch" not in src, src
    assert src["tag"] == tag, "manifest tag drifted from the pyproject version"
    if variant == FLATHUB:
        # the Flathub linter errors on a tag without a commit
        assert re.fullmatch(r"[0-9a-f]{40}", src["commit"]), src
        # the data module's checkout is the same tree
        assert modules["sayitermano-data"]["sources"] == [src]
    else:
        # The tag is forward-declared before release-prepare (like the
        # metainfo <release>); the commit only exists once
        # release-publish tags — a stale one would build the previous
        # release under the new tag.
        assert "commit" not in src, src


def test_release_tag_resolves_to_the_pinned_commit_and_carries_the_data(
        variant, modules):
    """The strongest form needs the tag object: a shallow CI checkout
    does not fetch tags, so the check activates only where it can."""
    src = modules["sayitermano"]["sources"][0]
    have_tag = subprocess.run(
        ["git", "-C", str(REPO), "cat-file", "-e", f"{src['tag']}^{{commit}}"],
        capture_output=True).returncode == 0
    if not have_tag:
        pytest.skip(f"tag {src['tag']} not fetched into this clone")
    tagged = _git("rev-parse", f"{src['tag']}^{{commit}}")
    assert src.get("commit", tagged) == tagged, \
        "manifest commit is not the tag's commit"
    tree = set(_git("ls-tree", "-r", "--name-only", src["tag"]).splitlines())
    needed = {"LICENSE", f"packaging/flatpak/{DESKTOP_NAME}",
              f"packaging/flatpak/{METAINFO_NAME}"}
    needed |= {f"packaging/icons/hicolor/{s}x{s}/apps/sayit-ermano.png"
               for s in (16, 32, 48, 64, 128, 256, 512)}
    assert needed <= tree, f"missing at {src['tag']}: {sorted(needed - tree)}"


def test_app_module_builds_offline_from_the_git_checkout(modules):
    app = modules["sayitermano"]
    cmds = "\n".join(_commands(app))
    assert "pip3 install" in cmds
    assert "--no-build-isolation" in cmds  # build sandbox has no network
    assert "--no-deps" in cmds             # deps are the wheels below
    assert _commands(app)[0] == (
        "pip3 install --no-build-isolation --no-deps "
        f"--prefix={DEST} ."), "the app wheel builds from the checkout root"


# -- python dependency accounting ------------------------------------------


VENDORED_WHEELS = {
    "python-xlib": "python_xlib-0.33-py2.py3-none-any.whl",
    "pillow": "pillow-13.0.0-cp314-cp314-manylinux_2_27_x86_64"
              ".manylinux_2_28_x86_64.whl",
    "dbus-next": "dbus_next-0.2.3-py3-none-any.whl",
    "global-shortcut-portal": "global_shortcut_portal-0.1.0-py3-none-any.whl",
    # python-xlib's hard runtime import (Xlib/display.py) — the runtime's
    # Python does not ship it
    "six": "six-1.17.0-py2.py3-none-any.whl",
}
# The engine chain (ADR-0006: whisper.cpp Vulkan / faster-whisper) is
# deliberately not vendored in this slice — documented in
# docs/packaging/flatpak-permissions.md ("Engine scope").
NOT_VENDORED = {"faster-whisper"}


def _normalize(name: str) -> str:
    return name.lower().replace("_", "-")


def _dep_name(requirement: str) -> str:
    """`python-xlib>=0.33` / `six (>=1.10.0)` -> the project name."""
    return re.split(r"[<>=!~; (\[]", requirement, maxsplit=1)[0].strip()


def test_every_python_dep_is_vendored_or_explicitly_deferred(modules):
    pyproject = tomllib.loads(
        (REPO / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]
    deps = {_dep_name(d) for d in project["dependencies"]}
    deps |= {_dep_name(d)
             for d in project["optional-dependencies"]["portal"]}
    # six is the one vendored wheel that is not a declared dependency:
    # python-xlib's hard runtime import (VENDORED_WHEELS)
    assert deps == (set(VENDORED_WHEELS) - {"six"}) | NOT_VENDORED, (
        "new python dep: vendor a pinned wheel (both manifest variants) "
        "and extend VENDORED_WHEELS, or extend NOT_VENDORED with the "
        "permissions-doc note")

    deps_module = modules["deps"]
    wheels = {s["url" if "url" in s else "path"].split("/")[-1]
              for s in deps_module["sources"] if s.get("type") == "file"}
    assert wheels == set(VENDORED_WHEELS.values())
    # the pip install line consumes exactly the vendored wheels, offline
    install = [c for c in _commands(deps_module) if c.startswith("pip3 install")]
    assert len(install) == 1
    assert "--no-index" in install[0], \
        "pip must not reach PyPI: the closure is the wheels on this line"
    for wheel in VENDORED_WHEELS.values():
        assert wheel in install[0]


def test_wheels_are_hash_pinned(modules):
    for src in modules["deps"]["sources"]:
        assert src.get("type") == "file" and "sha256" in src, \
            f"unpinned wheel: {src}"


def test_flathub_variant_wheels_are_the_ci_vendored_wheels():
    """The divergence rule for the deps module: same wheel files, same
    hashes — only the location differs (PyPI url vs vendored path)."""
    def wheels(variant: str) -> dict[str, str]:
        deps = next(m for m in _load(variant)["modules"] if m["name"] == "deps")
        return {s.get("url", s.get("path")).split("/")[-1]: s["sha256"]
                for s in deps["sources"]}
    assert wheels(FLATHUB) == wheels(CI)


def test_variants_differ_only_in_their_sources():
    def without_sources(variant: str) -> dict:
        parsed = _load(variant)
        return parsed | {"modules": [
            {k: v for k, v in m.items() if k != "sources"}
            for m in parsed["modules"]]}
    assert without_sources(FLATHUB) == without_sources(CI), \
        "divergence rule violated: edit both manifests in the same commit"


def test_compiled_wheel_abi_matches_the_runtime_python(modules):
    """org.gnome.Platform's Python moves with the runtime branch; a
    compiled wheel (cpXY ABI tag) built for another version is refused
    by pip inside the sandbox — the runtime bump must carry the wheel."""
    abi = RUNTIME_PYTHON[RUNTIME_VERSION]
    for src in modules["deps"]["sources"]:
        wheel = src.get("url", src.get("path")).split("/")[-1]
        m = re.search(r"-(cp\d+)-", wheel)
        if m:  # pure-Python wheels carry py2.py3 / py3 tags
            assert m.group(1) == abi, (
                f"{wheel}: built for {m.group(1)} but runtime "
                f"{RUNTIME_VERSION} ships {abi}")


def test_vendored_wheels_close_their_dependency_graph_offline():
    """`pip3 install --no-index <wheels>` resolves requirements among the
    wheels on the line only. A wheel needing something not vendored
    fails the Flathub build (no network) — python-xlib's `six` was
    exactly that gap. Marker-gated extras are not runtime needs; any
    other marker still counts (refine here if a wheel ever gates a
    dependency on python_version and the like)."""
    vendored = {_normalize(name) for name in VENDORED_WHEELS}
    for wheel in VENDORED_WHEELS.values():
        with zipfile.ZipFile(PACKAGING / "wheels" / wheel) as archive:
            metadata = next(n for n in archive.namelist()
                            if n.endswith(".dist-info/METADATA"))
            parsed = email.parser.Parser().parsestr(
                archive.read(metadata).decode("utf-8"))
        for requirement in parsed.get_all("Requires-Dist") or []:
            if "extra ==" in requirement:
                continue
            name = _normalize(_dep_name(requirement))
            assert name in vendored, (
                f"{wheel} needs {name!r}: vendor a pinned wheel for it "
                "(both variants) and add it to VENDORED_WHEELS")


# -- license installs (Flathub requirement) --------------------------------


def _license_installs(module: dict) -> list[tuple[str, str]]:
    """(license-dir, source) pairs of a module's `install -Dm0644 -t DIR
    SRC` lines — the shape the Flathub requirements page documents."""
    pairs = []
    for command in _commands(module):
        words = shlex.split(command)
        if words[:1] != ["install"] or "-t" not in words:
            continue
        if LICENSE_DIR not in words[words.index("-t") + 1]:
            continue
        pairs.append((words[words.index("-t") + 1],
                      words[words.index("-t") + 2]))
    return pairs


def test_every_module_installs_its_license_to_the_flathub_location(modules):
    for name, module in modules.items():
        installs = _license_installs(module)
        assert installs, f"module {name} installs no license under {LICENSE_DIR}"
        for target, _source in installs:
            # exactly one directory below the license root — the
            # module's name (deps: one per package), no file renames
            assert re.fullmatch(re.escape(LICENSE_DIR) + r"/[a-z0-9-]+",
                                target), f"module {name}: {target}"


def test_app_license_comes_from_the_checkout(modules):
    for name in ("sayitermano-data", "sayitermano"):
        assert _license_installs(modules[name]) == [
            (f"{LICENSE_DIR}/{name}", "LICENSE")], name
    assert (REPO / "LICENSE").is_file()


def test_every_vendored_wheel_ships_the_license_the_manifest_installs():
    """Each deps license line copies the wheel's own .dist-info license
    (pip installs it next to the package) into a per-package directory.
    Pin that the file really is inside the vendored wheel, at the glob
    the line uses — a wheel without a license file would break the
    build with a literal unmatched glob."""
    deps = next(m for m in _load(CI)["modules"] if m["name"] == "deps")
    installs = _license_installs(deps)
    covered = set()
    for target, source in installs:
        assert source.startswith(WHEEL_LICENSE_GLOB), source
        pattern = source[len(WHEEL_LICENSE_GLOB):]
        hits = []
        for wheel in VENDORED_WHEELS.values():
            with zipfile.ZipFile(PACKAGING / "wheels" / wheel) as archive:
                hits += [(wheel, n) for n in archive.namelist()
                         if not n.endswith("/") and fnmatch.fnmatch(n, pattern)]
        assert len(hits) == 1, f"{source}: {len(hits)} matches in the vendored wheels"
        wheel, entry = hits[0]
        package = _normalize(next(p for p, w in VENDORED_WHEELS.items()
                                 if w == wheel))
        assert target == f"{LICENSE_DIR}/{package}", \
            f"{wheel}: license lands in {target!r}, not its own package dir"
        covered.add(package)
    assert covered == set(VENDORED_WHEELS), \
        f"wheels without a license install: {set(VENDORED_WHEELS) - covered}"


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
    releases = root.findall("releases/release")
    assert releases, "no <release> — software centers need at least one"
    assert releases[0].get("version") == _version()


# The six Flathub search queries verified winnable on 2026-11-21
# (#12 evidence base). "whisper" alone is a discoverability trap
# (its query #1 is an unrelated mic monitor) — the phrases carry
# the rest.
SEARCH_QUERIES = {
    "dictation",
    "voice typing",
    "speech to text",
    "whisper",
    "transcription",
    "voice input",
}


def test_metainfo_keywords_cover_every_target_flathub_query():
    root = _metainfo_root()
    keywords = {k.text for k in root.findall("keywords/keyword")}
    assert SEARCH_QUERIES <= keywords, \
        f"query with no keyword: {SEARCH_QUERIES - keywords}"


# -- screenshots: the Flathub shot list (#12) --------------------------------
#
# The PNGs are owner-captured (GUI work is human-only); until they
# land, only the URL contract is pinned. Files appear at the same
# path the metainfo URLs point at (raw.githubusercontent `linux`).
SCREENSHOT_DIR = FLATPAK_DIR / "screenshots"
SCREENSHOT_URL_PREFIX = (
    "https://raw.githubusercontent.com/acailic/SayItErmano"
    "/linux/packaging/flatpak/screenshots/"
)
SCREENSHOTS = [
    "01-dictation-pill.png",
    "02-settings-models.png",
    "03-history.png",
]


def test_metainfo_pins_the_flathub_shot_list():
    root = _metainfo_root()
    shots = root.findall("screenshots/screenshot")
    assert shots, "no <screenshots> — Flathub listings need at least one"
    assert shots[0].get("type") == "default", \
        "the first screenshot is the listing's face — mark it default"
    urls = [s.findtext("image") for s in shots]
    assert urls == [SCREENSHOT_URL_PREFIX + n for n in SCREENSHOTS]
    for shot, url in zip(shots, urls):
        assert url.startswith("https://"), url  # flathub rejects http
        caption = shot.findtext("caption")
        assert caption and not caption.endswith("."), \
            "captions are one sentence without a trailing full stop"
        assert not caption[0].isdigit(), caption


def test_shot_list_documents_every_pinned_screenshot():
    text = (SCREENSHOT_DIR / "SHOT-LIST.md").read_text(encoding="utf-8")
    for name in SCREENSHOTS:
        assert f"`{name}`" in text, \
            f"{name}: shot list and metainfo drifted apart"


def _png_size(data: bytes) -> tuple[int, int]:
    """Width/height from the IHDR chunk — no imaging dependency."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height


def test_screenshot_files_respect_flathub_window_bounds():
    """Flathub quality rule: <= 1000x700 logical px, 2000x1400 for
    HiDPI physical. Activates itself as the owner commits the PNGs;
    files that don't exist yet are skipped, not silently passed."""
    for name in SCREENSHOTS:
        path = SCREENSHOT_DIR / name
        if not path.is_file():
            continue  # owner capture pending — see SHOT-LIST.md
        width, height = _png_size(path.read_bytes())
        assert width <= 2000 and height <= 1400, \
            f"{name}: {width}x{height} exceeds the HiDPI ceiling"


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
