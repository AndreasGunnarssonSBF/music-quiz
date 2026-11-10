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
plutil -replace CFBundleShortVersionString -string "2.3" "$PLIST"
plutil -replace CFBundleDisplayName -string "Musikquiz" "$PLIST"
APP="$DIST/Musikquiz.app"

if [[ -n "${MACOS_SIGN_IDENTITY:-}" ]]; then
  # Riktig signering + notarisering (kräver Apple Developer-konto, se docs/signering.md).
  # Då försvinner varningen vid första starten helt.
  ENT="$ROOT/build/entitlements.plist"
  echo "Signerar med: $MACOS_SIGN_IDENTITY"
  # Inifrån och ut: bibliotek och körbara filer först, sist själva appen.
  find "$APP/Contents" -type f \( -name "*.dylib" -o -name "*.so" -o -perm -u+x \) ! -path "*/MacOS/Musikquiz" -print0 |
    while IFS= read -r -d '' f; do
      codesign --force --timestamp --options runtime --entitlements "$ENT" --sign "$MACOS_SIGN_IDENTITY" "$f"
    done
  codesign --force --timestamp --options runtime --entitlements "$ENT" --sign "$MACOS_SIGN_IDENTITY" "$APP"
  codesign --verify --strict --verbose=2 "$APP"
  if [[ -n "${APPLE_ID:-}" && -n "${APPLE_TEAM_ID:-}" && -n "${APPLE_APP_PASSWORD:-}" ]]; then
    echo "Notariserar hos Apple (tar några minuter)…"
    (cd "$DIST" && ditto -c -k --keepParent Musikquiz.app notarize.zip)
    xcrun notarytool submit "$DIST/notarize.zip" --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" \
      --password "$APPLE_APP_PASSWORD" --wait
    xcrun stapler staple "$APP"
    rm -f "$DIST/notarize.zip"
  fi
else
  # Ingen certifikat: ad hoc-signering (fungerar, men macOS varnar första gången).
  codesign --force --deep --sign - "$APP"
fi

# 5. Zip för utskick (ditto behåller behörigheter och symlänkar)
(cd "$DIST" && ditto -c -k --sequesterRsrc --keepParent Musikquiz.app Musikquiz-mac.zip)
rm -rf "$DIST/Musikquiz"  # PyInstallers mellanmapp
echo "Klart: $DIST/Musikquiz.app och $DIST/Musikquiz-mac.zip"
