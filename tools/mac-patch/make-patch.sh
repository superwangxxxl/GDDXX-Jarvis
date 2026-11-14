#!/bin/bash
# Build a deployable patch package for an installed Jarvis 2.0.4 from this checkout:
#   bash tools/mac-patch/make-patch.sh [output-dir] [--from <git revision already installed>]
#
# Without --from: the package carries every file under jarvis/ hud/ app/ that differs from the v2.0.4 tag, and install.sh accepts
# each of those files as the stock 2.0.4 one or as already patched.
# With --from REV (e.g. the commit of the patch that is already installed): the package carries only what changed since REV, and
# install.sh accepts a file only as it was in REV (the installed state) or already new. It also checks that the rest of that
# earlier patch is in place, so a stock 2.0.4 is refused with a clear message instead of getting half an upgrade. Nobody has to use
# --force to put this on top of the earlier package.
# The sha256 of every accepted old file and of the new one is in manifest.txt.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="$ROOT/dist"; FROM=""
while [ $# -gt 0 ]; do
  case "$1" in
    --from) FROM="$2"; shift 2;;
    *) OUT="$1"; shift;;
  esac
done
STOCK=v2.0.4
cd "$ROOT"
git rev-parse -q --verify "$STOCK^{commit}" >/dev/null || { echo "tag $STOCK not found (git fetch --tags)"; exit 1; }
HEAD_SHORT="$(git rev-parse --short HEAD)"
if [ -n "$FROM" ]; then
  FROM_SHORT="$(git rev-parse --short "$FROM^{commit}")"
  NAME="jarvis-2.0.4-upgrade-${FROM_SHORT}-to-${HEAD_SHORT}"
else
  FROM="$STOCK"; FROM_SHORT=""
  NAME="jarvis-2.0.4-zh-weather-dws-patch"
fi
git diff --quiet HEAD -- jarvis hud app || { echo "jarvis/ hud/ app/ have uncommitted changes: commit first, the package must equal a commit"; exit 1; }
PKG="$OUT/$NAME"
rm -rf "$PKG" "$PKG.tar.gz"; mkdir -p "$PKG/files"
fsha() { if command -v sha256sum >/dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
gsha() { git show "$1:$2" 2>/dev/null | { sha256sum 2>/dev/null || shasum -a 256; } | cut -d' ' -f1; }
exists_in() { git cat-file -e "$1:$2" 2>/dev/null; }
: > "$PKG/manifest.txt"
# a delta package only works on top of the earlier patch: every other file that patch changed must be there as it was
if [ "$FROM" != "$STOCK" ]; then
  git diff --name-only "$STOCK" "$FROM" -- jarvis hud app | while read -r path; do
    git diff --quiet "$FROM" HEAD -- "$path" && echo "# requires $path $(gsha "$FROM" "$path")" >> "$PKG/manifest.txt" || true
  done
fi
git diff --name-status "$FROM" HEAD -- jarvis hud app | while IFS=$'\t' read -r st path rest; do
  case "$st" in
    M|A)
      mkdir -p "$PKG/files/$(dirname "$path")"; cp "$ROOT/$path" "$PKG/files/$path"
      olds="-"
      exists_in "$FROM" "$path" && olds="$(gsha "$FROM" "$path")"
      echo "$st $path $(fsha "$ROOT/$path") $olds" >> "$PKG/manifest.txt";;
    *) echo "unsupported change $st $path (this patch only adds and modifies files)"; exit 1;;
  esac
done
cp "$ROOT/tools/mac-patch/install.sh" "$ROOT/tools/mac-patch/rollback.sh" "$PKG/"; chmod 755 "$PKG/install.sh" "$PKG/rollback.sh"
cp "$ROOT/docs/weather-dingtalk.md" "$PKG/README-中文.md"; cp "$ROOT/docs/chinese-voice.md" "$PKG/README-中文语音.md"
echo "stock $STOCK, installed state $FROM, this package $(git rev-parse HEAD)" > "$PKG/BASE"
(cd "$OUT" && tar czf "$NAME.tar.gz" "$NAME")
(cd "$OUT" && { sha256sum "$NAME.tar.gz" 2>/dev/null || shasum -a 256 "$NAME.tar.gz"; } > "$NAME.tar.gz.sha256")
echo "built $OUT/$NAME.tar.gz ($(grep -vc '^#' "$PKG/manifest.txt" | tr -d ' ') files)"
