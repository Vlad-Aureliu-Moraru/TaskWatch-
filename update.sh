#!/bin/sh
set -e

REPO="Vlad-Aureliu-Moraru/TaskWatch-"
VERSION_FILE="${HOME}/.local/share/taskwatch/version"
TMP_DIR="/tmp/taskwatch-update"

detect_downloader() {
    if command -v curl >/dev/null 2>&1; then
        echo "curl"
    elif command -v wget >/dev/null 2>&1; then
        echo "wget"
    else
        echo "Error: need curl or wget" >&2
        exit 1
    fi
}

download() {
    local url="$1" out="$2"
    case "$DOWNLOADER" in
        curl) curl -sSfL "$url" -o "$out" ;;
        wget) wget -q "$url" -O "$out" ;;
    esac
}

get_current_version() {
    local v
    v=$("${HOME}/.local/bin/taskwatch" --version 2>/dev/null | sed 's/.*v//')
    if [ -z "$v" ] && [ -f "$VERSION_FILE" ]; then
        v=$(cat "$VERSION_FILE")
    fi
    echo "${v:-0.0.0}"
}

fetch_latest_tag() {
    local api_url="https://api.github.com/repos/${REPO}/releases/latest"
    local tmp="/tmp/taskwatch-api.json"
    download "$api_url" "$tmp" 2>/dev/null || return 1
    local tag
    tag=$(sed -n 's/.*"tag_name": *"\(v[^"]*\)".*/\1/p' "$tmp")
    rm -f "$tmp"
    echo "$tag"
}

version_compare() {
    printf '%s\n%s\n' "$1" "$2" | sort -V | head -1
}

DOWNLOADER=$(detect_downloader)

echo "TaskWatch+ Update Check"
echo "-----------------------"

CURRENT=$(get_current_version)
echo "Current version: v${CURRENT}"

LATEST_TAG=$(fetch_latest_tag)
if [ -z "$LATEST_TAG" ]; then
    echo "Error: could not fetch latest release" >&2
    exit 1
fi
echo "Latest version:  ${LATEST_TAG}"

LATEST_VER=$(echo "$LATEST_TAG" | sed 's/^v//')

if [ "$(version_compare "$CURRENT" "$LATEST_VER")" = "$CURRENT" ] && [ "$CURRENT" != "$LATEST_VER" ]; then
    echo "Updating to ${LATEST_TAG}..."
else
    echo "Already up to date."
    exit 0
fi

TARBALL="taskwatch-${LATEST_TAG}-linux-x86_64.tar.gz"
TARBALL_URL="https://github.com/${REPO}/releases/download/${LATEST_TAG}/${TARBALL}"

rm -rf "$TMP_DIR"
mkdir -p "$TMP_DIR"

echo "Downloading ${TARBALL}..."
download "$TARBALL_URL" "${TMP_DIR}/${TARBALL}"

echo "Extracting..."
tar xzf "${TMP_DIR}/${TARBALL}" -C "$TMP_DIR"

EXTRACTED_DIR="${TMP_DIR}/taskwatch-${LATEST_TAG}"
if [ ! -d "$EXTRACTED_DIR" ]; then
    EXTRACTED_DIR=$(find "$TMP_DIR" -maxdepth 1 -type d ! -path "$TMP_DIR" | head -1)
fi

echo "Installing..."
(cd "$EXTRACTED_DIR" && ./install.sh)

mkdir -p "$(dirname "$VERSION_FILE")"
echo "$LATEST_VER" > "$VERSION_FILE"

rm -rf "$TMP_DIR"

echo "Done. Updated to TaskWatch+ ${LATEST_TAG}"
