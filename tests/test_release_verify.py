"""Release provenance chain (Q4/E5) — fixture-driven publish rehearsal.

Every rejection the plan demands is exercised against the same pure
functions the workflows call (scripts/release_verify.py):

* a same-version artifact built from DIFFERENT source is rejected
  (tracked-source digest mismatch);
* a failed or wrong workflow run is rejected; the prepare run is
  resolved by the PARENT of the release SHA (prepare pushes the bump
  commit after building), never by comparing to the release SHA itself;
* an artifact from a stale/foreign run for the same version name is
  rejected (resolution is by run identity, not name/index);
* expired artifacts, altered package bytes, and a moved dependency lock
  are rejected;
* the happy path proves the COMPLETE chain — manifest -> deb bytes ->
  source digest -> lock -> evidence — with no tag, release, or push
  anywhere in sight.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "release_verify", REPO / "scripts" / "release_verify.py")
rv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rv)

HAS_DPKG = shutil.which("dpkg-deb") is not None
needs_dpkg = pytest.mark.skipif(
    not HAS_DPKG, reason="dpkg-deb unavailable (not a Debian-family host)")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def make_mini_deb(tmp_path: Path, version: str) -> Path:
    """A real (tiny) deb with a correct control block."""
    stage = tmp_path / "stage"
    (stage / "DEBIAN").mkdir(parents=True)
    (stage / "usr" / "bin").mkdir(parents=True)
    (stage / "usr" / "bin" / "sayit-ermano").write_text("#!/bin/sh\n")
    (stage / "DEBIAN" / "control").write_text(
        "Package: sayit-ermano\n"
        f"Version: {version}-1\n"
        "Architecture: amd64\n"
        "Maintainer: test\n"
        "Description: test package\n")
    out = tmp_path / f"sayit-ermano_{version}-1_amd64.deb"
    subprocess.run(["dpkg-deb", "--build", "--root-owner-group",
                    str(stage), str(out)],
                   check=True, capture_output=True, timeout=120)
    return out


def git_repo(tmp_path: Path, *, files: dict[str, str] | None = None) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    def git(*a: str) -> None:
        subprocess.run(["git", "-C", str(repo), *a], check=True,
                       capture_output=True, timeout=60)
    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (repo / "packaging" / "deb").mkdir(parents=True)
    files = files if files is not None else {
        "pyproject.toml": "version = '1'",
        "packaging/deb/constraints.txt": "a==1 --hash=sha256:" + "0" * 64,
    }
    for rel, content in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(content)
    git("add", "-A")
    git("commit", "-qm", "init")
    return repo


def manifest_for(repo: Path, deb: Path, *, version: str,
                 run_id: int = 555) -> dict:
    return rv.build_manifest(repo, deb, version=version, run_id=run_id,
                             head_sha="f" * 40)


# ---------------------------------------------------------------------------
# digests
# ---------------------------------------------------------------------------

class TestTrackedSourceDigest:
    def test_digest_covers_tracked_worktree_content(self, tmp_path):
        repo = git_repo(tmp_path)
        d1 = rv.tracked_source_digest(repo)
        # untracked files cannot influence it
        (repo / "untracked.txt").write_text("noise")
        assert rv.tracked_source_digest(repo) == d1
        # a tracked file's WORKTREE change does (this is what carries the
        # pre-commit version bump: prepare builds from the sed'd tree)
        (repo / "pyproject.toml").write_text("version = '2'")
        d2 = rv.tracked_source_digest(repo)
        assert d2 != d1
        # ...and committing that change keeps the digest stable, because
        # the committed bytes are what the worktree already had — exactly
        # the prepare->publish equivalence being proven
        (repo / "untracked.txt").unlink()  # keep the tracked SET stable
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True,
                       capture_output=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "bump"],
                       check=True, capture_output=True)
        assert rv.tracked_source_digest(repo) == d2

    def test_different_source_tree_different_digest(self, tmp_path):
        a = git_repo(tmp_path / "a")
        b = git_repo(tmp_path / "b",
                     files={"pyproject.toml": "version = 'other'",
                            "packaging/deb/constraints.txt":
                                "a==1 --hash=sha256:" + "0" * 64})
        assert rv.tracked_source_digest(a) != rv.tracked_source_digest(b)


# ---------------------------------------------------------------------------
# run/artifact selection (the E5 core: identity, not name)
# ---------------------------------------------------------------------------

def _run(path, sha, status="completed", conclusion="success", rid=555):
    return {"id": rid, "name": "release-prepare", "path": path,
            "head_sha": sha, "status": status, "conclusion": conclusion}


class TestSelectPrepareRun:
    PARENT = "a" * 40

    def test_selects_successful_prepare_for_parent_sha(self):
        runs = [_run("ci.yml", "b" * 40),
                _run(rv.PREPARE_WORKFLOW, self.PARENT, rid=555),
                _run(rv.PREPARE_WORKFLOW, "c" * 40)]
        assert rv.select_prepare_run(runs, parent_sha=self.PARENT) == 555

    def test_failed_prepare_run_is_rejected(self):
        runs = [_run(rv.PREPARE_WORKFLOW, self.PARENT,
                     conclusion="failure")]
        with pytest.raises(rv.ReleaseError, match="did not succeed"):
            rv.select_prepare_run(runs, parent_sha=self.PARENT)

    def test_wrong_workflow_or_wrong_sha_is_rejected(self):
        # a successful run of the WRONG workflow for that sha...
        runs = [_run("ci.yml", self.PARENT)]
        with pytest.raises(rv.ReleaseError, match="no completed"):
            rv.select_prepare_run(runs, parent_sha=self.PARENT)
        # ...and the right workflow built for a DIFFERENT parent (a stale
        # prepare from before the branch moved) matches nothing
        runs = [_run(rv.PREPARE_WORKFLOW, "d" * 40)]
        with pytest.raises(rv.ReleaseError, match="re-run release-prepare"):
            rv.select_prepare_run(runs, parent_sha=self.PARENT)

    def test_green_ci_gate_requires_exact_sha(self):
        sha = "e" * 40
        ok = [_run(rv.CI_WORKFLOW, sha)]
        rv.verify_green_ci_run(ok, head_sha=sha)  # no raise
        with pytest.raises(rv.ReleaseError, match="no successful"):
            rv.verify_green_ci_run([_run(rv.CI_WORKFLOW, sha,
                                         conclusion="failure")],
                                   head_sha=sha)
        with pytest.raises(rv.ReleaseError):
            rv.verify_green_ci_run([_run(rv.CI_WORKFLOW, "other" * 8)],
                                   head_sha=sha)


class TestSelectArtifact:
    def _art(self, aid, rid, expired=False, name="deb-v9.9.9"):
        return {"id": aid, "name": name, "expired": expired,
                "workflow_run": {"id": rid}}

    def test_resolves_by_run_identity_not_index(self):
        arts = [self._art(1, 999), self._art(2, 555)]  # stale first on purpose
        art = rv.select_artifact(arts, name="deb-v9.9.9", run_id=555)
        assert art["id"] == 2

    def test_same_version_artifact_from_another_run_rejected(self):
        arts = [self._art(1, 999)]  # same NAME, foreign/stale run
        with pytest.raises(rv.ReleaseError, match="stale or"):
            rv.select_artifact(arts, name="deb-v9.9.9", run_id=555)

    def test_expired_artifact_rejected(self):
        arts = [self._art(1, 555, expired=True)]
        with pytest.raises(rv.ReleaseError, match="expired"):
            rv.select_artifact(arts, name="deb-v9.9.9", run_id=555)

    def test_missing_artifact_rejected(self):
        with pytest.raises(rv.ReleaseError, match="no artifact"):
            rv.select_artifact([], name="deb-v9.9.9", run_id=555)


# ---------------------------------------------------------------------------
# the full publish chain
# ---------------------------------------------------------------------------

@needs_dpkg
class TestPublishChainRehearsal:
    VERSION = "0.9.0"

    def _stage(self, tmp_path, *, lock_content=None):
        repo = git_repo(tmp_path)
        if lock_content is not None:
            (repo / "packaging/deb/constraints.txt").write_text(lock_content)
        deb = make_mini_deb(tmp_path, self.VERSION)
        mpath = tmp_path / "manifest.json"
        return repo, deb, mpath

    def test_full_chain_proves_manifest_to_source_without_tagging(self, tmp_path):
        repo, deb, mpath = self._stage(tmp_path)
        m = manifest_for(repo, deb, version=self.VERSION)
        mpath.write_text(json.dumps(m))
        evidence = (f"install smoke ok on ubuntu-24.04 VM, deb sha "
                    f"{m['package']['sha256']}")
        # the complete chain: manifest -> package bytes -> source digest
        # -> lock -> evidence. No tag, no release, no push happened here.
        out = rv.verify_publish(repo, mpath, deb, version=self.VERSION,
                                evidence=evidence)
        assert out["package"]["control_version"] == f"{self.VERSION}-1"

    def test_artifact_built_from_other_source_is_rejected(self, tmp_path):
        repo, deb, mpath = self._stage(tmp_path)
        mpath.write_text(json.dumps(manifest_for(repo, deb,
                                                 version=self.VERSION)))
        # the release checkout is a DIFFERENT source tree (same version
        # string): someone re-ran prepare with local changes committed
        other = git_repo(tmp_path / "other",
                         files={"pyproject.toml": "version = '1'",
                                "packaging/deb/constraints.txt":
                                    "a==1 --hash=sha256:" + "0" * 64,
                                "fluidvoice/x.py": "CHANGED = True"})
        with pytest.raises(rv.ReleaseError,
                           match="NOT what the artifact was built from"):
            rv.verify_publish(other, mpath, deb, version=self.VERSION,
                              evidence="EVIDENCE-SKIP: rehearsal")

    def test_altered_package_bytes_are_rejected(self, tmp_path):
        repo, deb, mpath = self._stage(tmp_path)
        m = manifest_for(repo, deb, version=self.VERSION)
        mpath.write_text(json.dumps(m))
        # same file name, flipped byte: the downloaded artifact was
        # tampered with / corrupted after prepare uploaded it
        data = bytearray(deb.read_bytes())
        data[len(data) // 2] ^= 0xFF
        deb.write_bytes(bytes(data))  # same path, flipped byte
        with pytest.raises(rv.ReleaseError, match="altered or wrong"):
            rv.verify_publish(repo, mpath, deb,
                              version=self.VERSION,
                              evidence="EVIDENCE-SKIP: rehearsal")

    def test_moved_dependency_lock_is_rejected(self, tmp_path):
        repo, deb, mpath = self._stage(tmp_path)
        mpath.write_text(json.dumps(manifest_for(repo, deb,
                                                 version=self.VERSION)))
        (repo / "packaging/deb/constraints.txt").write_text(
            "b==2 --hash=sha256:" + "1" * 64)  # lock changed after build
        with pytest.raises(rv.ReleaseError, match="lock moved"):
            rv.verify_publish(repo, mpath, deb, version=self.VERSION,
                              evidence="EVIDENCE-SKIP: rehearsal")

    def test_version_disagreement_rejected(self, tmp_path):
        repo, deb, mpath = self._stage(tmp_path)
        m = manifest_for(repo, deb, version=self.VERSION)
        mpath.write_text(json.dumps(m))
        with pytest.raises(rv.ReleaseError, match="version"):
            rv.verify_publish(repo, mpath, deb, version="0.9.1",
                              evidence="EVIDENCE-SKIP: rehearsal")

    def test_evidence_must_reference_the_artifact_or_waive_loudly(self, tmp_path):
        repo, deb, mpath = self._stage(tmp_path)
        m = manifest_for(repo, deb, version=self.VERSION)
        mpath.write_text(json.dumps(m))
        with pytest.raises(rv.ReleaseError, match="missing release evidence"):
            rv.verify_publish(repo, mpath, deb, version=self.VERSION)
        with pytest.raises(rv.ReleaseError, match="bound to the exact bytes"):
            rv.verify_publish(repo, mpath, deb, version=self.VERSION,
                              evidence="looks fine to me")
        # explicit waiver is accepted — but it is a decision on record,
        # never a silent skip
        rv.verify_publish(repo, mpath, deb, version=self.VERSION,
                          evidence="EVIDENCE-SKIP: patch release, no "
                                   "pipeline change")

    def test_manifest_schema_tampering_rejected(self, tmp_path):
        repo, deb, mpath = self._stage(tmp_path)
        m = manifest_for(repo, deb, version=self.VERSION)
        m["workflow"]["path"] = ".github/workflows/ci.yml"  # foreign wf
        mpath.write_text(json.dumps(m))
        with pytest.raises(rv.ReleaseError, match="workflow"):
            rv.verify_publish(repo, mpath, deb, version=self.VERSION,
                              evidence="EVIDENCE-SKIP: rehearsal")


class TestLocklibWiring:
    def test_workflows_use_shared_pins_coverage_not_inline_copies(self):
        """Q12: the stale-constraints check is ONE tested implementation;
        the workflows call locklib instead of carrying inline copies."""
        for wf in ("release-prepare.yml", "release-publish.yml"):
            text = (REPO / ".github" / "workflows" / wf).read_text()
            assert "pins-coverage" in text, wf
            assert "stale constraints — runtime deps" not in text, (
                f"{wf} still carries an inline stale-constraints copy")
