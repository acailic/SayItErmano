"""Proactive mic-quality verdicts while dictating (mic-quality-warning.md).

The REACTIVE guards (dead-mic notice, hallucination guard, preview
silencing) fire after a take; this module watches the growing PCM DURING
the take and answers three questions once >= MIN_AUDIO_S of audio exists:

- dead  : effectively digital silence (muted source, wedged PipeWire
          path, wrong device) - the 2026-09-10 BT incident failure mode
- far   : speech IS present (VAD frames) but its level sits far below
          THIS device's own recent norm -> move closer / raise volume
- clip  : pinned peaks on a meaningful fraction of samples -> distortion

Each verdict fires at most once per take (the latch); thresholds are
constants, not settings. The speech gate reuses preview.py's VAD frame
RMS + ZCR fricative guard so "speech present" means the same thing here
and in the preview VAD. Baselines live in a sidecar (config store
discipline: atomic 0600, corrupt -> {} with one warning).
"""
from __future__ import annotations

import array
import json
import logging
import os
from pathlib import Path

from . import paths
from .preview import VAD_FRAME_RMS, VAD_ZCR_MAX

log = logging.getLogger(__name__)

RATE = 16000
WIDTH = 2  # s16 mono
HOP_S = 0.1
MIN_AUDIO_S = 1.5
#: fixed far-floor when a device has no baseline yet (dBFS)
FIXED_FLOOR_DBFS = -38.0
#: how far below the device's rolling speech-RMS norm counts as "far"
FAR_BELOW_BASELINE_DB = 12.0
DEAD_HOP_RMS = 1e-4            # ~ -80 dBFS: digital silence territory
DEAD_SPEECH_FRAC = 0.05
FAR_SPEECH_FRAC = 0.40
CLIP_SAMPLE_FS = 0.98
CLIP_RATIO = 0.02
#: takes averaged into a device baseline (rolling mean window)
BASELINE_TAKES = 5
_WARN = "mic-baseline.json unreadable - starting empty"

MESSAGES = {
    "dead": "Microphone is silent — check the input device "
            "(muted, disconnected or the wrong source)",
    "far": "Very quiet input — move closer to the mic "
           "or raise the input volume",
    "clip": "Input is clipping — lower the mic volume",
}


def _frame_speech(frame: array.array) -> bool:
    """VAD gate ported from preview.trailing_silence_s (RMS + fricative
    ZCR guard): a frame counts as speech the same way there."""
    n = len(frame)
    if n == 0:
        return False
    rms = (sum(s * s for s in frame) / n) ** 0.5 / 32768.0
    if rms >= VAD_FRAME_RMS:
        return True
    changes = sum(1 for a, b in zip(frame, frame[1:])
                  if (a < 0) != (b < 0))
    return changes / max(1, n - 1) >= VAD_ZCR_MAX  # low-energy fricative


class LevelWatch:
    """Accumulates PCM into 100 ms hops and latches verdicts once."""

    def __init__(self, rate: int = RATE):
        self._rate = rate
        self._hop = array.array("h")
        self._hop_samples = int(rate * HOP_S)
        self._hop_speech = 0        # speech hops seen
        self._hops = 0              # total hops
        self._speech_energy = 0.0   # sum of rms^2 over speech hops
        self._speech_n = 0
        self._clip = 0              # samples >= CLIP_SAMPLE_FS
        self._samples = 0
        self._warned: set[str] = set()

    # -- accumulation -------------------------------------------------------

    def feed(self, pcm: bytes) -> None:
        """Feed raw little-endian s16 mono bytes (tail of the take)."""
        usable = pcm[: (len(pcm) // WIDTH) * WIDTH]
        self._hop.frombytes(usable)
        self._samples += len(usable) // WIDTH
        while len(self._hop) >= self._hop_samples:
            frame = self._hop[:self._hop_samples]
            del self._hop[:self._hop_samples]
            self._hops += 1
            n = len(frame)
            peak = max(1, max(abs(s) for s in frame))
            rms = (sum(s * s for s in frame) / n) ** 0.5 / 32768.0
            self._clip += sum(1 for s in frame
                              if abs(s) / 32768.0 >= CLIP_SAMPLE_FS)
            if _frame_speech(frame):
                self._hop_speech += 1
                self._speech_energy += rms * rms
                self._speech_n += 1
            elif peak / 32768.0 >= CLIP_SAMPLE_FS:
                pass  # loud non-speech hop: clipping still counts

    # -- derived quantities ---------------------------------------------------

    @property
    def elapsed_s(self) -> float:
        return self._samples / self._rate

    @property
    def speech_frac(self) -> float:
        return self._hop_speech / self._hops if self._hops else 0.0

    @property
    def speech_dbfs(self) -> float | None:
        if not self._speech_n:
            return None
        import math
        return 20.0 * math.log10(
            max((self._speech_energy / self._speech_n) ** 0.5, 1e-9))

    @property
    def clip_ratio(self) -> float:
        return self._clip / self._samples if self._samples else 0.0

    def summary(self) -> dict:
        return {"elapsed_s": round(self.elapsed_s, 2),
                "speech_frac": round(self.speech_frac, 3),
                "speech_dbfs": (round(self.speech_dbfs, 1)
                                if self.speech_dbfs is not None else None),
                "clip_ratio": round(self.clip_ratio, 4)}

    # -- verdicts --------------------------------------------------------------

    def verdicts(self, floor_dbfs: float = FIXED_FLOOR_DBFS) -> list[str]:
        """New (un-latched) verdicts for the audio fed so far; [] until
        MIN_AUDIO_S exists. Each kind fires at most once per watch."""
        if self.elapsed_s < MIN_AUDIO_S:
            return []
        out: list[str] = []
        if "dead" not in self._warned \
                and self.speech_frac < DEAD_SPEECH_FRAC:
            # near-digital-silence window: level must also be floor-low
            if (self.speech_dbfs is None or
                    self.speech_dbfs < floor_dbfs):
                out.append("dead")
        if "far" not in self._warned and self.speech_dbfs is not None \
                and self.speech_frac >= FAR_SPEECH_FRAC \
                and self.speech_dbfs < floor_dbfs:
            out.append("far")
        if "clip" not in self._warned and self.clip_ratio >= CLIP_RATIO:
            out.append("clip")
        self._warned.update(out)
        return out


# -- per-device baseline sidecar ----------------------------------------------

def _resolve(path) -> Path:
    return Path(path) if path is not None else paths.mic_baseline_file()


def load_baselines(path=None) -> dict[str, dict]:
    """{source: {"takes": int, "speech_dbfs": float}}; missing file is {},
    corrupt degrades to {} with one warning."""
    p = _resolve(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        log.warning(_WARN)
        return {}
    if not isinstance(data, dict):
        log.warning(_WARN)
        return {}
    out = {}
    for k, v in data.items():
        if (isinstance(v, dict) and isinstance(v.get("takes"), int)
                and isinstance(v.get("speech_dbfs"), (int, float))
                and str(k).strip()):
            out[str(k)] = {"takes": v["takes"],
                           "speech_dbfs": float(v["speech_dbfs"])}
    return out


def update_baseline(source: str, speech_dbfs: float, path=None) -> None:
    """Fold one take's speech RMS into the device's rolling mean."""
    if not source or speech_dbfs is None:
        return
    p = _resolve(path)
    data = load_baselines(path=p)
    cur = data.get(source) or {"takes": 0, "speech_dbfs": speech_dbfs}
    n = min(cur["takes"], BASELINE_TAKES - 1)
    mean = (cur["speech_dbfs"] * n + speech_dbfs) / (n + 1)
    data[source] = {"takes": cur["takes"] + 1, "speech_dbfs": mean}
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def level_floor_for(source: str, path=None) -> float:
    """Far-floor for a device: the fixed floor, or its recent norm minus
    FAR_BELOW_BASELINE_DB when a baseline exists (whichever is higher)."""
    entry = load_baselines(path=path).get(source) if source else None
    if not entry:
        return FIXED_FLOOR_DBFS
    return max(FIXED_FLOOR_DBFS,
               entry["speech_dbfs"] - FAR_BELOW_BASELINE_DB)


def monitor(raw_path, device: str = "", *, floor_dbfs: float | None = None,
            notify=None, log=None, poll_s: float = 0.3, stop=None):
    """Tail the growing headerless raw-PCM take file (16 kHz s16 mono —
    the same file the preview engines tail) and fire one notification per
    verdict kind. Returns the LevelWatch for post-take summary/baseline
    use. `stop` is a threading.Event (tests + the take-end paths)."""
    import threading
    import time
    from pathlib import Path

    watch = LevelWatch()
    floor = floor_dbfs if floor_dbfs is not None else level_floor_for(device)
    path = Path(raw_path)
    ended = stop if stop is not None else threading.Event()
    off = 0
    while not ended.is_set():
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if size > off:
            try:
                with open(path, "rb") as fh:
                    fh.seek(off)
                    chunk = fh.read()
            except OSError:
                chunk = b""
            off += len(chunk)
            watch.feed(chunk)
            for kind in watch.verdicts(floor):
                if log:
                    try:
                        log(f"mic warning: {kind} {watch.summary()}")
                    except Exception:  # noqa: S110 - logging is best-effort
                        pass
                if notify:
                    try:
                        notify("SayItErmano", MESSAGES[kind])
                    except Exception:  # noqa: S110 - notice is best-effort
                        pass
        time.sleep(poll_s)
    return watch


def analyze_wav(path) -> dict:
    """One-shot summary of a finished take's WAV (baseline fold-in and
    doctor); {} when unreadable."""
    import wave
    try:
        with wave.open(str(path), "rb") as w:
            rate = w.getframerate()
            pcm = w.readframes(w.getnframes())
    except Exception:
        return {}
    watch = LevelWatch(rate=rate)
    watch.feed(pcm)
    return watch.summary()
