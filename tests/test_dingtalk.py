"""DingTalk read-only plugin against a fake dws (no network, no DingTalk, nothing of the user's).

    python tests/test_dingtalk.py

Covers: dws not configured / missing / not logged in / hanging / garbage output, the strict command whitelist (write
commands, extra flags, command injection), the environment handed to dws, and the legitimate read-only answer.
This is a mock: it proves the program's behaviour, not that the real dws prints the shape the reader expects."""
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME = tempfile.mkdtemp()
os.environ["JARVIS_HOME"] = HOME
os.environ["JARVIS_APP_DIR"] = tempfile.mkdtemp()
os.environ["JARVIS_LLM_API_KEY"] = "sk-must-not-reach-dws"
os.environ.pop("JARVIS_DWS_PATH", None)
sys.path.insert(0, str(ROOT))

from jarvis import config, tools                      # noqa: E402
from jarvis.plugins import dingtalk as dt             # noqa: E402

BIN = Path(tempfile.mkdtemp())
LOG = BIN / "calls.jsonl"
FAKE = BIN / "dws"
FAKE.write_text(f"#!/bin/sh\nexec {sys.executable} {ROOT / 'tests' / 'fake_dws.py'} \"$@\"\n")
FAKE.chmod(FAKE.stat().st_mode | stat.S_IXUSR)
os.environ["DWS_FAKE_LOG"] = str(LOG)

SENT = []


class Ctx:
    ui = staticmethod(SENT.append)


dt.bind(Ctx)


def configure(path=None, timeout=20):
    config.update(dingtalk={"dws_path": str(path if path is not None else FAKE), "timeout": timeout})


def calls():
    return [json.loads(x) for x in LOG.read_text().splitlines()] if LOG.exists() else []


def reset(mode="ok"):
    dt.bind(Ctx)                       # tools.collect() binds every plugin to its own hooks: take them back
    LOG.unlink(missing_ok=True)
    SENT.clear()
    os.environ["DWS_FAKE_MODE"] = mode


def expect_error(res, code):
    assert res["ok"] is False and res["error"] == code, res
    assert "instruction" in res and res["message"]
    return res


def main():
    # -- not configured: the tool is not even offered
    assert dt.provide() == ([], "")
    expect_error(dt.latest_minutes(), "not_configured")
    assert "dingtalk_latest_minutes" not in tools.names()

    # -- configured but wrong: missing, relative, not named dws, not executable, parent-dir tricks
    reset()
    configure(BIN / "nope" / "dws")
    expect_error(dt.latest_minutes(), "not_installed")
    for bad in ("dws", "./dws", str(BIN / "other"), str(BIN / ".." / BIN.name / "dws")):
        configure(bad)
        expect_error(dt.latest_minutes(), "not_installed")
    notexec = BIN / "plain" / "dws"
    notexec.parent.mkdir()
    notexec.write_text("#!/bin/sh\n")
    notexec.chmod(0o644)
    configure(notexec)
    expect_error(dt.latest_minutes(), "not_installed")
    assert not calls()

    # -- the legitimate read-only answer
    configure()
    assert dt.provide()[0][0][0]["function"]["name"] == "dingtalk_latest_minutes"
    assert "dingtalk_latest_minutes" in tools.names()
    assert tools.collect("我最新一条听记是什么")[3] == "dingtalk_latest_minutes"
    assert tools.collect("最近的会议纪要")[3] == "dingtalk_latest_minutes"
    assert tools.collect("set a timer for five minutes")[3] != "dingtalk_latest_minutes"
    reset()
    r = dt.latest_minutes()
    assert r["ok"] and r["title"] == "Title of new-task-0002" and r["scope"] == "mine", r
    assert r["time"] == "2026-11-12 01:30" or r["time"].startswith("2026-11-1"), r                  # the start time from `get info`
    assert r["link"] == "https://example.invalid/minutes/new-task-0002", r
    assert "Someone Private" not in json.dumps(r), "the creator is not passed on"
    c = calls()
    assert len(c) == 2, c
    assert c[0]["argv"] == ["minutes", "list", "mine", "--limit", "10", "--format", "json", "--timeout", "20"], c[0]
    assert c[1]["argv"] == ["minutes", "get", "info", "--id", "new-task-0002", "--format", "json", "--timeout", "20"], c[1]
    assert str(BIN) in c[0]["path"].split(os.pathsep)[0]                    # the node/dws directory comes first
    assert "JARVIS_LLM_API_KEY" not in c[0]["env"] and "DWS_FAKE_MODE" in c[0]["env"], "only an allow-list of the environment is passed on"
    assert SENT == [{"type": "dingtalk_minutes", "title": r["title"], "time": r["time"], "url": r["link"]}], SENT
    reset()
    assert dt.latest_minutes(scope="all")["ok"] and calls()[0]["argv"][2] == "all"
    reset()
    r = dt.latest_minutes(scope="mine; rm -rf /", limit=1000, extra="--yes")     # model-supplied junk can only pick a scope
    assert r["ok"] and calls()[0]["argv"][:5] == ["minutes", "list", "mine", "--limit", "10"], calls()[0]
    assert all("--yes" not in x["argv"] and "rm -rf" not in " ".join(x["argv"]) for x in calls())

    # -- no timestamps in the list: honest about it
    reset("notime")
    r = dt.latest_minutes()
    assert r["ok"] and "first entry" in r["note"] and calls()[1]["argv"][4] == "first-task-0001", (r, calls())

    # -- dws says it is not logged in / fails / hangs / prints garbage
    reset("auth")
    r = expect_error(dt.latest_minutes(), "not_authorized")
    assert "expired" not in json.dumps(r) and "login again" not in json.dumps(r), "raw dws text is not echoed"
    assert r["exit_code"] == 1 and len(calls()) == 1 and not SENT
    reset("json_error")
    expect_error(dt.latest_minutes(), "not_authorized")
    reset("fail")
    expect_error(dt.latest_minutes(), "failed")
    reset("notjson")
    expect_error(dt.latest_minutes(), "bad_output")
    reset("huge")
    expect_error(dt.latest_minutes(), "bad_output")
    reset("empty")
    expect_error(dt.latest_minutes(), "no_minutes")
    reset("shape")
    r = expect_error(dt.latest_minutes(), "bad_output")
    assert "SECRET-VALUE" not in json.dumps(r) and "rows" in json.dumps(r["shape"]), "key names only, never values"
    reset("sleep")
    import time
    t0 = time.time()
    try:
        dt._exec([str(FAKE), "minutes", "list", "mine", "--limit", "10", "--format", "json", "--timeout", "5"], 1)
        raise AssertionError("should have timed out")
    except dt.DwsError as e:
        assert e.code == "timeout" and time.time() - t0 < 10
    reset("evil_id")                                                       # an id from dws that is not a plain id is never passed on
    expect_error(dt.latest_minutes(), "bad_output")
    assert len(calls()) == 1 and not Path("/tmp/jarvis-pwned").exists()
    reset("badurl")
    r = dt.latest_minutes()
    assert r["ok"] and r["link"] == "not provided" and SENT[0]["url"] == "", r
    reset("credurl")
    assert dt.latest_minutes()["link"] == "not provided"

    # -- the whitelist itself: nothing but the two read-only shapes
    f = str(FAKE)
    good_tail = ["--format", "json", "--timeout", "20"]
    assert dt.check_argv([f, "minutes", "list", "mine", "--limit", "10", *good_tail]) == "list"
    assert dt.check_argv([f, "minutes", "get", "info", "--id", "abcd1234", *good_tail]) == "info"
    bad = [
        [f, "minutes", "update", "title", "--id", "abcd1234", *good_tail],
        [f, "minutes", "upload", "create", "--x", "1", *good_tail],
        [f, "minutes", "permission", "add", "--id", "abcd1234", *good_tail],
        [f, "minutes", "record", "start", "--x", "1", *good_tail],
        [f, "chat", "message", "send", "--text", "hi", *good_tail],
        [f, "oa", "approval", "approve", "--id", "abcd1234", *good_tail],
        [f, "doc", "delete", "--id", "abcd1234", "--yes", "x", "y"],
        [f, "minutes", "list", "shared", "--limit", "10", *good_tail],
        [f, "minutes", "list", "mine", "--limit", "1000", *good_tail],
        [f, "minutes", "list", "mine", "--limit", "10", "--format", "json", "--timeout", "0"],
        [f, "minutes", "list", "mine", "--limit", "10", "--format", "json", "--timeout", "999"],
        [f, "minutes", "list", "mine", "--limit", "10", "--yes", "--format", "json", "--timeout", "20"],
        [f, "minutes", "list", "mine", "--limit", "10", "--format", "json", "--timeout", "20", "--mock"],
        [f, "minutes", "list", "mine;id", "--limit", "10", *good_tail],
        [f, "minutes", "get", "info", "--id", "x; rm -rf /", *good_tail],
        [f, "minutes", "get", "info", "--id", "$(id)", *good_tail],
        [f, "minutes", "get", "info", "--id", "`id`", *good_tail],
        [f, "minutes", "get", "info", "--id", "--help", *good_tail],
        [f, "minutes", "get", "info", "--id", "../../etc/passwd", *good_tail],
        [f, "minutes", "get", "info", "--id", "abc\nrm", *good_tail],
        [f, "minutes", "get", "info", "--id", "ab", *good_tail],
        [f, "minutes", "get", "transcription", "--id", "abcd1234", *good_tail],
        [f, "minutes", "get", "info", "--id", "abcd1234", "--format", "json", "--timeout", "20", "--dry-run"],
        ["sh", "-c", "id", "a", "b", "c", "d", "e", "f", "g"],
        ["dws", "minutes", "list", "mine", "--limit", "10", *good_tail],
        ["/bin/rm", "minutes", "list", "mine", "--limit", "10", *good_tail],
        [f, "minutes", "list"], [], [f], "minutes list mine", None,
    ]
    for argv in bad:
        try:
            dt.check_argv(argv)
        except dt.DwsError as e:
            assert e.code == "rejected", (argv, e.code)
        else:
            raise AssertionError(f"whitelist let this through: {argv}")
        reset()
        try:
            dt._exec(argv, 5)
            raise AssertionError(f"_exec ran {argv}")
        except dt.DwsError:
            pass
        assert not calls(), f"dws was launched for {argv}"

    print("ok: dingtalk read-only plugin")


if __name__ == "__main__":
    main()
