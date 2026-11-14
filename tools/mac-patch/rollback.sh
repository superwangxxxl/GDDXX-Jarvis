#!/bin/bash
# Undo install.sh: restore the original files from the newest backup (or --backup DIR), remove the files the patch added and
# the compiled location helper.   ./rollback.sh [--backup DIR] [--clean-config]
set -euo pipefail
SUPPORT="${JARVIS_APP_DIR:-$HOME/Library/Application Support/Jarvis}"
DATA="${JARVIS_HOME:-$HOME/.jarvis}"
BK=""; CLEAN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --backup) BK="$2"; shift 2;;
    --clean-config) CLEAN=1; shift;;
    *) echo "unknown option $1"; exit 2;;
  esac
done
[ -n "$BK" ] || BK="$(ls -1d "$SUPPORT"/patch-backup/*/ 2>/dev/null | sort | tail -1 | sed 's:/$::')"
[ -n "$BK" ] && [ -d "$BK/files" ] || { echo "no backup found in $SUPPORT/patch-backup"; exit 1; }
APP="$(cat "$BK/app.txt")"; RES="$APP/Contents/Resources"
(cd "$BK/files" && find . -type f | sed 's:^\./::') | while read -r path; do cp -p "$BK/files/$path" "$RES/$path"; done
while read -r path; do [ -n "$path" ] && rm -f "$RES/$path"; done < "$BK/added.txt"
find "$RES/jarvis" "$RES/hud" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
rm -rf "$SUPPORT/JarvisLocate.app"
if [ $CLEAN = 1 ] && [ -f "$BK/config-sections.json" ]; then
  CFG="$DATA/config.json" BK="$BK" "${JARVIS_PATCH_PYTHON:-$RES/python/bin/python3}" - <<'PYEOF'
import json, os
cfg = json.load(open(os.environ["CFG"], encoding="utf-8"))
old = json.load(open(os.path.join(os.environ["BK"], "config-sections.json")))
for k, v in old.items():
    cfg.pop(k, None) if v is None else cfg.__setitem__(k, v)
cfg.pop("weather", None)
tmp = os.environ["CFG"] + ".tmp"
open(tmp, "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False, indent=2)); os.chmod(tmp, 0o600); os.replace(tmp, os.environ["CFG"])
PYEOF
fi
echo "[rollback] restored the original 2.0.4 files from $BK. System Settings > Privacy & Security > Location Services may still list 'Jarvis Location': remove it there if you like."
