"""用 Whisper 时间戳查 TTS 配音的语速是否前后一致（见 配音.md）。

用法:
  python tts_pace.py chunks 项目/配音        # 每块的吐字速度（去掉 >0.15 s 的停顿），标出比中位数快 10% 以上的块
  python tts_pace.py windows 项目/配音/1.mp3 ...   # 每 30 秒一个窗口的语速，看有没有越念越快

- 音节数按文字估：汉字 1、每个数字 1、英文词约每 2.5 个字母 1（4 个字母以下按字母念）。
- chunks 模式比只看总时长准：短句、逗号多的句子停顿占比不同，总时长会误判快慢。
- 合格标准（星曜35APO，2026-11-15）：块的吐字速度在中位数 ±10% 内；成品每个 30 秒窗口
  整体 4.2–5.2 音节/秒，前后没有上升趋势。偏快的块把语速调到 -6 ~ -12 重做（结果有随机性，重做后再测）。
需要 faster-whisper（large-v3，GPU）。Windows 上若报找不到 cublas，把 torch\\lib 加进 PATH。
"""
import glob, json, os, re, sys
torch_lib = os.path.join(os.path.dirname(sys.executable), "Lib", "site-packages", "torch", "lib")
if os.path.isdir(torch_lib):
    os.environ["PATH"] = torch_lib + os.pathsep + os.environ["PATH"]
from faster_whisper import WhisperModel
import numpy as np

def syl(t):
    han = len(re.findall(r"[一-鿿]", t))
    letters = sum(max(1, round(len(w) / 2.5)) if len(w) > 3 else len(w) for w in re.findall(r"[A-Za-z]+", t))
    return han + letters + len(re.findall(r"\d", t))

model = WhisperModel("large-v3", device="cuda", compute_type="float16")
def words(p):
    segs, info = model.transcribe(p, language="zh", word_timestamps=True, initial_prompt="以下是普通话的句子。")
    return [w for s in segs for w in s.words], info.duration

mode, args = sys.argv[1], sys.argv[2:]
if mode == "chunks":
    folder = args[0]
    chunks = json.load(open(os.path.join(folder, "chunks.json"), encoding="utf-8"))
    res = {}
    for c in chunks:
        p = sorted(glob.glob(os.path.join(folder, "takes", c["id"] + "*.mp3")))[-1]
        ws, _ = words(p)
        gaps = [max(0, ws[i + 1].start - ws[i].end) for i in range(len(ws) - 1)]
        speak = (ws[-1].end - ws[0].start) - sum(g for g in gaps if g > 0.15)
        res[c["id"]] = (os.path.basename(p), syl(c["text"]) / speak)
    med = float(np.median([v for _, v in res.values()]))
    for k, (f, v) in res.items():
        print(f"{k} {f:10s} {v:.2f}" + ("  <-- 偏快，重做" if v > med * 1.10 else ""))
    print(f"中位数 {med:.2f} 音节/秒")
elif mode == "windows":
    for p in args:
        ws, dur = words(p)
        print(f"== {os.path.basename(p)}  {dur:.1f} s")
        t0 = 0.0
        while t0 < dur:
            t1 = min(t0 + 30, dur)
            sel = [w for w in ws if t0 <= (w.start + w.end) / 2 < t1]
            n = sum(syl(w.word) for w in sel)
            talk = sum(w.end - w.start for w in sel)
            print(f"  {t0:5.0f}-{t1:<5.0f}s  整体 {n / (t1 - t0):.2f}/s  去停顿 {n / talk if talk else 0:.2f}/s")
            t0 = t1
else:
    raise SystemExit(__doc__)
