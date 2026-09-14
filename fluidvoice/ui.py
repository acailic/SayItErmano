"""User feedback: desktop notifications + start/stop sounds (upstream sfx)."""
from __future__ import annotations

import shutil
import subprocess
from importlib import resources

SOUNDS = {"start": "FV_start_0.m4a", "stop": "FV_end_0.m4a"}


def notify(title: str, body: str = "", timeout_ms: int = 2500, enabled: bool = True) -> None:
    if not enabled or not shutil.which("notify-send"):
        return
    subprocess.run(["notify-send", "-a", "SayItErmano", "-t", str(timeout_ms),
                    title, body], check=False)


def play_sound(which: str, volume: float = 1.0, enabled: bool = True) -> None:
    if not enabled or which not in SOUNDS:
        return
    player = next((t for t in ("pw-play", "paplay") if shutil.which(t)), None)
    if not player:
        return
    import subprocess
    try:
        asset = resources.files("fluidvoice.assets.sfx").joinpath(SOUNDS[which])
        with resources.as_file(asset) as path:
            args = [player, str(path)]
            if player == "paplay":
                vol = max(0, min(1, volume))
                args = [player, f"--volume={int(vol * 65536)}", str(path)]
            subprocess.run(args, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=False)
    except Exception:  # noqa: S110 — best-effort path; caller must proceed
        pass
