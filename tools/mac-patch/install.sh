#!/bin/bash
# Patch an installed Jarvis 2.0.4 (macOS) in place: weather from the device location, Chinese voice, DingTalk read-only minutes.
#   ./install.sh [--app /Applications/Jarvis.app] [--dws /absolute/path/to/dws] [--zh] [--dry-run] [--force] [--resign]
#   --zh  also switch Jarvis to Chinese voice mode. The Chinese model folders (wake word, Kokoro voice) are verified FIRST,
#         file by file against the official archive's manifest, including a folder that is already on disk; anything missing,
#         half-downloaded or different is replaced from the verified official archive. Only if that succeeds are the app files
#         changed and voice_lang/reply_lang set to zh. If it fails, the app and ~/.jarvis/config.json are left as they were.
# The install is all-or-nothing: if copying the files fails, the originals are put back; the config is changed last, in one
# atomic write, and never if it cannot be read as JSON. Quit Jarvis first. Undo with rollback.sh.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="/Applications/Jarvis.app"; DWS=""; DRY=0; FORCE=0; RESIGN=0; ZH=0
while [ $# -gt 0 ]; do
  case "$1" in
    --app) APP="$2"; shift 2;;
    --dws) DWS="$2"; shift 2;;
    --dry-run) DRY=1; shift;;
    --force) FORCE=1; shift;;
    --resign) RESIGN=1; shift;;
    --zh) ZH=1; shift;;
    -h|--help) sed -n 2,10p "$0"; exit 0;;
    *) echo "unknown option $1"; exit 2;;
  esac
done
RES="$APP/Contents/Resources"
DATA="${JARVIS_HOME:-$HOME/.jarvis}"
SUPPORT="${JARVIS_APP_DIR:-$HOME/Library/Application Support/Jarvis}"
PY="${JARVIS_PATCH_PYTHON:-$RES/python/bin/python3}"
BK=""; COPIED=0
sha() { if command -v sha256sum >/dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
say() { echo "[patch] $*"; }
# put back the original files if this run already replaced some of them (the config is only written after this point passes)
undo_files() {
  [ "$COPIED" = 1 ] && [ -n "$BK" ] || return 0
  say "undoing the file changes of this run"
  (cd "$BK/files" && find . -type f | sed 's:^\./::') | while read -r p; do cp -p "$BK/files/$p" "$RES/$p"; done
  while read -r p; do [ -n "$p" ] && { rm -f "$RES/$p"; rmdir "$RES/$(dirname "$p")" 2>/dev/null || true; }; done < "$BK/added.txt"
  COPIED=0
}
die() { echo "[patch] ERROR: $*" >&2; undo_files; exit 1; }

[ "${JARVIS_PATCH_ALLOW_NON_MAC:-0}" = 1 ] || [ "$(uname)" = Darwin ] || die "this patch is for macOS"
[ -d "$RES/jarvis" ] || die "no Jarvis at $APP (use --app)"
VER="$(grep CFBundleShortVersionString "$APP/Contents/Info.plist" | sed -n 's:.*</key>[[:space:]]*<string>\(.*\)</string>.*:\1:p' | head -1)"
[ "$VER" = "2.0.4" ] || die "this patch is for Jarvis 2.0.4, found '$VER'"
if [ "${JARVIS_PATCH_ALLOW_NON_MAC:-0}" != 1 ] && pgrep -f "$APP/Contents/Resources/(app|python)|jarvis.*-m jarvis" >/dev/null 2>&1; then die "Jarvis is running: quit it first"; fi

# 1. checks before anything is changed
BAD=0
while read -r st path new old; do      # every file to replace must be the stock 2.0.4 file (or already patched)
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
if [ -n "$DWS" ]; then
  case "$DWS" in /*) ;; *) die "--dws needs an absolute path";; esac
  [ -x "$DWS" ] && [ "$(basename "$DWS")" = dws ] || die "$DWS is not an executable named dws"
fi
if [ -e "$DATA/config.json" ]; then     # never write over a config this script cannot read: it holds the model key
  CFG="$DATA/config.json" "$PY" -c 'import json,os; json.load(open(os.environ["CFG"], encoding="utf-8"))' 2>/dev/null \
    || die "$DATA/config.json cannot be read as JSON: not touching it (fix or move it, then run again)"
fi
[ $DRY = 1 ] && { say "dry run: $(wc -l < "$HERE/manifest.txt" | tr -d ' ') files would be copied into $RES"; exit 0; }

# 2. the Chinese models, verified before the app or the config change
if [ $ZH = 1 ]; then
  say "verifying the Chinese speech models; missing or different ones are downloaded again from the official release (about 400 MB, size and sha256 checked)..."
  # the patched installer runs from the patch folder (the app is not modified yet); jarvis/ comes from the installed app
  PYTHONPATH="$RES" JARVIS_MODELS_FROM="$RES/models-bundle" JARVIS_LANG=zh "$PY" "$HERE/files/app/install.py" --no-page --models-only \
    || die "the Chinese models could not be verified or downloaded, so nothing was changed (the app, voice_lang and reply_lang stay as they were). Run this again when the network is back."
  say "Chinese models verified"
fi

# 3. backup into a NEW folder (older backups are never touched); only files that differ from the patched version are saved
STAMP="$(date +%Y%m%d-%H%M%S)-$$"
BK="$SUPPORT/patch-backup/$STAMP"
mkdir -p "$SUPPORT/patch-backup"; mkdir "$BK" || die "backup folder $BK already exists"
mkdir "$BK/files"; chmod 700 "$SUPPORT/patch-backup" "$BK" 2>/dev/null || true
: > "$BK/added.txt"; N=0
while read -r st path new old; do
  if [ "$st" = M ]; then
    [ "$(sha "$RES/$path")" = "$new" ] && continue
    mkdir -p "$BK/files/$(dirname "$path")"; cp -p "$RES/$path" "$BK/files/$path"; N=1
  else
    [ -e "$RES/$path" ] && continue
    echo "$path" >> "$BK/added.txt"; N=1
  fi
done < "$HERE/manifest.txt"
echo "$APP" > "$BK/app.txt"
[ $N = 1 ] && say "backup of the original files: $BK" || say "files already patched: nothing to back up (this run's record: $BK)"

# 4. copy and verify
COPIED=1
while read -r st path new old; do
  mkdir -p "$RES/$(dirname "$path")"
  cp "$HERE/files/$path" "$RES/$path" || die "could not write $path (macOS may need App Management / Full Disk Access for this Terminal)"
  [ "$(sha "$RES/$path")" = "$new" ] || die "checksum mismatch after copying $path"
done < "$HERE/manifest.txt"
find "$RES/jarvis" "$RES/hud" "$RES/app" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
say "files copied and verified"

# 5. optional ad-hoc re-sign. Off by default: the app keeps its signature identity, so the microphone and camera permissions
#    macOS already granted stay valid. Use --resign if macOS says the app is damaged (then re-allow the microphone once).
if [ $RESIGN = 1 ]; then codesign --force --deep -s - "$APP" && say "re-signed ad hoc"; fi

# 6. the location helper: compiled once with the Xcode command line tools, then reused (failing to build it is not fatal)
if [ "${JARVIS_PATCH_SKIP_HELPER:-0}" != 1 ]; then
  say "preparing the location helper (needs the Xcode command line tools; may take a minute)..."
  PYTHONPATH="$RES" "$PY" - <<'PYEOF' || true
from jarvis.platforms import maclocate
app, why = maclocate.ensure_helper()
print("[patch] location helper ready" if app else "[patch] location helper NOT built: %s (%s); the weather will ask you to choose a city" % (why["reason"], why["message"]))
PYEOF
fi

# 7. settings, last and in one atomic write: the previous values of the keys we touch are saved in this run's backup folder
PATCH='{}'
if [ -n "$DWS" ]; then PATCH="$(DWS_PATH="$DWS" "$PY" -c 'import json,os; print(json.dumps({"dingtalk": {"dws_path": os.environ["DWS_PATH"]}}))')"; fi
if [ $ZH = 1 ]; then PATCH="$(P="$PATCH" "$PY" -c 'import json,os; d=json.loads(os.environ["P"]); d.update(voice_lang="zh", reply_lang="zh"); print(json.dumps(d))')"; fi
if [ "$PATCH" != "{}" ]; then
  mkdir -p "$DATA"
  PATCH_JSON="$PATCH" CFG="$DATA/config.json" BK="$BK" "$PY" - <<'PYEOF' || die "could not write the settings (the app files were put back; ~/.jarvis/config.json was not changed)"
import json, os
cfg_path = os.environ["CFG"]
try:
    cfg = json.load(open(cfg_path, encoding="utf-8"))
except FileNotFoundError:
    cfg = {}
patch = json.loads(os.environ["PATCH_JSON"])
json.dump({k: cfg.get(k) for k in patch}, open(os.path.join(os.environ["BK"], "config-sections.json"), "x"))
for k, v in patch.items():
    cfg[k] = {**(cfg.get(k) or {}), **v} if isinstance(v, dict) else v
tmp = cfg_path + ".tmp"
open(tmp, "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False, indent=2))
os.chmod(tmp, 0o600)
os.replace(tmp, cfg_path)
PYEOF
  chmod 600 "$BK/config-sections.json" 2>/dev/null || true
fi
[ -n "$DWS" ] && say "dws path saved in $DATA/config.json (stays on this Mac)" || say "DingTalk is off until you pass --dws /absolute/path/to/dws (or set dingtalk.dws_path in $DATA/config.json)"
[ $ZH = 1 ] && say "Chinese voice mode is on (models verified): wake word 嗨，贾维斯, Chinese recognition and voice, Chinese answers" \
  || say "Chinese voice mode: pass --zh, or switch later in 菜单 > 语言与语音 (it verifies and downloads the models itself)."
say "done. Open Jarvis. Checklists: README-中文.md (weather, DingTalk) and README-中文语音.md (Chinese voice), section 'Mac 验收'. Undo with: $HERE/rollback.sh"
