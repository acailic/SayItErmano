"""2026-09-11 5h learning session - Phase A decode matrix.

For every saved take: the daemon's GPU final text (pseudo ground truth),
a CPU full-take decode, and segmented-preview committed text at several
window sizes/conditioning variants. Writes JSON results next to this
script. CPU int8 (GPU is held by the production daemon).
"""
import json
import re
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np
from faster_whisper import WhisperModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fluidvoice.preview import SegmentedPreviewEngine  # noqa: E402

AUDIO = Path.home() / ".local/share/sayit-ermano/audio"
OUT = Path(__file__).parent / "tmp_learn_results.json"


def journal_typed():
    """wav-timestamp (HH:MM:SS) -> typed final text from the journal."""
    log = subprocess.run(
        ["journalctl", "--user", "-u", "sayit-ermano", "--since", "2026-09-11 02:20",
         "--no-pager"], capture_output=True, text=True).stdout
    out = {}
    for line in log.splitlines():
        m = re.search(r"(\d\d:\d\d:\d\d).*typed \(.*\): (.*)$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def wav_pcm(path):
    with wave.open(str(path)) as w:
        return w.readframes(w.getnframes())


def main():
    m = WhisperModel("large-v3", device="cpu", compute_type="int8")
    typed = journal_typed()
    results = []
    for wav in sorted(AUDIO.glob("20260911-*.wav")):
        # name: 20260911-HHMMSS-<ts>.wav; the journal "typed" line lands
        # 0-3 s BEFORE the history writer saves the wav
        hhmmss = wav.name.split("-")[1]
        h, mi, se = int(hhmmss[0:2]), int(hhmmss[2:4]), int(hhmmss[4:6])
        final = None
        for delta in range(0, 4):
            t = h * 3600 + mi * 60 + se - delta
            key = f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"
            if key in typed:
                final = typed[key]
                break
        pcm = wav_pcm(wav)
        total = len(pcm) / 32000.0
        if total < 2.0:
            continue
        rec = {"wav": wav.name, "s": round(total, 1), "final_gpu": final}
        a = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        t0 = time.time()
        gen, _ = m.transcribe(np.ascontiguousarray(a), language="en", beam_size=1)
        rec["full_cpu"] = " ".join(s.text.strip() for s in gen)
        rec["full_cpu_s"] = round(time.time() - t0, 1)

        def fw(pcm_bytes, ctx):
            import io
            gen, _ = m.transcribe(io.BytesIO(pcm_bytes), language="en",
                                  initial_prompt=ctx, beam_size=1,
                                  condition_on_previous_text=False,
                                  without_timestamps=True)
            return " ".join(s.text.strip() for s in gen if s.text.strip())

        def committed(pcm, seg_s):
            d = Path(tempfile.mkdtemp())
            raw = d / "t.raw"
            raw.write_bytes(pcm[:3200])
            eng = SegmentedPreviewEngine(raw, fw, lambda t, s=0: None,
                                         interval=seg_s, min_audio=1.0,
                                         segment_s=seg_s)
            data = b""
            t = seg_s
            while t <= total + 1e-9:
                data += pcm[int((t - seg_s) * 32000):int(t * 32000)]
                raw.write_bytes(data)
                eng._tick(data, t)
                t += seg_s
            return " ".join(x for x in eng.committed if x)

        for seg in (2.0, 3.0, 4.0):
            t0 = time.time()
            rec[f"prev{int(seg)}"] = committed(pcm, seg)
            rec[f"prev{int(seg)}_s"] = round(time.time() - t0, 1)
        results.append(rec)
        print(f"[done] {wav.name} ({total:.1f}s)", flush=True)
        Path(OUT).write_text(json.dumps(results, indent=1))
    print("ALL DONE ->", OUT)


if __name__ == "__main__":
    main()
