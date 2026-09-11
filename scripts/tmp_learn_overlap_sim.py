"""2026-09-11 learning session - overlap/re-commit preview simulation.

Simulates a preview engine whose commit windows carry LEFT AUDIO overlap
(so whisper sees cross-boundary context) and whose overlapping region can
CORRECT already-committed text (in place). Compares assembled preview
text against the full-take decode, against the current no-overlap
committed tiling, and reports the rewrite (flicker) cost.

CPU int8 large-v3. Writes scripts/tmp_learn_overlap_results.json.
"""
import io
import json
import sys
import wave
from pathlib import Path

import numpy as np
from faster_whisper import WhisperModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fluidvoice.audio_utils import raw_to_wav_bytes  # noqa: E402

AUDIO = Path.home() / ".local/share/sayit-ermano/audio"
OUT = Path(__file__).parent / "tmp_learn_overlap_results.json"


def pcm_of(path):
    with wave.open(str(path)) as w:
        return w.readframes(w.getnframes())


def decode_span(m, a, rate, start, end):
    """Audio slice -> (segments with timestamps), via wav bytes."""
    i0, i1 = int(start * rate), int(end * rate)
    wav = raw_to_wav_bytes((a[i0:i1] * 32768).astype(np.int16).tobytes(), rate)
    gen, _ = m.transcribe(io.BytesIO(wav), language="en", beam_size=1,
                          condition_on_previous_text=False)
    return list(gen)


def words(text):
    return text.lower().split()


def agreement(a, b):
    """Crude bag-of-words F1 - enough to rank variants."""
    from collections import Counter
    wa, wb = Counter(words(a)), Counter(words(b))
    inter = sum((wa & wb).values())
    if not inter:
        return 0.0
    p, r = inter / max(1, sum(wb.values())), inter / max(1, sum(wa.values()))
    return 2 * p * r / (p + r)


def main():
    m = WhisperModel("large-v3", device="cpu", compute_type="int8")
    results = []
    for wav in sorted(AUDIO.glob("20260911-*.wav")):
        pcm = pcm_of(wav)
        a = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        total = len(a) / 16000.0
        if total < 3.0:
            continue
        gen, _ = m.transcribe(np.ascontiguousarray(a), language="en",
                              beam_size=1)
        final = " ".join(s.text.strip() for s in gen)

        rec = {"wav": wav.name, "s": round(total, 1), "final": final}

        # -- current engine: no-overlap tiling, seg=2, text ctx prompt
        committed, rewrites2 = [], 0
        for k in range(int(total / 2.0)):
            segs = decode_span(m, a, 16000, k * 2.0, k * 2.0 + 2.0)
            committed.append(" ".join(s.text.strip() for s in segs))
        rec["tile2"] = " ".join(committed)
        rec["tile2_f"] = round(agreement(rec["tile2"], final), 2)

        # -- overlap re-commit: seg=2 windows with 1s left overlap,
        #    timestamps slice the new region; overlapping region may
        #    correct the previous commit tail
        hop, seg, ov = 1.0, 2.0, 1.0
        pieces = []          # committed pieces, newest last
        rewrites = 0
        pos = 0.0
        while pos + seg <= total + 1e-9:
            start = max(0.0, pos - ov)
            end = pos + seg
            segs = decode_span(m, a, 16000, start, end)
            new_region_start = pos if pieces else 0.0
            new_text = " ".join(
                s.text.strip() for s in segs if s.end > new_region_start + 0.05
                or not pieces)
            # correction: the overlap decode re-speaks earlier words
            overlap_text = " ".join(
                s.text.strip() for s in segs
                if s.end <= new_region_start + 0.05) if pieces else ""
            if pieces and overlap_text.strip():
                tail = pieces[-1].lower().split()
                new = overlap_text.lower().split()
                # count words of the previous piece that the overlap
                # decode disagrees with (rewrite pressure proxy)
                common = 0
                for w in new[-6:]:
                    if w in tail[-6:]:
                        common += 1
                rewrites += max(0, len(new[-6:]) - common)
            pieces.append(new_text)
            pos += seg
        rec["ov2"] = " ".join(p for p in pieces if p)
        rec["ov2_f"] = round(agreement(rec["ov2"], final), 2)
        rec["ov2_rewrites"] = rewrites
        results.append(rec)
        print(f"[done] {wav.name} tile2={rec['tile2_f']} ov2={rec['ov2_f']} "
              f"rewrites={rewrites}", flush=True)
        Path(OUT).write_text(json.dumps(results, indent=1))
    print("DONE ->", OUT)


if __name__ == "__main__":
    main()
