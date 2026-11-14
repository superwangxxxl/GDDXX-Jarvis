"""Weather: real data or "not available", never demo numbers, never a position guessed from the network.
All network and location calls are replaced by fakes, so this runs anywhere (and says nothing about macOS itself:
the Core Location permission flow needs a real Mac, see docs/weather-dingtalk.md).

    python tests/test_weather.py
"""
import io
import json
import os
import sys
import tempfile
import threading
import time
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["JARVIS_HOME"] = tempfile.mkdtemp()
os.environ["JARVIS_APP_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, str(ROOT))

from jarvis import config, platforms, tools                      # noqa: E402
from jarvis.platforms import maclocate, generic                  # noqa: E402
from jarvis.plugins import weather as wx, amap                   # noqa: E402

DEVICE = (12.3456, 65.4321)                 # made-up coordinates: nothing real in this repository
CITY = (-33.0001, 44.0002)
NET = {"fail": False, "calls": [], "now_shift": timedelta(0), "air": True, "bad": False, "future": False}
LOC = {"res": {"ok": True, "lat": DEVICE[0], "lon": DEVICE[1], "name": "Testville", "at": 0}, "calls": 0, "delay": None}
SENT = []


class Ctx:
    ui = staticmethod(SENT.append)


def fake_locate(**kw):
    LOC["calls"] += 1
    if LOC["delay"]:
        LOC["delay"].wait(5)
    return dict(LOC["res"])


def hm(dt):
    return dt.strftime("%Y-%m-%dT%H:%M")


def fake_http(url, timeout=10):
    NET["calls"].append(url)
    if NET["fail"]:
        raise OSError("network down")
    if "geocoding" in url:
        if "Nowhere" in url:
            return {}
        return {"results": [{"name": "Cityname", "admin1": "Region", "country": "Land", "latitude": CITY[0], "longitude": CITY[1]},
                            {"name": "Cityname", "admin1": "Region", "country": "Land", "latitude": 999, "longitude": 1},
                            {"name": "Other", "latitude": "x", "longitude": 1}]}
    off = 8 * 3600
    local_now = datetime.now(timezone.utc) + timedelta(seconds=off) + NET["now_shift"]
    if "air-quality" in url:
        if not NET["air"]:
            raise OSError("air down")
        return {"utc_offset_seconds": off, "current": {"time": hm(local_now), "us_aqi": 41.2}}
    if NET["bad"]:
        return {"current": {"temperature_2m": "warm"}}
    if NET["future"]:
        local_now += timedelta(hours=9)
    day = local_now.date()
    return {"utc_offset_seconds": off,
            "current": {"time": hm(local_now), "temperature_2m": 17.6, "relative_humidity_2m": 63, "apparent_temperature": 16.2,
                        "weather_code": 3, "wind_speed_10m": 3.24, "wind_direction_10m": 45},
            "daily": {"time": [str(day + timedelta(days=i)) for i in range(3)], "weather_code": [3, 61, 0],
                      "temperature_2m_max": [20.4, 18.0, 23.9], "temperature_2m_min": [11.2, 12.0, 10.4]}}


wx._http_json = fake_http
wx.platforms.locate = fake_locate


def fresh(mode="device", city=None):
    NET.update(fail=False, now_shift=timedelta(0), air=True, bad=False, future=False)
    NET["calls"].clear()
    SENT.clear()
    LOC.update(calls=0, delay=None, res={"ok": True, "lat": DEVICE[0], "lon": DEVICE[1], "name": "Testville", "at": 0})
    with wx._lock:
        wx._S.update(loc=None, loc_err=None, thread=None, wx=None, found=[])
    config.update(weather={"mode": mode, **({"city": city} if city else {})})
    wx.bind(Ctx)


def no_coords(obj):
    s = json.dumps(obj, ensure_ascii=False)
    for v in (*DEVICE, *CITY):
        assert str(v) not in s and f"{v:.2f}" not in s, f"coordinates leaked: {s[:200]}"


def main():
    # -- device location -> real data with its source and time
    fresh()
    out = io.StringIO()
    with redirect_stdout(out):
        m = wx.current()
    assert m["status"] == "ok" and m["location_source"] == "device" and m["source"] == "Open-Meteo", m
    assert m["city"] == "Testville" and m["temp"] == 18 and m["now"] == "阴" and m["humidity"] == 63 and m["feels"] == 16, m
    assert m["wind"] == "东北风 3.2 m/s" and m["aqi"] == {"value": 41, "scale": "US AQI", "label": "优"}, m
    assert [d["high"] for d in m["days"]] == [20, 18, 24] and m["days"][1]["day"] == "小雨" and m["days"][0]["low"] == 11, m["days"]
    assert abs(m["observed_epoch"] - time.time()) < 120 and len(m["observed_hm"]) == 5 and m["fetched_hm"], m
    assert LOC["calls"] == 1
    forecast = [u for u in NET["calls"] if "api.open-meteo.com/v1/forecast" in u][0]
    assert "latitude=12.35" in forecast and "longitude=65.43" in forecast, "coordinates are rounded to ~1 km before leaving the Mac"
    assert out.getvalue() == "" or "12.3" not in out.getvalue()

    # -- the position is reused for an hour; a manual relocate asks again; a failed relocate drops the old position
    n = len(NET["calls"])
    assert wx.current()["status"] == "ok" and LOC["calls"] == 1 and len(NET["calls"]) == n, "fresh result is reused"
    wx.current(force=True)
    assert LOC["calls"] == 1 and len(NET["calls"]) > n
    wx.current(relocate=True)
    assert LOC["calls"] == 2
    LOC["res"] = {"ok": False, "reason": "denied", "message": "定位权限被拒绝。"}
    m = wx.current(relocate=True)
    assert m["status"] == "unavailable" and m["reason"] == "denied" and m["can_choose_city"], m
    assert wx.current()["reason"] == "denied" and LOC["calls"] == 3, "no automatic retry after a denial"

    # -- permission denied / restricted / services off / unsupported: not available, nothing fetched, nothing guessed
    for reason in ("denied", "restricted", "services_off", "timeout", "no_toolchain", "build_failed", "helper_failed"):
        fresh()
        LOC["res"] = {"ok": False, "reason": reason, "message": "m-" + reason}
        m = wx.current()
        assert m["status"] == "unavailable" and m["reason"] == reason and m["message"] == "m-" + reason and m["can_choose_city"], m
        assert not NET["calls"], "no weather request without a real position (no IP, no time zone fallback)"
        t = wx.get_weather()
        assert t["ok"] is False and t["error"] == reason and "Do not guess" in t["instruction"], t
    fresh()
    wx.platforms.locate = generic.locate                      # Windows/Linux today
    m = wx.current()
    wx.platforms.locate = fake_locate
    assert m["status"] == "unavailable" and m["reason"] == "unsupported" and not NET["calls"], m

    # -- transient failures are retried later, not hammered
    fresh()
    LOC["res"] = {"ok": False, "reason": "unavailable", "message": "x"}
    wx.current()
    wx.current()
    assert LOC["calls"] == 1
    wx._S["loc_err"]["at"] -= wx.RETRY_TRANSIENT + 1
    wx.current()
    assert LOC["calls"] == 2

    # -- waiting for the permission prompt: the voice tool does not hang
    fresh()
    LOC["delay"] = threading.Event()
    t0 = time.time()
    m = wx.current(wait=0.3)
    assert m["status"] == "unavailable" and m["reason"] == "pending" and time.time() - t0 < 3, m
    LOC["delay"].set()
    time.sleep(0.3)
    assert wx.current()["status"] == "ok", "the answer arrives once the user has decided"

    # -- network failures, freshness
    fresh()
    NET["fail"] = True
    m = wx.current()
    assert m["status"] == "unavailable" and m["reason"] == "network", m
    fresh()
    wx.current()
    NET["fail"] = True
    m = wx.current(force=True)
    assert m["status"] == "ok" and m["stale"] is True and m["stale_reason"] == "network", "a recent result is kept, and marked"
    wx._S["wx"]["msg"]["observed_epoch"] -= wx.MAX_AGE + 60
    m = wx.current(force=True)
    assert m["status"] == "unavailable" and m["reason"] == "network", "old data is not shown as current"
    fresh()
    NET["now_shift"] = -timedelta(hours=4)                    # the service answers with a 4-hour-old observation
    m = wx.current()
    assert m["status"] == "unavailable" and m["reason"] == "stale", m
    fresh()
    NET["future"] = True
    assert wx.current()["reason"] == "service_error"
    fresh()
    NET["bad"] = True
    assert wx.current()["reason"] == "service_error"
    fresh()
    NET["air"] = False
    m = wx.current()
    assert m["status"] == "ok" and m["aqi"] is None, "no air-quality data is shown as missing, not made up"

    # -- chosen city
    fresh("city")
    assert wx.current()["reason"] == "no_city" and not NET["calls"] and LOC["calls"] == 0
    fresh()
    wx.msg({"type": "weather_search", "q": "Cityname"})
    found = [s for s in SENT if s["type"] == "weather_cities"][-1]
    assert [c["label"] for c in found["items"]] == ["Cityname · Region · Land"] and not found["error"], found
    no_coords(found)
    wx.msg({"type": "weather_search", "q": "Nowhere"})
    assert [s for s in SENT if s["type"] == "weather_cities"][-1]["error"]
    NET["fail"] = True
    wx.msg({"type": "weather_search", "q": "Cityname"})
    assert "连不上" in [s for s in SENT if s["type"] == "weather_cities"][-1]["error"]
    NET["fail"] = False
    wx.msg({"type": "weather_search", "q": "Cityname"})
    SENT.clear()
    wx.msg({"type": "weather_pick", "i": 5})                  # out of range: ignored
    wx.msg({"type": "weather_pick", "i": "x"})
    assert not SENT and config.section("weather")["mode"] == "device"
    wx.msg({"type": "weather_pick", "i": 0})
    saved = config.section("weather")
    assert saved["mode"] == "city" and saved["city"]["name"] == "Cityname" and saved["city"]["lat"] == CITY[0], saved
    m = [s for s in SENT if s["type"] == "weather"][-1]
    assert m["status"] == "ok" and m["location_source"] == "city" and m["city"] == "Cityname · Region" and LOC["calls"] == 0, m
    assert "latitude=-33.00" in [u for u in NET["calls"] if "/v1/forecast" in u][-1]
    SENT.clear()
    LOC["res"] = {"ok": True, "lat": DEVICE[0], "lon": DEVICE[1], "name": "Testville", "at": 0}
    wx.msg({"type": "weather_device"})
    assert config.section("weather")["mode"] == "device" and config.section("weather")["city"]["name"] == "Cityname"
    assert [s["status"] for s in SENT if s["type"] == "weather"] == ["pending", "ok"], SENT
    assert SENT[-1]["location_source"] == "device" and SENT[-1]["city"] == "Testville"
    config.update(weather={"mode": "city", "city": {"name": "Bad", "lat": "x", "lon": 5}})
    assert wx.current()["reason"] == "no_city", "a damaged saved city is not used"

    # -- the voice tool
    fresh()
    t = wx.get_weather()
    assert t["ok"] and t["temp_c"] == 18 and t["location"] == "Testville" and t["source"] == "Open-Meteo" and t["air_quality"]["us_aqi"] == 41, t
    assert t["location_source"] == "this Mac's location" and t["age_minutes"] <= 2 and len(t["next_days"]) == 3
    no_coords(t)
    t = wx.get_weather("Cityname")
    assert t["ok"] and t["location"] == "Cityname · Region · Land" and t["location_source"] == "the city the user asked about", t
    assert wx.get_weather("Nowhere")["error"] == "no_such_city"
    NET["fail"] = True
    assert wx.get_weather("Cityname")["error"] == "network"
    schemas, run, hints, force = tools.collect("what's the weather")
    assert "get_weather" in run and run["get_weather"] is wx.get_weather and "make up numbers" in hints, (sorted(run), hints[-200:])
    assert sum(1 for s in schemas if s["function"]["name"] == "get_weather") == 1, "AMap no longer offers a second get_weather"

    # -- the HUD message carries what the panel must show
    fresh()
    wx.push()
    assert [s["status"] for s in SENT] == ["pending", "ok"]
    for k in ("source", "location_source", "observed_hm", "observed_epoch", "fetched_hm", "city", "temp", "now", "days", "aqi", "stale"):
        assert k in SENT[-1], k
    no_coords(SENT)

    # -- the plugin can be switched off (rollback)
    config.update(plugins={"weather": False})
    assert tools.plugin("weather") is None and "get_weather" not in tools.names()
    config.update(plugins=None)

    # -- AMap no longer turns the IP address into a position
    assert amap.here() == {"city": "", "adcode": "", "location": "", "from": ""}
    amap.get = lambda *a, **k: (_ for _ in ()).throw(AssertionError("AMap must not be called to guess a position"))
    try:
        amap.route("somewhere")
        raise AssertionError("route without a start point must fail")
    except RuntimeError as e:
        assert "no start point" in str(e)
    assert not hasattr(amap, "weather")

    # -- macOS location client (the helper app is faked: this checks the Python side, not Core Location)
    check_maclocate()
    print("ok: weather")


def check_maclocate():
    import subprocess
    app = Path(tempfile.mkdtemp()) / "JarvisLocate.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    os.environ["JARVIS_LOCATE_HELPER"] = str(app)
    real_run = subprocess.run
    seen = []

    def make(payload, rc=0, raise_timeout=False):
        def run(cmd, **kw):
            seen.append(cmd)
            if raise_timeout:
                raise subprocess.TimeoutExpired(cmd, 1)
            out = cmd[cmd.index("--out") + 1]
            if payload is not None:
                Path(out).write_text(payload if isinstance(payload, str) else json.dumps(payload))
            return subprocess.CompletedProcess(cmd, rc, "", "")
        return run

    subprocess.run = make({"ok": True, "lat": 12.5, "lon": 65.5, "accuracy": 65.0, "name": "Testville"})
    r = maclocate.locate()
    assert r["ok"] and r["lat"] == 12.5 and r["name"] == "Testville", r
    cmd = seen[-1]
    assert cmd[:6] == ["/usr/bin/open", "-W", "-n", "-a", str(app), "--args"] and "--timeout" in cmd, cmd
    assert not list((Path(os.environ["JARVIS_HOME"]) / "run").glob("locate-*.json")), "the result file is removed after reading"
    for reason in ("denied", "restricted", "services_off", "timeout", "unavailable"):
        subprocess.run = make({"ok": False, "reason": reason})
        r = maclocate.locate()
        assert r == {"ok": False, "reason": reason, "message": maclocate.MESSAGES[reason]}, r
    for payload in ({"ok": True, "lat": 0, "lon": 0}, {"ok": True, "lat": 91, "lon": 5}, {"ok": True, "lat": "x", "lon": 5}, {"ok": True},
                    {"ok": False, "reason": "weird"}):
        subprocess.run = make(payload)
        r = maclocate.locate()
        assert r["ok"] is False and r["reason"] == "unavailable", (payload, r)
    subprocess.run = make(None)
    assert maclocate.locate()["reason"] == "helper_failed"
    subprocess.run = make("not json")
    assert maclocate.locate()["reason"] == "helper_failed"
    subprocess.run = make(None, raise_timeout=True)
    assert maclocate.locate()["reason"] == "timeout"

    # no helper and no developer tools: say so, and never call swiftc (macOS would open an installer dialog)
    os.environ.pop("JARVIS_LOCATE_HELPER")
    calls = []

    def build_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1 if cmd[0].endswith("xcode-select") else 0, "", "")
    subprocess.run = build_run
    r = maclocate.locate()
    assert r["reason"] == "no_toolchain" and [c[0] for c in calls] == ["/usr/bin/xcode-select"], (r, calls)
    # the build path: tools present -> compiled once (here by a fake swiftc), stamped, moved into place, reused on the next call
    target = Path(tempfile.mkdtemp()) / "Support"
    maclocate.paths.APP = target
    calls.clear()

    def ok_run(cmd, **kw):
        calls.append(cmd)
        if cmd[1] == "swiftc":
            Path(cmd[cmd.index("-o") + 1]).write_text("#!/bin/sh\n")
            os.chmod(cmd[cmd.index("-o") + 1], 0o755)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    subprocess.run = ok_run
    app, why = maclocate.ensure_helper()
    assert why is None and app == target / "JarvisLocate.app" and (app / "Contents" / "Info.plist").exists(), (app, why)
    assert [c[0] for c in calls] == ["/usr/bin/xcode-select", "/usr/bin/xcrun", "/usr/bin/codesign"] and "-swift-version" in calls[1], calls
    n = len(calls)
    assert maclocate.ensure_helper()[0] == app and len(calls) == n, "a built helper is reused without compiling again"
    maclocate._source_hash = lambda: "changed"
    assert maclocate.ensure_helper()[0] == app and len(calls) > n, "a changed helper source is rebuilt"
    subprocess.run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1 if cmd[1] == "swiftc" else 0, "boom", "")
    shutil_rm = __import__("shutil").rmtree
    shutil_rm(target, ignore_errors=True)
    maclocate._source_hash = lambda: "again"
    assert maclocate.ensure_helper() == (None, maclocate.fail("build_failed")), "a failed compile is reported, not hidden"
    subprocess.run = real_run
    assert (maclocate.SRC / "main.swift").exists() and (maclocate.SRC / "Info.plist").exists()
    plist = (maclocate.SRC / "Info.plist").read_text(encoding="utf-8")
    assert "NSLocationWhenInUseUsageDescription" in plist and "LSUIElement" in plist
    assert "Always" not in plist, "the helper only ever asks for when-in-use access"
    swift = (maclocate.SRC / "main.swift").read_text(encoding="utf-8")
    assert "requestWhenInUseAuthorization" in swift and "requestLocation" in swift
    assert "requestAlwaysAuthorization" not in swift and "startMonitoring" not in swift and "allowsBackgroundLocationUpdates" not in swift
    for banned in ("ipinfo", "ip-api", "URLSession", "TimeZone"):
        assert banned not in swift, banned


if __name__ == "__main__":
    main()
