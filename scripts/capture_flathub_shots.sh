#!/bin/bash
# Capture the Flathub store screenshots headlessly (owner-rerunnable).
#
# Produces 02-settings-models.png and 03-history.png exactly per
# packaging/flatpak/screenshots/SHOT-LIST.md: sandbox HOME (never the real
# one), Xvfb :99, GTK from the system + the repo venv (".venv-gtk" — see
# docs/dev/testing.md for how it is provisioned), window-only import.
#
# 01-dictation-pill.png stays a live-desktop owner capture: the pill needs
# a real recording with waveform + partials, which a headless sandbox
# cannot produce honestly.
#
# Usage: bash scripts/capture_flathub_shots.sh <output-dir> [workdir]
set -euo pipefail
OUT="${1:?output dir}"; REPO="${2:-$(pwd)}"
SANDBOX="$(mktemp -d /tmp/siet-shots.XXXXXX)"
export HOME="$SANDBOX/home"
export XDG_RUNTIME_DIR="$SANDBOX/runtime"
export DISPLAY=:99
export GSK_RENDERER=cairo
mkdir -p "$HOME/.local/share/sayit-ermano" "$HOME/.cache/sayit-ermano/models" \
         "$XDG_RUNTIME_DIR" "$OUT"

# A locally-present model makes the Models page show a real "Use"/"Active"
# state — symlink avoids copying gigabytes (skip if the host cache is empty).
REAL_HOME="${REAL_HOME:-$(getent passwd "$(id -u)" | cut -d: -f6)}"
if [ -d "$REAL_HOME/.cache/sayit-ermano/models/faster-whisper" ]; then
    ln -sfn "$REAL_HOME/.cache/sayit-ermano/models/faster-whisper" \
        "$HOME/.cache/sayit-ermano/models/faster-whisper"
fi

# Seed fixture history (realistic sentences, never the owner's real data).
python3 - "$HOME/.local/share/sayit-ermano/history.jsonl" <<'PY'
import json, sys, time
now = time.time()
rows = [
    dict(ts=now-3600, app="firefox", backend="faster-whisper", duration_s=14.2,
         raw="um lets schedule the review for thursday morning and, send the notes to the team",
         text="Let's schedule the review for Thursday morning and send the notes to the team.",
         ai="polish"),
    dict(ts=now-7200, app="gnome-terminal", backend="faster-whisper", duration_s=8.7,
         raw="git rebase interactive head tilde three",
         text="git rebase -i HEAD~3", ai=None),
    dict(ts=now-90000, app="gedit", backend="faster-whisper", duration_s=21.5,
         raw="the parser rejects nested quotes so we escape them at the boundary layer instead",
         text="The parser rejects nested quotes, so we escape them at the boundary layer instead.",
         ai="polish"),
    dict(ts=now-172800, app="firefox", backend="faster-whisper", duration_s=11.1,
         raw="reminder to renew the domain before the twenty eighth",
         text="Reminder to renew the domain before the 28th.", ai="polish"),
]
with open(sys.argv[1], "w") as f:
    for r in rows:
        f.write(json.dumps(r) + "\n")
PY

command -v Xvfb >/dev/null || { echo "Xvfb required (xvfb-run package)" >&2; exit 1; }
command -v import >/dev/null || { echo "ImageMagick 'import' required" >&2; exit 1; }
command -v xdotool >/dev/null || { echo "xdotool required" >&2; exit 1; }

pkill -f "Xvfb :9[9]" 2>/dev/null || true
Xvfb :99 -screen 0 1200x800x24 >/dev/null 2>&1 &
sleep 1

shot() {  # shot <window-pid-pattern-file> <size> <png>
    local png="$1" size="$2"
    xdotool windowsize "$WID" $size
    sleep 1.5
    import -window "$WID" "$OUT/$png"
}

# -- 02: Settings -> Models (sidebar row 3 of the Settings group) --------
cd "$REPO"
".venv-gtk/bin/sayit-ermano" app --open settings >/dev/null 2>&1 &
sleep 6
WID=$(xdotool search --onlyvisible --class sayit-ermano | head -1)
xdotool mousemove --window "$WID" 100 191 click 1   # Models sidebar row
sleep 2
shot 02-settings-models.png 960x680
pkill -f "sayit-ermano ap[p]" || true

# -- 03: History (daemon first, so the offline banner is absent) ----------
".venv-gtk/bin/sayit-ermano" daemon >/dev/null 2>&1 &
sleep 4
".venv-gtk/bin/sayit-ermano" app --open history >/dev/null 2>&1 &
sleep 6
WID=$(xdotool search --onlyvisible --class sayit-ermano | head -1)
shot 03-history.png 900x680

pkill -f "sayit-ermano daemo[n]" || true
pkill -f "sayit-ermano ap[p]" || true
pkill -f "Xvfb :9[9]" || true
rm -rf "$SANDBOX"
echo "captured: $OUT/02-settings-models.png $OUT/03-history.png"
echo "still owner-live: 01-dictation-pill.png (needs a real recording)"
