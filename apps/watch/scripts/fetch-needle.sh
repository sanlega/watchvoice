#!/usr/bin/env bash
# Downloads the needle engine (watchOS device) and the Whistle model into apps/watch/Vendor/needle/ (gitignored)
# and fails unless each file matches its pinned sha256. Skips files that are already present and valid.
#   Engine: https://huggingface.co/Cactus-Compute/needle3 @ d6c7d6a2e9655505e0f8e23339356dd1705900eb (watchos-arm64/)
#   Model:  https://huggingface.co/Cactus-Compute/whistle @ 741118687e1f008f06d1351a402a731274789790 (Apache-2.0)
# To upgrade: bump the commit and the hashes together (CI caches Vendor/ by this file's hash).
set -euo pipefail

cd "$(dirname "$0")/.."
DEST="Vendor/needle"
mkdir -p "$DEST"

NEEDLE_BASE_URL="https://huggingface.co/Cactus-Compute/needle3/resolve/d6c7d6a2e9655505e0f8e23339356dd1705900eb/watchos-arm64"
WHISTLE_URL="https://huggingface.co/Cactus-Compute/whistle/resolve/741118687e1f008f06d1351a402a731274789790/whistle.cact"

sha256() {
  if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1"; else sha256sum "$1"; fi | cut -d' ' -f1
}

# fetch URL NAME SHA256
fetch() {
  if [ -f "$DEST/$2" ] && [ "$(sha256 "$DEST/$2")" = "$3" ]; then
    echo "ok $2 (cached)"
    return
  fi
  echo "-> $2"
  curl -fsSL --retry 3 --proto '=https' -o "$DEST/$2.part" "$1"
  actual=$(sha256 "$DEST/$2.part")
  if [ "$actual" != "$3" ]; then
    rm -f "$DEST/$2.part"
    echo "error: $2 sha256 $actual, expected $3" >&2
    exit 1
  fi
  mv "$DEST/$2.part" "$DEST/$2"
}

fetch "$NEEDLE_BASE_URL/needle.h" needle.h a27dac4b3239eead91d124b7f7dbce5495c13019903fd817b68c16d39b055a99
fetch "$NEEDLE_BASE_URL/libneedle.a" libneedle.a f794e7996701ea83d98d0670c23aeb8c7a4519adc9526c1489f1e08046e12f9c
fetch "$WHISTLE_URL" whistle.cact b6e02f048568ac5d01a2042556c658061e699acbc0aa2a1439f52f3d461dffeb  # 16,919,407 B

if command -v lipo >/dev/null 2>&1; then
  lipo -info "$DEST/libneedle.a"   # expect arm64_32 + arm64 (device only)
fi
