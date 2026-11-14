"""macOS device location, through Apple's Core Location and the user's own Location Services permission.

macOS grants location access per app (bundle id + the usage text in its Info.plist). The Python that ships inside Jarvis
is a bare interpreter without either, so asking Core Location from it directly is not reliable. Instead a tiny helper
app (maclocate_helper/main.swift, a few dozen lines) is compiled once with the Xcode command line tools into
~/Library/Application Support/Jarvis/JarvisLocate.app, signed ad hoc, and launched through `open` for each fix. macOS
then shows its normal "JarvisLocate would like to use your current location" prompt, and the choice appears in
System Settings > Privacy & Security > Location Services.

locate() -> {"ok": True, "lat", "lon", "accuracy", "name"?}   or   {"ok": False, "reason", "message"}
reasons: denied, restricted, services_off, timeout, unavailable, no_toolchain, build_failed, helper_failed

No IP address or time zone is ever turned into a position here: without a fix the answer is "not available"."""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .. import paths

NAME = "JarvisLocate.app"
SRC = Path(__file__).with_name("maclocate_helper")
_build_lock = threading.Lock()

MESSAGES = {
    "denied": "定位权限被拒绝。可以在「系统设置 → 隐私与安全性 → 定位服务」里允许 Jarvis Location，或者直接选择一个城市。",
    "restricted": "这台 Mac 限制了定位服务。请选择一个城市。",
    "services_off": "系统的定位服务是关闭的（系统设置 → 隐私与安全性 → 定位服务）。可以打开它，或者选择一个城市。",
    "timeout": "等待定位授权超时。可以再试一次，或者选择一个城市。",
    "unavailable": "暂时拿不到设备位置。可以再试一次，或者选择一个城市。",
    "no_toolchain": "定位组件需要 Xcode 命令行工具来编译一次（终端运行 xcode-select --install）。现在可以先选择一个城市。",
    "build_failed": "定位组件编译失败，详见 ~/.jarvis/voice.log。现在可以先选择一个城市。",
    "helper_failed": "定位组件没有返回结果。现在可以先选择一个城市。",
}


def fail(reason, detail=""):
    return {"ok": False, "reason": reason, "message": MESSAGES.get(reason, MESSAGES["unavailable"]), **({"detail": detail} if detail else {})}


def helper_path():
    return Path(os.environ.get("JARVIS_LOCATE_HELPER") or (paths.APP / NAME))


def _source_hash():
    h = hashlib.sha256()
    for name in ("main.swift", "Info.plist"):
        h.update((SRC / name).read_bytes())
    return h.hexdigest()


def _built(app, digest):
    stamp = app / "Contents" / "Resources" / "source.sha256"
    exe = app / "Contents" / "MacOS" / "JarvisLocate"
    try:
        return exe.exists() and os.access(exe, os.X_OK) and stamp.read_text().strip() == digest
    except OSError:
        return False


def _run(cmd, timeout):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout, stdin=subprocess.DEVNULL)


def ensure_helper():
    """(app path, None) when the helper is built and current, else (None, failure dict)."""
    app = helper_path()
    if os.environ.get("JARVIS_LOCATE_HELPER"):
        return (app, None) if (app / "Contents" / "MacOS").exists() else (None, fail("helper_failed", "JARVIS_LOCATE_HELPER is not an app"))
    digest = _source_hash()
    if _built(app, digest):
        return app, None
    with _build_lock:
        if _built(app, digest):
            return app, None
        try:
            if _run(["/usr/bin/xcode-select", "-p"], 15).returncode != 0:       # never call swiftc without the tools: macOS would pop an installer
                return None, fail("no_toolchain")
            stage = Path(tempfile.mkdtemp(prefix="jarvis-locate-"))
            try:
                bundle = stage / NAME
                (bundle / "Contents" / "MacOS").mkdir(parents=True)
                (bundle / "Contents" / "Resources").mkdir()
                shutil.copy(SRC / "Info.plist", bundle / "Contents" / "Info.plist")
                (bundle / "Contents" / "Resources" / "source.sha256").write_text(digest)
                r = _run(["/usr/bin/xcrun", "swiftc", "-O", "-swift-version", "5", "-o", str(bundle / "Contents" / "MacOS" / "JarvisLocate"),
                          str(SRC / "main.swift")], 300)
                if r.returncode != 0:
                    print(f"location helper: swiftc failed: {r.stdout[-600:]}", flush=True)
                    return None, fail("build_failed")
                r = _run(["/usr/bin/codesign", "--force", "-s", "-", str(bundle)], 60)
                if r.returncode != 0:
                    print(f"location helper: codesign failed: {r.stdout[-300:]}", flush=True)
                    return None, fail("build_failed")
                app.parent.mkdir(parents=True, exist_ok=True)
                if app.exists():
                    shutil.rmtree(app, ignore_errors=True)
                shutil.move(str(bundle), str(app))
            finally:
                shutil.rmtree(stage, ignore_errors=True)
        except (OSError, subprocess.SubprocessError) as e:
            print(f"location helper: {type(e).__name__}: {e}", flush=True)
            return None, fail("build_failed")
    return app, None


def _valid(r):
    try:
        lat, lon = float(r["lat"]), float(r["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return None
    name = r.get("name")
    try:
        acc = float(r.get("accuracy", -1))
    except (TypeError, ValueError):
        acc = -1.0
    return {"ok": True, "lat": lat, "lon": lon, "accuracy": acc, "name": name.strip()[:60] if isinstance(name, str) else "", "at": time.time()}


def locate(timeout=90, ask_for=60):
    """One position fix from Location Services. `ask_for` is how long the helper waits for the user to answer the prompt."""
    app, why = ensure_helper()
    if why:
        return why
    run_dir = paths.DATA / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(run_dir, 0o700)
    except OSError:
        pass
    out = run_dir / f"locate-{os.getpid()}-{int(time.time() * 1000)}.json"
    try:
        r = subprocess.run(["/usr/bin/open", "-W", "-n", "-a", str(app), "--args", "--out", str(out), "--timeout", str(int(ask_for))],
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return fail("timeout")
    except OSError as e:
        return fail("helper_failed", type(e).__name__)
    try:
        data = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fail("helper_failed", f"open exit {r.returncode}")
    finally:
        try:
            out.unlink()
        except OSError:
            pass
    if not isinstance(data, dict):
        return fail("helper_failed")
    if data.get("ok"):
        return _valid(data) or fail("unavailable")
    reason = data.get("reason")
    return fail(reason if reason in MESSAGES else "unavailable")
