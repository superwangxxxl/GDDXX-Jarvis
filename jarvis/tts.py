"""Local Chinese/English speech with a sci-fi treatment, streamed sentence by sentence
so playback starts fast and can be cut off mid-sentence."""
import os
import re
import numpy as np
from scipy.signal import lfilter
from . import config, models

VOICE = 68          # zm_029, the default Chinese male voice (unchanged for existing configs)
SR_OUT = 24000

# The Chinese male speakers of kokoro-multi-lang-v1_1 (103 speakers; sid 58-102 are "zm", Chinese male), from the model's
# published table (k2-fsa.github.io/sherpa/onnx/tts/all/Chinese-English/kokoro-multi-lang-v1_1.html). The names are the
# model's own; nothing here is made up, and a voice outside this list is never used.
MALE_NAMES = ["zm_009", "zm_010", "zm_011", "zm_012", "zm_013", "zm_014", "zm_015", "zm_016", "zm_020", "zm_025", "zm_029", "zm_030",
              "zm_031", "zm_033", "zm_034", "zm_035", "zm_037", "zm_041", "zm_045", "zm_050", "zm_052", "zm_053", "zm_054", "zm_055",
              "zm_056", "zm_057", "zm_058", "zm_061", "zm_062", "zm_063", "zm_064", "zm_065", "zm_066", "zm_068", "zm_069", "zm_080",
              "zm_081", "zm_082", "zm_089", "zm_091", "zm_095", "zm_096", "zm_097", "zm_098", "zm_100"]
MALE_SIDS = range(58, 58 + len(MALE_NAMES))
# Median pitch (Hz) of each speaker reading the same sentence, measured on this model's output: a hint for choosing a deeper
# or lighter voice, not a quality score.
MALE_F0 = {58: 126, 59: 138, 60: 113, 61: 143, 62: 122, 63: 112, 64: 126, 65: 166, 66: 196, 67: 122, 68: 106, 69: 144, 70: 123,
           71: 110, 72: 110, 73: 137, 74: 128, 75: 211, 76: 155, 77: 153, 78: 161, 79: 122, 80: 142, 81: 124, 82: 145, 83: 136,
           84: 144, 85: 179, 86: 141, 87: 169, 88: 109, 89: 167, 90: 141, 91: 147, 92: 146, 93: 139, 94: 140, 95: 146, 96: 141,
           97: 137, 98: 164, 99: 176, 100: 124, 101: 102, 102: 136}
FX_MODES = ("scifi", "robot", "off")
DEFAULT_FX = ("scifi", 1.0)          # what every existing installation has always had: a light echo and bass


def male_speakers():
    return [{"sid": s, "name": MALE_NAMES[s - 58], "f0": MALE_F0.get(s)} for s in MALE_SIDS]


def clean_style(speaker=None, mode=None, amount=None):
    """(sid, mode, amount) with anything invalid replaced by the default: only a real Chinese male speaker of the model, one of the
    known effects, an amount between 0 and 1."""
    try:
        sid = int(speaker)
    except (TypeError, ValueError):
        sid = VOICE
    if sid not in MALE_SIDS:
        sid = VOICE
    mode = mode if mode in FX_MODES else DEFAULT_FX[0]
    try:
        amount = min(1.0, max(0.0, float(amount)))
    except (TypeError, ValueError):
        amount = DEFAULT_FX[1]
    return sid, mode, amount


def configured_style():
    fx = config.get("voice_fx")
    fx = fx if isinstance(fx, dict) else {}
    return clean_style(config.get("voice_speaker"), fx.get("mode"), fx.get("amount"))


def sentences(text):
    parts = re.split(r"(?<=[。！？!?；;\n])|(?<=[.](?=\s))|(?<=[，])(?=.{12,})", text)
    return [p.strip() for p in parts if p and p.strip()]


def scifi(x, sr=SR_OUT, amount=1.0):
    """Port of the old Jarvis FFmpeg chain (aecho 20/45/70 ms + bass/treble + band-limit),
    done in numpy so it works on streamed chunks."""
    if amount <= 0:
        return x
    y = x.copy()
    for ms, g in ((20, 0.45), (45, 0.32), (70, 0.12)):
        d = int(sr * ms / 1000)
        y[d:] += amount * g * 0.55 * x[:-d]
    # gentle low shelf (+~4 dB around 110 Hz) and a 90 Hz high-pass, both one-pole
    a_lp = np.exp(-2 * np.pi * 110 / sr)
    y = y + 0.6 * amount * lfilter([1 - a_lp], [1, -a_lp], y)
    a_hp = np.exp(-2 * np.pi * 90 / sr)
    hp = lfilter([a_hp, -a_hp], [1, -a_hp], y)
    peak = np.abs(hp).max() + 1e-6
    return (hp / peak * 0.9).astype(np.float32)


def robot(x, sr=SR_OUT, amount=1.0):
    """A clearly mechanical / electronic robot voice — not a mild echo: ring modulation at a carrier plus a quieter
    octave, a short resonant comb, then band-limit and soft-limit. Changes colour only; speed and words stay the same;
    peak held at 0.9. Kept milder than a hard vocoder so Mandarin consonants (下午…) stay recognisable."""
    from scipy.signal import butter, sosfilt
    x = np.asarray(x, np.float32)
    if amount <= 0 or len(x) == 0:
        return limit(x)
    t = np.arange(len(x)) / sr
    depth = 0.88 * amount
    carrier = np.sin(2 * np.pi * ROBOT_HZ * t) + 0.35 * np.sin(2 * np.pi * (2 * ROBOT_HZ) * t)
    carrier = carrier / (np.abs(carrier).max() + 1e-9)
    y = x * ((1 - depth) + depth * carrier)
    d = max(1, int(sr * 0.006))
    a = np.zeros(d + 1)
    a[0], a[d] = 1.0, -0.65 * amount
    y = lfilter([1.0], a, y)
    y = sosfilt(butter(2, [150, 6500], btype="band", fs=sr, output="sos"), y)
    return limit(np.tanh(1.55 * y / (np.abs(y).max() + 1e-9)))


ROBOT_HZ = 70.0


def limit(x, peak=0.9):
    x = np.nan_to_num(np.asarray(x, np.float32))
    m = float(np.abs(x).max()) if len(x) else 0.0
    return (x * (peak / m)).astype(np.float32) if m > 1e-6 else x


def apply_fx(x, sr, mode, amount):
    """The voice effect: scifi (light echo, the long-standing default), robot (mechanical), off. amount 0..1 is the strength."""
    if mode == "robot":
        return robot(x, sr, amount)
    if mode == "scifi":
        y = scifi(x, sr, amount)
        return limit(y) if len(y) and float(np.abs(y).max()) > 0.9 else y          # the long-standing output is unchanged; only a too-hot signal is pulled down
    return limit(x)


# The old Jarvis release's exact FFmpeg treatment (src/core/voice/jarvis_tts.py)
METALLIC_FILTER = ("aecho=0.8:0.85:20|45|70:0.45|0.32|0.12,"
                   "chorus=0.4:0.6:45:0.2:0.18:1.2,"
                   "bass=g=4:f=110,treble=g=2.5,highpass=f=90,lowpass=f=8500")


def metallic(x, sr):
    """Run the original FFmpeg chain when ffmpeg is installed; numpy approximation otherwise."""
    import shutil, subprocess
    ff = shutil.which("ffmpeg")
    if not ff:
        try:
            import imageio_ffmpeg                  # pip-installed ffmpeg, no Homebrew needed
            ff = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            ff = None
    if not ff:
        return scifi(x, sr)
    p = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", "pipe:0",
                        "-af", METALLIC_FILTER, "-f", "f32le", "-ac", "1", "pipe:1"],
                       input=np.asarray(x, np.float32).tobytes(), capture_output=True, check=True)
    y = np.frombuffer(p.stdout, np.float32)
    peak = float(np.abs(y).max()) if y.size else 0.0
    return (y * (0.97 / 1.5 / peak) if peak > 1e-6 else y).astype(np.float32)


class PiperSpeaker:
    """The community Piper "jarvis-high" voice (English) with the old metallic treatment."""
    def __init__(self, model_path=None, length_scale=1.05, fx=True):
        from piper import PiperVoice, SynthesisConfig
        path = str(model_path or models.JARVIS_VOICE)
        self.voice = PiperVoice.load(path, config_path=path + ".json")
        self.cfg = SynthesisConfig(length_scale=length_scale)
        self.sr = self.voice.config.sample_rate
        self.fx = fx
        self.info = {"engine": "piper", "label": f"Piper {os.path.basename(path).split('.')[0]}（本机离线）", "lang": "英文", "local": True}

    def synth(self, text):
        from scipy.signal import resample_poly
        from math import gcd
        g = gcd(SR_OUT, self.sr)
        for s in sentences(text):
            # the English Piper voice reads Chinese characters as "Chinese letter"; drop them
            s = re.sub(r"[\u3000-\u303f\u3400-\u9fff\uff00-\uffef]+", " ", s).strip()
            if not re.search(r"[A-Za-z0-9]", s):
                continue
            chunks = [c.audio_float_array for c in self.voice.synthesize(s, syn_config=self.cfg)]
            if not chunks:
                continue
            x = np.concatenate(chunks).astype(np.float32)
            if self.fx:
                x = metallic(x, self.sr)
            yield resample_poly(x, SR_OUT // g, self.sr // g).astype(np.float32)


class SpeechError(RuntimeError):
    """The voice engine cannot speak; the message is meant for the user (shown in the HUD)."""


def make_speaker():
    """The speaker for the configured language, with .info = {engine, label, lang, local, error?} so the HUD can say which
    engine is talking and in which language. Never raises: a missing engine becomes a BrokenSpeaker that says why."""
    if models.LANG == "en":
        try:
            return PiperSpeaker()
        except Exception as e:
            return BrokenSpeaker("英文语音引擎（Piper jarvis-high）无法启动：" + f"{type(e).__name__}: {e}"[:160])
    mode = config.get("voice_tts") or "auto"          # auto | local (Kokoro model) | system (macOS voice)
    why = []
    if mode in ("auto", "local"):
        if models.TTS_DIR.exists():
            try:
                return Speaker()
            except Exception as e:
                why.append(f"本机中文语音模型（Kokoro）无法加载：{type(e).__name__}: {e}"[:200])
        else:
            why.append("本机中文语音模型（Kokoro）还没有安装")
    if mode in ("auto", "system"):
        try:
            from . import platforms
            voices = platforms.say_voices()
        except Exception as e:
            voices, why = [], why + [f"读取系统声音失败：{type(e).__name__}"]
        if voices:
            return SystemSpeaker(pick_voice(voices, config.get("system_voice")))
        why.append("这台电脑没有可用的系统中文声音")
    return BrokenSpeaker("没有可用的中文语音引擎：" + "；".join(why) + "。回答只会显示文字。")


def pick_voice(voices, wanted=None):
    """The user's chosen system voice if it exists, else the best Chinese one installed (premium, enhanced, then Tingting)."""
    if wanted and wanted in voices:
        return wanted
    def rank(v):
        low = v.lower()
        return (0 if "premium" in low or "高品质" in v else 1 if "enhanced" in low or "增强" in v else 2,
                0 if "tingting" in low or "ting-ting" in low else 1 if "meijia" in low or "mei-jia" in low else 2)
    return sorted(voices, key=rank)[0]


class Speaker:
    """Chinese + English with the Kokoro model run by sherpa-onnx, fully on this computer. The voice is a real speaker of the
    model and the effect is a separate, reversible setting (config: voice_speaker, voice_fx); speech speed is never touched."""
    def __init__(self, voice=None, speed=1.0, fx=None, engine=None):
        self.tts = engine or models.tts()
        sid, mode, amount = configured_style() if voice is None and fx is None else clean_style(
            voice, *(fx if isinstance(fx, (tuple, list)) else (DEFAULT_FX[0], fx if fx is not None else DEFAULT_FX[1])))
        self.speed = speed
        self.configure(sid, mode, amount)

    def configure(self, voice, mode, amount):
        self.voice, self.mode, self.amount = voice, mode, amount
        name = MALE_NAMES[voice - 58]
        fx = {"scifi": f"轻回声 {round(amount * 100)}%", "robot": f"机械效果 {round(amount * 100)}%", "off": "无效果"}[mode]
        self.info = {"engine": "kokoro", "label": f"Kokoro 多语言 v1.1 · 男声 {name}（sid {voice}）· {fx}（本机离线）", "lang": "中文", "local": True,
                     "speaker": voice, "speaker_name": name, "fx": mode, "amount": amount}

    def synth(self, text):
        """Yield float32 chunks at 24 kHz, one per sentence."""
        for s in sentences(text):
            a = self.tts.generate(s, sid=self.voice, speed=self.speed)
            yield apply_fx(np.asarray(a.samples, np.float32), a.sample_rate, self.mode, self.amount)


class SystemSpeaker:
    """A Chinese voice that ships with the operating system (macOS `say`): runs locally, no download, no network."""
    def __init__(self, voice):
        self.voice = voice
        self.info = {"engine": "system", "label": f"macOS 系统声音「{voice}」（本机）", "lang": "中文", "local": True}

    def synth(self, text):
        from scipy.signal import resample_poly
        from math import gcd
        from . import platforms
        for s in sentences(text):
            x, sr = platforms.say_wav(s, self.voice)
            if len(x) == 0:
                continue
            g = gcd(SR_OUT, sr)
            y = resample_poly(x, SR_OUT // g, sr // g).astype(np.float32)
            _, mode, amount = configured_style() if config.get("voice_fx") else (0, "off", 0.0)     # the effect only when asked for
            yield apply_fx(y, SR_OUT, mode, amount)


class BrokenSpeaker:
    def __init__(self, reason):
        self.reason = reason
        self.info = {"engine": "none", "label": "没有可用的语音引擎", "lang": "—", "local": True, "error": reason}

    def synth(self, text):
        raise SpeechError(self.reason)
        yield                                           # pragma: no cover  (makes this a generator)
