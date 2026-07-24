#!/bin/sh

BIN_DIR="${HOME}/.local/bin"
APP_DIR="${HOME}/.local/share/applications"
ICON_DIR="${HOME}/.local/share/icons/hicolor/256x256/apps"
TASKWATCH_DIR="${HOME}/.local/share/taskwatch"
OPENCODE_CMD_DIR="${HOME}/.config/opencode/commands"

KEEP_DATA=false
PURGE_DATA=false
for arg; do
    case "$arg" in
        --keep-data) KEEP_DATA=true ;;
        --purge-data) PURGE_DATA=true ;;
    esac
done

echo "Uninstalling TaskWatch+..."

if pip show taskwatch >/dev/null 2>&1; then
    pip uninstall taskwatch -y --quiet 2>/dev/null || pip uninstall taskwatch -y
fi

rm -f "$BIN_DIR/taskwatch"
rm -f "$APP_DIR/taskwatch.desktop"
rm -f "$ICON_DIR/TaskWatch+.png"
rm -f "$TASKWATCH_DIR/update.sh"
rm -f "$TASKWATCH_DIR/version"

for cmd in done compact taskwatch-attach taskwatch-next taskwatch-plan taskwatch-review; do
    rm -f "$OPENCODE_CMD_DIR/${cmd}.md"
done

command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APP_DIR" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -f -t "$HOME/.local/share/icons" 2>/dev/null || true

if $PURGE_DATA; then
    rm -rf "$TASKWATCH_DIR"
    echo "Data purged."
elif $KEEP_DATA; then
    echo "Data kept at $TASKWATCH_DIR"
else
    printf "Remove taskwatch data directory (%s) with all tasks and config? [y/N] " "$TASKWATCH_DIR"
    read -r answer
    case "$answer" in
        [yY]*) rm -rf "$TASKWATCH_DIR"; echo "Data removed." ;;
        *) echo "Data kept at $TASKWATCH_DIR" ;;
    esac
fi

echo "Done."
