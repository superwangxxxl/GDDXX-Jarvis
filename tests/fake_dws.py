"""A stand-in for the dws command, for tests only: it prints canned JSON and records how it was called.
Behaviour comes from DWS_FAKE_MODE (DWS_* variables are the ones the plugin lets through); calls are appended to DWS_FAKE_LOG."""
import json
import os
import sys
import time

mode = os.environ.get("DWS_FAKE_MODE", "ok")
log = os.environ.get("DWS_FAKE_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": sys.argv[1:], "env": sorted(os.environ), "path": os.environ.get("PATH", "")}) + "\n")

args = sys.argv[1:]
if mode == "auth":
    sys.stderr.write("error: token expired, please login again\n")
    sys.exit(1)
if mode == "fail":
    sys.stderr.write("error: something else broke\n")
    sys.exit(2)
if mode == "sleep":
    time.sleep(60)
if mode == "notjson":
    print("this is not json")
    sys.exit(0)
if mode == "json_error":
    print(json.dumps({"success": False, "error": "login required"}))
    sys.exit(0)
if mode == "huge":
    sys.stdout.write("[" + "0," * 600_000 + "0]")
    sys.exit(0)

NOW_MS = int(time.time() * 1000)
if args[:3] == ["minutes", "list", "mine"] or args[:3] == ["minutes", "list", "all"]:
    if mode == "empty":
        print(json.dumps({"items": [], "hasMore": False}))
    elif mode == "shape":
        print(json.dumps({"payload": {"rows": [{"foo": "SECRET-VALUE", "bar": 1}]}}))
    elif mode == "evil_id":
        print(json.dumps({"items": [{"taskUuid": "x; touch /tmp/jarvis-pwned", "createTime": NOW_MS}]}))
    elif mode == "notime":
        print(json.dumps({"result": {"minutes": [{"taskUuid": "first-task-0001", "title": "A"}, {"taskUuid": "second-task-0002", "title": "B"}]}}))
    else:      # deliberately not sorted newest-first
        print(json.dumps({"items": [{"taskUuid": "old-task-0001", "title": "older", "createTime": NOW_MS - 7 * 86400_000},
                                     {"taskUuid": "new-task-0002", "title": "newest", "createTime": NOW_MS - 3600_000},
                                     {"taskUuid": "mid-task-0003", "title": "middle", "createTime": NOW_MS - 2 * 86400_000}],
                          "nextCursor": "c1"}))
elif args[:3] == ["minutes", "get", "info"]:
    tid = args[args.index("--id") + 1]
    url = {"badurl": "javascript:alert(1)", "credurl": "https://user:pw@example.invalid/x"}.get(mode, f"https://example.invalid/minutes/{tid}")
    print(json.dumps({"data": {"title": f"Title of {tid}", "startTime": "2026-11-12T09:30:00+08:00", "endTime": "2026-11-12T10:30:00+08:00",
                               "creator": "Someone Private", "minutesUrl": url}}))
else:
    print(json.dumps({"error": "unexpected command"}))
    sys.exit(3)
