"""Real weather for the HUD and for "what's the weather": from Open-Meteo, a public weather API with documented endpoints
that needs no account and no key (https://open-meteo.com, free for non-commercial use).

Where "here" is, in this order of honesty:
  - mode "device" (default): the Mac's own Location Services, after the user's permission (platforms.locate).
  - mode "city": a city the user picked in the weather panel (found with Open-Meteo's geocoding search).
  - otherwise nothing: the panel says the weather is not available. The position is never guessed from the IP address or
    the time zone, and no demo numbers are ever shown.
Every answer says where it came from (device / chosen city), the source, and when the data was observed. Data older than
three hours, or a failed update with nothing recent to fall back on, shows as not available.

Settings (~/.jarvis/config.json):  "weather": {"mode": "device" | "city", "city": {"name", "admin1", "country", "lat", "lon"}}
Switch the plugin off with "plugins": {"weather": false}. Device coordinates stay in memory: they are neither saved nor logged."""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from .. import config, platforms

FORECAST = "https://api.open-meteo.com/v1/forecast"
AIR = "https://air-quality-api.open-meteo.com/v1/air-quality"
GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
SOURCE = "Open-Meteo"
UA = "Jarvis/2.0 (weather; personal use)"
MAX_AGE = 3 * 3600          # observations older than this are not shown as the weather
FRESH = 10 * 60             # a fetch this recent is reused
RELOCATE = 3600             # the device position is asked again after this long
RETRY_TRANSIENT = 900       # after a transient location failure, an automatic refresh waits this long
TRANSIENT = ("unavailable", "helper_failed")

WMO = {0: "晴", 1: "晴间多云", 2: "多云", 3: "阴", 45: "雾", 48: "雾凇", 51: "毛毛雨", 53: "毛毛雨", 55: "毛毛雨", 56: "冻雨", 57: "冻雨",
       61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "冻雨", 71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒", 80: "小阵雨",
       81: "阵雨", 82: "强阵雨", 85: "阵雪", 86: "强阵雪", 95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "雷阵雨伴冰雹"}
WIND8 = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]
REASONS = {
    "network": "天气服务暂时连不上，也没有足够新的旧数据可以显示。",
    "service_error": "天气服务返回了无法识别的数据。",
    "stale": "最近一次天气数据已经过期（超过 3 小时）。",
    "no_city": "还没有选择城市。",
    "pending": "正在等待系统的定位授权…",
    "disabled": "天气功能已在设置里关闭。",
}

CTX = None
_lock = threading.RLock()
_S = {"loc": None, "loc_err": None, "thread": None, "wx": None, "found": []}


def bind(ctx):
    global CTX
    CTX = ctx


def log(msg):
    print(f"weather: {msg}", flush=True)


# -- network ---------------------------------------------------------------
def _http_json(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.URLError as e:
        if "CERTIFICATE" not in str(e).upper():
            raise
        import ssl, certifi
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context(cafile=certifi.where())) as r:
            return json.load(r)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v


def _epoch(local_time, offset):
    """Open-Meteo time 'YYYY-MM-DDTHH:MM' in the place's own zone + its UTC offset -> unix seconds."""
    dt = datetime.strptime(str(local_time)[:16], "%Y-%m-%dT%H:%M")
    return (dt - timedelta(seconds=offset)).replace(tzinfo=timezone.utc).timestamp()


def aqi_label(v):
    return "优" if v <= 50 else "良" if v <= 100 else "轻度污染" if v <= 150 else "中度污染" if v <= 200 else "重度污染" if v <= 300 else "严重污染"


def _wind(speed, deg):
    return f"{WIND8[int(((deg % 360) + 22.5) // 45) % 8]}风 {speed:.1f} m/s"


def fetch_weather(lat, lon, now=None):
    """Current conditions + 3-day outlook for a place -> dict. Raises ValueError for a response that cannot be trusted."""
    now = now if now is not None else time.time()
    q = urllib.parse.urlencode({
        "latitude": f"{lat:.2f}", "longitude": f"{lon:.2f}", "timezone": "auto", "forecast_days": 3, "wind_speed_unit": "ms",
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,wind_direction_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min"})
    d = _http_json(f"{FORECAST}?{q}")
    cur, offset = d.get("current"), d.get("utc_offset_seconds")
    if not isinstance(cur, dict) or not _num(cur.get("temperature_2m")) or not _num(offset) or not isinstance(cur.get("time"), str):
        raise ValueError("unexpected forecast response")
    observed = _epoch(cur["time"], offset)
    if observed > now + 3600:
        raise ValueError("observation is in the future")
    days = []
    daily = d.get("daily") or {}
    for i, day in enumerate(daily.get("time") or []):
        try:
            hi, lo = daily["temperature_2m_max"][i], daily["temperature_2m_min"][i]
            if not (_num(hi) and _num(lo)):
                continue
            wd = datetime.strptime(day, "%Y-%m-%d").date()
            code = daily["weather_code"][i]
            days.append({"date": day, "week": (wd.weekday() + 1) % 7, "day": WMO.get(code, "天气未知") if _num(code) else "天气未知",
                         "high": round(hi), "low": round(lo)})
        except (KeyError, IndexError, ValueError, TypeError):
            continue
    code = cur.get("weather_code")
    out = {"temp": round(cur["temperature_2m"]), "now": WMO.get(code, "天气未知") if _num(code) else "天气未知",
           "observed_epoch": observed, "observed_hm": cur["time"][11:16], "days": days}
    if _num(cur.get("apparent_temperature")):
        out["feels"] = round(cur["apparent_temperature"])
    if _num(cur.get("relative_humidity_2m")):
        out["humidity"] = round(cur["relative_humidity_2m"])
    if _num(cur.get("wind_speed_10m")) and _num(cur.get("wind_direction_10m")):
        out["wind"] = _wind(cur["wind_speed_10m"], cur["wind_direction_10m"])
    out["aqi"] = fetch_aqi(lat, lon, now)
    return out


def fetch_aqi(lat, lon, now):
    """US AQI from Open-Meteo's air-quality model (CAMS), or None: the panel then says 'AQI 不可用'."""
    try:
        q = urllib.parse.urlencode({"latitude": f"{lat:.2f}", "longitude": f"{lon:.2f}", "timezone": "auto", "current": "us_aqi"})
        d = _http_json(f"{AIR}?{q}")
        cur, offset = d["current"], d["utc_offset_seconds"]
        v = cur["us_aqi"]
        if not _num(v) or not _num(offset) or now - _epoch(cur["time"], offset) > MAX_AGE:
            return None
        return {"value": round(v), "scale": "US AQI", "label": aqi_label(v)}
    except Exception as e:
        log(f"air quality unavailable: {type(e).__name__}")
        return None


def search_city(q, count=6):
    """Open-Meteo geocoding -> [{name, admin1, country, lat, lon, label}]."""
    q = (q or "").strip()[:60]
    if not q:
        return []
    r = _http_json(f"{GEOCODE}?" + urllib.parse.urlencode({"name": q, "count": count, "language": "zh", "format": "json"}))
    items = []
    for x in (r.get("results") or [])[:count]:
        try:
            lat, lon = float(x["latitude"]), float(x["longitude"])
            name = str(x["name"])[:60]
        except (KeyError, TypeError, ValueError):
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        admin, country = str(x.get("admin1") or "")[:60], str(x.get("country") or "")[:60]
        parts = []
        for p in (name, admin, country):
            if p and p not in parts:
                parts.append(p)
        items.append({"name": name, "admin1": admin, "country": country, "lat": lat, "lon": lon, "label": " · ".join(parts)})
    return items


# -- where is "here" --------------------------------------------------------
def settings():
    c = config.section("weather")
    return c.get("mode") if c.get("mode") in ("device", "city") else "device", c.get("city") if isinstance(c.get("city"), dict) else None


def _city_loc(city):
    try:
        lat, lon = float(city["lat"]), float(city["lon"])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError
        label = " · ".join(dict.fromkeys(p for p in (city.get("name"), city.get("admin1")) if p)) or "已选城市"
        return {"ok": True, "lat": lat, "lon": lon, "label": label, "source": "city"}
    except (KeyError, TypeError, ValueError):
        return None


def _do_locate():
    res = platforms.locate()
    res = {**res, "at": time.time()}
    with _lock:
        if res.get("ok"):
            _S["loc"], _S["loc_err"] = res, None
        else:
            _S["loc"], _S["loc_err"] = None, res        # a failed fix means the old position can no longer be trusted


def _device(relocate=False, wait=100):
    now = time.time()
    with _lock:
        if relocate:
            _S["loc_err"] = None
        else:
            loc, err = _S["loc"], _S["loc_err"]
            if loc and now - loc["at"] < RELOCATE:
                return loc
            if err and (err.get("reason") not in TRANSIENT or now - err["at"] < RETRY_TRANSIENT):
                return err
        th = _S["thread"]
        if th is None or not th.is_alive():
            th = _S["thread"] = threading.Thread(target=_do_locate, daemon=True)
            th.start()
    th.join(wait)
    with _lock:
        if th.is_alive():
            return {"ok": False, "reason": "pending", "message": REASONS["pending"]}
        return _S["loc"] or _S["loc_err"] or {"ok": False, "reason": "unavailable", "message": "暂时拿不到设备位置。"}


def position(wait=20):
    """{lat, lon, name} for other features (the 地球此刻 wall), or None. Same honesty rules as the weather."""
    mode, city = settings()
    loc = _city_loc(city) if mode == "city" and city else _device(wait=wait) if mode == "device" else None
    if loc and loc.get("ok"):
        return {"lat": loc["lat"], "lon": loc["lon"], "name": loc.get("label") or loc.get("name") or "当前位置"}
    return None


# -- the answer -------------------------------------------------------------
def unavailable(reason, message="", mode=None, detail=""):
    mode = mode or settings()[0]
    return {"type": "weather", "status": "unavailable", "reason": reason, "message": message or REASONS.get(reason, "天气暂时不可用。"),
            "mode": mode, "source": SOURCE, "can_choose_city": True, **({"detail": detail} if detail else {})}


def _weather_for(lat, lon, label, loc_source, mode, force=False, now=None):
    now = now if now is not None else time.time()
    key = (round(lat, 2), round(lon, 2), loc_source)
    with _lock:
        last = _S["wx"]
    if last and last["key"] == key and not force and now - last["fetched"] < FRESH and now - last["msg"]["observed_epoch"] <= MAX_AGE:
        return dict(last["msg"])
    try:
        w = fetch_weather(lat, lon, now)
    except Exception as e:
        log(f"update failed: {type(e).__name__}")
        if last and last["key"] == key and now - last["msg"]["observed_epoch"] <= MAX_AGE:
            return {**last["msg"], "stale": True, "stale_reason": "network"}
        return unavailable("service_error" if isinstance(e, ValueError) else "network", mode=mode)
    if now - w["observed_epoch"] > MAX_AGE:
        return unavailable("stale", mode=mode)
    msg = {"type": "weather", "status": "ok", "mode": mode, "city": label, "location_source": loc_source, "source": SOURCE,
           "stale": False, "fetched_epoch": now, "fetched_hm": time.strftime("%H:%M", time.localtime(now)), **w}
    with _lock:
        _S["wx"] = {"key": key, "fetched": now, "msg": msg}
    return dict(msg)


def current(force=False, relocate=False, wait=100):
    """The HUD's weather message for where the user is: status 'ok' with data, or 'unavailable' with the reason."""
    mode, city = settings()
    if mode == "city":
        loc = _city_loc(city) if city else None
        if not loc:
            return unavailable("no_city", mode=mode)
        return _weather_for(loc["lat"], loc["lon"], loc["label"], "city", mode, force)
    loc = _device(relocate, wait)
    if not loc.get("ok"):
        return unavailable(loc.get("reason", "unavailable"), loc.get("message", ""), mode, loc.get("detail", ""))
    m = _weather_for(loc["lat"], loc["lon"], loc.get("name") or "当前位置", "device", mode, force)
    if m.get("status") == "ok":                       # when and how well the Mac located itself (not the coordinates)
        m["located_epoch"], m["located_hm"] = loc["at"], time.strftime("%H:%M", time.localtime(loc["at"]))
        if isinstance(loc.get("accuracy"), (int, float)) and loc["accuracy"] >= 0:
            m["accuracy_m"] = round(loc["accuracy"])
    return m


def push(force=False, relocate=False):
    ui = getattr(CTX, "ui", None)
    if ui is None:
        return
    mode, _ = settings()
    if mode == "device" and (relocate or not (_S["loc"] and time.time() - _S["loc"]["at"] < RELOCATE)):
        ui({"type": "weather", "status": "pending", "mode": mode, "message": "正在获取设备位置…", "source": SOURCE})
    ui(current(force, relocate))


def msg(m):
    """Messages from the weather panel: weather_refresh, weather_device, weather_search, weather_pick."""
    ui = getattr(CTX, "ui", None)
    t = m.get("type")
    if t == "weather_refresh":
        push(force=True)
    elif t == "weather_device":
        _, city = settings()
        config.update(weather={"mode": "device", **({"city": city} if city else {})})
        with _lock:
            _S["wx"] = None
        push(force=True, relocate=True)
    elif t == "weather_search":
        q = str(m.get("q", ""))[:60]
        try:
            items = search_city(q)
            err = "" if items else "没有找到这个城市，换个写法试试（中文或英文名都可以）。"
        except Exception as e:
            log(f"city search failed: {type(e).__name__}")
            items, err = [], "城市搜索暂时连不上。"
        with _lock:
            _S["found"] = items
        if ui:
            ui({"type": "weather_cities", "q": q, "items": [{k: v for k, v in x.items() if k in ("name", "label")} for x in items], "error": err})
    elif t == "weather_pick":
        with _lock:
            found = list(_S["found"])
        try:
            c = found[int(m.get("i"))]
        except (IndexError, TypeError, ValueError):
            return
        config.update(weather={"mode": "city", "city": {k: c[k] for k in ("name", "admin1", "country", "lat", "lon")}})
        with _lock:
            _S["wx"] = None
        push(force=True)


# -- the voice tool ----------------------------------------------------------
def _age_minutes(w, now=None):
    return max(0, round(((now if now is not None else time.time()) - w["observed_epoch"]) / 60))


def tool_result(w, where):
    if w.get("status") != "ok":
        return {"ok": False, "error": w.get("reason", "unavailable"), "message": w.get("message", ""),
                "instruction": "Tell the user the weather is not available and why; offer to use a city they name. Do not guess or make up weather."}
    return {"ok": True, "location": w["city"], "location_source": where, "source": SOURCE, "observed_local_time": w["observed_hm"],
            "age_minutes": _age_minutes(w), "stale": bool(w.get("stale")), "temp_c": w["temp"], "feels_like_c": w.get("feels"),
            "condition": w["now"], "humidity_pct": w.get("humidity"), "wind": w.get("wind"),
            "air_quality": ({"us_aqi": w["aqi"]["value"], "level": w["aqi"]["label"]} if w.get("aqi") else "not available"),
            "next_days": [{"date": d["date"], "condition": d["day"], "high_c": d["high"], "low_c": d["low"]} for d in w["days"]]}


def get_weather(city=""):
    """The weather where the user is, or in a city they name."""
    city = (city or "").strip()
    if city:
        try:
            found = search_city(city, 1)
        except Exception as e:
            log(f"city search failed: {type(e).__name__}")
            return {"ok": False, "error": "network", "message": "城市搜索暂时连不上。"}
        if not found:
            return {"ok": False, "error": "no_such_city", "message": f"没有找到城市 {city[:30]}。"}
        c = found[0]
        w = _weather_for(c["lat"], c["lon"], c["label"], "named", "named")
        return tool_result(w, "the city the user asked about")
    w = current(wait=25)
    return tool_result(w, "this Mac's location" if w.get("location_source") == "device" else "a city the user picked by hand (NOT this Mac's location)")


TOOL = {"type": "function", "function": {
    "name": "get_weather",
    "description": "Real weather now and the next 3 days from Open-Meteo, with air quality when known. Without a city: where the user "
                   "is (device location they allowed, or the city they chose). If it says not available, say so; never invent weather.",
    "parameters": {"type": "object", "properties": {"city": {"type": "string", "description": "optional city name, in any language"}}}}}

HINT = (" For weather use get_weather. Say where the data is for and when it was observed if the user asks how fresh it is. If "
        "get_weather reports that the weather is not available, say that plainly and do not make up numbers.")


def provide():
    return [(TOOL, get_weather)], HINT
