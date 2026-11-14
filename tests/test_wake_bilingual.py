"""Bilingual wake phrases in Chinese voice mode: “Hey Jarvis” / SenseVoice “JERVIOUS” aliases, without voice_lang=en.

    python tests/test_wake_bilingual.py

Covers: robust aliases, false-wake boundaries, command retention after the wake word, and reply_lang=zh
staying put (wake language must not flip answers to English)."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
os.environ["JARVIS_HOME"] = tempfile.mkdtemp()
os.environ["JARVIS_LANG"] = "zh"
sys.path.insert(0, str(ROOT))
for m in ("sherpa_onnx", "onnxruntime", "livekit", "livekit.rtc", "piper", "soundfile", "sounddevice", "cv2"):
    try:
        __import__(m)
    except Exception:
        sys.modules[m] = MagicMock()

from jarvis import config, models                                  # noqa: E402
from jarvis.wake import split_wake, greet_wake                     # noqa: E402
from jarvis.wakeword import DualWake                               # noqa: E402
from jarvis.engine import NAME_LIKE, WAKE_REMNANT                  # noqa: E402


def expect(text, woke, cmd, trusted=False):
    got = split_wake(text, trusted=trusted)
    assert got == (woke, cmd), (text, got, (woke, cmd))


def main():
    # -- SenseVoice-style English mishears of Jarvis (the Mac screenshot: JERVIOUS)
    for raw, cmd in (
        ("Hey JERVIOUS", ""),
        ("hey jervious, 今天天气怎么样", "今天天气怎么样"),
        ("Hey JARVIOUS 把灯打开", "把灯打开"),
        ("JERVIOUS，现在几点了", "现在几点了"),
        ("Hi jarvius 帮我看看日程", "帮我看看日程"),
        ("Hey Jarvis, 上海明天天气", "上海明天天气"),
        ("嘿 Jarvis 播放音乐", "播放音乐"),
    ):
        expect(raw, True, cmd)

    # Chinese wake still works (must not regress)
    expect("嗨，贾维斯，今天天气怎么样", True, "今天天气怎么样")
    expect("嘿贾维斯把客厅的灯打开", True, "把客厅的灯打开")
    expect("贾维斯，现在几点了", True, "现在几点了")

    # -- false wakes: common English words / unrelated chatter must NOT match STRONG
    for raw in (
        "the customer service is closed",
        "Travis is here",
        "I am jealous of that",
        "jar of pickles",
        "various options",
        "今天天气不错我们去公园吧",
        "Hello everyone, welcome to the show",
        "Hi everyone, welcome today",
    ):
        woke, cmd = split_wake(raw)
        assert not woke, (raw, woke, cmd)

    # greet_wake: short "Hi." can wake; long greetings to other people do not
    assert greet_wake("Hi.") == (True, "")
    assert greet_wake("Hi everyone, welcome to the show")[0] is False

    # -- wake remnant / name-like: JERVIOUS strips cleanly; NAME_LIKE sees it after an acoustic hit
    assert WAKE_REMNANT.sub("", "Hey JERVIOUS，把灯打开").strip(" ，,") == "把灯打开"
    assert NAME_LIKE.search("hey jervious") and NAME_LIKE.search("JARVIS")
    assert not NAME_LIKE.search("service") and not NAME_LIKE.search("jealous")

    # -- DualWake: first detector wins; score follows the hitter
    class A:
        score = 0.0

        def process(self, frame):
            self.score = 0.1
            return None

    class B:
        score = 0.0

        def process(self, frame):
            self.score = 0.82
            return "hey jarvis"

    d = DualWake(A(), B())
    assert d.process(b"x") == "hey jarvis" and abs(d.score - 0.82) < 1e-6
    class Hit:
        def process(self, frame):
            return "嗨贾维斯"
    d2 = DualWake(Hit(), B())
    assert d2.process(b"x") == "嗨贾维斯" and d2.score == 1.0

    # -- reply_lang=zh is independent of an English wake string (never flipped by split_wake / DualWake)
    config.update(voice_lang="zh", reply_lang="zh")
    models.LANG = "zh"
    assert config.reply_lang() == "zh"
    expect("Hey JERVIOUS, 今天天气怎么样", True, "今天天气怎么样")
    assert config.reply_lang() == "zh" and config.voice_lang() == "zh", "wake text must not switch languages"
    # explicit reply_lang still wins if someone set English answers on Chinese voice (legacy); wake still must not touch it
    config.update(reply_lang="en")
    expect("嗨，贾维斯，开灯", True, "开灯")
    assert config.get("reply_lang") == "en"
    config.update(reply_lang="zh")
    assert config.reply_lang() == "zh"

    # zh mode wake() uses DualWake only when openWakeWord files exist; otherwise Chinese KWS alone
    import types as _types
    import jarvis.wakeword as ww
    real_ready, real_kws_fn = models.en_wake_ready, models.kws
    real_hj, real_sk = ww.HeyJarvis, ww.SherpaKws
    try:
        models.kws = lambda threads=1: object()
        ww.SherpaKws = lambda spotter: _types.SimpleNamespace(process=lambda f: None)
        ww.HeyJarvis = lambda d: _types.SimpleNamespace(process=lambda f: None, score=0.0)
        models.en_wake_ready = lambda: False
        assert type(models.wake("zh")).__name__ != "DualWake"
        models.en_wake_ready = lambda: True
        assert type(models.wake("zh")).__name__ == "DualWake"
        assert type(models.wake("en")).__name__ != "DualWake"   # English mode stays openWakeWord-only
    finally:
        models.en_wake_ready, models.kws = real_ready, real_kws_fn
        ww.HeyJarvis, ww.SherpaKws = real_hj, real_sk

    print("ok: bilingual wake aliases + reply_lang")


if __name__ == "__main__":
    main()
