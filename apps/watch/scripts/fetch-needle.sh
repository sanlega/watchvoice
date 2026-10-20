#!/usr/bin/env bash
# Downloads the needle engine (watchOS) and the Whistle model into apps/watch/Vendor/needle/ (gitignored).
#   Engine: https://huggingface.co/Cactus-Compute/needle3/tree/main/watchos-arm64
#   Model:  https://huggingface.co/Cactus-Compute/whistle  (Apache-2.0)
# Exact file paths inside the HF repos are UNVERIFIED; override with env vars if they differ:
#   NEEDLE_BASE_URL, WHISTLE_URL
set -euo pipefail

cd "$(dirname "$0")/.."
DEST="Vendor/needle"
mkdir -p "$DEST"

NEEDLE_BASE_URL="${NEEDLE_BASE_URL:-https://huggingface.co/Cactus-Compute/needle3/resolve/main/watchos-arm64}"
WHISTLE_URL="${WHISTLE_URL:-https://huggingface.co/Cactus-Compute/whistle/resolve/main/whistle.cact}"
EXPECTED_MODEL_BYTES=16919407

fetch() {
  echo "-> $2"
  curl -fL --retry 3 --proto '=https' -o "$DEST/$2.part" "$1"
  mv "$DEST/$2.part" "$DEST/$2"
}

fetch "$NEEDLE_BASE_URL/needle.h" needle.h
fetch "$NEEDLE_BASE_URL/libneedle.a" libneedle.a
fetch "$WHISTLE_URL" whistle.cact

size=$(wc -c < "$DEST/whistle.cact" | tr -d ' ')
if [ "$size" != "$EXPECTED_MODEL_BYTES" ]; then
  echo "error: whistle.cact is $size bytes, expected $EXPECTED_MODEL_BYTES" >&2
  exit 1
fi

if command -v lipo >/dev/null 2>&1; then
  lipo -info "$DEST/libneedle.a" || true   # expect arm64_32 + arm64 (device only)
fi

# TODO: pin these sha256 values after the first verified download and fail on mismatch.
echo "sha256:"
if command -v shasum >/dev/null 2>&1; then
  shasum -a 256 "$DEST/needle.h" "$DEST/libneedle.a" "$DEST/whistle.cact"
else
  sha256sum "$DEST/needle.h" "$DEST/libneedle.a" "$DEST/whistle.cact"
fi
