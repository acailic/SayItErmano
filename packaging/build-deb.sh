#!/usr/bin/env bash
# Build the SayItErmano .deb — the "install like a Mac app" experience:
#   sudo apt install ./sayit-ermano_<ver>_amd64.deb
#   -> /usr/bin/sayit-ermano CLI
#   -> launcher entry (opens the settings UI)
#   -> daemon autostarts at login (XDG autostart)
#   -> icon, systemd user unit
#
# The Python runtime is a bundled venv under /opt/sayit-ermano/venv so the
# deb needs no pip/uv on the target. --system-site-packages is used so a CUDA
# torch already on the machine (and its NVIDIA libs) are reused when present;
# without it the app still runs on CPU (faster-whisper int8).
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd)"
VERSION="$(.venv/bin/python -c 'import fluidvoice; print(fluidvoice.__version__)')"
PKGVER="${DEB_VERSION:-1}"
ARCH="${DEB_ARCH:-$(dpkg --print-architecture)}"
NAME="sayit-ermano"
STAGE="$(mktemp -d)/pkg"
trap 'rm -rf "$(dirname "$STAGE")"' EXIT

echo "== building $NAME ${VERSION}-${PKGVER} ($ARCH) =="

# 1. Application payload: bundled venv + package ---------------------------
mkdir -p "$STAGE/opt/$NAME"
python3 -m venv --system-site-packages "$STAGE/opt/$NAME/venv"
"$STAGE/opt/$NAME/venv/bin/pip" install -q --upgrade pip
"$STAGE/opt/$NAME/venv/bin/pip" install -q --no-cache-dir .
rm -rf "$STAGE/opt/$NAME/venv/share"  # docs/man from wheels

# strip the pyc cache (rebuilt on first run) to shrink the package
find "$STAGE/opt/$NAME/venv" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
# sane permissions (pip creates some group-writable entries).
# -type f/d only: never touch symlinks (venv python symlinks point at the
# system interpreter and chmod would follow them).
find "$STAGE" -type d -exec chmod 755 {} +
find "$STAGE/opt" -type f -exec chmod go-w {} + || true

# 2. CLI wrapper ------------------------------------------------------------
mkdir -p "$STAGE/usr/bin"
cat > "$STAGE/usr/bin/sayit-ermano" <<'WRAP'
#!/bin/sh
# SayItErmano launcher - keeps the user's session environment (DISPLAY,
# XAUTHORITY, XDG_RUNTIME_DIR) which sudo/apt contexts would otherwise lose.
exec /opt/sayit-ermano/venv/bin/python -m fluidvoice "$@"
WRAP
chmod 755 "$STAGE/usr/bin/sayit-ermano"

# 3. Desktop integration ----------------------------------------------------
install -Dm644 packaging/sayit-ermano.desktop \
    "$STAGE/usr/share/applications/sayit-ermano.desktop"
install -Dm644 packaging/autostart.desktop \
    "$STAGE/etc/xdg/autostart/sayit-ermano.desktop"
# hicolor PNG sizes rendered from the macOS app icon (exact brand asset)
for png in packaging/icons/hicolor/*x*/apps/sayit-ermano.png; do
    install -Dm644 "$png" \
        "$STAGE/usr/share/icons/hicolor/${png#packaging/icons/hicolor/}"
done
# bundled symbolic UI icons under our own names (theme-swap-proof)
for svg in "$REPO"/fluidvoice/assets/icons/symbolic/actions/*.svg; do
    install -Dm644 "$svg" \
        "$STAGE/usr/share/icons/hicolor/symbolic/actions/$(basename "$svg")"
done

# 4. systemd user unit (optional alternative to XDG autostart) -------------
mkdir -p "$STAGE/usr/lib/systemd/user"
cat > "$STAGE/usr/lib/systemd/user/sayit-ermano.service" <<UNIT
[Unit]
Description=SayItErmano dictation daemon
PartOf=graphical-session.target
After=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/bin/sayit-ermano daemon
Restart=on-failure
RestartSec=3

[Install]
WantedBy=graphical-session.target
UNIT

# 5. Debian control ---------------------------------------------------------
mkdir -p "$STAGE/DEBIAN"
cat > "$STAGE/DEBIAN/control" <<CONTROL
Package: $NAME
Version: $VERSION-$PKGVER
Section: sound
Priority: optional
Architecture: $ARCH
Depends: python3 (>= 3.11), pipewire-audio-utils | pulseaudio-utils, xdotool, xclip, libnotify-bin, python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1
Recommends: pulseaudio-utils
Conflicts: fluidvoice-linux
Replaces: fluidvoice-linux
Provides: fluidvoice-linux
Maintainer: SayItErmano contributors
Description: SayItErmano - local voice dictation with AI polish (Linux port of FluidVoice)
 Community port of altic-dev/FluidVoice behavior to Linux: global hotkey,
 on-device Whisper transcription (CUDA when available), spoken punctuation
 commands, optional AI polish via any OpenAI-compatible endpoint, text
 typed into the focused app. Native GTK settings/history app (sayit-ermano app).
 Replaces the pre-rename fluidvoice-linux package and takes over its
 config/history/model directories on first run.
CONTROL

cat > "$STAGE/DEBIAN/postinst" <<'POST'
#!/bin/sh
set -e
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
fi
# enable + (re)start the daemon for the installing desktop user:
#  - systemd-managed: daemon-reload + try-restart so upgrades take effect
#    immediately (no manual restart or logout needed)
#  - daemon autostarted via XDG (outside systemd): stop it and take over
#    via the systemd unit we enable above
if command -v systemctl >/dev/null 2>&1 && [ -n "${SUDO_USER:-}" ]; then
    su -s /bin/sh "$SUDO_USER" -c '
        export XDG_RUNTIME_DIR="/run/user/$(id -u)"
        systemctl --user daemon-reload 2>/dev/null || true
        systemctl --user enable sayit-ermano.service 2>/dev/null || true
        if systemctl --user is-active --quiet sayit-ermano.service 2>/dev/null; then
            systemctl --user try-restart sayit-ermano.service 2>/dev/null || true
        elif pgrep -f "/opt/sayit-ermano/.*/python -m fluidvoice daemon" >/dev/null 2>&1; then
            pkill -f "/opt/sayit-ermano/.*/python -m fluidvoice daemon" 2>/dev/null || true
            sleep 1
            systemctl --user start sayit-ermano.service 2>/dev/null || true
        fi' || true
fi
exit 0
POST
chmod 755 "$STAGE/DEBIAN/postinst"

cat > "$STAGE/DEBIAN/postrm" <<'POST'
#!/bin/sh
set -e
if [ "$1" = "remove" ] && command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || true
fi
exit 0
POST
chmod 755 "$STAGE/DEBIAN/postrm"

# 6. Pack -------------------------------------------------------------------
OUT="dist/${NAME}_${VERSION}-${PKGVER}_${ARCH}.deb"
mkdir -p dist
# xz (threaded, level 9 extreme) instead of this dpkg's zstd default: the
# payload is native .so libraries that zstd compresses ~15% worse. xz debs
# are the classic format - older apt/dpkg read them too.
DEBFLAGS=(-Zxz -z9 -Sextreme --uniform-compression --threads-max="$(nproc)")
fakeroot dpkg-deb --build --root-owner-group "${DEBFLAGS[@]}" "$STAGE" "$OUT" 2>/dev/null \
    || dpkg-deb --build --root-owner-group "${DEBFLAGS[@]}" "$STAGE" "$OUT"
echo "== built: $OUT ($(du -h "$OUT" | cut -f1)) =="
echo "install with:  sudo apt install ./$OUT"
