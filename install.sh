#!/bin/sh
set -e

BIN_DIR="${HOME}/.local/bin"
APP_DIR="${HOME}/.local/share/applications"
ICON_DIR="${HOME}/.local/share/icons/hicolor/256x256/apps"

mkdir -p "$BIN_DIR" "$APP_DIR" "$ICON_DIR"

cp taskwatch "$BIN_DIR/"
chmod +x "$BIN_DIR/taskwatch"

sed "s|^Exec=.*|Exec=${BIN_DIR}/taskwatch tui|" taskwatch.desktop > "$APP_DIR/taskwatch.desktop"

cp TaskWatch+.png "$ICON_DIR/"

command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APP_DIR"
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -f -t "$HOME/.local/share/icons" 2>/dev/null || true

echo "Installed. Launch with: taskwatch tui"
