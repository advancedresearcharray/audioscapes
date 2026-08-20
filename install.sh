#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BIN="${HOME}/.local/bin"
APP="${HOME}/.local/share/applications"
ICON="${HOME}/.local/share/icons/hicolor/scalable/apps"
UNIT="${HOME}/.config/systemd/user"

mkdir -p "$BIN" "$APP" "$ICON" "$UNIT"

ln -sfn "$ROOT/cascade-eq" "$BIN/cascade-eq"
chmod +x "$ROOT/cascade-eq"

install -m 644 "$ROOT/data/cascade-eq.desktop" "$APP/cascade-eq.desktop"
install -m 644 "$ROOT/data/icons/hicolor/scalable/apps/cascade-eq.svg" "$ICON/cascade-eq.svg"
install -m 644 "$ROOT/data/cascade-eq.service" "$UNIT/cascade-eq.service"

# Point the desktop file at this checkout if cascade-eq is not on PATH yet.
if ! command -v cascade-eq >/dev/null 2>&1; then
  sed -i "s|^Exec=.*|Exec=$ROOT/cascade-eq gui|" "$APP/cascade-eq.desktop"
fi

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APP" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true
fi

systemctl --user daemon-reload >/dev/null 2>&1 || true

cat <<EOF
Installed Cascade EQ.

Run the control panel:
  cascade-eq gui

Process all Ubuntu audio:
  cascade-eq enable

Autostart the DSP daemon:
  systemctl --user enable --now cascade-eq.service

If 'cascade-eq' is not found, add ~/.local/bin to PATH:
  export PATH="\$HOME/.local/bin:\$PATH"
EOF
