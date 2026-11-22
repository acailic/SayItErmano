"""scripts/upgrade_rehearsal.py — the retention/version assertions (Q9).

Fake-driven like tests/test_qa_matrix.py: the one-shot dispatch, deb
staging and venv building run against stub runners and stub venvs; the
REAL rehearsal (pip, dpkg-deb, install-one-shot.sh) is the evidence run
recorded under docs/quality/upgrade/.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "upgrade_rehearsal", REPO / "scripts" / "upgrade_rehearsal.py")
ur = importlib.util.module_from_spec(_spec)
sys.modules["upgrade_rehearsal"] = ur
_spec.loader.exec_module(ur)


# ── version bump ────────────────────────────────────────────────────────────

def test_bump_version_rewrites_the_line():
    src = '"""doc"""\n\n__version__ = "0.9.0"\n'
    assert '__version__ = "0.9.1"' in ur.bump_version_src(src, "0.9.1")


def test_bump_version_refuses_ambiguous_source():
    with pytest.raises(ValueError):
        ur.bump_version_src('__version__ = "0.1.0"\n__version__ = "0.2.0"\n',
                            "0.9.1")
    with pytest.raises(ValueError):
        ur.bump_version_src("no version here\n", "0.9.1")


def test_parse_version_output():
    assert ur.parse_version_output("0.9.1\n") == "0.9.1"
    assert ur.parse_version_output("sayit-ermano 0.10.2") == "0.10.2"
    with pytest.raises(ValueError):
        ur.parse_version_output("nope")


# ── byte-identity / history diff ────────────────────────────────────────────

def test_diff_snapshot_identical_and_tampered():
    before = b"row1\nrow2\n"
    same = ur.diff_snapshot("history.jsonl", before, before)
    assert same["identical"] and same["before_sha256"] == same["after_sha256"]
    other = ur.diff_snapshot("history.jsonl", before, b"row1\nrowX\n")
    assert not other["identical"]
    assert other["before_sha256"] != other["after_sha256"]
    assert other["before_bytes"] == other["after_bytes"] == 10


def test_history_rows_parses_jsonl_and_ignores_blank_lines():
    text = ('{"ts": 1, "text": "a"}\n\n{"ts": 2, "text": "b"}\n')
    rows = ur.history_rows(text)
    assert [r["text"] for r in rows] == ["a", "b"]


# ── config preservation ─────────────────────────────────────────────────────

BEFORE_CFG = ('[general]\nlanguage = "en"\n\n[hotkey]\nkey = "F9"\n'
              'cancel_key = "F12"\n\n[sounds]\nvolume = 0.6\n')


def test_config_preserved_identical_and_with_new_defaults():
    assert ur.config_preserved(BEFORE_CFG, BEFORE_CFG)["preserved"]
    # an upgrade that ADDS keys (new defaults) keeps every user setting
    after = BEFORE_CFG + '\n[brand_new]\nenabled = true\n'
    result = ur.config_preserved(BEFORE_CFG, after)
    assert result["preserved"] and result["keys_after"] > result["keys_before"]


def test_config_preserved_catches_loss_and_change():
    lost = ('[general]\nlanguage = "en"\n\n[sounds]\nvolume = 0.6\n')
    result = ur.config_preserved(BEFORE_CFG, lost)
    assert not result["preserved"]
    assert "hotkey.key" in result["missing"] and "hotkey.cancel_key" \
        in result["missing"]
    changed = BEFORE_CFG.replace('key = "F9"', 'key = "F10"')
    result = ur.config_preserved(BEFORE_CFG, changed)
    assert not result["preserved"] and result["changed"] == ["hotkey.key"]


# ── deb staging with a stub venv builder + recording runner ────────────────

class Proc:
    def __init__(self, rc=0, out="", err=""):
        self.returncode = rc
        self.stdout = out
        self.stderr = err


class RecordingRunner:
    """Runs nothing; records argv, answers scripted results."""

    def __init__(self, results: dict | None = None):
        self.calls: list[list] = []
        self.results = results or {}

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        for needle, proc in self.results.items():
            if needle in " ".join(cmd):
                return proc
        return Proc()


def stub_venv_builder(venv_dir: Path, tree: Path, run=None, with_deps=True):
    binpy = venv_dir / "bin" / "python"
    binpy.parent.mkdir(parents=True)
    binpy.write_text("#!/bin/sh\necho 0.0.0-stub\n")
    binpy.chmod(0o755)


@pytest.mark.skipif(not Path("/usr/bin/dpkg-deb").exists(),
                    reason="dpkg-deb required to stage the package")
def test_build_deb_stages_the_payload_contract(tmp_path):
    tree = tmp_path / "tree"
    (tree / "fluidvoice").mkdir(parents=True)
    deb = ur.build_deb(tmp_path / "pkg", tree, "9.9.9", run=ur.default_run,
                       venv_builder=stub_venv_builder, with_deps=False)
    assert deb.exists()
    out = subprocess.run(["dpkg-deb", "-c", str(deb)], capture_output=True,
                         text=True)
    listed = out.stdout
    assert "opt/sayit-ermano/venv/bin/python" in listed
    assert "usr/bin/sayit-ermano" in listed
    assert "usr/share/applications/sayit-ermano.desktop" in listed
    assert "etc/xdg/autostart/sayit-ermano.desktop" in listed
    ctrl = subprocess.run(["dpkg-deb", "-f", str(deb), "Version"],
                          capture_output=True, text=True).stdout.strip()
    assert ctrl == "9.9.9-1"


def test_build_venv_pip_command_and_failure(tmp_path):
    runner = RecordingRunner()
    ur.build_venv(tmp_path / "venv", tmp_path, run=runner, with_deps=False)
    pip_calls = [c for c in runner.calls
                 if c[:3] == [sys.executable, "-m", "pip"]]
    assert pip_calls, runner.calls
    assert "--no-deps" in pip_calls[0] and str(tmp_path) in pip_calls[0]

    failing = RecordingRunner(results={"--python": Proc(rc=2, err="no net")})
    with pytest.raises(RuntimeError, match="staged venv"):
        ur.build_venv(tmp_path / "venv2", tmp_path, run=failing)


def test_run_one_shot_passes_home_and_answers_prompt(tmp_path):
    runner = RecordingRunner()
    ur.run_one_shot(tmp_path, tmp_path / "x.deb", update=True, run=runner)
    call = runner.calls[0]
    assert call[:2] == ["bash", str(ur.ONE_SHOT)]
    assert "--update" in call and str(tmp_path / "x.deb") in call


def test_step_check_accumulates_and_flags():
    step = ur.Step("x")
    assert step.check(True, "fine")
    assert not step.check(False, "broken")
    assert not step.ok
    assert step.details["checks"] == [{"what": "fine", "ok": True},
                                      {"what": "broken", "ok": False}]


def test_write_evidence_writes_dated_file_and_table(tmp_path):
    report = {"started_utc": "2026-11-22T06:00:00Z", "from_version": "0.9.0",
              "to_version": "0.9.1", "commit": "abc1234", "passed": True,
              "steps": [{"name": "upgrade", "ok": True,
                         "checks": [{"what": "x", "ok": True}]}]}
    path = ur.write_evidence(report, evidence_dir=tmp_path)
    assert path.name == "2026-11-22-v0.9.0-to-v0.9.1.json"
    md = (tmp_path / "README.md").read_text()
    assert "v0.9.0 -> v0.9.1" in md and "PASS" in md and "abc1234" in md
