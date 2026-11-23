"""PAID_ARTIFACT — the paid-artifact AGPL policy switch (ADR-0008).

The rights inventory
(docs/research/2026-11-22-rights-and-redistribution-inventory.md) found
the one copyleft wrinkle: the vendored ``global-shortcut-portal`` wheel
is AGPL-3.0-only and installs into the Flatpak; a PAID artifact must
offer that wheel's Corresponding Source or drop it. Both remediation
paths are implemented behind one switch (packaging/paidartifact.py)
and pinned here OFFLINE, with fakes — no deb build, no flatpak-builder:

* the switch itself: default off, ``PAID_ARTIFACT=1`` REQUIRES an
  explicit ``PAID_AGPL_POLICY`` (a compliance switch must not guess);
* the dependency-set filter: drop-portal removes exactly the AGPL
  wheel — dbus-next (MIT, EIS insertion) stays in every mode;
* the deb lock guard: the AGPL wheel never enters constraints;
* the flatpak manifest transform: drop-portal removes the wheel from
  the deps pip line + license line + source block, source appends the
  pinned sdist — verified against BOTH committed manifests, with the
  manifest contract re-parsed (tests.test_flatpak_manifest.parser);
* the vendored wheel + sdist match the content pins option A installs;
* build-deb.sh wiring: resolve-before-use, option-A block gating,
  script filenames == module constants.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_flatpak_manifest import CI, FLATHUB, MANIFEST_PATHS, parse_manifest

REPO = Path(__file__).resolve().parents[1]
PACKAGING = REPO / "packaging"

_spec = importlib.util.spec_from_file_location(
    "paidartifact", PACKAGING / "paidartifact.py")
paidartifact = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(paidartifact)

AGPL_WHEEL = paidartifact.AGPL_WHEEL
AGPL_SDIST = paidartifact.AGPL_SDIST


def _env(paid: str | None, policy: str | None = None) -> dict[str, str]:
    env = {"PATH": "/usr/bin:/bin"}
    if paid is not None:
        env["PAID_ARTIFACT"] = paid
    if policy is not None:
        env["PAID_AGPL_POLICY"] = policy
    return env


# -- the switch --------------------------------------------------------------


class TestResolveMode:
    def test_default_is_off(self):
        assert paidartifact.resolve_mode(_env(None)) == "off"
        assert paidartifact.resolve_mode(_env("")) == "off"
        assert paidartifact.resolve_mode(_env("0")) == "off"

    def test_paid_requires_an_explicit_policy(self):
        with pytest.raises(paidartifact.PaidArtifactError, match="ADR-0008"):
            paidartifact.resolve_mode(_env("1"))
        with pytest.raises(paidartifact.PaidArtifactError,
                           match="PAID_AGPL_POLICY"):
            paidartifact.resolve_mode(_env("1", "bundle-it"))

    @pytest.mark.parametrize("policy,mode", [("source", "source"),
                                             ("drop-portal", "drop-portal")])
    def test_both_policies_resolve(self, policy, mode):
        assert paidartifact.resolve_mode(_env("1", policy)) == mode

    def test_non_boolean_paid_value_is_an_error(self):
        with pytest.raises(paidartifact.PaidArtifactError,
                           match="expected '1'"):
            paidartifact.resolve_mode(_env("yes"))

    def test_free_build_ignores_a_stray_policy(self):
        # off means off: a leftover PAID_AGPL_POLICY cannot re-engage it
        assert paidartifact.resolve_mode(_env("0", "source")) == "off"


# -- the dependency-set filter ------------------------------------------------

FAKE_WHEELS = [
    "six-1.17.0-py2.py3-none-any.whl",
    "python_xlib-0.33-py2.py3-none-any.whl",
    "dbus_next-0.2.3-py3-none-any.whl",
    "global_shortcut_portal-0.1.0-py3-none-any.whl",
]


class TestFilterWheels:
    def test_drop_portal_removes_exactly_the_agpl_wheel(self):
        assert paidartifact.filter_wheels(FAKE_WHEELS, "drop-portal") == [
            "six-1.17.0-py2.py3-none-any.whl",
            "python_xlib-0.33-py2.py3-none-any.whl",
            "dbus_next-0.2.3-py3-none-any.whl",
        ]

    def test_dbus_next_stays_in_every_mode(self):
        """dbus-next is MIT and EIS insertion (fluidvoice/portal_eis.py)
        imports it — only the AGPL wheel is the remediation target."""
        for mode in ("off", "source", "drop-portal"):
            assert any(w.startswith("dbus_next-")
                       for w in paidartifact.filter_wheels(FAKE_WHEELS, mode))

    def test_source_and_off_keep_the_set_unchanged(self):
        for mode in ("off", "source"):
            assert paidartifact.filter_wheels(FAKE_WHEELS, mode) \
                == FAKE_WHEELS

    def test_version_bumps_of_the_agpl_wheel_are_caught_too(self):
        wheels = FAKE_WHEELS + ["global_shortcut_portal-9.9.9-py3-none-any.whl"]
        out = paidartifact.filter_wheels(wheels, "drop-portal")
        assert not any("global_shortcut" in w for w in out)


# -- the deb lock guard --------------------------------------------------------


class TestDebLockGuard:
    CLEAN_LOCK = ("# header\n"
                  "pillow-13.0.0 --hash=sha256:" + "a" * 64 + "\n")
    AGPL_LOCK = ("# header\n"
                 "global-shortcut-portal==0.1.0 --hash=sha256:" + "b" * 64
                 + "\n")

    @pytest.mark.parametrize("mode", ["source", "drop-portal"])
    def test_agpl_pin_in_the_lock_fails_every_paid_mode(self, mode):
        with pytest.raises(paidartifact.PaidArtifactError,
                           match="vendored pin"):
            paidartifact.deb_lock_guard(self.AGPL_LOCK, mode)

    @pytest.mark.parametrize("mode", ["off", "source", "drop-portal"])
    def test_clean_lock_passes(self, mode):
        summary = paidartifact.deb_lock_guard(self.CLEAN_LOCK, mode)
        assert "absent" in summary or "not engaged" in summary

    def test_committed_constraints_pass_both_paid_modes(self):
        text = (PACKAGING / "deb" / "constraints.txt").read_text("utf-8")
        for mode in ("source", "drop-portal"):
            assert "absent" in paidartifact.deb_lock_guard(text, mode)


# -- the flatpak manifest transform: fakes -------------------------------------


def _mini_manifest(*, remote: bool, wheel_last: bool = True) -> str:
    """The deps-module shape of both committed manifests, minimal: a
    pip line, per-package license lines, per-wheel file sources."""
    wheels = ["six-1.17.0-py2.py3-none-any.whl",
              "global_shortcut_portal-0.1.0-py3-none-any.whl"]
    if not wheel_last:
        wheels.reverse()
    pip = "      - pip3 install --no-index --prefix=$FLATPAK_DEST " \
        + " ".join(wheels)
    locate = "url: https://files.pythonhosted.org/x/y/{name}" if remote \
        else "path: wheels/{name}"
    sources = "\n".join(
        f"      - type: file\n        {locate.format(name=w)}\n"
        f"        sha256: {'c' * 64}" for w in wheels)
    return f"""id: io.github.acailic.SayItErmano
modules:
  - name: sayitermano
    buildsystem: simple
    build-commands:
      - pip3 install --no-build-isolation --no-deps --prefix=$FLATPAK_DEST .
    sources:
      - type: git
        url: https://github.com/acailic/SayItErmano.git
        tag: v9.9.9
  - name: deps
    buildsystem: simple
    build-commands:
{pip}
      - install -Dm0644 -t $FLATPAK_DEST/share/licenses/$FLATPAK_ID/six $FLATPAK_DEST/lib/python3*/site-packages/six-*.dist-info/LICENSE
      - install -Dm0644 -t $FLATPAK_DEST/share/licenses/$FLATPAK_ID/global-shortcut-portal $FLATPAK_DEST/lib/python3*/site-packages/global_shortcut_portal-*.dist-info/licenses/LICENSE
    sources:
{sources}
"""


class TestFlatpakTransformFakes:
    def test_off_is_an_identity(self):
        text = _mini_manifest(remote=False)
        assert paidartifact.flatpak_transform(text, "off") == text

    def test_drop_portal_removes_wheel_license_and_source(self):
        out = paidartifact.flatpak_transform(_mini_manifest(remote=False),
                                             "drop-portal")
        assert AGPL_WHEEL not in out
        assert "six-1.17.0-py2.py3-none-any.whl" in out
        assert "licenses/$FLATPAK_ID/global-shortcut-portal" not in out
        assert "licenses/$FLATPAK_ID/six" in out
        assert paidartifact.DROP_NOTE in out
        # no double spaces where the wheel token was removed
        pip = [ln for ln in out.splitlines()
               if "pip3 install --no-index" in ln]
        assert pip == ["      - pip3 install --no-index "
                       "--prefix=$FLATPAK_DEST six-1.17.0-py2.py3-none-any.whl"]

    def test_drop_portal_mid_line_wheel_keeps_single_spaces(self):
        """Regression: the wheel is not always the LAST pip token — the
        remainder must survive with clean spacing."""
        out = paidartifact.flatpak_transform(
            _mini_manifest(remote=True, wheel_last=False), "drop-portal")
        pip = [ln for ln in out.splitlines()
               if "pip3 install --no-index" in ln]
        assert pip == ["      - pip3 install --no-index "
                       "--prefix=$FLATPAK_DEST six-1.17.0-py2.py3-none-any.whl"]

    def test_source_appends_sdist_and_license_install_local(self):
        out = paidartifact.flatpak_transform(_mini_manifest(remote=False),
                                             "source")
        assert f"path: wheels/{AGPL_SDIST}" in out
        assert paidartifact.AGPL_SDIST_URL not in out
        assert f"sha256: {paidartifact.AGPL_SDIST_SHA256}" in out
        assert AGPL_WHEEL in out  # option A keeps the wheel installed
        assert paidartifact.SDIST_INSTALL in out
        # the install command lands in build-commands, right before the
        # deps module's `sources:` key (the LAST one — the app module
        # has its own)
        deps_at = out.rindex("    sources:")
        cmds = out[:deps_at]
        assert cmds.splitlines()[-1] == "      - " + paidartifact.SDIST_INSTALL

    def test_source_appends_remote_url_for_the_flathub_shape(self):
        out = paidartifact.flatpak_transform(_mini_manifest(remote=True),
                                             "source")
        assert f"url: {paidartifact.AGPL_SDIST_URL}" in out
        assert "path: wheels/" not in out

    @pytest.mark.parametrize("mangle", [
        lambda t: t.replace("pip3 install --no-index", "pip3 install"),
        lambda t: t.replace("global-shortcut-portal $", "gone $"),
        lambda t: t.replace(
            "      - type: file\n        path: wheels/"
            + AGPL_WHEEL, ""),
    ])
    def test_unrecognized_shapes_fail_loudly(self, mangle):
        with pytest.raises(paidartifact.PaidArtifactError):
            paidartifact.flatpak_transform(mangle(_mini_manifest(
                remote=False)), "drop-portal")


# -- the flatpak manifest transform: the committed manifests -------------------


class TestFlatpakTransformCommitted:
    """The real rehearsal input: both committed variants transform into
    manifests that still satisfy the structural contract."""

    @pytest.mark.parametrize("variant", [CI, FLATHUB])
    def test_drop_portal(self, variant):
        original = MANIFEST_PATHS[variant].read_text("utf-8")
        out = paidartifact.flatpak_transform(original, "drop-portal")
        assert AGPL_WHEEL not in out
        assert "licenses/$FLATPAK_ID/global-shortcut-portal" not in out
        assert "global_shortcut_portal-" not in out
        assert "dbus_next-0.2.3-py3-none-any.whl" in out
        manifest = parse_manifest(out)
        deps = next(m for m in manifest["modules"] if m["name"] == "deps")
        wheels = [s.get("url", s.get("path")).split("/")[-1]
                  for s in deps["sources"]]
        assert AGPL_WHEEL not in wheels
        pip = next(c for c in deps["build-commands"]
                   if c.startswith("pip3 install"))
        for wheel in wheels:
            assert wheel in pip
        assert "dbus_next" in pip and "pillow" in pip

    @pytest.mark.parametrize("variant", [CI, FLATHUB])
    def test_source(self, variant):
        original = MANIFEST_PATHS[variant].read_text("utf-8")
        out = paidartifact.flatpak_transform(original, "source")
        manifest = parse_manifest(out)
        deps = next(m for m in manifest["modules"] if m["name"] == "deps")
        wheels = [s.get("url", s.get("path")).split("/")[-1]
                  for s in deps["sources"]]
        assert wheels.count(AGPL_SDIST) == 1
        sdist = next(s for s in deps["sources"]
                     if (s.get("url", s.get("path", "")).endswith(AGPL_SDIST)))
        assert sdist["sha256"] == paidartifact.AGPL_SDIST_SHA256
        assert sdist["type"] == "file"
        if variant == FLATHUB:
            assert sdist["url"].startswith("https://")
        else:
            assert sdist["path"] == f"wheels/{AGPL_SDIST}"
        installs = [c for c in deps["build-commands"]
                    if c.startswith("install ")]
        assert any(c.endswith(f"licenses/$FLATPAK_ID/global-shortcut-portal/"
                              f"{AGPL_SDIST}") for c in installs)
        # the wheel itself stays installed (option A bundles it)
        assert any(AGPL_WHEEL in c
                   for c in deps["build-commands"] if "pip3 install" in c)

    @pytest.mark.parametrize("mode", ["source", "drop-portal"])
    def test_variants_still_differ_only_in_their_sources(self, mode):
        def without_sources(variant: str) -> dict:
            parsed = parse_manifest(paidartifact.flatpak_transform(
                MANIFEST_PATHS[variant].read_text("utf-8"), mode))
            return parsed | {"modules": [
                {k: v for k, v in m.items() if k != "sources"}
                for m in parsed["modules"]]}
        assert without_sources(FLATHUB) == without_sources(CI), \
            "paid transform broke the divergence rule"

    @pytest.mark.parametrize("mode", ["source", "drop-portal"])
    def test_permissions_and_identity_are_untouched(self, mode):
        original = MANIFEST_PATHS[CI].read_text("utf-8")
        out = paidartifact.flatpak_transform(original, mode)
        parsed = parse_manifest(out)
        assert parsed["id"] == "io.github.acailic.SayItErmano"
        assert {a for a in parsed["finish-args"] if not a.startswith("--env=")} \
            == {"--socket=wayland", "--socket=pulseaudio",
                "--device=dri", "--share=network"}


# -- the vendored AGPL artifacts ------------------------------------------------


class TestVendoredArtifacts:
    def test_wheel_and_sdist_match_their_pins(self):
        assert paidartifact.verify_vendored().startswith("AGPL artifacts OK")

    def test_license_extraction_writes_the_agpl_text(self, tmp_path):
        dest = tmp_path / "LICENSE"
        paidartifact.extract_license(dest)
        text = dest.read_text("utf-8")
        assert "GNU AFFERO GENERAL PUBLIC LICENSE" in text
        assert "Version 3" in text


# -- build-deb.sh wiring (script-contract, the TestBuildDebContract style) -----


class TestBuildDebWiring:
    SCRIPT = (PACKAGING / "build-deb.sh").read_text("utf-8")

    def test_mode_is_resolved_through_the_module_before_any_use(self):
        resolve_at = self.SCRIPT.index("paidartifact.py\" resolve")
        first_use_at = self.SCRIPT.index('"$PAID_MODE"')
        assert resolve_at < first_use_at

    def test_free_build_is_the_default(self):
        assert 'PAID_ARTIFACT=1 engages paid mode (default off' in self.SCRIPT

    def test_option_a_block_is_gated_on_the_source_mode(self):
        gate = self.SCRIPT.index('[ "$PAID_MODE" = "source" ]')
        block = self.SCRIPT.index('"$PIP" install -q --no-deps '
                                  '"$REPO/$AGPL_WHEEL"')
        assert gate < block

    def test_guard_runs_for_any_paid_mode(self):
        assert '[ "$PAID_MODE" = "off" ] \\\n    || ' in self.SCRIPT

    def test_script_filenames_match_the_module_pins(self):
        """The shell hardcodes paths; the module owns the truth — this
        pins them together so a wheel bump cannot drift."""
        for filename in (AGPL_WHEEL, AGPL_SDIST):
            assert f'packaging/wheels/{filename}"' in self.SCRIPT


# -- the CLI (subprocess, offline) ----------------------------------------------


class TestCli:
    def _run(self, *args: str, env: dict[str, str]):
        return subprocess.run(
            [sys.executable, str(PACKAGING / "paidartifact.py"), *args],
            capture_output=True, text=True, timeout=60, env=env)

    def test_resolve_prints_the_mode(self):
        for policy, mode in (("source", "source"),
                             ("drop-portal", "drop-portal")):
            r = self._run("resolve", env=_env("1", policy))
            assert r.returncode == 0, r.stderr
            assert r.stdout.strip() == mode

    def test_resolve_rejects_a_paid_build_without_a_policy(self):
        r = self._run("resolve", env=_env("1"))
        assert r.returncode == 1
        assert "ADR-0008" in r.stderr
        assert "PAID_AGPL_POLICY" in r.stderr

    def test_flatpak_is_identity_when_the_switch_is_off(self, tmp_path):
        src = MANIFEST_PATHS[CI]
        out = tmp_path / "paid.yml"
        r = self._run("flatpak", str(src), "-o", str(out), env=_env(None))
        assert r.returncode == 0, r.stderr
        assert out.read_text("utf-8") == src.read_text("utf-8")

    def test_flatpak_rewrites_under_the_paid_switch(self, tmp_path):
        out = tmp_path / "paid.yml"
        r = self._run("flatpak", str(MANIFEST_PATHS[CI]), "-o", str(out),
                      env=_env("1", "drop-portal"))
        assert r.returncode == 0, r.stderr
        assert AGPL_WHEEL not in out.read_text("utf-8")

    def test_verify_checks_the_vendored_artifacts(self):
        r = self._run("verify", env=_env(None))
        assert r.returncode == 0, r.stderr
        assert "match their pins" in r.stdout
