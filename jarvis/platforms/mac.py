"""macOS: Apple's built-in Vision framework for hands and text (offline, fast on Apple silicon), AVFoundation for the
camera permission, and a login item so Jarvis opens with the morning briefing."""
import os
import re
import subprocess
import tempfile
import time
import wave

NAME = "Mac"

from .maclocate import locate          # noqa: F401  (device position through Location Services)


def open_path(p):
    subprocess.run(["open", str(p)])


_VOICE = re.compile(r"^(.+?)\s+([A-Za-z]{2,3}[_-][A-Za-z0-9]{2,4})\s+#")


def say_voices(lang="zh"):
    """Names of the installed system voices for a language (`say -v ?`): local voices only, nothing is sent anywhere."""
    out = subprocess.run(["/usr/bin/say", "-v", "?"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=15).stdout
    found = []
    for line in out.splitlines():
        m = _VOICE.match(line)
        if m and m.group(2).lower().startswith(lang):
            found.append((m.group(2).lower() != "zh_cn", m.group(1).strip()))       # mainland Mandarin voices first
    return [name for _, name in sorted(found, key=lambda x: x[0])]


def say_wav(text, voice):
    """Speak one sentence with a system voice into a temporary WAV and return (float32 samples, sample rate)."""
    import numpy as np
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        r = subprocess.run(["/usr/bin/say", "-v", voice, "-o", path, "--file-format=WAVE", "--data-format=LEI16@24000", "-f", "-"],
                           input=text, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError(f"say failed ({r.returncode}): {r.stderr.strip()[:120]}")
        with wave.open(path, "rb") as w:
            frames, rate, width, ch = w.readframes(w.getnframes()), w.getframerate(), w.getsampwidth(), w.getnchannels()
        if width != 2:
            raise RuntimeError("unexpected sample format from say")
        x = np.frombuffer(frames, np.int16).astype(np.float32) / 32768.0
        return (x.reshape(-1, ch).mean(axis=1) if ch > 1 else x), rate
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _permission(want, send):
    """Ask macOS for the camera ourselves: OpenCV cannot show the prompt from a background thread."""
    try:
        import AVFoundation as AV
    except Exception:
        return True
    st = AV.AVCaptureDevice.authorizationStatusForMediaType_(AV.AVMediaTypeVideo)
    if st == 3:
        return True
    if st in (1, 2):
        send({"type": "hand", "status": "denied"})
        return False
    send({"type": "hand", "status": "no_camera"})
    AV.AVCaptureDevice.requestAccessForMediaType_completionHandler_(AV.AVMediaTypeVideo, lambda granted: None)
    for _ in range(240):                            # up to 2 minutes for the user to answer
        time.sleep(0.5)
        st = AV.AVCaptureDevice.authorizationStatusForMediaType_(AV.AVMediaTypeVideo)
        if st != 0 or not want():
            break
    if st != 3:
        send({"type": "hand", "status": "denied" if st in (1, 2) else "no_camera"})
    return st == 3


def open_camera(want=lambda: True, send=lambda m: None):
    if not _permission(want, send):
        return None
    os.environ["OPENCV_AVFOUNDATION_SKIP_AUTH"] = "1"
    import cv2
    from .generic import open_camera as _open
    return _open(want, send, cv2.CAP_AVFOUNDATION)


JOINTS = {"wrist": "Wrist", "thumb_tip": "ThumbTip", "thumb_ip": "ThumbIP", "thumb_mp": "ThumbMP",
          "index_tip": "IndexTip", "index_pip": "IndexPIP", "index_mcp": "IndexMCP",
          "middle_tip": "MiddleTip", "middle_pip": "MiddlePIP", "middle_mcp": "MiddleMCP",
          "ring_tip": "RingTip", "ring_pip": "RingPIP", "ring_mcp": "RingMCP",
          "little_tip": "LittleTip", "little_pip": "LittlePIP", "little_mcp": "LittleMCP"}


class VisionHands:
    def __init__(self):
        import Vision
        from Foundation import NSData
        self.V, self.NSData = Vision, NSData
        self.names = {k: getattr(Vision, "VNHumanHandPoseObservationJointName" + v) for k, v in JOINTS.items()}
        self.req = Vision.VNDetectHumanHandPoseRequest.alloc().init()
        self.req.setMaximumHandCount_(2)

    def detect(self, frame):
        import cv2
        ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        data = self.NSData.dataWithBytes_length_(jpg.tobytes(), len(jpg))
        self.V.VNImageRequestHandler.alloc().initWithData_options_(data, None).performRequests_error_([self.req], None)
        out = []
        for obs in (self.req.results() or [])[:2]:
            pts = {}
            for key, jn in self.names.items():
                p, _err = obs.recognizedPointForJointName_error_(jn, None)
                if p is not None and p.confidence() > 0.3:
                    # Vision: origin bottom-left of the camera image. HUD: mirrored, origin top-left.
                    pts[key] = (1.0 - p.x(), 1.0 - p.y())
            out.append(pts)
        return out


def hand_detector():
    try:
        return VisionHands()
    except ImportError:
        from .generic import hand_detector as mp
        return mp()


def ocr(path):
    """Text boxes on an image: [{"t", "x", "y", "w", "h"}], coordinates 0..1 with y measured from the top."""
    try:
        import Vision
        from Foundation import NSURL
    except ImportError:
        from .generic import ocr as rapid
        return rapid(path)
    url = NSURL.fileURLWithPath_(str(path))
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setRecognitionLanguages_(["zh-Hans", "en-US"])
    req.setUsesLanguageCorrection_(False)
    res = handler.performRequests_error_([req], None)
    if isinstance(res, tuple) and not res[0]:
        raise RuntimeError(f"text recognition failed: {res[1]}")
    boxes = []
    for obs in req.results() or []:
        cand = obs.topCandidates_(1)
        if not cand:
            continue
        bb = obs.boundingBox()
        x, y, w, h = bb.origin.x, bb.origin.y, bb.size.width, bb.size.height
        boxes.append({"t": str(cand[0].string()).strip(), "x": x, "y": 1 - (y + h), "w": w, "h": h})
    return boxes


def autostart(on=True):
    """A login item for Jarvis.app (System Settings > General > Login Items)."""
    app = os.environ.get("JARVIS_APP_BUNDLE", "/Applications/Jarvis.app")
    if on:
        script = f'tell application "System Events" to make login item at end with properties {{path:"{app}", hidden:false}}'
    else:
        script = 'tell application "System Events" to delete (every login item whose name is "Jarvis")'
    r = subprocess.run(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=30)
    return {"ok": r.returncode == 0, "on": on, "error": r.stderr[-300:] if r.returncode else ""}
