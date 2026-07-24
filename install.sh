#!/bin/sh
set -e

BIN_DIR="${HOME}/.local/bin"
APP_DIR="${HOME}/.local/share/applications"
ICON_DIR="${HOME}/.local/share/icons/hicolor/256x256/apps"
TASKWATCH_DIR="${HOME}/.local/share/taskwatch"

if [ -f "taskwatch/__main__.py" ]; then
    # Installing from source — use pip
    pip install . --quiet --break-system-packages 2>/dev/null || pip install . --quiet
    ENTRY_POINT=$(command -v taskwatch) || ENTRY_POINT="${HOME}/.local/bin/taskwatch"
    echo "  (installed via pip, entry point: ${ENTRY_POINT})"
else
    # Installing from a release tarball with pre-built binary
    mkdir -p "$BIN_DIR"
    cp taskwatch "$BIN_DIR/"
    chmod +x "$BIN_DIR/taskwatch"
    ENTRY_POINT="${BIN_DIR}/taskwatch"
fi

mkdir -p "$APP_DIR" "$ICON_DIR" "$TASKWATCH_DIR"

cp update.sh "$TASKWATCH_DIR/" 2>/dev/null || true
chmod +x "$TASKWATCH_DIR/update.sh" 2>/dev/null || true

cp TaskWatch+.png "$ICON_DIR/" 2>/dev/null || true

# Best-effort user terminal detection — avoids hardcoding the DE default
TERMINAL=""
for term in kitty alacritty wezterm gnome-terminal konsole xfce4-terminal foot xterm; do
    if command -v "$term" >/dev/null 2>&1; then
        TERMINAL="$term"
        break
    fi
done
if [ -z "$TERMINAL" ] && command -v x-terminal-emulator >/dev/null 2>&1; then
    TERMINAL="x-terminal-emulator"
fi

if [ -n "$TERMINAL" ]; then
    sed -e "s|^Exec=.*|Exec=${TERMINAL} -e ${ENTRY_POINT} tui|" \
        -e "s|^Terminal=true|Terminal=false|" \
        taskwatch.desktop > "$APP_DIR/taskwatch.desktop"
else
    # No terminal found — let the DE open its preferred terminal
    sed -e "s|^Exec=.*|Exec=${ENTRY_POINT} tui|" \
        taskwatch.desktop > "$APP_DIR/taskwatch.desktop"
fi

command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APP_DIR"
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -f -t "$HOME/.local/share/icons" 2>/dev/null || true

# Install opencode custom commands
OPENCODE_CMD_DIR="${HOME}/.config/opencode/commands"
if command -v opencode >/dev/null 2>&1; then
    mkdir -p "$OPENCODE_CMD_DIR"
    if [ -d "taskwatch/opencode-commands" ]; then
        cp taskwatch/opencode-commands/*.md "$OPENCODE_CMD_DIR/" 2>/dev/null || true
        echo "  (opencode commands installed)"
    fi
fi

echo "Installed. Launch: taskwatch tui"
