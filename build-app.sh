#!/usr/bin/env bash
set -euo pipefail
PATCHER_ROOT="$(cd "$(dirname "$0")" && pwd)"
APP="$PATCHER_ROOT/../Spotify Patcher.app"
export CLANG_MODULE_CACHE_PATH="${TMPDIR:-/tmp}/spotify-patcher-clang-module-cache"
export SWIFT_MODULECACHE_PATH="${TMPDIR:-/tmp}/spotify-patcher-swift-module-cache"
mkdir -p "$CLANG_MODULE_CACHE_PATH" "$SWIFT_MODULECACHE_PATH"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
xcrun swiftc -swift-version 5 -O -target arm64-apple-macos13.0 -framework AppKit -framework UniformTypeIdentifiers -framework WebKit \
  "$PATCHER_ROOT/Main.swift" -o "$APP/Contents/MacOS/SpotifyPatcher"
cp "$PATCHER_ROOT/backend.py" "$PATCHER_ROOT/chroma.py" "$PATCHER_ROOT/index.html" "$PATCHER_ROOT/style.css" "$PATCHER_ROOT/app.js" "$APP/Contents/Resources/"
cp -R "$PATCHER_ROOT/assets" "$APP/Contents/Resources/"
cp "$PATCHER_ROOT/Patcher.icns" "$APP/Contents/Resources/Patcher.icns"
python3 - "$APP" <<'PY'
import plistlib, sys
from pathlib import Path
app = Path(sys.argv[1])
info = dict(CFBundleName='Spotify Patcher', CFBundleDisplayName='Spotify Patcher',
            CFBundleIdentifier='local.spotify.patcher', CFBundleVersion='5',
            CFBundleShortVersionString='2.3', CFBundleExecutable='SpotifyPatcher',
            CFBundlePackageType='APPL', CFBundleIconFile='Patcher', NSHighResolutionCapable=True, LSMinimumSystemVersion='13.0',
            NSHumanReadableCopyright='Local build helper. Not affiliated with Spotify, Vitrine or Chroma.',
            CFBundleDocumentTypes=[dict(CFBundleTypeName='Spotify IPA', CFBundleTypeRole='Editor',
                LSHandlerRank='Alternate', CFBundleTypeExtensions=['ipa'])])
(app/'Contents/Info.plist').write_bytes(plistlib.dumps(info))
(app/'Contents/Resources/config.json').write_text('{}\n')
PY
codesign --force --deep --sign - "$APP"
printf 'Built %s\n' "$APP"
