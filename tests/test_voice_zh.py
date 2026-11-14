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

    # the Chinese path is the one that runs: no Parakeet; wake is Chinese KWS, optionally DualWake if openWakeWord is present
    eng, _ = run(np.zeros(1600, np.float32))
    assert eng.asr.pk is None and type(eng.wake).__name__ in ("SherpaKws", "DualWake"), (eng.asr.pk, type(eng.wake).__name__)

    # text -> Chinese speech -> recognition: the same words come back (leading pad: SenseVoice can drop the first syllable)
    pad = np.zeros(int(0.4 * models.SR), np.float32)
    heard = eng.asr.text(np.concatenate([pad, speech(sp, "你好，今天的天气怎么样？"), pad]), mixed=True)
    want_line = "你好今天的天气怎么样"
    got_line = heard.replace("，", "").replace("？", "").replace("。", "")
    assert __import__("difflib").SequenceMatcher(None, want_line, got_line).ratio() >= 0.9, heard
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
    # the speaker table is the model's: 103 speakers, voices.bin is exactly 103 x (510 x 256 float32), the male ones are sid 58-102
    assert sp.tts.num_speakers == 103 and (models.TTS_DIR / "voices.bin").stat().st_size == 103 * 510 * 256 * 4
    assert min(tts.MALE_SIDS) == 58 and max(tts.MALE_SIDS) == sp.tts.num_speakers - 1

    # real male speakers + the mechanical effect: clear Mandarin, no clipping, a different sound from the plain voice and from each other
    line = "你好，我是贾维斯。现在是下午三点，天气晴朗。"
    want = "你好我是贾维斯现在是下午三点天气晴朗"
    made = {}
    for sid in (68, 101, 63, 72):
        raw = np.asarray(sp.tts.generate(line, sid=sid, speed=1.0).samples, np.float32)
        for mode, amount in (("off", 0.0), ("scifi", 1.0), ("robot", 1.0), ("robot", 0.5)):
            y = tts.apply_fx(raw, tts.SR_OUT, mode, amount)
            assert np.isfinite(y).all() and float(np.abs(y).max()) <= 0.9001, (sid, mode)
            text = eng.asr.text(resample_poly(y, 2, 3).astype(np.float32), mixed=True).replace("，", "").replace("。", "")
            assert __import__("difflib").SequenceMatcher(None, want, text).ratio() >= 0.9, (sid, mode, amount, text)      # clear Mandarin: at most a character or two off
            made[(sid, mode, amount)] = y
    assert not np.array_equal(made[(101, "off", 0.0)], made[(63, "off", 0.0)]), "different speakers sound different"
    n = min(len(made[(63, "off", 0.0)]), len(made[(63, "robot", 1.0)]))
    assert float(np.mean(np.abs(made[(63, "robot", 1.0)][:n] - made[(63, "off", 0.0)][:n]))) > 0.05, "the effect really changes the sound"
    assert int(tts.MALE_F0[101]) <= 105 and int(tts.MALE_F0[63]) <= 115, "the low voices offered are low"
    t0 = __import__("time").time()
    tts.apply_fx(np.asarray(sp.tts.generate(line, sid=63, speed=1.0).samples, np.float32), tts.SR_OUT, "robot", 1.0)
    assert __import__("time").time() - t0 < 5          # includes synthesis; the effect alone is milliseconds
    print("ok: Chinese voice mode with the real local models")


if __name__ == "__main__":
    main()
