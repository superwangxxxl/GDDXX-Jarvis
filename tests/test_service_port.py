"""Starting while an older voice service still holds the port (a crash, a force quit or a restart in the middle of a run).

    python tests/test_service_port.py

The window closing does not mean the last voice service is gone; the new service must take the port back from an older Jarvis
voice service of the same user, and must never touch anything else that happens to hold the port."""
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

if os.name == "nt":
    print("skipped: POSIX process test")
    sys.exit(0)

ROOT = Path(__file__).resolve().parents[1]
HOME = tempfile.mkdtemp()
os.environ["JARVIS_HOME"] = HOME
os.environ["JARVIS_APP_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, str(ROOT))
for m in ("sherpa_onnx", "livekit", "livekit.rtc", "piper", "soundfile", "onnxruntime", "sounddevice", "imageio_ffmpeg", "cv2"):
    try:
        __import__(m)
    except Exception:
        sys.modules[m] = MagicMock()
from jarvis import service                                  # noqa: E402

FAKE = Path(tempfile.mkdtemp())
(FAKE / "jarvis").mkdir()
(FAKE / "jarvis" / "__init__.py").write_text("")
(FAKE / "jarvis" / "__main__.py").write_text(
    "import os, socket, sys, time\ns = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
    "s.bind(('127.0.0.1', int(os.environ['PORT']))); s.listen(); print('listening', flush=True); time.sleep(120)\n")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def start(cmd, port, cwd=None):
    p = subprocess.Popen(cmd, cwd=cwd, env={**os.environ, "PORT": str(port), "PYTHONPATH": str(FAKE)}, stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "listening"
    return p


def alive(p):
    return p.poll() is None


def main():
    pidfile = Path(HOME) / "voice.pid"

    # a free port: nothing to do
    port = free_port()
    assert service.reclaim_port("127.0.0.1", port) == (True, "")

    # an older Jarvis voice service (python -m jarvis) holds it, and its pid file says so: it is stopped, the port is free
    port = free_port()
    old = start([sys.executable, "-m", "jarvis"], port, cwd=FAKE)
    pidfile.write_text(f"{old.pid} 1\n")
    assert service.port_in_use("127.0.0.1", port) and service.is_jarvis_service(old.pid)
    assert service.reclaim_port("127.0.0.1", port) == (True, "")
    old.wait(5)
    assert not alive(old) and not service.port_in_use("127.0.0.1", port)

    # the same without a pid file (an older version never wrote one): found through the listening socket
    if subprocess.run(["which", "lsof"], capture_output=True).returncode == 0:
        pidfile.unlink(missing_ok=True)
        port = free_port()
        old = start([sys.executable, "-m", "jarvis"], port, cwd=FAKE)
        assert service.reclaim_port("127.0.0.1", port) == (True, "")
        old.wait(5)
        assert not alive(old)

    # something else holds the port, even if a stale pid file points at it: it is NOT touched, and the answer says who
    port = free_port()
    other = start([sys.executable, "-c", "import os,socket,time\ns=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)\n"
                   "s.bind(('127.0.0.1',int(os.environ['PORT'])));s.listen();print('listening',flush=True);time.sleep(120)"], port)
    pidfile.write_text(f"{other.pid} 1\n")
    assert not service.is_jarvis_service(other.pid)
    t0 = time.time()
    free, who = service.reclaim_port("127.0.0.1", port, wait=0.2)
    assert free is False and str(other.pid) in who and alive(other), (free, who)
    assert time.time() - t0 < 10
    other.terminate(); other.wait(5)

    # a pid that is not a process at all (a stale pid file) is harmless
    pidfile.write_text("999999 1\n")
    assert service.reclaim_port("127.0.0.1", free_port()) == (True, "")
    # our own pid file is written and removed
    service.write_pidfile()
    assert pidfile.read_text().split()[0] == str(os.getpid())
    service.remove_pidfile()
    assert not pidfile.exists()
    print("ok: an older voice service no longer blocks the port")


if __name__ == "__main__":
    main()
