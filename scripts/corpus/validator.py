"""Batch manifest validator (the ``validate`` CLI's engine).

Structural defects (types, vocabularies, duplicates) are already
rejected by the loader; this pass owns the *semantic* checks that make
a recording batch safe to export and record against:

- every case's speaker exists in the roster and matches its id token
- taxonomy counts vs the spec's targets (warn under/over)
- recorded cases have audio on disk (existence only — never read bytes)
  and a transcript; negatives have an empty transcript
- audio and the manifest itself live OUTSIDE git (inside the worktree
  only under a gitignored directory such as ``eval-private/``)
- every speaker carries a consent reference that names them
  (withdrawal is by speaker id, spec §1.4)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .model import (
    ALL_TAXONOMIES,
    DATE_RE,
    ENVIRONMENTS,
    NEGATIVE_TAXONOMIES,
    PRIVATE_LICENSE,
    SPEC_NEGATIVE_MINIMUM,
    SPEC_NEGATIVE_TARGET,
    SPEC_SPEAKERS_MINIMUM,
    SPEC_SPEAKERS_TARGET,
    SPEC_SPEECH_TOTAL_RANGE,
    SPEC_SPEECH_TOTAL_TARGET,
    SPEC_TAXONOMY_TARGETS,
    BatchManifest,
)
from .split import SealedSplit, SplitError, assert_no_held_out

ERROR = "error"
WARNING = "warning"

# id token that carries the stratum: "S07-T3-ENCS-012" -> "T3"
_ID_STRATUM_RE = re.compile(r"^(?:S(\d{2})-)?(T[1-6]|N[1-5])-[A-Z0-9]{2,8}"
                            r"-\d{3}$")


@dataclass(frozen=True)
class Finding:
    severity: str          # "error" | "warning"
    message: str

    @property
    def is_error(self) -> bool:
        return self.severity == ERROR


def _find_repo_root(start: Path) -> Path | None:
    """Nearest ancestor (or self) containing ``.git`` (file or dir)."""
    probe = start.resolve()
    if probe.is_file():
        probe = probe.parent
    for cand in (probe, *probe.parents):
        if (cand / ".git").exists():
            return cand
    return None


def _dir_patterns(gitignore: Path) -> list[tuple[bool, str]]:
    """(negated, pattern) pairs for directory patterns in a .gitignore."""
    patterns: list[tuple[bool, str]] = []
    try:
        text = gitignore.read_text(encoding="utf-8")
    except OSError:
        return patterns
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        neg = line.startswith("!")
        pat = line[1:] if neg else line
        pat = pat.rstrip("/")
        if not pat or pat.startswith("/") or "*" in pat or "?" in pat:
            continue  # anchored/glob dir patterns: out of scope, documented
        patterns.append((neg, pat))
    return patterns


def _under_ignored_dir(path: Path, repo_root: Path) -> bool:
    """True if *path* sits under a directory ignored by name (gitignore
    subset: plain and ``name/name2/`` directory patterns, ``!`` negation
    in file order — the repo's ``eval-private/`` convention)."""
    gi = repo_root / ".gitignore"
    if not gi.is_file():
        return False
    try:
        rel = path.resolve().relative_to(repo_root)
    except ValueError:
        return False
    dir_parts = rel.parts[:-1] if rel.parts else ()
    ignored = False
    for neg, pat in _dir_patterns(gi):
        parts = tuple(pat.split("/"))
        hit = any(dir_parts[i:i + len(parts)] == parts
                  for i in range(len(dir_parts) - len(parts) + 1))
        if hit:
            ignored = not neg
    return ignored


def outside_git_ok(path: Path) -> bool:
    """A private-artifact placement test: outside any git repo, or under
    a gitignored directory inside one."""
    root = _find_repo_root(path)
    if root is None:
        return True
    return _under_ignored_dir(path, root)


def validate_batch(batch: BatchManifest) -> list[Finding]:
    findings: list[Finding] = []
    err = lambda msg: findings.append(Finding(ERROR, msg))  # noqa: E731
    warn = lambda msg: findings.append(Finding(WARNING, msg))  # noqa: E731

    # --- manifest placement (audio's neighborhood) -------------------
    if not outside_git_ok(batch.path):
        warn(f"{batch.path}: batch manifest is inside a git repo but not "
             "under a gitignored directory — keep recordings under "
             "eval-private/ (docs/eval/corpus-spec.md §2)")

    # --- speakers ----------------------------------------------------
    if len(batch.speakers) < SPEC_SPEAKERS_MINIMUM:
        warn(f"{len(batch.speakers)} speakers — spec minimum is "
             f"{SPEC_SPEAKERS_MINIMUM} (target {SPEC_SPEAKERS_TARGET[0]}–"
             f"{SPEC_SPEAKERS_TARGET[1]})")
    for s in batch.speakers:
        if s.id not in s.consent_ref:
            warn(f"speaker {s.id}: consent_ref {s.consent_ref!r} does not "
                 "mention the speaker id — withdrawal looks cases up by "
                 "speaker id (spec §1.4)")
        if len(set(s.mic_classes)) < 2:
            warn(f"speaker {s.id}: records on {len(set(s.mic_classes))} mic "
                 "class(es) — spec §5 wants ≥2 per speaker")

    # --- cases -------------------------------------------------------
    seen_taxonomy: dict[str, int] = {}
    seen_negative = 0
    mic_mix: dict[str, int] = {}
    for case in batch.cases:
        prefix = f"{batch.path}: case {case.id!r}: "
        speaker = batch.speaker(case.speaker) if case.speaker else None
        if case.speaker and speaker is None:
            err(f"{prefix}speaker {case.speaker!r} is not in the speakers "
                f"table (known: {', '.join(s.id for s in batch.speakers)})")

        m = _ID_STRATUM_RE.match(case.id)
        if m is None:
            warn(f"{prefix}id does not follow "
                 "'<SNN-><Tn|Nn>-<LANG>-<NNN>' (spec §2), e.g. "
                 "'S07-T3-ENCS-012'")
        else:
            id_speaker = f"S{m.group(1)}" if m.group(1) else None
            if id_speaker and id_speaker != case.speaker:
                err(f"{prefix}id embeds speaker {id_speaker!r} but the case "
                    f"names speaker {case.speaker!r}")
            strat = m.group(2)
            want_neg = case.taxonomy in NEGATIVE_TAXONOMIES
            if (strat.startswith("N") != want_neg
                    or (strat.startswith("T")
                        and case.taxonomy[:2] != strat.lower())):
                err(f"{prefix}id stratum {strat!r} does not match taxonomy "
                    f"{case.taxonomy!r}")

        if case.is_negative:
            if case.reference_text:
                err(f"{prefix}negative case must have an empty "
                    f"reference_text, got {case.reference_text!r}")
            if case.expected_guard == "ok":
                warn(f"{prefix}negative case expects guard 'ok' — noise/"
                     "silence cases expect 'flag' (or 'none' when the "
                     "outcome is genuinely unmeasured)")
            if case.status == "recorded":
                seen_negative += 1
        else:
            if case.status == "recorded":
                seen_taxonomy[case.taxonomy] = (
                    seen_taxonomy.get(case.taxonomy, 0) + 1)
                if not (case.reference_text or "").strip():
                    err(f"{prefix}recorded speech case has no "
                        "reference_text — transcripts are typed at review "
                        "time and WER needs them")
                if case.snr_db is None:
                    warn(f"{prefix}recorded without snr_db — record the "
                         "post-hoc estimate (spec §5)")

        if case.status == "recorded" and case.audio:
            audio_path = batch.resolve_audio(case)
            if not audio_path.exists():
                err(f"{prefix}audio not found: {audio_path} (status is "
                    "'recorded'; flip to 'planned' until the file exists)")
            elif not outside_git_ok(audio_path):
                err(f"{prefix}audio {case.audio!r} is inside the git repo "
                    f"but not under a gitignored directory — audio lives "
                    "outside git (eval-private/…, spec §2)")
            else:
                mic_mix[case.mic_class] = mic_mix.get(case.mic_class, 0) + 1
        if case.status == "planned" and not case.audio:
            warn(f"{prefix}planned case has no audio path yet")

        lic = case.effective_license(batch.license)
        if lic != PRIVATE_LICENSE:
            warn(f"{prefix}license {lic!r} is not the default private tier "
                 f"{PRIVATE_LICENSE!r} — a public corpus needs a separate "
                 "signed grant (spec §1)")
        if case.environment and case.environment not in ENVIRONMENTS:
            warn(f"{prefix}environment {case.environment!r} is not one of "
                 f"{list(ENVIRONMENTS)} (spec §5)")
        if case.session_date and not DATE_RE.match(case.session_date):
            warn(f"{prefix}session_date {case.session_date!r} is not "
                 "YYYY-MM-DD")
        if speaker is not None:
            if case.language not in speaker.languages:
                warn(f"{prefix}language {case.language!r} is not listed "
                     f"for speaker {speaker.id} ({list(speaker.languages)})")
            if case.mic_class not in speaker.mic_classes:
                warn(f"{prefix}mic_class {case.mic_class!r} is not listed "
                     f"for speaker {speaker.id} "
                     f"({list(speaker.mic_classes)})")
            if case.consent_ref and case.consent_ref != speaker.consent_ref:
                warn(f"{prefix}consent_ref differs from speaker "
                     f"{speaker.id}'s roster entry")

    # --- corpus shape vs the spec ------------------------------------
    targets = batch.taxonomy_targets or SPEC_TAXONOMY_TARGETS
    for tid, target in sorted(targets.items()):
        got = seen_taxonomy.get(tid, 0)
        if got != target:
            rel = "under" if got < target else "over"
            warn(f"taxonomy {tid} ({ALL_TAXONOMIES[tid]}): {got} recorded "
                 f"vs target {target} ({rel} by {abs(got - target)})")
    speech_total = sum(seen_taxonomy.values())
    if not SPEC_SPEECH_TOTAL_RANGE[0] <= speech_total \
            <= SPEC_SPEECH_TOTAL_RANGE[1]:
        warn(f"recorded speech total {speech_total} is outside the spec "
             f"range {SPEC_SPEECH_TOTAL_RANGE} (target "
             f"{SPEC_SPEECH_TOTAL_TARGET})")
    if seen_negative < SPEC_NEGATIVE_MINIMUM:
        warn(f"recorded negatives {seen_negative} — spec minimum is "
             f"{SPEC_NEGATIVE_MINIMUM} (target {SPEC_NEGATIVE_TARGET})")
    if mic_mix:
        lo, hi = min(mic_mix.values()), max(mic_mix.values())
        if hi > 2 * lo:
            warn(f"mic class mix is uneven (min {lo}, max {hi}) — spec §5 "
                 "wants the class mix roughly even")
    return findings


def validate_no_heldout(batch: BatchManifest,
                        sealed: SealedSplit) -> list[Finding]:
    """The spec §6.3 invariant as a finding: this (train) batch contains
    ZERO held-out ids."""
    try:
        assert_no_held_out([c.id for c in batch.cases], sealed,
                           source=str(batch.path))
    except SplitError as e:
        return [Finding(ERROR, str(e))]
    return []


def format_findings(findings: list[Finding], path: Path) -> str:
    errors = [f for f in findings if f.is_error]
    warnings = [f for f in findings if not f.is_error]
    lines = [f"{path}: {len(errors)} error(s), {len(warnings)} warning(s)"]
    for f in errors:
        lines.append(f"error: {f.message}")
    for f in warnings:
        lines.append(f"warning: {f.message}")
    return "\n".join(lines)
