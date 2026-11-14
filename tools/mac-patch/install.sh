#!/bin/bash
# Patch an installed Jarvis 2.0.4 (macOS) in place: weather from the device location, DingTalk read-only minutes.
#   ./install.sh [--app /Applications/Jarvis.app] [--dws /absolute/path/to/dws] [--dry-run] [--force] [--resign]
# Only touches files listed in manifest.txt, after backing them up (see rollback.sh). Quit Jarvis first.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="/Applications/Jarvis.app"; DWS=""; DRY=0; FORCE=0; RESIGN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --app) APP="$2"; shift 2;;
    --dws) DWS="$2"; shift 2;;
    --dry-run) DRY=1; shift;;
    --force) FORCE=1; shift;;
    --resign) RESIGN=1; shift;;
    -h|--help) sed -n 2,5p "$0"; exit 0;;
    *) echo "unknown option $1"; exit 2;;
  esac
done
RES="$APP/Contents/Resources"
DATA="${JARVIS_HOME:-$HOME/.jarvis}"
SUPPORT="${JARVIS_APP_DIR:-$HOME/Library/Application Support/Jarvis}"
PY="${JARVIS_PATCH_PYTHON:-$RES/python/bin/python3}"
sha() { if command -v sha256sum >/dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
say() { echo "[patch] $*"; }
die() { echo "[patch] ERROR: $*" >&2; exit 1; }

[ "${JARVIS_PATCH_ALLOW_NON_MAC:-0}" = 1 ] || [ "$(uname)" = Darwin ] || die "this patch is for macOS"
[ -d "$RES/jarvis" ] || die "no Jarvis at $APP (use --app)"
VER="$(grep -A1 CFBundleShortVersionString "$APP/Contents/Info.plist" | sed -n 's:.*<string>\(.*\)</string>.*:\1:p')"
[ "$VER" = "2.0.4" ] || die "this patch is for Jarvis 2.0.4, found '$VER'"
if [ "${JARVIS_PATCH_ALLOW_NON_MAC:-0}" != 1 ] && pgrep -f "$APP/Contents/Resources/(app|python)|jarvis.*-m jarvis" >/dev/null 2>&1; then die "Jarvis is running: quit it first"; fi

# 1. every file to replace must be the stock 2.0.4 file (or already patched)
BAD=0
while read -r st path new old; do
  cur="$RES/$path"
  if [ "$st" = M ]; then
    [ -f "$cur" ] || { say "missing $path"; BAD=1; continue; }
    h="$(sha "$cur")"
    [ "$h" = "$old" ] || [ "$h" = "$new" ] || { say "$path is not the stock 2.0.4 file"; BAD=1; }
  elif [ -e "$cur" ] && [ "$(sha "$cur")" != "$new" ]; then
    say "$path already exists with other content"; BAD=1
  fi
done < "$HERE/manifest.txt"
if [ $BAD = 1 ] && [ $FORCE = 0 ]; then die "installed files differ from 2.0.4 (use --force to patch anyway; originals are backed up)"; fi
[ $DRY = 1 ] && { say "dry run: $(wc -l < "$HERE/manifest.txt" | tr -d ' ') files would be copied into $RES"; exit 0; }

# 2. backup
STAMP="$(date +%Y%m%d-%H%M%S)"
BK="$SUPPORT/patch-backup/$STAMP"
mkdir -p "$BK/files"; chmod 700 "$SUPPORT/patch-backup" "$BK" 2>/dev/null || true
: > "$BK/added.txt"
while read -r st path new old; do
  if [ "$st" = M ]; then mkdir -p "$BK/files/$(dirname "$path")"; cp -p "$RES/$path" "$BK/files/$path"; else echo "$path" >> "$BK/added.txt"; fi
done < "$HERE/manifest.txt"
echo "$APP" > "$BK/app.txt"
say "backup of the original files: $BK"

# 3. copy
while read -r st path new old; do
  mkdir -p "$RES/$(dirname "$path")"
  cp "$HERE/files/$path" "$RES/$path" || die "could not write $path (macOS may need App Management / Full Disk Access for this Terminal)"
  [ "$(sha "$RES/$path")" = "$new" ] || die "checksum mismatch after copying $path"
done < "$HERE/manifest.txt"
find "$RES/jarvis" "$RES/hud" "$RES/app" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
say "files copied and verified"

# 4. optional: ad-hoc re-sign. Off by default: the app keeps its signature identity, so the microphone and camera permissions
#    macOS already granted stay valid. Use --resign if macOS says the app is damaged (then re-allow the microphone once).
if [ $RESIGN = 1 ]; then codesign --force --deep -s - "$APP" && say "re-signed ad hoc"; fi

# 5. the location helper: compiled once with the Xcode command line tools, then reused
if [ "${JARVIS_PATCH_SKIP_HELPER:-0}" != 1 ]; then
  say "preparing the location helper (needs the Xcode command line tools; may take a minute)..."
  PYTHONPATH="$RES" "$PY" - <<'PYEOF' || true
from jarvis.platforms import maclocate
app, why = maclocate.ensure_helper()
print("[patch] location helper ready" if app else "[patch] location helper NOT built: %s (%s); the weather will ask you to choose a city" % (why["reason"], why["message"]))
PYEOF
fi

# 6. optional, local only: where your own dws lives
if [ -n "$DWS" ]; then
  case "$DWS" in /*) ;; *) die "--dws needs an absolute path";; esac
  [ -x "$DWS" ] && [ "$(basename "$DWS")" = dws ] || die "$DWS is not an executable named dws"
  mkdir -p "$DATA"
  DWS_PATH="$DWS" CFG="$DATA/config.json" BK="$BK" "$PY" - <<'PYEOF'
import json, os
cfg_path = os.environ["CFG"]
try:
    cfg = json.load(open(cfg_path, encoding="utf-8"))
except Exception:
    cfg = {}
json.dump({"dingtalk": cfg.get("dingtalk")}, open(os.path.join(os.environ["BK"], "config-sections.json"), "w"))
cfg["dingtalk"] = {**(cfg.get("dingtalk") or {}), "dws_path": os.environ["DWS_PATH"]}
tmp = cfg_path + ".tmp"
open(tmp, "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False, indent=2))
os.chmod(tmp, 0o600)
os.replace(tmp, cfg_path)
PYEOF
  say "dws path saved in $DATA/config.json (stays on this Mac)"
else
  say "DingTalk is off until you pass --dws /absolute/path/to/dws (or set dingtalk.dws_path in $DATA/config.json)"
fi
say "done. Open Jarvis. Checklist: README-中文.md, section 'Mac 验收'. Undo with: $HERE/rollback.sh"
