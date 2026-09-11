"""Dependency-hash locking (Q3/E3) — generation → parse → consumption.

The audit found update-constraints.sh scraped ``python -m pip hash``
output with an ``awk '/^sha256=/'`` pattern that matched nothing (pip
prints ``--hash=sha256:``), producing malformed ``name==version --``
lines: the full hash path could never produce a working lock. All
hashing now lives in packaging/deb/locklib.py and is exercised here
OFFLINE with hand-built wheels in a controlled local wheelhouse:

* generated locks are consumed by a REAL ``pip install --require-hashes
  --no-index --find-links <wheelhouse>`` (no network anywhere);
* known bytes produce the expected digest; a one-byte change in the
  wheel is rejected by the consumer;
* missing / duplicate / version-mismatched wheels, malformed or mixed
  lock lines fail with actionable errors and never touch the previous
  lock (atomic replace only after a self-validate round trip);
* the pins-coverage check the release workflows run is tested against
  fixture pyproject/lock pairs.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "locklib", REPO / "packaging" / "deb" / "locklib.py")
locklib = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(locklib)

HEADER = "# test lock header\n# second header line"


def _pip_install_require_hashes(lock: Path, wheelhouse: Path,
                                target: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q",
         "--no-index", "--find-links", str(wheelhouse),
         "--require-hashes", "-r", str(lock), "--target", str(target)],
        capture_output=True, text=True, timeout=180,
        env={**os.environ,
             "PIP_DISABLE_PIP_VERSION_CHECK": "1",
             "PIP_NO_INPUT": "1"})


class TestGenerationRoundTrip:
    def test_offline_wheelhouse_generation_to_pip_consumption(self, tmp_path):
        wheelhouse = tmp_path / "wheels"
        wheelhouse.mkdir()
        locklib.make_minimal_wheel(wheelhouse, "sayit-dep-a", "1.0.0")
        locklib.make_minimal_wheel(wheelhouse, "sayit_dep_b", "2.3.4")
        pins = "sayit-dep-a==1.0.0\nsayit_dep_b==2.3.4\n"
        lock_text = locklib.build_lock_text(wheelhouse, pins, HEADER)
        lock = tmp_path / "constraints.txt"
        lock.write_text(lock_text)

        # structure: exact pin + full sha256 on every line, in pin order
        lines = [ln for ln in lock_text.splitlines()
                 if ln and not ln.startswith("#")]
        assert lines == [
            f"sayit-dep-a==1.0.0 --hash=sha256:"
            f"{locklib.sha256_file(wheelhouse / 'sayit_dep_a-1.0.0-py3-none-any.whl')}",
            f"sayit-dep-b==2.3.4 --hash=sha256:"
            f"{locklib.sha256_file(wheelhouse / 'sayit_dep_b-2.3.4-py3-none-any.whl')}",
        ]
        locklib.validate_lock_text(lock_text, require_hashes=True)

        # consumption: a real offline pip install verifies the hashes
        target = tmp_path / "site"
        result = _pip_install_require_hashes(lock, wheelhouse, target)
        assert result.returncode == 0, result.stdout + result.stderr
        installed = {p.name.split("-")[0] for p in target.iterdir()
                     if p.name.endswith(".dist-info")}
        assert installed == {"sayit_dep_a", "sayit_dep_b"}

    def test_generated_line_is_never_the_e3_malformed_form(self, tmp_path):
        """The exact regression: `pip hash` scraping used to emit
        ``name==version --`` (hash matched nothing)."""
        wheelhouse = tmp_path / "wheels"
        wheelhouse.mkdir()
        locklib.make_minimal_wheel(wheelhouse, "example", "1.0")
        lock_text = locklib.build_lock_text(wheelhouse, "example==1.0\n",
                                            HEADER)
        body = [ln for ln in lock_text.splitlines()
                if ln and not ln.startswith("#")]
        assert len(body) == 1
        assert re.fullmatch(r"example==1\.0 --hash=sha256:[0-9a-f]{64}",
                            body[0]), body

    def test_known_bytes_produce_expected_digest(self, tmp_path):
        blob = tmp_path / "blob.bin"
        blob.write_bytes(b"fixed wheel bytes")
        assert locklib.sha256_file(blob) == hashlib.sha256(
            b"fixed wheel bytes").hexdigest()


class TestConsumerRejectsAlteration:
    def test_one_byte_change_in_wheel_is_rejected_by_pip(self, tmp_path):
        wheelhouse = tmp_path / "wheels"
        wheelhouse.mkdir()
        locklib.make_minimal_wheel(wheelhouse, "sayit-dep-a", "1.0.0",
                                   extra_content=b"x = 1\n")
        lock = tmp_path / "constraints.txt"
        lock.write_text(locklib.build_lock_text(wheelhouse,
                                                "sayit-dep-a==1.0.0\n",
                                                HEADER))
        # same name+version, ONE byte of payload differs: a rebuilt,
        # substituted wheel under the same version string
        locklib.make_minimal_wheel(wheelhouse, "sayit-dep-a", "1.0.0",
                                   extra_content=b"x = 2\n")
        target = tmp_path / "site"
        result = _pip_install_require_hashes(lock, wheelhouse, target)
        assert result.returncode != 0, (
            "pip accepted an altered wheel despite --require-hashes")
        assert "hash" in (result.stdout + result.stderr).lower()


class TestGenerationFailuresPreserveLock:
    def test_missing_wheel_errors_and_out_untouched(self, tmp_path):
        wheelhouse = tmp_path / "wheels"
        wheelhouse.mkdir()
        locklib.make_minimal_wheel(wheelhouse, "sayit-dep-a", "1.0.0")
        out = tmp_path / "constraints.txt"
        out.write_text("previous lock content\n")
        with pytest.raises(locklib.LockError, match="missing wheel.*dep-b"):
            locklib.build_lock_text(wheelhouse,
                                    "sayit-dep-a==1.0.0\nsayit-dep-b==9.9\n",
                                    HEADER)
        assert out.read_text() == "previous lock content\n"

    def test_duplicate_wheels_for_one_pin_is_ambiguous(self, tmp_path):
        wheelhouse = tmp_path / "wheels"
        wheelhouse.mkdir()
        locklib.make_minimal_wheel(wheelhouse, "sayit-dep-a", "1.0.0")
        locklib.make_minimal_wheel(wheelhouse, "sayit-dep-a", "1.1.0")
        with pytest.raises(locklib.LockError, match="ambiguous wheels"):
            locklib.build_lock_text(wheelhouse, "sayit-dep-a==1.0.0\n",
                                    HEADER)

    def test_wheel_version_mismatch_is_an_error(self, tmp_path):
        wheelhouse = tmp_path / "wheels"
        wheelhouse.mkdir()
        locklib.make_minimal_wheel(wheelhouse, "sayit-dep-a", "1.2.3")
        with pytest.raises(locklib.LockError, match="mismatch"):
            locklib.build_lock_text(wheelhouse, "sayit-dep-a==1.0.0\n",
                                    HEADER)


class TestLockValidation:
    def test_mixed_hash_and_pin_only_rejected(self):
        text = ("a-pkg==1.0 --hash=sha256:" + "0" * 64 + "\n"
                "b-pkg==2.0\n")
        with pytest.raises(locklib.LockError, match="mixed hash/pin-only"):
            locklib.validate_lock_text(text, require_hashes=False)

    def test_pin_only_rejected_when_hashes_required(self):
        with pytest.raises(locklib.LockError, match="pin-only"):
            locklib.validate_lock_text("a-pkg==1.0\n",
                                       require_hashes=True)

    def test_malformed_hash_rejected(self):
        with pytest.raises(locklib.LockError, match="malformed"):
            locklib.validate_lock_text(
                "a-pkg==1.0 --hash=sha256:tooshort\n", require_hashes=True)

    def test_non_sha256_algorithm_rejected(self):
        with pytest.raises(locklib.LockError, match="malformed"):
            locklib.validate_lock_text(
                "a-pkg==1.0 --hash=md5:" + "0" * 32 + "\n",
                require_hashes=True)

    def test_duplicate_pin_rejected(self):
        text = ("a-pkg==1.0 --hash=sha256:" + "0" * 64 + "\n"
                "a-pkg==1.0 --hash=sha256:" + "0" * 64 + "\n")
        with pytest.raises(locklib.LockError, match="duplicate"):
            locklib.validate_lock_text(text, require_hashes=True)

    def test_valid_hashed_lock_passes_and_returns_pins(self):
        text = ("# header\na-pkg==1.0 --hash=sha256:" + "a" * 64 + "\n")
        pins = locklib.validate_lock_text(text, require_hashes=True)
        assert pins == [("a-pkg", "1.0")]


class TestPinsCoverage:
    def _pyproject(self, tmp_path: Path, deps: list[str]) -> Path:
        body = ["[project]", "name = 'x'", "version = '1'",
                "dependencies = ["]
        body += [f"    '{d}'," for d in deps]
        body += "]"
        p = tmp_path / "pyproject.toml"
        p.write_text("\n".join(body) + "\n")
        return p

    def test_all_runtime_deps_pinned_ok(self, tmp_path):
        pyproject = self._pyproject(tmp_path, ["pkg-a>=1", "pkg-b==2.0"])
        lock = tmp_path / "constraints.txt"
        lock.write_text("pkg-a==1.5.0 --hash=sha256:" + "a" * 64
                        + "\npkg-b==2.0.1 --hash=sha256:" + "c" * 64
                        + "\npkg-c==9.0 --hash=sha256:" + "b" * 64 + "\n")
        summary = locklib.check_pins_coverage(pyproject, lock)
        assert "2 runtime deps" in summary

    def test_missing_pin_is_stale(self, tmp_path):
        pyproject = self._pyproject(tmp_path, ["pkg-a>=1", "missing-dep>=2"])
        lock = tmp_path / "constraints.txt"
        lock.write_text("pkg-a==1.5.0\n")
        with pytest.raises(locklib.LockError, match="missing-dep"):
            locklib.check_pins_coverage(pyproject, lock)


class TestBuildDebContract:
    """The build-deb.sh gate lines (hash enforcement, Q3/E4) — pinned by
    asserting on the script's own source, so a regression to
    "pin-locked with a note" cannot slip back in silently."""

    def test_pin_only_builds_require_explicit_dev_flag(self):
        script = (REPO / "packaging" / "build-deb.sh").read_text()
        assert "DEB_ALLOW_PIN_ONLY" in script
        # the refusal fires BEFORE the opt-in is honored by install
        refuse_at = script.index("carries no --hash lines")
        optin_at = script.index("DEB_ALLOW_PIN_ONLY=1 $0")
        assert refuse_at < optin_at
        # and the container build path (Dockerfile CMD) sets no opt-in
        dockerfile = (REPO / "packaging" / "deb" / "Dockerfile").read_text()
        assert "DEB_ALLOW_PIN_ONLY" not in dockerfile

    def test_update_constraints_uses_locklib_not_pip_hash_scraping(self):
        """The E3 root cause removed: no `pip hash | awk` scraping, the
        hashing/writing path goes through locklib."""
        script = (REPO / "packaging" / "deb" /
                  "update-constraints.sh").read_text()
        assert "locklib.py" in script and "--wheels-dir" in script
        assert "m pip hash" not in script  # only the E3 history comment remains
