"""The voice chain's failure handling and the Chinese voice setup, without speech models or a microphone.

    python tests/test_voice_chain.py

Checks that a dead reply thread, a failing voice engine or a crashing recognizer ends in a visible error and a service that
listens again (not a silent one), that Chinese voice engines are chosen and labelled honestly, that the language switch checks
models and restarts, and that the Mac system-voice client parses `say` correctly (with a fake `say`)."""
import json
import os
import subprocess
import sys
import tempfile
import time
import types
import wave
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
os.environ["JARVIS_HOME"] = tempfile.mkdtemp()
os.environ["JARVIS_APP_DIR"] = tempfile.mkdtemp()
os.environ["JARVIS_VOICE_MODELS"] = tempfile.mkdtemp()          # no models here
os.environ.pop("JARVIS_LANG", None)
sys.path.insert(0, str(ROOT))

for m in ("sherpa_onnx", "livekit", "livekit.rtc", "piper", "soundfile", "onnxruntime", "sounddevice", "imageio_ffmpeg", "cv2"):
    try:
        __import__(m)
    except Exception:
        sys.modules[m] = MagicMock()

import numpy as np                                        # noqa: E402
from jarvis import brain, config, models, platforms, service, tts      # noqa: E402
from jarvis.platforms import mac                          # noqa: E402

SAY_LIST = """Alex                en_US    # Most people recognize me by my voice.
Eddy (Chinese (China mainland)) zh_CN    # 你好！我叫Eddy。
Tingting            zh_CN    # 你好！我叫Tingting。
Meijia              zh_TW    # 你好，我叫美佳。
Sinji               zh_HK    # 你好，我叫善怡。
Tingting (Enhanced) zh_CN    # 你好！我叫Tingting。
Kyoko               ja_JP    # こんにちは、私の名前はKyokoです。
"""


class FakeEngine:
    def __init__(self):
        self.state, self.calls, self.busy_since = "thinking", [], 0.0
        self.asr = types.SimpleNamespace(last="SenseVoice（中/英/日/韩/粤）")
        self.wake = types.SimpleNamespace(score=0.0)

    def set_speaking(self, on, said=""):
        self.calls.append(("speaking", on))
        self.state = "speaking" if on else "awake"

    def _reset(self):
        self.calls.append("reset")
        self.state = "standby"

    def feed(self, x):
        raise ValueError("model exploded")

    def now(self):
        return 1000.0


def make_service(speaker):
    s = service.Service.__new__(service.Service)
    s.sent = []
    s.broadcast = s.sent.append
    s.gen, s.muted = 0, False
    s.engine, s.speaker = FakeEngine(), speaker
    s.player = types.SimpleNamespace(push=lambda x: None, busy=lambda: False, stop=lambda: None)
    s.last_error, s.engine_err_at, s.fallback = None, 0.0, None
    return s


def wait(cond, secs=3):
    t = time.time()
    while time.time() - t < secs and not cond():
        time.sleep(0.02)
    assert cond(), "timed out"


def main():
    # -- config: voice and answer language
    assert config.voice_lang() == "en" and config.reply_lang() == "en"
    config.update(voice_lang="zh")
    assert config.voice_lang() == "zh" and config.reply_lang() == "zh", "the answers follow the voice language"
    config.update(reply_lang="en")
    assert config.reply_lang() == "en", "an explicit reply_lang wins (English voice + Chinese subtitle users)"
    config.update(voice_lang=None, reply_lang=None)

    # -- the Mac system voice client: parses `say -v ?`, writes a WAV, reads it back
    real_run = subprocess.run
    calls = []

    def fake_say(cmd, **kw):
        calls.append(cmd)
        if cmd[1:3] == ["-v", "?"]:
            return subprocess.CompletedProcess(cmd, 0, SAY_LIST, "")
        out = cmd[cmd.index("-o") + 1]
        assert "--file-format=WAVE" in cmd and cmd[-2:] == ["-f", "-"] and kw["input"], cmd          # text goes in on stdin, not on argv
        with wave.open(out, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
            w.writeframes((np.sin(np.arange(2400) / 10) * 12000).astype(np.int16).tobytes())
        return subprocess.CompletedProcess(cmd, 0, "", "")
    subprocess.run = fake_say
    try:
        names = mac.say_voices()
        assert names == ["Eddy (Chinese (China mainland))", "Tingting", "Tingting (Enhanced)", "Meijia", "Sinji"], names
        assert tts.pick_voice(names) == "Tingting (Enhanced)" and tts.pick_voice(names, "Sinji") == "Sinji" and tts.pick_voice(["Eddy", "Tingting"]) == "Tingting"
        x, sr = mac.say_wav("你好，今天天气不错。", "Tingting")
        assert sr == 24000 and len(x) == 2400 and x.dtype == np.float32 and 0.3 < float(np.abs(x).max()) < 0.5
        sp = tts.SystemSpeaker("Tingting")
        platforms.say_wav = mac.say_wav
        chunks = list(sp.synth("你好。今天天气不错！"))
        assert len(chunks) == 2 and all(c.dtype == np.float32 and abs(float(np.abs(c).max()) - 0.9) < 0.01 for c in chunks)
        assert sp.info == {"engine": "system", "label": "macOS 系统声音「Tingting」（本机）", "lang": "中文", "local": True}
    finally:
        subprocess.run = real_run

    # -- choosing the Chinese voice engine, honestly labelled
    models.LANG = "zh"
    platforms.say_voices = lambda lang="zh": []
    b = tts.make_speaker()
    assert b.info["engine"] == "none" and "没有可用的中文语音引擎" in b.info["error"] and "Kokoro" in b.info["error"], b.info
    try:
        next(b.synth("你好"))
        raise AssertionError("a broken speaker must raise, not stay silent")
    except tts.SpeechError as e:
        assert "没有可用" in str(e)
    platforms.say_voices = lambda lang="zh": ["Eddy", "Tingting"]
    assert tts.make_speaker().info["label"] == "macOS 系统声音「Tingting」（本机）"
    config.update(system_voice="Eddy")
    assert tts.make_speaker().info["label"].startswith("macOS 系统声音「Eddy」")
    config.update(system_voice=None, voice_tts="local")
    assert tts.make_speaker().info["engine"] == "none", "an explicit choice is not silently replaced by another engine"
    models.TTS_DIR = Path(tempfile.mkdtemp())                           # a Kokoro folder that exists but is not a model
    config.update(voice_tts="auto")
    sp = tts.make_speaker()
    assert sp.info["engine"] in ("system", "none") and "Kokoro" not in sp.info.get("label", ""), sp.info
    models.LANG = "en"
    real_piper = tts.PiperSpeaker
    tts.PiperSpeaker = lambda: (_ for _ in ()).throw(FileNotFoundError("no voice file"))
    b = tts.make_speaker()
    tts.PiperSpeaker = real_piper
    assert b.info["engine"] == "none" and "Piper" in b.info["error"] and "no voice file" in b.info["error"], "English voice missing: visible too"
    config.update(voice_tts=None)

    # -- a voice engine that fails: the answer stays on screen, the user is told, Jarvis listens again
    models.LANG = "zh"
    s = make_service(tts.BrokenSpeaker("没有可用的中文语音引擎：测试。"))
    s.say("你好", gen=0)
    wait(lambda: ("speaking", False) in s.engine.calls)
    errs = [m for m in s.sent if m["type"] == "voice_error"]
    assert errs and errs[0]["stage"] == "tts" and "测试" in errs[0]["text"] and "屏幕上" in errs[0]["text"], s.sent
    assert any(m["type"] == "jarvis" and m["text"] == "你好" for m in s.sent), "the answer is shown even when it cannot be spoken"
    assert s.sent[-1] == {"type": "state", "state": "listening"} and s.engine.state == "awake", "Jarvis listens again after the failed speech"

    # Chinese voice mode speaks the Chinese line, not the English one
    spoken = []
    s = make_service(types.SimpleNamespace(info={}, synth=lambda t: (spoken.append(t), iter(()))[1]))
    s.say("Done, sir.", gen=0, zh="好了。")
    wait(lambda: ("speaking", False) in s.engine.calls)
    assert spoken == ["好了。"] and any(m["type"] == "jarvis" and m["text"] == "好了。" for m in s.sent), spoken
    models.LANG = "en"
    spoken.clear()
    s = make_service(types.SimpleNamespace(info={}, synth=lambda t: (spoken.append(t), iter(()))[1]))
    s.say("Done, sir.", gen=0, zh="好了。")
    wait(lambda: ("speaking", False) in s.engine.calls)
    assert spoken == ["Done, sir."] and any(m["type"] == "jarvis" and m["zh"] == "好了。" for m in s.sent), "English mode keeps the subtitle"

    # -- a reply thread that dies still ends in an error and a spoken apology, not silence
    s = make_service(types.SimpleNamespace(info={}, synth=lambda t: (spoken.append(t), iter(()))[1]))
    s.push_schedule = s.push_trip = lambda: None
    orig = service.answer_pair
    service.answer_pair = lambda text, spoken=True: (_ for _ in ()).throw(RuntimeError("brain crashed"))
    spoken.clear()
    s.handle("你好")
    wait(lambda: spoken)
    service.answer_pair = orig
    assert any(m["type"] == "voice_error" and m["stage"] == "brain" and "brain crashed" in m["text"] for m in s.sent), s.sent
    assert spoken and "something went wrong" in spoken[0]

    # -- a recognizer that crashes: visible, rate limited, engine reset, not "no microphone"
    s = make_service(None)
    s.feed_engine(np.zeros(160, np.float32))
    s.feed_engine(np.zeros(160, np.float32))
    errs = [m for m in s.sent if m["type"] == "voice_error"]
    assert len(errs) == 1 and errs[0]["stage"] == "stt" and "model exploded" in errs[0]["text"], errs
    assert s.engine.calls == ["reset", "reset"]

    # -- thinking/speaking with nothing happening is reset after a while
    s = make_service(None)
    s.engine.busy_since = 1000.0 - 31
    s.engine.state = "speaking"
    s.unstick()
    assert s.engine.state == "standby" and any(m["type"] == "voice_error" and "卡住" in m["text"] for m in s.sent)
    s = make_service(None)
    s.engine.state, s.engine.busy_since = "thinking", 1000.0 - 60          # an LLM call may take a minute: leave it alone
    s.unstick()
    assert s.engine.state == "thinking" and not s.sent

    # -- the HUD info: engines, languages, nothing hidden
    s = make_service(tts.SystemSpeaker("Tingting"))
    models.LANG = "zh"
    info = s.voice_info()
    assert info["lang"] == "zh" and info["wake_word"] == "嗨，贾维斯" and info["tts"]["engine"] == "system" and "本机离线" in info["stt"], info
    assert info["stt_last"] == "SenseVoice（中/英/日/韩/粤）" and info["brain"] == "还没有接入大模型"
    config.update(llm={"provider": "deepseek", "base_url": "https://x/v1", "api_key": "sk-SECRET", "model": "deepseek-chat"})
    info = s.voice_info()
    assert "deepseek" in info["brain"] and "SECRET" not in json.dumps(info), info
    models.LANG = "en"
    assert s.voice_info()["wake_word"] == "Hey Jarvis"

    # -- the language switch: models are checked, the installer runs with the target language, the service restarts
    models.LANG = "en"
    s = make_service(tts.BrokenSpeaker("x"))
    models.ready = lambda lang=None: (False, ["中文唤醒词模型（KWS）"]) if not getattr(models, "_ok", False) else (True, [])

    class Proc:
        def __init__(self, lines, rc):
            self.stdout, self.rc = iter(lines), rc

        def wait(self):
            models._ok = self.rc == 0
            return self.rc
    seen = {}

    def popen(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw["env"]
        return Proc(["10:00:00 下载模型文件夹 kokoro-multi-lang-v1_1\n", "10:00:09 !!! RuntimeError: 模型文件夹 kokoro 校验不通过\n"], 1)
    real_popen = subprocess.Popen
    subprocess.Popen = popen
    execs = []
    real_exec = os.execv
    os.execv = lambda *a: execs.append(a)
    try:
        s.switch_voice("zh")
        stages = [m["stage"] for m in s.sent if m["type"] == "voice_switch"]
        assert stages[0] == "download" and stages[-1] == "error" and not execs, stages
        assert "校验不通过" in s.sent[-1]["text"] and config.get("voice_lang") is None, "a failed download changes nothing"
        assert seen["env"]["JARVIS_LANG"] == "zh" and seen["cmd"][-3:] == ["install.py", "--no-page", "--models-only"][-3:] or seen["cmd"][-2:] == ["--no-page", "--models-only"], seen["cmd"]
        subprocess.Popen = lambda cmd, **kw: Proc(["10:00:00 下载模型 1/3\n"], 0)
        s.sent.clear()
        time.sleep(0.01)
        service.time.sleep = lambda x: None
        s.switch_voice("zh")
        assert [m["stage"] for m in s.sent if m["type"] == "voice_switch"][-1] == "restart" and execs, s.sent
        assert config.get("voice_lang") == "zh" and config.get("reply_lang") == "zh"
        assert execs[0][1][-2:] == ["-m", "jarvis"]
    finally:
        subprocess.Popen = real_popen
        os.execv = real_exec

    # -- starting up with missing models falls back to the other language, and says so
    config.update(voice_lang=None, reply_lang=None)
    models.LANG = "zh"
    ready = {"zh": (False, ["Kokoro 中文语音模型"]), "en": (True, [])}
    models.ready = lambda lang=None: ready[lang or models.LANG]
    service.pick_language()
    assert models.LANG == "en" and models.FALLBACK == {"wanted": "zh", "using": "en", "missing": ["Kokoro 中文语音模型"]}
    assert config.voice_lang() == "en" and config.reply_lang() == "en"
    s = make_service(tts.BrokenSpeaker("x"))
    s.fallback = models.FALLBACK
    assert s.voice_info()["fallback"]["wanted"] == "zh"
    config.RUNTIME.clear()

    # -- why the language model could not be reached
    import urllib.error
    assert "密钥无效" in brain.llm_reason(urllib.error.HTTPError("u", 401, "x", {}, None))
    assert "网络" in brain.llm_reason(urllib.error.URLError("down")) and "额度" in brain.llm_reason(urllib.error.HTTPError("u", 429, "x", {}, None))
    config.update(reply_lang="zh")
    assert brain.canned("offline", "密钥无效或已过期") == "抱歉，我暂时连不上语言模型：密钥无效或已过期。"
    config.update(reply_lang=None)
    print("ok: voice chain")


if __name__ == "__main__":
    main()
