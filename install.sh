#!/bin/sh
set -e

BIN_DIR="${HOME}/.local/bin"
APP_DIR="${HOME}/.local/share/applications"
ICON_DIR="${HOME}/.local/share/icons/hicolor/256x256/apps"
TASKWATCH_DIR="${HOME}/.local/share/taskwatch"

mkdir -p "$BIN_DIR" "$APP_DIR" "$ICON_DIR" "$TASKWATCH_DIR"

cp taskwatch "$BIN_DIR/"
chmod +x "$BIN_DIR/taskwatch"

cp update.sh "$TASKWATCH_DIR/"
chmod +x "$TASKWATCH_DIR/update.sh"

for term in kitty alacritty wezterm gnome-terminal konsole xfce4-terminal foot xterm; do
    if command -v "$term" >/dev/null 2>&1; then
        TERMINAL="$term"
        break
    fi
done
if [ -z "$TERMINAL" ] && command -v x-terminal-emulator >/dev/null 2>&1; then
    TERMINAL="x-terminal-emulator"
fi
if [ -z "$TERMINAL" ]; then
    echo "Error: no supported terminal found"
    exit 1
fi
sed -e "s|^Exec=.*|Exec=${TERMINAL} -e ${BIN_DIR}/taskwatch tui|" -e "s|^Terminal=true|Terminal=false|" taskwatch.desktop > "$APP_DIR/taskwatch.desktop"

cp TaskWatch+.png "$ICON_DIR/"

command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APP_DIR"
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -f -t "$HOME/.local/share/icons" 2>/dev/null || true

echo "Installed. Launch with: $TERMINAL -e taskwatch tui"
