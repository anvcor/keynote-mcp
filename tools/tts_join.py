"""把 TTS 小块拼成每章一个 mp3，并写段落时间表（见 配音.md）。

用法: python tts_join.py 项目/配音 [--takes 项目/配音/takes] [--gap-sent 0.35] [--gap-para 0.7]

- 读 配音/chunks.json（tts_chunks.py 生成）和 takes/ 里的 cNN.mp3；同一块有重做版（c49b、c49c）时取字母最大的。
- 每块去掉首尾静音（留 40 ms），同一段内的块之间停 --gap-sent 秒，段落之间停 --gap-para 秒，章末不留停顿。
- 输出 配音/1.mp3 … N.mp3（24 kHz 单声道 128 kbps）和 配音/段落时间表.csv
  （文件,段落,图,开始秒,结束秒,开头），tts_srt.py 和剪辑都用它。
需要 numpy、imageio-ffmpeg。
"""
import argparse, csv, glob, json, os, subprocess
import numpy as np, imageio_ffmpeg

ap = argparse.ArgumentParser()
ap.add_argument("folder"); ap.add_argument("--takes")
ap.add_argument("--gap-sent", type=float, default=0.35); ap.add_argument("--gap-para", type=float, default=0.7)
a = ap.parse_args()
takes = a.takes or os.path.join(a.folder, "takes")
FF = imageio_ffmpeg.get_ffmpeg_exe(); SR = 24000
chunks = json.load(open(os.path.join(a.folder, "chunks.json"), encoding="utf-8"))

def load(p):
    raw = subprocess.run([FF, "-v", "error", "-i", p, "-f", "s16le", "-ac", "1", "-ar", str(SR), "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768

def trim(x, thr=0.01, pad=0.04):
    env = np.convolve(np.abs(x), np.ones(240) / 240, "same")
    idx = np.where(env > thr)[0]
    if not len(idx):
        return x
    return x[max(0, idx[0] - int(pad * SR)): min(len(x), idx[-1] + int(pad * SR))]

def take(cid):
    fs = sorted(glob.glob(os.path.join(takes, cid + "*.mp3")))
    if not fs:
        raise SystemExit(f"缺少 {cid}.mp3")
    return fs[-1]

rows = []
for seg in sorted({c["seg"] for c in chunks}):
    parts = [c for c in chunks if c["seg"] == seg]
    buf, t = [], 0.0
    for k, c in enumerate(parts):
        x = trim(load(take(c["id"])))
        if c["part"] == 0:
            rows.append({"文件": f"{seg}.mp3", "段落": c["para"], "图": c.get("fig", ""),
                         "开始秒": round(t, 2), "开头": c["text"][:16]})
        buf.append(x); t += len(x) / SR
        if c["last_in_para"]:
            rows[-1]["结束秒"] = round(t, 2)
        if k < len(parts) - 1:
            g = a.gap_para if c["last_in_para"] else a.gap_sent
            buf.append(np.zeros(int(g * SR), np.float32)); t += g
    y = np.concatenate(buf)
    pcm = (np.clip(y, -1, 1) * 32767).astype(np.int16).tobytes()
    dst = os.path.join(a.folder, f"{seg}.mp3")
    subprocess.run([FF, "-v", "error", "-y", "-f", "s16le", "-ac", "1", "-ar", str(SR), "-i", "-", "-b:a", "128k", dst],
                   input=pcm, check=True)
    print(f"{seg}.mp3  {len(y) / SR:.1f} s  {len(parts)} 块")
with open(os.path.join(a.folder, "段落时间表.csv"), "w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, ["文件", "段落", "图", "开始秒", "结束秒", "开头"]); w.writeheader(); w.writerows(rows)
print("段落时间表.csv", len(rows), "段")
