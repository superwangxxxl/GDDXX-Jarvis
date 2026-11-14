"""Chinese voice mode with the REAL local models: speech -> wake word -> recognition -> command, and text -> Chinese speech.

    JARVIS_VOICE_MODELS=<models folder with the zh group> python tests/test_voice_zh.py

The "speech" is Chinese made by the same Kokoro model Jarvis speaks with, so this proves the routing (the Chinese models are
the ones that run in Chinese mode; Parakeet and the English wake word are not involved) and the TTS -> STT round trip. It is
not a microphone test: real voices, rooms and a real Mac are checked by hand. Skipped when the models are not installed."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["JARVIS_LANG"] = "zh"
os.environ["JARVIS_HOME"] = tempfile.mkdtemp()
os.environ.setdefault("JARVIS_VOICE_MODELS", str(ROOT / "models"))
sys.path.insert(0, str(ROOT))

import numpy as np                                    # noqa: E402
from scipy.signal import resample_poly                # noqa: E402
from jarvis import models, tts                        # noqa: E402

ok, missing = models.ready("zh")
if not ok:
    print("skipped: the Chinese models are not installed:", ", ".join(missing))
    sys.exit(0)

from jarvis.engine import VoiceEngine                 # noqa: E402


def speech(speaker, text):
    """Chinese speech at 16 kHz as the microphone path delivers it."""
    x = np.concatenate(list(speaker.synth(text)))
    return resample_poly(x, 2, 3).astype(np.float32)


def run(text_audio, tail=3):
    ev = []
    e = VoiceEngine(lambda n, d: ev.append((n, d)))
    a = np.concatenate([np.zeros(16000, np.float32), text_audio, np.zeros(16000 * tail, np.float32)])
    for i in range(0, len(a), 1600):
        e.feed(a[i:i + 1600])
    return e, ev


def main():
    sp = tts.make_speaker()
    assert sp.info["engine"] == "kokoro" and "本机离线" in sp.info["label"] and sp.info["lang"] == "中文", sp.info

    # the Chinese path is the one that runs: no Parakeet, no English wake word
    eng, _ = run(np.zeros(1600, np.float32))
    assert eng.asr.pk is None and type(eng.wake).__name__ == "SherpaKws", (eng.asr.pk, eng.wake)

    # text -> Chinese speech -> recognition: the same words come back
    heard = eng.asr.text(speech(sp, "你好，今天的天气怎么样？"), mixed=True)
    assert heard.replace("，", "").replace("？", "") == "你好今天的天气怎么样", heard
    assert eng.asr.last.startswith("SenseVoice"), eng.asr.last

    # wake word + command in one breath, the command arrives complete (it used to lose its first words)
    for phrase, want in (("嗨，贾维斯，今天天气怎么样？", "今天天气怎么样"), ("嗨贾维斯，把客厅的灯打开", "把客厅的灯打开"), ("贾维斯，现在几点了", "现在几点了")):
        e, ev = run(speech(sp, phrase))
        names = [n for n, _ in ev]
        cmd = [d["text"] for n, d in ev if n == "command"]
        assert "wake" in names and cmd == [want], (phrase, ev)

    # speech without the wake word is shown as heard-but-ignored, and nothing runs
    e, ev = run(speech(sp, "今天天气不错我们去公园吧"))
    assert [n for n, _ in ev] == ["heard"] and ev[0][1]["wake"] is False and "公园" in ev[0][1]["text"], ev

    # a Chinese reply is spoken in Chinese (non-empty audio at the output rate), line by line
    chunks = list(sp.synth("好的。我已经把明天的会议记下来了！"))
    assert len(chunks) == 2 and all(len(c) > tts.SR_OUT * 0.5 and c.dtype == np.float32 for c in chunks)
    print("ok: Chinese voice mode with the real local models")


if __name__ == "__main__":
    main()
