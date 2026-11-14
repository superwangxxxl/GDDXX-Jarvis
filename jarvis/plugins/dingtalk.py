"""DingTalk, read-only, through the user's own DingTalk Workspace CLI (dws). First use: "what is my latest 听记?" -> title,
time and the link to the source. Nothing else is fetched: no transcript, no summary, no other people's content.

How it stays safe:
  - one tool, `dingtalk_latest_minutes`, with an enum argument; the model never supplies a command, a path or a flag;
  - the program only ever runs two exact command shapes (see check_argv): `dws minutes list <mine|all> --limit 10 ...` and
    `dws minutes get info --id <validated id> ...`, both read-only commands, without a shell. Anything else (send a message,
    approve, delete, update, upload, --yes, --mock ...) is refused before anything is executed;
  - dws runs with the user's own login: Jarvis never reads, copies or asks for a token, cookie or password, and the child
    process gets only a small allow-list of environment variables (not the language-model key);
  - the dws path is local configuration, never in this repository:  ~/.jarvis/config.json
        "dingtalk": {"dws_path": "/absolute/path/to/dws", "timeout": 20}      (or the environment variable JARVIS_DWS_PATH)
    Without it the tool is not offered. Switch the whole plugin off with "plugins": {"dingtalk": false}.

The command lines come from the dws help of version 1.0.57 (minutes list mine|all: --limit/--cursor/--query/--start/--end;
minutes get info: --id; global --format json, --timeout seconds). dws's JSON field names are not part of that help, so the
reader looks for the documented ones (taskUuid, title, creation/start time, link) and, if the shape is something else, answers
bad_output with the key names it saw (never values)."""
import json
import os
import re
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from .. import config

CTX = None
LIMIT = "10"
DWS_NAMES = ("dws", "dws.exe", "dws.cmd")
SCOPES = ("mine", "all")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.=+/\-]{3,127}$")
MAX_OUT = 1_000_000
ENV_KEEP = {"HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TZ", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
            "XDG_CACHE_HOME", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy"}
BASE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin"

TITLE_KEYS = ("title", "name", "taskTitle", "minutesTitle", "subject")
ID_KEYS = ("taskUuid", "task_uuid", "taskUUID", "uuid", "id")
TIME_KEYS = ("createTime", "create_time", "createdAt", "created_at", "gmtCreate", "startTime", "start_time", "beginTime", "time")
URL_HINT = re.compile(r"url|link", re.I)
TIME_HINT = re.compile(r"create|start|begin|gmt|time|date", re.I)
AUTH_HINT = re.compile(r"未登录|未授权|登录|授权|过期|login|log in|auth|unauthori[sz]ed|token|credential|expired|401|403", re.I)

MESSAGES = {
    "not_configured": "还没有配置 dws 的路径（~/.jarvis/config.json 的 dingtalk.dws_path）。",
    "not_installed": "找不到 dws 命令，或者它不可执行，请检查配置的路径。",
    "not_authorized": "dws 还没有登录或授权已过期。请在终端里用 dws 重新登录（具体命令见 dws auth --help），然后再试。",
    "timeout": "dws 没有在限定时间内返回，已中止。稍后再试。",
    "failed": "dws 执行失败了。",
    "bad_output": "dws 返回的内容无法识别。",
    "no_minutes": "没有找到听记。",
    "rejected": "这个操作不在只读白名单里，已拒绝。",
    "invalid_argument": "参数不合法，已拒绝。",
}


class DwsError(Exception):
    def __init__(self, code, detail=None):
        super().__init__(code)
        self.code, self.detail = code, detail or {}

    def result(self):
        return {"ok": False, "error": self.code, "message": MESSAGES.get(self.code, MESSAGES["failed"]), **self.detail,
                "instruction": "Tell the user plainly that DingTalk could not be read and why. Do not guess minutes or make anything up."}


def bind(ctx):
    global CTX
    CTX = ctx


# -- configuration and the whitelist -----------------------------------------
def settings():
    c = config.section("dingtalk")
    try:
        timeout = max(5, min(60, int(c.get("timeout", 20))))
    except (TypeError, ValueError):
        timeout = 20
    return (os.environ.get("JARVIS_DWS_PATH") or str(c.get("dws_path") or "")).strip(), timeout


def dws_path():
    """The configured dws as an absolute path, or raises DwsError (not_configured / not_installed)."""
    raw, _ = settings()
    if not raw:
        raise DwsError("not_configured")
    p = Path(raw).expanduser()
    if not p.is_absolute() or p.name not in DWS_NAMES or ".." in p.parts:
        raise DwsError("not_installed", {"detail": "dws_path must be an absolute path to the dws command"})
    if not p.is_file() or not os.access(p, os.X_OK):
        raise DwsError("not_installed")
    return str(p)


def check_argv(argv):
    """The whole whitelist. argv[0] is the dws command; the rest must be exactly one of the two read-only command shapes."""
    if not isinstance(argv, (list, tuple)) or len(argv) != 10 or not all(isinstance(a, str) for a in argv):
        raise DwsError("rejected")
    if not os.path.isabs(argv[0]) or os.path.basename(argv[0]) not in DWS_NAMES:
        raise DwsError("rejected")
    rest = list(argv[1:])
    tail = rest[5:]
    tail_ok = tail[:3] == ["--format", "json", "--timeout"] and len(tail) == 4 and tail[3].isdigit() and 1 <= int(tail[3]) <= 60
    if rest[:2] == ["minutes", "list"] and rest[2] in SCOPES and rest[3:5] == ["--limit", LIMIT] and tail_ok:
        return "list"
    if rest[:3] == ["minutes", "get", "info"] and rest[3] == "--id" and ID_RE.match(rest[4]) and ".." not in rest[4] and tail_ok:
        return "info"
    raise DwsError("rejected")


def _env(dws):
    env = {k: v for k, v in os.environ.items() if k in ENV_KEEP or k.startswith("DWS_")}
    env["PATH"] = os.path.dirname(dws) + os.pathsep + BASE_PATH          # dws may be a node script that needs the node beside it
    return env


def _exec(argv, timeout):
    check_argv(argv)
    posix = os.name == "posix"
    try:
        p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                             env=_env(argv[0]), start_new_session=posix)
    except (FileNotFoundError, PermissionError, NotADirectoryError):
        raise DwsError("not_installed")
    except OSError as e:
        raise DwsError("failed", {"detail": type(e).__name__})
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL) if posix else p.kill()
        except OSError:
            p.kill()
        p.communicate()
        raise DwsError("timeout")
    if len(out) > MAX_OUT:
        raise DwsError("bad_output", {"detail": "output too large"})
    if p.returncode != 0:
        text = (out + err).decode("utf-8", "replace")
        raise DwsError("not_authorized" if AUTH_HINT.search(text) else "failed", {"exit_code": p.returncode})
    try:
        data = json.loads(out.decode("utf-8", "replace"))
    except ValueError:
        raise DwsError("bad_output", {"detail": "not JSON"})
    if isinstance(data, dict) and (data.get("success") is False or data.get("error") or data.get("errcode") not in (None, 0, "0")):
        raise DwsError("not_authorized" if AUTH_HINT.search(json.dumps(data, ensure_ascii=False)[:2000]) else "failed")
    return data


# -- reading dws's JSON, tolerantly -------------------------------------------
def _clean(s, n=200):
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(s)).strip()[:n] if isinstance(s, (str, int, float)) and not isinstance(s, bool) else ""


def _to_epoch(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        if s.isdigit():
            v = int(s)
        else:
            try:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00").replace("/", "-"))
                return dt.timestamp()
            except ValueError:
                return None
    if isinstance(v, (int, float)):
        t = float(v) / 1000 if v > 1e11 else float(v)
        return t if 946684800 <= t <= time.time() + 86400 else None
    return None


def _walk(o, depth=0):
    """Every list of dicts in the JSON (breadth first, shallow)."""
    queue = [(o, 0)]
    while queue:
        cur, d = queue.pop(0)
        if isinstance(cur, list) and cur and all(isinstance(x, dict) for x in cur):
            yield cur
        children = cur if isinstance(cur, list) else list(cur.values()) if isinstance(cur, dict) else []
        if d < 4:
            queue.extend((c, d + 1) for c in children if isinstance(c, (list, dict)))


def _first(d, keys):
    for k in keys:
        if k in d and d[k] not in (None, "", [], {}):
            return d[k]
    return None


def _shape(data):
    """Key names only: enough to adapt the reader without ever exposing content."""
    def keys(o, d=0):
        if isinstance(o, dict):
            return {k: (keys(v, d + 1) if d < 2 else "…") for k, v in list(o.items())[:20]}
        if isinstance(o, list):
            return [keys(o[0], d + 1)] if o and d < 2 else []
        return type(o).__name__
    return keys(data)


def pick_latest(data):
    """(task id, epoch or None, ordered?) of the newest minutes in dws's list answer."""
    for items in _walk(data):
        rows = []
        for it in items:
            tid = _first(it, ID_KEYS)
            if not isinstance(tid, str) or not ID_RE.match(tid) or ".." in tid:
                continue
            t = _to_epoch(_first(it, TIME_KEYS))
            if t is None:
                for k, v in it.items():
                    if TIME_HINT.search(k):
                        t = _to_epoch(v)
                        if t is not None:
                            break
            rows.append((tid, t))
        if rows:
            dated = [r for r in rows if r[1] is not None]
            if dated:
                best = max(dated, key=lambda r: r[1])
                return best[0], best[1], True
            return rows[0][0], None, False
    if _has_empty_list(data):
        raise DwsError("no_minutes")
    raise DwsError("bad_output", {"shape": _shape(data)})


def _has_empty_list(o, depth=0):
    if isinstance(o, list):
        return not o or (depth < 3 and any(_has_empty_list(x, depth + 1) for x in o if isinstance(x, (list, dict))))
    return isinstance(o, dict) and depth < 3 and any(_has_empty_list(v, depth + 1) for v in o.values() if isinstance(v, (list, dict)))


def safe_url(u):
    u = _clean(u, 2000)
    if not u or re.search(r"\s", u):
        return ""
    p = urlsplit(u)
    return u if p.scheme in ("http", "https") and p.netloc and "@" not in p.netloc else ""


def read_info(data):
    """(title, epoch or None, link) from `minutes get info`, or raises bad_output."""
    cands = [data] if isinstance(data, dict) else []
    queue = list(cands)
    seen = 0
    while queue and seen < 50:
        cur = queue.pop(0)
        seen += 1
        if isinstance(cur, dict):
            if _first(cur, TITLE_KEYS) is not None or any(isinstance(v, str) and URL_HINT.search(k) for k, v in cur.items()):
                cands.append(cur)
            queue.extend(v for v in cur.values() if isinstance(v, dict))
    for d in cands:
        title = _clean(_first(d, TITLE_KEYS))
        url = ""
        for k, v in d.items():
            if URL_HINT.search(k) and isinstance(v, str) and safe_url(v):
                url = safe_url(v)
                break
        t = _to_epoch(_first(d, ("startTime", "start_time", "beginTime", "createTime", "create_time", "gmtCreate")))
        if title or url:
            return title, t, url
    raise DwsError("bad_output", {"shape": _shape(data)})


# -- the tool -------------------------------------------------------------------
def latest_minutes(scope="mine", **_ignored):
    """Title, time and source link of the user's newest DingTalk minutes (听记). Read-only."""
    try:
        scope = scope if scope in SCOPES else "mine"
        dws = dws_path()
        _, timeout = settings()
        flags = ["--format", "json", "--timeout", str(timeout)]
        listing = _exec([dws, "minutes", "list", scope, "--limit", LIMIT, *flags], timeout + 5)
        task_id, listed_at, ordered = pick_latest(listing)
        title, started, url = read_info(_exec([dws, "minutes", "get", "info", "--id", task_id, *flags], timeout + 5))
        when = started or listed_at
        res = {"ok": True, "source": "DingTalk 听记 (read-only, local dws)", "scope": scope, "title": title or "(untitled)",
               "time": datetime.fromtimestamp(when).strftime("%Y-%m-%d %H:%M") if when else "unknown",
               "link": url or "not provided", "shown_on_screen": True}
        if not ordered:
            res["note"] = "dws gave no timestamps in the list, so 'latest' is the first entry dws returned."
    except DwsError as e:
        return e.result()
    ui = getattr(CTX, "ui", None)
    if ui:
        ui({"type": "dingtalk_minutes", "title": res["title"], "time": res["time"], "url": url})
    return res


TOOL = {"type": "function", "function": {
    "name": "dingtalk_latest_minutes",
    "description": "Read-only: the user's most recent DingTalk meeting minutes (听记): its title, time and a link to the source, "
                   "through the user's own logged-in dws. Use when asked for the latest/last/recent 听记, 妙记 or meeting minutes. "
                   "It reads nothing else and cannot change anything.",
    "parameters": {"type": "object", "properties": {"scope": {"type": "string", "enum": list(SCOPES), "description":
                   "mine = minutes I created (default); all = every minutes I can access, including shared ones"}}}}}

HINT = (" For the latest DingTalk minutes call dingtalk_latest_minutes. Tell the user its title and time in a sentence; the source "
        "link is shown on screen, so say that instead of reading the link aloud. If it reports an error, say what it says and "
        "never invent minutes.")

WANT = re.compile(r"(听记|妙记|会议纪要|纪要|meeting minutes|dingtalk minutes)", re.I)
LATEST = re.compile(r"(最新|最近|上一[条次个场]|刚[刚才]|latest|last|recent|newest)", re.I)


def wants(text):
    return "dingtalk_latest_minutes" if WANT.search(text) and LATEST.search(text) else None


def provide():
    return ([(TOOL, latest_minutes)], HINT) if settings()[0] else ([], "")
