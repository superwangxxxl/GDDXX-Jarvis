"""Write listening samples of the Chinese male voices and the mechanical effect, plus a README that says exactly what each one is.

    JARVIS_VOICE_MODELS=<models folder with kokoro-multi-lang-v1_1> python tools/voice_samples.py <output folder>

Every file is the same sentence (no personal information), made by the local Kokoro model: no cloud, no cloned or celebrity voice.
Each sample is also run through the local recogniser and measured (peak level, median pitch), and the numbers are written down,
so what the README says is what the file is. Whether a voice sounds right is for a person to judge, not these numbers."""
import difflib
import os
import re
import sys
import wave
from pathlib import Path

os.environ["JARVIS_LANG"] = "zh"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np                                        # noqa: E402
from scipy.signal import resample_poly                    # noqa: E402
from jarvis import models, tts                            # noqa: E402

TEXT = "你好，我是贾维斯。现在是下午三点，天气晴朗。"
SAMPLES = [   # (file, sid, effect, amount, what it is for)
    ("A-zm_098-natural.wav", 101, "off", 0.0, "实测最低沉的男声说话人，不加任何效果：平实、低沉"),
    ("B-zm_014-mechanical.wav", 63, "robot", 1.0, "另一位偏低的男声说话人 + 机械（金属感）效果 100%：明显机械风"),
    ("ref-current-default-zm_029-scifi.wav", 68, "scifi", 1.0, "此前所有安装的默认声音（zm_029 + 轻回声），仅作对照"),
]


def median_f0(x, sr):
    fr, hop, out = int(sr * 0.04), int(sr * 0.01), []
    e = float(np.sqrt(np.mean(x ** 2)))
    for i in range(0, len(x) - fr, hop):
        w = x[i:i + fr]
        if float(np.sqrt(np.mean(w ** 2))) < 0.5 * e:
            continue
        w = w - w.mean()
        ac = np.correlate(w, w, "full")[fr - 1:]
        lo, hi = int(sr / 400), int(sr / 60)
        k = lo + int(np.argmax(ac[lo:hi]))
        if ac[0] > 0 and ac[k] / ac[0] > 0.5:
            out.append(sr / k)
    return float(np.median(out)) if out else 0.0


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    engine, asr = models.tts(), models.Transcriber()
    rows = []
    for name, sid, mode, amount, why in SAMPLES:
        a = engine.generate(TEXT, sid=sid, speed=1.0)                  # speed stays 1.0: the voice is chosen by speaker, never by slowing down
        raw = np.asarray(a.samples, np.float32)
        y = tts.apply_fx(raw, a.sample_rate, mode, amount)
        with wave.open(str(out / name), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(a.sample_rate)
            w.writeframes((np.clip(y, -1, 1) * 32767).astype("<i2").tobytes())
        heard = asr.text(resample_poly(y, 2, 3).astype(np.float32), mixed=True)
        ref, hyp = re.sub(r"[，。\s]", "", TEXT), re.sub(r"[，。！？\s]", "", heard)
        rows.append((name, sid, tts.MALE_NAMES[sid - 58], mode, amount, len(y) / a.sample_rate, float(np.abs(y).max()), median_f0(raw, a.sample_rate),
                     1 - difflib.SequenceMatcher(None, ref, hyp).ratio(), why))
    lines = ["# 中文男声试听样本", "", f"同一句话：「{TEXT}」（不含个人信息）", "",
             "来源：全部由本机 Kokoro 多语言 v1.1 模型（sherpa-onnx 官方发布的开源模型，Apache-2.0）合成，没有联网、没有云端、没有克隆任何真人或名人的声音。"
             "声线是模型自带的中文男声说话人（编号和名字来自模型官方的说话人表），语速 1.0；机械效果是 Jarvis 里单独的、可关闭的后处理（环形调制 + 短共振梳状滤波 + 限幅）。", "",
             "| 文件 | 说话人 | 效果 | 时长 | 峰值 | 说话人基频（中位数） | 本机识别回读的差异率 | 说明 |", "|---|---|---|---|---|---|---|---|"]
    for n, sid, spk, mode, amount, dur, peak, f0, cer, why in rows:
        fx = {"off": "无", "scifi": f"轻回声 {round(amount * 100)}%", "robot": f"机械 {round(amount * 100)}%"}[mode]
        lines.append(f"| `{n}` | {spk}（sid {sid}） | {fx} | {dur:.1f} s | {peak:.2f} | {f0:.0f} Hz | {cer:.2f} | {why} |")
    lines += ["", "怎么用：在 Jarvis「菜单 → 语言与语音」里选同一个说话人、同一个效果，点「试听（不保存）」，满意再点「使用这个声音」；"
              "不满意点「恢复默认」就回到 zm_029 + 轻回声。", "",
              "这些数字只说明文件没有削波、识别器能听清普通话、基频确实偏低；**声音好不好听、够不够机械，要你自己听**，在你确认之前不能算解决。"]
    (out / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for r in rows:
        print(r[0], f"peak {r[6]:.2f} f0 {r[7]:.0f} Hz diff {r[8]:.2f}")


if __name__ == "__main__":
    main()
