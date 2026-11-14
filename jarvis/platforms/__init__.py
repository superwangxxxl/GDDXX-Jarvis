"""The few things that differ between macOS and Windows, behind one set of functions.

  open_path(p)                 show a file or folder in the system's own app
  open_camera(want, send)      an OpenCV capture of the front camera (asks for permission where the OS needs it)
  hand_detector()              finds hands in a camera frame: detect(frame_bgr) -> [{joint: (x, y)}]
                               (x, y mirrored, 0..1 from the top-left, joints named as in gesture.py)
  ocr(path)                    text boxes on an image: [{"t", "x", "y", "w", "h"}], 0..1 from the top-left
  autostart(on)                open Jarvis when the user logs in (the morning briefing needs it)
  locate()                     the device position from the system's own location service (macOS Core Location), or
                               {"ok": False, "reason", "message"}: never a guess from the IP address or time zone
  say_voices() / say_wav(t, v) installed system voices (macOS `say`) and one sentence of speech from one: a local fallback
                               for Chinese speech when the Kokoro model is not installed

macOS uses Apple's built-in Vision framework; Windows and Linux use MediaPipe and RapidOCR (pip packages).
"""
import sys

if sys.platform == "darwin":
    from .mac import open_path, open_camera, hand_detector, ocr, autostart, locate, say_voices, say_wav, NAME          # noqa: F401
elif sys.platform.startswith("win"):
    from .win import open_path, open_camera, hand_detector, ocr, autostart, locate, say_voices, say_wav, NAME          # noqa: F401
else:
    from .generic import open_path, open_camera, hand_detector, ocr, autostart, locate, say_voices, say_wav, NAME      # noqa: F401
