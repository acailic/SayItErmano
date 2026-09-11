"""First-use funnel step counters - LOCAL ONLY (F-19 instrumentation).

A tiny JSON file in the user's data dir recording WHICH setup steps were
reached and when, so the phase-1 user tests (and the user alone - the
file is never sent anywhere, there is no network code in this module at
all) can answer "where do new users drop off". Steps:

    onboarding_started, model_downloaded, tryout_ok,
    real_insertion_reported_ok, real_insertion_reported_failed

Opt-out, any of:
  * the onboarding checkbox (writes ``opt_out: true`` into the file),
  * ``SAYITERMANO_NO_FUNNEL=1``,
  * deleting the file (nothing recreates it until the next step fires -
    which re-records from scratch, so prefer the checkbox).

No GTK imports: headless tests and tooling can use this directly.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .. import paths

STEPS = ("onboarding_started", "model_downloaded", "tryout_ok",
         "real_insertion_reported_ok", "real_insertion_reported_failed")
ENV_OPT_OUT = "SAYITERMANO_NO_FUNNEL"


class FunnelCounters:
    """Append-once step recorder (first timestamp wins; details of a
    repeated step are merged, never overwritten)."""

    def __init__(self, path: Path | None = None):
        self.path = path if path is not None \
            else paths.data_dir() / "onboarding_funnel.json"

    # -- state ---------------------------------------------------------------

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent,
                                   prefix=".funnel-", suffix=".json")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(data, fh, indent=1, sort_keys=True)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def opted_out(self) -> bool:
        if os.environ.get(ENV_OPT_OUT, "").strip().lower() in (
                "1", "true", "yes", "on"):
            return True
        return bool(self._load().get("opt_out"))

    def opt_out(self) -> None:
        """Stop recording (and drop) future steps; recorded history stays
        readable so the user can verify what was kept."""
        data = self._load()
        data["opt_out"] = True
        self._save(data)

    # -- recording -----------------------------------------------------------

    def record(self, step: str, **detail) -> bool:
        """Record `step` reached, now. True when written; False when the
        step is unknown, we are opted out, or the write failed (counters
        must never break onboarding)."""
        if step not in STEPS or self.opted_out():
            return False
        import time
        data = self._load()
        entry = data.get(step)
        if not isinstance(entry, dict):
            entry = data[step] = {"ts": time.time()}
        if detail:
            known = entry.setdefault("detail", {})
            if isinstance(known, dict):
                known.update({k: v for k, v in detail.items()
                              if isinstance(v, (str, int, float, bool))})
        try:
            self._save(data)
        except OSError:
            return False
        return True

    def steps(self) -> dict:
        """Recorded steps verbatim (tests assert file contents through
        this AND through the JSON on disk)."""
        return {k: v for k, v in self._load().items() if k in STEPS}
