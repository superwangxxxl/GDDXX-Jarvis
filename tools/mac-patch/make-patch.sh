#!/bin/bash
# Build the deployable patch package for the installed Jarvis 2.0.4 from this checkout:
#   bash tools/mac-patch/make-patch.sh [output-dir]
# It copies every file under jarvis/ hud/ app/ that differs from the v2.0.4 tag, records the sha256 of the v2.0.4 original
# next to the new one (install.sh refuses to patch a file that is not the stock 2.0.4 one) and packs install/rollback.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${1:-$ROOT/dist}"
BASE=v2.0.4
NAME=jarvis-2.0.4-weather-dws-patch
PKG="$OUT/$NAME"
cd "$ROOT"
git rev-parse -q --verify "$BASE^{commit}" >/dev/null || { echo "tag $BASE not found (git fetch --tags)"; exit 1; }
rm -rf "$PKG" "$PKG.tar.gz"; mkdir -p "$PKG/files"
sha() { if command -v sha256sum >/dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
: > "$PKG/manifest.txt"
git diff --name-status "$BASE" HEAD -- jarvis hud app | while IFS=$'\t' read -r st path rest; do
  case "$st" in
    M|A)
      mkdir -p "$PKG/files/$(dirname "$path")"; cp "$ROOT/$path" "$PKG/files/$path"
      if [ "$st" = M ]; then old="$(git show "$BASE:$path" | { sha256sum 2>/dev/null || shasum -a 256; } | cut -d' ' -f1)"; else old="-"; fi
      echo "$st $path $(sha "$ROOT/$path") $old" >> "$PKG/manifest.txt";;
    *) echo "unsupported change $st $path (this patch only adds and modifies files)"; exit 1;;
  esac
done
cp "$ROOT/tools/mac-patch/install.sh" "$ROOT/tools/mac-patch/rollback.sh" "$PKG/"; chmod 755 "$PKG/install.sh" "$PKG/rollback.sh"
cp "$ROOT/docs/weather-dingtalk.md" "$PKG/README-中文.md"
echo "$BASE $(git rev-parse HEAD)" > "$PKG/BASE"
(cd "$OUT" && tar czf "$NAME.tar.gz" "$NAME")
(cd "$OUT" && { sha256sum "$NAME.tar.gz" 2>/dev/null || shasum -a 256 "$NAME.tar.gz"; } > "$NAME.tar.gz.sha256")
echo "built $OUT/$NAME.tar.gz ($(wc -l < "$PKG/manifest.txt" | tr -d ' ') files)"
