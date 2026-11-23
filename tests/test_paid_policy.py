"""PAID_ARTIFACT policy hooks (ADR-0008) — the one-switch AGPL remediation.

The rights inventory
(docs/research/2026-11-22-rights-and-redistribution-inventory.md §2.1,
"The AGPL finding") found the one real copyleft wrinkle: the vendored
``global-shortcut-portal`` wheel is AGPL-3.0-only and installs into the
Flatpak. A paid artifact must either offer that wheel's Corresponding
Source (option A) or drop the ``portal`` extra (option B) — and the
decision belongs to the OPERATOR, so BOTH paths ship behind one switch
and are tested here, offline, with fakes plus the real committed inputs:

* option normalization: the env contract (default A, A/B accepted,
  garbage refused with an actionable message);
* the dependency-set filtering (option B): a FAKE lock drops exactly
  the AGPL pin; the REAL committed deb lock filters to itself — the
  "deb never carried the wheel" guard the ADR relies on;
* the flatpak manifest transform: option B removes the wheel from the
  pip line, the license install and the source item (parsed output
  stays a well-formed manifest for BOTH committed variants); option A
  appends the ``agpl-source-offer`` module with the Flathub-required
  license install;
* the option-A offer bytes: extracted from the REAL vendored wheel —
  license text, package sources, and the SOURCE notice naming the
  pinned sdist;
* the build-deb.sh wiring (source-contract assertions, like
  TestBuildDebContract in test_deb_lock.py pins the hash gate);
* the ADR states the OPERATOR decision marker and the rehearsal
  commands (the docs the operator actually runs).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from tests.test_flatpak_manifest import (
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
        assert paid.normalize_option(None) == paid.OPTION_A
        assert paid.normalize_option("") == paid.OPTION_A
        assert paid.normalize_option("   ") == paid.OPTION_A

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

        # switch ON + option B: the AGPL pin is gone
        rc = paid.main(["filter-lock", "--lock", str(lock)],
                       env={paid.SWITCH_ENV: "1",
                            paid.OPTION_ENV: "B"})
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
                       env={paid.SWITCH_ENV: "1", paid.OPTION_ENV: "b"})
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

    def test_offer_module_is_identical_for_both_variants(self):
        """The divergence rule survives the transform: the appended
        module adds no sources, so both variants grow identical bytes."""
        ci = paid.transform_manifest_text(
            MANIFEST_PATHS[CI].read_text("utf-8"), "A")
        flathub = paid.transform_manifest_text(
            MANIFEST_PATHS[FLATHUB].read_text("utf-8"), "A")
        assert ci.split("  - name: agpl-source-offer")[1] \
            == flathub.split("  - name: agpl-source-offer")[1]

    def test_offer_commands_copy_sources_and_license(self):
        parsed = parse_manifest(
            paid.transform_manifest_text(FAKE_MANIFEST, "A"))
        offer = parsed["modules"][-1]
        cmds = "\n".join(offer["build-commands"])
        assert "share/licenses/$FLATPAK_ID/agpl-source-offer" in cmds
        assert "cp -r" in cmds and "/src" in cmds


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
