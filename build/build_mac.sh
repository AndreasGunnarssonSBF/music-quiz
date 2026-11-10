#!/bin/zsh
# Bygger dist/Musikquiz.app och dist/Musikquiz-mac.zip (Apple Silicon).
#   ./build/build_mac.sh
# Kräver Python 3.12 (Homebrew: brew install python@3.12) och internet första gången
# (PyInstaller och cloudflared hämtas). Byggmappen läggs utanför OneDrive.
# Ange CLOUDFLARED=/sökväg/till/cloudflared för att använda en redan nedladdad binär.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${TMPDIR:-/tmp}/musikquiz-build"
DIST="$ROOT/dist"
PY="${PYTHON:-$(command -v python3.12 || echo /opt/homebrew/bin/python3.12)}"
CF_VERSION="2026.10.7"
mkdir -p "$WORK" "$DIST"

# 1. Python-miljö med PyInstaller
if [[ ! -x "$WORK/venv/bin/pyinstaller" ]]; then
  "$PY" -m venv "$WORK/venv"
  "$WORK/venv/bin/pip" install -q pyinstaller==6.11.1
fi

# 2. cloudflared för Apple Silicon
CF="${CLOUDFLARED:-$WORK/cloudflared}"
if [[ ! -x "$CF" ]]; then
  echo "Hämtar cloudflared $CF_VERSION…"
  curl -fsSL -o "$WORK/cf.tgz" "https://github.com/cloudflare/cloudflared/releases/download/$CF_VERSION/cloudflared-darwin-arm64.tgz"
  tar -xzf "$WORK/cf.tgz" -C "$WORK"
  CF="$WORK/cloudflared"
fi
mkdir -p "$WORK/bin" && cp "$CF" "$WORK/bin/cloudflared" && chmod +x "$WORK/bin/cloudflared"

# 3. Appikon (assets/Musikquiz.icns, skapad från src/static/icon.svg)
ICON="$ROOT/assets/Musikquiz.icns"

# 4. Bygg appen
rm -rf "$DIST/Musikquiz.app" "$DIST/Musikquiz-mac.zip"
"$WORK/venv/bin/pyinstaller" --noconfirm --clean --windowed --name Musikquiz \
  --osx-bundle-identifier se.musikquiz.app \
  --icon "$ICON" \
  --add-data "$ROOT/src/static:static" \
  --add-binary "$WORK/bin/cloudflared:bin" \
  --paths "$ROOT/src" \
  --distpath "$DIST" --workpath "$WORK/pyi" --specpath "$WORK" \
  "$ROOT/src/app.py"

# Ingen Dock-ikon: appen öppnar bara webbläsaren och avslutar sig.
PLIST="$DIST/Musikquiz.app/Contents/Info.plist"
plutil -replace LSUIElement -bool true "$PLIST"
plutil -replace CFBundleShortVersionString -string "2.2" "$PLIST"
plutil -replace CFBundleDisplayName -string "Musikquiz" "$PLIST"
codesign --force --deep --sign - "$DIST/Musikquiz.app"

# 5. Zip för utskick (ditto behåller behörigheter och symlänkar)
(cd "$DIST" && ditto -c -k --sequesterRsrc --keepParent Musikquiz.app Musikquiz-mac.zip)
rm -rf "$DIST/Musikquiz"  # PyInstallers mellanmapp
echo "Klart: $DIST/Musikquiz.app och $DIST/Musikquiz-mac.zip"
