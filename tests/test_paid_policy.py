"""PAID_ARTIFACT policy hooks (ADR-0008) — the one-switch AGPL remediation.

The rights inventory
(docs/research/2026-11-22-rights-and-redistribution-inventory.md §2.1,
"The AGPL finding") found the one real copyleft wrinkle: the vendored
``global-shortcut-portal`` wheel is AGPL-3.0-only and installs into the
Flatpak. A paid artifact must either offer that wheel's Corresponding
Source (option A) or drop the ``portal`` extra (option B) — and the
decision belongs to the OPERATOR, so BOTH paths ship behind one switch
and are tested here, offline, with fakes plus the real committed inputs:

* the fail-closed gate: a paid build's option comes ONLY from the
  operator's decision marker in ADR-0008 — while it reads OPERATOR
  every paid path (each subcommand, the deb hook) refuses with a message
  naming the ADR and produces nothing; a decided marker opens exactly
  the recorded option for both A and B, and PAID_ARTIFACT_OPTION may
  confirm it but never contradict it; a mistyped switch is an error,
  never a silent free build;
* the vendored Corresponding-Source sdist: the REAL
  ``packaging/wheels/global_shortcut_portal-0.1.0.tar.gz`` matches its
  sha256 pin AND is the source of the REAL vendored wheel (identity,
  license, every installed .py); fakes prove the checks refuse;
* option normalization: A/B accepted, garbage refused with an
  actionable message;
* the dependency-set filtering (option B): a FAKE lock drops exactly
  the AGPL pin; the REAL committed deb lock filters to itself — the
  "deb never carried the wheel" guard the ADR relies on;
* the flatpak manifest transform: option B removes the wheel from the
  pip line, the license install and the source item (parsed output
  stays a well-formed manifest for BOTH committed variants); option A
  appends the ``agpl-source-offer`` module with the Flathub-required
  license install and the sha256-pinned sdist as a source;
* the option-A offer bytes: extracted from the REAL vendored wheel —
  license text, package sources, the SOURCE notice naming the pinned
  sdist, and the sdist itself;
* the build-deb.sh wiring (source-contract assertions, like
  TestBuildDebContract in test_deb_lock.py pins the hash gate);
* the CLI end to end (subprocess) on a miniature checkout whose ADR
  marker is decided or not — never the committed one, so the operator's
  future decision breaks no test;
* the ADR carries exactly one valid decision marker that agrees with
  its Status line, and states the rehearsal commands.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from tests.test_flatpak_manifest import (
    BASELINE,
    CI,
    FLATHUB,
    MANIFEST_PATHS,
    _license_installs,
    parse_manifest,
)

REPO = Path(__file__).resolve().parents[1]
PACKAGING = REPO / "packaging"

_spec = importlib.util.spec_from_file_location(
    "paid_policy", PACKAGING / "paid_policy.py")
paid = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(paid)

_spec = importlib.util.spec_from_file_location(
    "locklib", PACKAGING / "deb" / "locklib.py")
locklib = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(locklib)

ADR = REPO / "docs" / "adr" / "ADR-0008-paid-artifact-agpl-policy.md"
RIGHTS_NOTE = REPO / "docs" / "research" / \
    "2026-11-22-rights-and-redistribution-inventory.md"
SDIST = PACKAGING / "wheels" / paid.AGPL_SDIST_NAME

ON = {paid.SWITCH_ENV: "1"}
PAID_A = {paid.SWITCH_ENV: "1", paid.OPTION_ENV: "A"}
PAID_B = {paid.SWITCH_ENV: "1", paid.OPTION_ENV: "B"}


def decided(tmp_path: Path, value: str) -> Path:
    """A stand-in ADR-0008 whose marker names `value`. Decided markers are
    only ever exercised on temporary copies: flipping the committed marker
    is the operator's act and must break no test."""
    adr = tmp_path / "ADR-0008.md"
    adr.write_text(
        f"# ADR-0008 (test copy)\n\n## Decision\n\n"
        f"{paid.DECISION_PREFIX} {value}\n", encoding="utf-8")
    return adr

# The fake manifest models the committed deps-module shape: a pip
# install line naming every wheel, per-package license installs, and
# one `- type: file` source item per wheel (url: for the flathub shape,
# path: for the CI shape — both must transform).
FAKE_MANIFEST = """\
id: io.github.acailic.SayItErmano
modules:
  - name: sayitermano
    buildsystem: simple
    build-commands:
      - pip3 install --no-build-isolation --no-deps --prefix=$FLATPAK_DEST .
    sources:
      - type: git
        url: https://example.invalid/app.git
        tag: v9.9
  - name: deps
    buildsystem: simple
    build-commands:
      - pip3 install --no-index --prefix=$FLATPAK_DEST six-1.17.0-py2.py3-none-any.whl global_shortcut_portal-0.1.0-py3-none-any.whl
      - install -Dm0644 -t $FLATPAK_DEST/share/licenses/$FLATPAK_ID/six $FLATPAK_DEST/lib/python3*/site-packages/six-*.dist-info/LICENSE
      - install -Dm0644 -t $FLATPAK_DEST/share/licenses/$FLATPAK_ID/global-shortcut-portal $FLATPAK_DEST/lib/python3*/site-packages/global_shortcut_portal-*.dist-info/licenses/LICENSE
    sources:
      - type: file
        url: https://example.invalid/six-1.17.0-py2.py3-none-any.whl
        sha256: """ + "a" * 64 + """
      - type: file
        url: https://example.invalid/global_shortcut_portal-0.1.0-py3-none-any.whl
        sha256: """ + "b" * 64 + """
"""

FAKE_LOCK = (
    "# header comment\n"
    "faster-whisper==1.2.1 --hash=sha256:" + "1" * 64 + "\n"
    "global-shortcut-portal==0.1.0 --hash=sha256:" + "2" * 64 + "\n"
    "dbus-next==0.2.3 --hash=sha256:" + "3" * 64 + "\n")


class TestOptionNormalization:
    def test_unset_option_is_the_adr_default(self):
        """The free-build reading: nothing recorded in the environment
        falls back to the caller's default (the ADR recommendation)."""
        assert paid.normalize_option(None) == paid.OPTION_A
        assert paid.normalize_option("") == paid.OPTION_A
        assert paid.normalize_option("   ") == paid.OPTION_A
        assert paid.normalize_option(None, default=paid.OPTION_B) \
            == paid.OPTION_B

    def test_both_options_accepted_case_insensitively(self):
        assert paid.normalize_option("A") == "A"
        assert paid.normalize_option("a") == "A"
        assert paid.normalize_option("B") == "B"
        assert paid.normalize_option("b") == "B"

    def test_garbage_is_an_actionable_error(self):
        with pytest.raises(paid.PolicyError, match="PAID_ARTIFACT_OPTION"):
            paid.normalize_option("C")

    def test_switch_defaults_off_and_reads_the_env(self):
        assert paid.switch_on({}) is False
        assert paid.switch_on({paid.SWITCH_ENV: "0"}) is False
        assert paid.switch_on({paid.SWITCH_ENV: "1"}) is True


class TestFilterLock:
    def test_option_b_drops_exactly_the_agpl_pin(self):
        out = paid.filter_lock_text(FAKE_LOCK, "B")
        assert "global-shortcut-portal==0.1.0" not in out
        assert "faster-whisper==1.2.1 --hash=sha256:" + "1" * 64 in out
        assert "dbus-next==0.2.3 --hash=sha256:" + "3" * 64 in out
        assert "# header comment" in out  # comments survive
        # what remains still validates as a hash-locked file
        assert locklib.validate_lock_text(out, require_hashes=True)

    def test_underscored_pin_name_is_caught_by_normalization(self):
        lock = ("global_shortcut_portal==0.1.0 --hash=sha256:"
                + "2" * 64 + "\na-pkg==1.0 --hash=sha256:" + "4" * 64 + "\n")
        out = paid.filter_lock_text(lock, "B")
        assert "global_shortcut_portal" not in out
        assert "a-pkg==1.0" in out

    def test_option_a_passes_the_lock_through(self):
        assert paid.filter_lock_text(FAKE_LOCK, "A") == FAKE_LOCK

    def test_option_b_without_agpl_pin_is_a_guard_noop(self):
        lock = "a-pkg==1.0 --hash=sha256:" + "4" * 64 + "\n"
        assert paid.filter_lock_text(lock, "B") == lock

    def test_committed_deb_lock_filters_to_itself(self):
        """The ADR's 'the deb already excludes the wheel' fact, pinned:
        the REAL committed lock carries no AGPL pin, so the paid filter
        changes nothing — and stays a hard guarantee it never does."""
        text = (PACKAGING / "deb" / "constraints.txt").read_text("utf-8")
        assert paid.filter_lock_text(text, "B") == text
        assert "global-shortcut-portal" not in text
        assert "global_shortcut_portal" not in text

    def test_cli_filter_lock_routes_through_the_switch(self, tmp_path,
                                                       capsys):
        lock = tmp_path / "constraints.txt"
        lock.write_text(FAKE_LOCK)

        # switch OFF: byte-identical passthrough
        rc = paid.main(["filter-lock", "--lock", str(lock)], env={})
        assert rc == 0
        assert capsys.readouterr().out == FAKE_LOCK

        # switch ON + option B: the AGPL pin is gone (marker decides B)
        rc = paid.main(["filter-lock", "--lock", str(lock)],
                       env=PAID_B, adr_path=decided(tmp_path, "B"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "global-shortcut-portal" not in out
        assert "faster-whisper==1.2.1" in out


class TestFlatpakTransformOptionB:
    def _transform_fake(self):
        return parse_manifest(
            paid.transform_manifest_text(FAKE_MANIFEST, "B"))

    def test_wheel_leaves_the_pip_install_line(self):
        deps = self._transform_fake()["modules"][-1]
        pip = [c for c in deps["build-commands"]
               if c.startswith("pip3 install")][0]
        assert "global_shortcut_portal" not in pip
        assert pip.endswith("six-1.17.0-py2.py3-none-any.whl")  # no trail
        assert "six-1.17.0-py2.py3-none-any.whl" in pip

    def test_wheel_license_install_line_is_gone_others_kept(self):
        deps = self._transform_fake()["modules"][-1]
        targets = [t for t, _ in _license_installs(deps)]
        assert "$FLATPAK_DEST/share/licenses/$FLATPAK_ID/six" in targets
        assert all("global-shortcut-portal" not in t for t in targets)

    def test_wheel_source_item_is_dropped_neighbor_kept(self):
        deps = self._transform_fake()["modules"][-1]
        urls = [s["url"] for s in deps["sources"]]
        assert urls == ["https://example.invalid/six-1.17.0-py2.py3-none-any.whl"]

    @pytest.mark.parametrize("variant", [CI, FLATHUB])
    def test_both_committed_manifests_stay_well_formed(self, variant):
        """The real inputs: option B output parses, drops exactly the
        AGPL wheel everywhere it APPEARS AS DATA (the header comments'
        AGPL combination note is documentation and stays), and keeps
        the rest intact."""
        text = MANIFEST_PATHS[variant].read_text("utf-8")
        out = paid.transform_manifest_text(text, "B")
        data_lines = [ln for ln in out.splitlines()
                      if ln.strip() and not ln.lstrip().startswith("#")]
        assert all("global_shortcut_portal" not in ln
                   for ln in data_lines)
        assert all("global-shortcut-portal" not in ln
                   for ln in data_lines)
        parsed = parse_manifest(out)
        deps = next(m for m in parsed["modules"] if m["name"] == "deps")
        wheels = [s for s in deps["sources"] if s.get("type") == "file"]
        assert len(wheels) == 4  # six, python-xlib, pillow, dbus-next
        pip = [c for c in deps["build-commands"]
               if c.startswith("pip3 install")][0]
        assert len(pip.split(".whl")) - 1 == 4  # four wheels installed
        assert len(_license_installs(deps)) == 4

    def test_option_b_without_the_wheel_is_an_error_not_a_silence(self):
        already_clean = FAKE_MANIFEST.replace(
            " global_shortcut_portal-0.1.0-py3-none-any.whl", "")
        with pytest.raises(paid.PolicyError, match="pip3 install"):
            paid.transform_manifest_text(already_clean, "B")

    def test_cli_flatpak_routes_through_the_switch(self, tmp_path, capsys):
        manifest = tmp_path / "app.yml"
        manifest.write_text(FAKE_MANIFEST)
        rc = paid.main(["flatpak", "--manifest", str(manifest)], env={})
        assert rc == 0
        assert capsys.readouterr().out == FAKE_MANIFEST  # free: identical

        rc = paid.main(["flatpak", "--manifest", str(manifest)],
                       env=PAID_B, adr_path=decided(tmp_path, "B"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "global_shortcut_portal" not in out
        parse_manifest(out)  # still a well-formed manifest


class TestFlatpakTransformOptionA:
    @pytest.mark.parametrize("variant", [CI, FLATHUB])
    def test_offer_module_is_appended_and_parses(self, variant):
        text = MANIFEST_PATHS[variant].read_text("utf-8")
        parsed = parse_manifest(paid.transform_manifest_text(text, "A"))
        offer = parsed["modules"][-1]
        assert offer["name"] == "agpl-source-offer"
        assert offer["buildsystem"] == "simple"

    @pytest.mark.parametrize("variant", [CI, FLATHUB])
    def test_offer_module_satisfies_the_flathub_license_rule(self, variant):
        """Every module installs a license under the Flathub location
        (the committed manifests' own test rule) — the appended module
        must not break it for the paid variant."""
        text = MANIFEST_PATHS[variant].read_text("utf-8")
        parsed = parse_manifest(paid.transform_manifest_text(text, "A"))
        for module in parsed["modules"]:
            installs = _license_installs(module)
            assert installs, module["name"]

    @pytest.mark.parametrize("variant", [CI, FLATHUB])
    def test_deps_module_is_untouched_by_option_a(self, variant):
        """Option A keeps the full dependency set (functional parity
        with the free build is the point of A) and only ADDS the offer."""
        text = MANIFEST_PATHS[variant].read_text("utf-8")
        before = parse_manifest(text)["modules"]
        after = parse_manifest(paid.transform_manifest_text(text, "A"))[
            "modules"]
        assert after[:-1] == before
        deps = next(m for m in after if m["name"] == "deps")
        pip = [c for c in deps["build-commands"]
               if c.startswith("pip3 install")][0]
        assert "global_shortcut_portal-0.1.0-py3-none-any.whl" in pip

    def test_offer_module_differs_between_variants_only_in_its_source(self):
        """The divergence rule survives the transform: both variants grow
        the same module — only the sdist's locator differs (local path vs
        remote url), exactly like their wheel sources."""
        def offer(variant: str) -> dict:
            text = paid.transform_manifest_text(
                MANIFEST_PATHS[variant].read_text("utf-8"), "A")
            return parse_manifest(text)["modules"][-1]
        ci, flathub = offer(CI), offer(FLATHUB)
        assert ci["name"] == flathub["name"] == "agpl-source-offer"
        assert ci["build-commands"] == flathub["build-commands"]
        (ci_source,), (remote_source,) = ci["sources"], flathub["sources"]
        assert ci_source == {"type": "file",
                             "path": f"wheels/{paid.AGPL_SDIST_NAME}",
                             "sha256": paid.AGPL_SDIST_SHA256}
        assert remote_source == {"type": "file",
                                 "url": paid.AGPL_SDIST_URL,
                                 "sha256": paid.AGPL_SDIST_SHA256}

    def test_offer_commands_copy_sources_license_and_the_sdist(self):
        parsed = parse_manifest(
            paid.transform_manifest_text(FAKE_MANIFEST, "A"))
        offer = parsed["modules"][-1]
        cmds = offer["build-commands"]
        joined = "\n".join(cmds)
        assert "share/licenses/$FLATPAK_ID/agpl-source-offer" in joined
        assert "cp -r" in joined and "/src" in joined
        assert (f"install -Dm0644 -t $FLATPAK_DEST/share/doc/"
                f"{paid.AGPL_DIST} {paid.AGPL_SDIST_NAME}") in cmds
        # the directory exists before anything is copied/installed into it
        mkdir = next(i for i, c in enumerate(cmds) if c.startswith("mkdir"))
        assert all("share/doc/" not in c for c in cmds[:mkdir])

    def test_offer_sdist_source_follows_the_variants_locator_style(self):
        """A manifest with vendored (`path: wheels/`) wheels — the CI
        bundle shape — gets the vendored sdist; a remote-only one gets
        the pinned PyPI url. Never both, never neither."""
        local_manifest = FAKE_MANIFEST.replace(
            "        url: https://example.invalid/", "        path: wheels/")
        assert "path: wheels/six-" in local_manifest  # the fake was reshaped
        local = paid.transform_manifest_text(local_manifest, "A")
        remote = paid.transform_manifest_text(FAKE_MANIFEST, "A")
        assert f"path: wheels/{paid.AGPL_SDIST_NAME}" in local
        assert paid.AGPL_SDIST_URL not in local
        assert f"url: {paid.AGPL_SDIST_URL}" in remote
        assert "path: wheels/" not in remote

    @pytest.mark.parametrize("mode", ["A", "B"])
    def test_variants_still_differ_only_in_their_sources(self, mode):
        """The committed manifests' own divergence rule, re-checked on
        the paid output: the transform must not make them diverge."""
        def without_sources(variant: str) -> dict:
            parsed = parse_manifest(paid.transform_manifest_text(
                MANIFEST_PATHS[variant].read_text("utf-8"), mode))
            return parsed | {"modules": [
                {k: v for k, v in m.items() if k != "sources"}
                for m in parsed["modules"]]}
        assert without_sources(FLATHUB) == without_sources(CI), \
            "paid transform broke the divergence rule"

    @pytest.mark.parametrize("mode", ["A", "B"])
    def test_permissions_and_identity_are_untouched(self, mode):
        text = MANIFEST_PATHS[CI].read_text("utf-8")
        parsed = parse_manifest(paid.transform_manifest_text(text, mode))
        assert parsed["id"] == "io.github.acailic.SayItErmano"
        assert {a for a in parsed["finish-args"] if not a.startswith("--env=")} \
            == BASELINE

    def test_ci_variant_paid_sources_all_exist_and_match_their_pins(self):
        """The paid CI-bundle manifest adds one local source (the sdist);
        every local file source of the output — the committed ones plus
        the new one — is on disk at its pinned bytes."""
        text = MANIFEST_PATHS[CI].read_text("utf-8")
        parsed = parse_manifest(paid.transform_manifest_text(text, "A"))
        pinned = [src for module in parsed["modules"]
                  for src in module.get("sources", [])
                  if src.get("type") == "file" and "path" in src]
        assert paid.AGPL_SDIST_NAME in {
            s["path"].split("/")[-1] for s in pinned}
        for src in pinned:
            path = PACKAGING / src["path"]
            assert path.is_file(), src["path"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() \
                == src["sha256"], f"sha256 drift for {src['path']}"


class TestAgplOfferBytes:
    """Option A's in-artifact offer, extracted from the REAL vendored
    wheel (offline zipfile work — no build, no network)."""

    def test_license_sources_and_notice_land_in_dest(self, tmp_path):
        dest = paid.write_agpl_offer(paid.agpl_wheel_path(), tmp_path / "d")
        license_text = (dest / "LICENSE").read_text("utf-8")
        assert "GNU AFFERO GENERAL PUBLIC LICENSE" in license_text
        assert "Version 3, 19 November 2007" in license_text
        sources = sorted(
            p.name for p in (dest / "src" / "global_shortcut_portal")
            .glob("*.py"))
        assert sources == [
            "__init__.py", "exceptions.py", "models.py", "portal.py",
            "session.py", "utils.py"]

    def test_notice_names_the_pinned_sdist_and_versions(self, tmp_path):
        dest = paid.write_agpl_offer(paid.agpl_wheel_path(), tmp_path / "d")
        notice = (dest / "SOURCE").read_text("utf-8")
        assert paid.AGPL_SDIST_URL in notice
        assert paid.AGPL_SDIST_SHA256 in notice
        assert "0.1.0" in notice
        assert paid.AGPL_REPO_URL in notice
        # the wheel identity is self-describing: anyone holding the
        # artifact can verify the offer matches what was installed
        assert locklib.sha256_file(paid.agpl_wheel_path()) in notice

    def test_a_wheel_that_is_not_the_agpl_one_is_refused(self, tmp_path):
        wheelhouse = tmp_path / "wheels"
        wheelhouse.mkdir()
        other = locklib.make_minimal_wheel(wheelhouse, "some-mit-pkg", "1.0")
        with pytest.raises(paid.PolicyError, match="not a"):
            paid.write_agpl_offer(other, tmp_path / "out")

    def test_wheel_without_license_text_is_refused(self, tmp_path):
        """A re-vendored AGPL wheel missing .dist-info/licenses/LICENSE
        must fail the paid build, not ship an offer with no license."""
        wheelhouse = tmp_path / "wheels"
        wheelhouse.mkdir()
        bare = locklib.make_minimal_wheel(
            wheelhouse, "global-shortcut-portal", "9.9")
        with pytest.raises(paid.PolicyError, match="LICENSE"):
            paid.write_agpl_offer(bare, tmp_path / "out")

    def test_the_sdist_itself_ships_in_the_offer(self, tmp_path):
        """The Corresponding-Source tarball is IN the artifact, byte for
        byte the vendored (pinned) file — not just a URL pointing at it."""
        dest = paid.write_agpl_offer(paid.agpl_wheel_path(), tmp_path / "d")
        assert (dest / paid.AGPL_SDIST_NAME).read_bytes() \
            == SDIST.read_bytes()
        assert paid.AGPL_SDIST_NAME in (dest / "SOURCE").read_text("utf-8")


# -- the vendored sdist: pinned, and the wheel's own source -------------------

SDIST_ROOT = f"{paid.AGPL_PACKAGE_DIR}-0.1.0"
FAKE_SOURCE = b"PORTAL = 1\n"
FAKE_LICENSE = b"GNU AFFERO GENERAL PUBLIC LICENSE, Version 3 (fake)\n"


def make_agpl_wheel(directory: Path, *,
                    source: bytes = FAKE_SOURCE) -> Path:
    """A hand-built stand-in for the vendored wheel carrying exactly what
    the offer extracts and the sdist must match: one package module, the
    dist-info license text, a License-Expression."""
    dist = f"{paid.AGPL_PACKAGE_DIR}-0.1.0"
    files = {
        f"{paid.AGPL_PACKAGE_DIR}/__init__.py": b"",
        f"{paid.AGPL_PACKAGE_DIR}/portal.py": source,
        f"{dist}.dist-info/METADATA": (
            f"Metadata-Version: 2.4\nName: {paid.AGPL_DIST}\n"
            f"Version: 0.1.0\nLicense-Expression: AGPL-3.0-only\n").encode(),
        f"{dist}.dist-info/licenses/LICENSE": FAKE_LICENSE,
    }
    path = directory / f"{dist}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for rel, data in files.items():
            archive.writestr(rel, data)
    return path


def sdist_files(override: dict[str, bytes | None] | None = None
                ) -> dict[str, bytes]:
    """The sdist content matching make_agpl_wheel's wheel, in upstream's
    src layout. `override` replaces a file's bytes (None drops it); a key
    that is not one of these files is a typo in the test, not a case."""
    files: dict[str, bytes] = {
        f"{SDIST_ROOT}/PKG-INFO": (
            f"Metadata-Version: 2.4\nName: {paid.AGPL_DIST}\nVersion: 0.1.0\n"
            f"License-Expression: AGPL-3.0-only\n").encode(),
        f"{SDIST_ROOT}/LICENSE": FAKE_LICENSE,
        f"{SDIST_ROOT}/src/{paid.AGPL_PACKAGE_DIR}/__init__.py": b"",
        f"{SDIST_ROOT}/src/{paid.AGPL_PACKAGE_DIR}/portal.py": FAKE_SOURCE,
    }
    for rel, data in (override or {}).items():
        assert rel in files, f"not a file of the fake sdist: {rel}"
        if data is None:
            del files[rel]
        else:
            files[rel] = data
    return files


def make_sdist(path: Path, files: dict[str, bytes] | None = None, *,
               extra: list[tarfile.TarInfo] | None = None) -> Path:
    """Pack `files` (+ optional stray members) as a gzipped tarball."""
    with tarfile.open(path, "w:gz") as archive:
        for rel, data in sorted((files if files is not None
                                 else sdist_files()).items()):
            info = tarfile.TarInfo(rel)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        for info in extra or []:
            archive.addfile(info, io.BytesIO(b"x"))
    return path


@pytest.fixture
def pin(monkeypatch):
    """Make a file 'the pinned bytes': AGPL_SDIST_SHA256 follows it, so
    what is under test is the content checks, not the hash."""
    def _pin(path: Path) -> Path:
        monkeypatch.setattr(paid, "AGPL_SDIST_SHA256", hashlib.sha256(
            path.read_bytes()).hexdigest())
        return path
    return _pin


class TestVendoredSdist:
    """The REAL vendored pair, as committed: the sdist under
    packaging/wheels/ is the pinned bytes and the source of the vendored
    wheel. These pin the shipped state; nothing here is a fake."""

    def test_sdist_is_vendored_under_its_pinned_name(self):
        assert paid.agpl_sdist_path() == SDIST
        assert SDIST.is_file()

    def test_vendored_bytes_match_the_sha256_pin(self):
        assert hashlib.sha256(SDIST.read_bytes()).hexdigest() \
            == paid.AGPL_SDIST_SHA256

    def test_pin_url_and_file_name_are_one_artifact(self):
        """One artifact, two locators: the PyPI url the Flathub-shaped
        paid manifest downloads names the file that is vendored."""
        assert paid.AGPL_SDIST_URL.rsplit("/", 1)[-1] \
            == paid.AGPL_SDIST_NAME

    def test_sdist_is_the_source_of_the_vendored_wheel(self):
        paid.verify_sdist(SDIST, paid.agpl_wheel_path())  # raises if not

    def test_both_declare_the_same_agpl_identity(self):
        """Independent of paid_policy: the tarball and the wheel say the
        same name, version and AGPL-3.0-only — the fact ADR-0008 rests on."""
        with tarfile.open(SDIST, "r:gz") as archive:
            stream = archive.extractfile(f"{SDIST_ROOT}/PKG-INFO")
            assert stream is not None
            pkg_info = stream.read().decode("utf-8")
        with zipfile.ZipFile(paid.agpl_wheel_path()) as archive:
            metadata = archive.read(
                f"{paid.AGPL_PACKAGE_DIR}-0.1.0.dist-info/METADATA"
            ).decode("utf-8")
        for field in (f"Name: {paid.AGPL_DIST}", "Version: 0.1.0",
                      "License-Expression: AGPL-3.0-only"):
            assert field in pkg_info and field in metadata, field

    def test_missing_vendored_sdist_is_refused(self, tmp_path):
        (tmp_path / "wheels").mkdir()
        with pytest.raises(paid.PolicyError, match="missing vendored sdist"):
            paid.agpl_sdist_path(tmp_path / "wheels")


class TestSdistVerification:
    """What verify_sdist (and so every option-A paid path) refuses. Fakes
    with the pin pointed at them: only the content checks can fail."""

    def test_a_matching_pair_passes_and_the_sdist_ships(self, tmp_path, pin):
        wheel = make_agpl_wheel(tmp_path)
        sdist = pin(make_sdist(tmp_path / paid.AGPL_SDIST_NAME))
        paid.verify_sdist(sdist, wheel)  # raises if not the source
        dest = paid.write_agpl_offer(wheel, tmp_path / "offer", sdist=sdist)
        assert (dest / paid.AGPL_SDIST_NAME).read_bytes() \
            == sdist.read_bytes()

    def test_unpinned_bytes_never_ship(self, tmp_path, pin):
        """The hash gate comes first: a tarball that is not the pinned
        bytes is refused before its content is even looked at — and no
        offer directory is left behind."""
        wheel = make_agpl_wheel(tmp_path)
        pin(make_sdist(tmp_path / "pinned.tar.gz"))
        other = make_sdist(tmp_path / "other.tar.gz", sdist_files({
            f"{SDIST_ROOT}/src/{paid.AGPL_PACKAGE_DIR}/portal.py":
                b"PORTAL = 2\n"}))
        with pytest.raises(paid.PolicyError, match="not the pin"):
            paid.verify_sdist(other, wheel)
        with pytest.raises(paid.PolicyError, match="not the pin"):
            paid.write_agpl_offer(wheel, tmp_path / "offer", sdist=other)
        assert not (tmp_path / "offer").exists()

    @pytest.mark.parametrize("override,refusal", [
        ({f"{SDIST_ROOT}/src/{paid.AGPL_PACKAGE_DIR}/portal.py":
         b"PORTAL = 2\n"}, "not the wheel's"),
        ({f"{SDIST_ROOT}/src/{paid.AGPL_PACKAGE_DIR}/portal.py": None},
         "no source in the sdist"),
        ({f"{SDIST_ROOT}/LICENSE": b"MIT\n"}, "license text"),
        ({f"{SDIST_ROOT}/PKG-INFO": (
            "Metadata-Version: 2.4\nName: global-shortcut-portal\n"
            "Version: 9.9.9\nLicense-Expression: AGPL-3.0-only\n").encode()},
         "Version"),
        ({f"{SDIST_ROOT}/PKG-INFO": (
            "Metadata-Version: 2.4\nName: global-shortcut-portal\n"
            "Version: 0.1.0\nLicense-Expression: MIT\n").encode()},
         "License-Expression"),
        ({f"{SDIST_ROOT}/PKG-INFO": None}, "PKG-INFO"),
    ])
    def test_a_tarball_that_is_not_the_wheels_source_is_refused(
            self, tmp_path, pin, override, refusal):
        """Pinned bytes that do not correspond to the wheel convey nothing:
        sources must be the wheel's, the license its license, the
        identity its identity."""
        wheel = make_agpl_wheel(tmp_path)
        sdist = pin(make_sdist(tmp_path / "s.tar.gz", sdist_files(override)))
        with pytest.raises(paid.PolicyError, match=refusal) as excinfo:
            paid.verify_sdist(sdist, wheel)
        assert sdist.name in str(excinfo.value)

    def test_a_wheel_of_another_version_has_no_source_here(self, tmp_path,
                                                           pin):
        """The mismatch is seen from either side: the sdist is pinned, the
        wheel beside it is not the one it is the source of."""
        sdist = pin(make_sdist(tmp_path / "s.tar.gz"))
        renamed = make_agpl_wheel(tmp_path).with_name(
            f"{paid.AGPL_PACKAGE_DIR}-9.9.9-py3-none-any.whl")
        renamed.with_name(f"{paid.AGPL_PACKAGE_DIR}-0.1.0-py3-none-any.whl") \
            .rename(renamed)
        with pytest.raises(paid.PolicyError, match="PKG-INFO"):
            paid.verify_sdist(sdist, renamed)

    def test_unsafe_member_paths_are_refused(self, tmp_path, pin):
        """The sdist is third-party input: a member that could escape an
        extraction directory stops the paid build here."""
        wheel = make_agpl_wheel(tmp_path)
        escape = tarfile.TarInfo("../escape.py")
        sdist = pin(make_sdist(tmp_path / "s.tar.gz", extra=[escape]))
        with pytest.raises(paid.PolicyError, match="unsafe member"):
            paid.verify_sdist(sdist, wheel)

    def test_non_regular_members_are_refused(self, tmp_path, pin):
        wheel = make_agpl_wheel(tmp_path)
        link = tarfile.TarInfo(f"{SDIST_ROOT}/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        sdist = pin(make_sdist(tmp_path / "s.tar.gz", extra=[link]))
        with pytest.raises(paid.PolicyError, match="not a regular file"):
            paid.verify_sdist(sdist, wheel)

    def test_a_file_that_is_not_a_tarball_is_refused(self, tmp_path, pin):
        wheel = make_agpl_wheel(tmp_path)
        junk = tmp_path / "junk.tar.gz"
        junk.write_bytes(b"not a tarball")
        with pytest.raises(paid.PolicyError, match="not a readable sdist"):
            paid.verify_sdist(pin(junk), wheel)


# -- the fail-closed gate ------------------------------------------------------


class TestOperatorDecisionMarker:
    """ADR-0008's marker is one line with three legal values; everything
    else — absent, duplicated, unknown, unreadable — refuses."""

    @pytest.mark.parametrize("value", ["OPERATOR", "A", "B", "**A**",
                                       "`B`", " A "])
    def test_the_marker_value_is_read(self, tmp_path, value):
        assert paid.operator_decision(decided(tmp_path, value)) \
            == value.strip(" *`")

    @pytest.mark.parametrize("text", [
        "# ADR without a marker\n",
        "Paid-artifact decision: A\nPaid-artifact decision: B\n",
        "Paid-artifact decision: C\n",
        "Paid-artifact decision: A (decided today)\n",
        "Paid-artifact decision:\n",
        "Paid-artifact Decision: A\n",
        "> Paid-artifact decision: A\n",
        "    Paid-artifact decision: A\n",
    ])
    def test_anything_but_one_clean_line_is_refused(self, tmp_path, text):
        adr = tmp_path / "adr.md"
        adr.write_text(text, encoding="utf-8")
        with pytest.raises(paid.PolicyError, match="ADR-0008") as excinfo:
            paid.operator_decision(adr)
        assert str(adr) in str(excinfo.value)  # the error names the file

    def test_an_unreadable_adr_is_refused(self, tmp_path):
        with pytest.raises(paid.PolicyError, match="ADR-0008"):
            paid.operator_decision(tmp_path / "missing.md")


class TestPaidOptionFailsClosed:
    """A paid build's option comes from the ADR-0008 marker alone."""

    @pytest.mark.parametrize("env", [ON, PAID_A, PAID_B])
    def test_no_decision_refuses_whatever_the_environment_says(
            self, tmp_path, env):
        """OPERATOR in the ADR + PAID_ARTIFACT=1: refused — even when
        PAID_ARTIFACT_OPTION names a valid option. No environment
        variable opens the gate, and the message says what does."""
        adr = decided(tmp_path, "OPERATOR")
        with pytest.raises(paid.PolicyError) as excinfo:
            paid.paid_option(env, adr)
        message = str(excinfo.value)
        assert "ADR-0008" in message
        assert f"{paid.SWITCH_ENV}=1 refused" in message
        assert "no paid artifact is built" in message
        assert f"{paid.DECISION_PREFIX} {paid.OPERATOR}" in message
        assert str(adr) in message  # where the decision is recorded
        assert f"{paid.OPTION_A} or {paid.OPTION_B}" in message

    @pytest.mark.parametrize("decision,env", [
        ("A", ON), ("A", PAID_A), ("A", {**ON, paid.OPTION_ENV: "a"}),
        ("B", ON), ("B", PAID_B),
    ])
    def test_a_recorded_decision_is_what_the_build_gets(
            self, tmp_path, decision, env):
        assert paid.paid_option(env, decided(tmp_path, decision)) == decision

    def test_an_unset_option_never_falls_back_to_the_recommendation(
            self, tmp_path):
        """No silent default: with the decision recorded as B, an unset
        PAID_ARTIFACT_OPTION builds B — the code default A never leaks
        into a paid build."""
        assert paid.paid_option(ON, decided(tmp_path, "B")) == "B"

    @pytest.mark.parametrize("decision,env", [("A", PAID_B), ("B", PAID_A)])
    def test_a_contradicting_option_is_refused(self, tmp_path, decision,
                                               env):
        """A pipeline cannot pick the option the operator did not record."""
        with pytest.raises(
                paid.PolicyError,
                match=f"contradicts ADR-0008's recorded decision {decision}"):
            paid.paid_option(env, decided(tmp_path, decision))

    def test_option_garbage_is_still_an_error(self, tmp_path):
        with pytest.raises(paid.PolicyError, match=paid.OPTION_ENV):
            paid.paid_option({**ON, paid.OPTION_ENV: "C"},
                             decided(tmp_path, "A"))


class TestPaidSwitch:
    """PAID_ARTIFACT parses to off, on — or a loud error. A typo must
    never read as a free build."""

    @pytest.mark.parametrize("value", ["true", "yes", "01", " 1", "1 "])
    def test_any_other_value_is_an_error(self, value):
        with pytest.raises(paid.PolicyError, match="expected '1'") as excinfo:
            paid.switch_on({paid.SWITCH_ENV: value})
        assert "ADR-0008" in str(excinfo.value)

    def test_a_mistyped_switch_refuses_every_cli_command(
            self, tmp_path, capsys):
        """Even with a decision recorded and a valid option in the
        environment, `PAID_ARTIFACT=true` is not a paid build — nor a
        free one."""
        lock = tmp_path / "lock.txt"
        lock.write_text(FAKE_LOCK)
        adr = decided(tmp_path, "A")
        for args in (["option"], ["filter-lock", "--lock", str(lock)]):
            assert paid.main(args, env={**PAID_A, paid.SWITCH_ENV: "true"},
                             adr_path=adr) == 1
            err = capsys.readouterr().err
            assert f"{paid.SWITCH_ENV}='true'" in err and "ADR-0008" in err

    def test_a_free_build_never_consults_the_marker(self, tmp_path, capsys):
        """Off means off: a stray PAID_ARTIFACT_OPTION cannot re-engage the
        gate, and an unreadable ADR is no obstacle to a free build."""
        lock = tmp_path / "lock.txt"
        lock.write_text(FAKE_LOCK)
        rc = paid.main(["filter-lock", "--lock", str(lock)],
                       env={paid.OPTION_ENV: "B"},
                       adr_path=tmp_path / "no-adr.md")
        assert rc == 0
        assert capsys.readouterr().out == FAKE_LOCK

    def test_verify_needs_neither_switch_nor_decision(self, capsys):
        assert paid.main(["verify"], env={}) == 0
        assert "AGPL source OK" in capsys.readouterr().out


# -- the CLI as build-deb.sh runs it (subprocess) ----------------------------


def checkout(tmp_path: Path, decision: str) -> Path:
    """A miniature checkout for subprocess runs: the policy module (+ its
    locklib), the REAL vendored wheel and sdist, and the committed
    ADR-0008 text with its marker set to `decision` — the exact edit the
    operator makes. The committed tree is never touched, so the value the
    operator records for real never matters to these tests."""
    root = tmp_path / "repo"
    pkg = root / "packaging"
    (pkg / "deb").mkdir(parents=True)
    (pkg / "wheels").mkdir()
    shutil.copy2(PACKAGING / "paid_policy.py", pkg / "paid_policy.py")
    shutil.copy2(PACKAGING / "deb" / "locklib.py", pkg / "deb" / "locklib.py")
    for vendored in (paid.agpl_wheel_path(), SDIST):
        shutil.copy2(vendored, pkg / "wheels" / vendored.name)
    text, markers = re.subn(
        r"(?m)^(Paid-artifact decision:).*$", rf"\g<1> {decision}",
        ADR.read_text("utf-8"))
    assert markers == 1, "the committed ADR must carry one decision marker"
    adr = root / "docs" / "adr" / ADR.name
    adr.parent.mkdir(parents=True)
    adr.write_text(text, encoding="utf-8")
    return root


class TestCliPaidGate:
    """``python3 packaging/paid_policy.py <cmd>`` as build-deb.sh invokes
    it: without a recorded decision every paid command exits 1 and
    produces nothing; with one, both options work end to end."""

    def _run(self, root: Path, *args: str, env: dict[str, str]):
        return subprocess.run(
            [sys.executable, str(root / "packaging" / "paid_policy.py"),
             *args],
            capture_output=True, text=True, timeout=60, env=env)

    def test_without_a_decision_every_command_refuses(self, tmp_path):
        """The acceptance: PAID_ARTIFACT=1 with no ADR decision fails
        closed with a clear message — for each paid command, with or
        without a PAID_ARTIFACT_OPTION — and nothing is written."""
        root = checkout(tmp_path, "OPERATOR")
        lock = tmp_path / "lock.txt"
        lock.write_text(FAKE_LOCK)
        manifest = tmp_path / "m.yml"
        manifest.write_text(FAKE_MANIFEST)
        dest = tmp_path / "offer"
        commands = {
            "option": ["option"],
            "filter-lock": ["filter-lock", "--lock", str(lock)],
            "agpl-offer": ["agpl-offer", "--dest", str(dest)],
            "flatpak": ["flatpak", "--manifest", str(manifest)],
        }
        for env in (ON, PAID_A, PAID_B):
            for name, args in commands.items():
                r = self._run(root, *args, env=env)
                assert r.returncode == 1, (name, env, r.stdout, r.stderr)
                assert r.stdout == "", name  # nothing produced
                assert "ERROR:" in r.stderr, name
                assert "ADR-0008" in r.stderr, r.stderr
                assert "refused" in r.stderr, r.stderr
                assert "OPERATOR" in r.stderr, r.stderr
                assert "no paid artifact is built" in r.stderr, r.stderr
        assert not dest.exists()

    def test_decided_a_builds_the_offer(self, tmp_path):
        root = checkout(tmp_path, "A")
        r = self._run(root, "option", env=ON)
        assert (r.returncode, r.stdout.strip()) == (0, "A"), r.stderr
        dest = tmp_path / "offer"
        r = self._run(root, "agpl-offer", "--dest", str(dest), env=PAID_A)
        assert r.returncode == 0, r.stderr
        assert (dest / paid.AGPL_SDIST_NAME).read_bytes() \
            == SDIST.read_bytes()
        assert (dest / "LICENSE").is_file()
        assert (dest / "SOURCE").is_file()
        assert (dest / "src" / paid.AGPL_PACKAGE_DIR / "portal.py").is_file()
        # the recorded decision binds: a contradicting option is refused
        refused = tmp_path / "refused"
        r = self._run(root, "agpl-offer", "--dest", str(refused), env=PAID_B)
        assert r.returncode == 1 and "contradicts ADR-0008" in r.stderr
        assert not refused.exists()

    def test_decided_a_flatpak_manifest_carries_the_pinned_sdist(
            self, tmp_path):
        root = checkout(tmp_path, "A")
        r = self._run(root, "flatpak", "--manifest", str(MANIFEST_PATHS[CI]),
                      env=ON)
        assert r.returncode == 0, r.stderr
        offer = parse_manifest(r.stdout)["modules"][-1]
        assert offer["name"] == "agpl-source-offer"
        assert offer["sources"] == [
            {"type": "file", "path": f"wheels/{paid.AGPL_SDIST_NAME}",
             "sha256": paid.AGPL_SDIST_SHA256}]

    def test_decided_b_filters_the_lock_and_drops_the_wheel(self, tmp_path):
        root = checkout(tmp_path, "B")
        r = self._run(root, "option", env=PAID_B)
        assert (r.returncode, r.stdout.strip()) == (0, "B"), r.stderr
        lock = tmp_path / "lock.txt"
        lock.write_text(FAKE_LOCK)
        r = self._run(root, "filter-lock", "--lock", str(lock), env=ON)
        assert r.returncode == 0, r.stderr
        assert "global-shortcut-portal" not in r.stdout
        assert "faster-whisper==1.2.1" in r.stdout
        r = self._run(root, "flatpak", "--manifest", str(MANIFEST_PATHS[CI]),
                      env=ON)
        assert r.returncode == 0, r.stderr
        assert "global_shortcut_portal" not in r.stdout
        parse_manifest(r.stdout)

    def test_option_a_refuses_a_drifted_sdist_before_shipping_it(
            self, tmp_path):
        """The build-time hash check: once the vendored file no longer is
        the pinned bytes, every option-A command that would ship it fails,
        and nothing is emitted."""
        root = checkout(tmp_path, "A")
        (root / "packaging" / "wheels" / paid.AGPL_SDIST_NAME) \
            .write_bytes(b"replaced after the pin")
        r = self._run(root, "verify", env={})
        assert r.returncode == 1 and "not the pin" in r.stderr
        r = self._run(root, "agpl-offer", "--dest", str(tmp_path / "offer"),
                      env=ON)
        assert r.returncode == 1 and "not the pin" in r.stderr
        assert not (tmp_path / "offer").exists()
        r = self._run(root, "flatpak", "--manifest", str(MANIFEST_PATHS[CI]),
                      env=ON)
        assert r.returncode == 1 and r.stdout == ""

    def test_free_build_is_untouched(self, tmp_path):
        """Switch off: byte-identical passthrough, the ADR (here:
        undecided) never consulted, and `option` reports the ADR's
        recommendation for what it is."""
        root = checkout(tmp_path, "OPERATOR")
        r = self._run(root, "flatpak", "--manifest", str(MANIFEST_PATHS[CI]),
                      env={paid.OPTION_ENV: "B"})
        assert r.returncode == 0, r.stderr
        assert r.stdout == MANIFEST_PATHS[CI].read_text("utf-8")
        r = self._run(root, "option", env={})
        assert (r.returncode, r.stdout.strip()) == (0, "A"), r.stderr

    def test_verify_passes_on_the_real_tree(self):
        r = subprocess.run(
            [sys.executable, str(PACKAGING / "paid_policy.py"), "verify"],
            capture_output=True, text=True, timeout=60, env={})
        assert r.returncode == 0, r.stderr
        assert "AGPL source OK" in r.stdout
        assert paid.AGPL_SDIST_NAME in r.stdout


class TestBuildDebWiring:
    """Source-contract assertions on build-deb.sh, following the
    TestBuildDebContract precedent in test_deb_lock.py: the switch, its
    default-off form, and both option branches must stay wired."""

    SCRIPT = (PACKAGING / "build-deb.sh").read_text("utf-8")

    def test_switch_defaults_off(self):
        assert '${PAID_ARTIFACT:-0}' in self.SCRIPT

    def test_both_option_branches_call_the_policy_module(self):
        assert "paid_policy.py\" option" in self.SCRIPT
        assert "paid_policy.py\" agpl-offer" in self.SCRIPT
        assert "paid_policy.py\" filter-lock" in self.SCRIPT

    def test_paid_lock_feeds_the_installs(self):
        """Whichever lock the paid step produces is the one pip
        verifies — both the hash-locked and the pin-only dev branch."""
        assert '--require-hashes -r "$PAID_CONSTRAINTS"' in self.SCRIPT
        assert '-c "$PAID_CONSTRAINTS" .' in self.SCRIPT

    def test_the_gate_runs_first_and_its_refusal_is_the_build_end(self):
        """`option` (the fail-closed gate) is resolved before the offer or
        the filtered lock can run; when it fails the script says so and
        exits — no payload is ever staged after a refusal."""
        gate = self.SCRIPT.index('paid_policy.py" option)')
        assert gate < self.SCRIPT.index('paid_policy.py" agpl-offer')
        assert gate < self.SCRIPT.index('paid_policy.py" filter-lock')
        assert gate < self.SCRIPT.index('mkdir -p "$STAGE/opt/$NAME"')
        refusal = self.SCRIPT[gate:gate + 400]
        assert '|| {' in refusal and "no artifact built (ADR-0008)" in refusal
        assert re.search(r"echo \"ERROR: [^\n]*\n\s+exit 1\n", refusal)

    def test_a_mistyped_switch_reaches_the_module(self):
        """The shell's own test treats everything but '0' as 'ask the
        module': '1' engages (and is refused while ADR-0008 is
        undecided), and a typo like 'true' hits the module's strict
        parser instead of falling through to a free build."""
        assert '[ "${PAID_ARTIFACT:-0}" != "0" ]' in self.SCRIPT
        assert '${PAID_ARTIFACT:-0}" = "1"' not in self.SCRIPT


class TestADRMarksTheOperatorDecision:
    def test_adr_exists_cites_the_rights_note_and_defers(self):
        text = ADR.read_text("utf-8")
        assert "OPERATOR" in text
        assert "2026-11-22-rights-and-redistribution-inventory.md" in text
        assert RIGHTS_NOTE.is_file()

    def test_adr_states_both_rehearsal_commands(self):
        """The paid-build rehearsal is documented (acceptance: docs
        state the command) — deb + flatpak, both carrying the switch."""
        text = ADR.read_text("utf-8")
        assert "PAID_ARTIFACT=1" in text
        assert "build-deb.sh" in text
        assert "paid_policy.py flatpak" in text

    def test_adr_option_sections_cover_both_options(self):
        text = ADR.read_text("utf-8")
        assert "Option A" in text and "Option B" in text

    def test_the_committed_marker_parses(self):
        """One well-formed marker, whichever value the operator has on
        record — OPERATOR today, A or B once decided (so the decision is
        the operator's edit and nothing else has to change)."""
        assert paid.operator_decision() in (paid.OPERATOR, paid.OPTION_A,
                                            paid.OPTION_B)

    def test_marker_and_status_line_agree(self):
        """OPERATOR means 'not decided': the human-readable Status says
        so too, and a decided marker no longer claims deferral."""
        deferred = "decision deferred to OPERATOR" in ADR.read_text("utf-8")
        assert (paid.operator_decision() == paid.OPERATOR) is deferred

    def test_hardening_paragraph_names_real_things(self):
        """The landed hardening is documented against what exists: the
        vendored sdist path, the fail-closed gate, and the one-line
        re-check the release checklist can run."""
        text = ADR.read_text("utf-8")
        assert f"packaging/wheels/{paid.AGPL_SDIST_NAME}" in text
        assert "fails closed" in text
        assert "paid_policy.py verify" in text
