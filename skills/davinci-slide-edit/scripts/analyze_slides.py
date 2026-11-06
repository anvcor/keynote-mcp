"""把 Keynote/PPT 导出的幻灯片视频切成"静止画面段"(hold)，并出一张带编号的缩略图总表。

用法: python3 analyze_slides.py 视频.mov --out 工作目录 [--fps 60]
输出: 工作目录/holds.json   [{"i","start","end","frames","mean","std","blank"}]  (帧号=源视频帧, end 不含)
      工作目录/holds_sheet_*.jpg  每页 5x4 缩略图，左上角是 hold 编号（暗画面已提亮）

分段方式：一帧只有同时满足 (1) 和上一帧几乎一样、(2) 和本段第一帧也几乎一样，才算同一段。
第 (2) 条是关键——Keynote 里文字慢慢淡入淡出时，相邻帧差很小，只看 (1) 会把
"空白 → 标题淡入 → 标题 → 淡出 → 空白" 误并成一段静止画面，剪出来标题就丢了。
"""
import argparse, json, os, subprocess
import numpy as np
from resolve_common import ffmpeg

ap = argparse.ArgumentParser()
ap.add_argument("video")
ap.add_argument("--out", required=True)
ap.add_argument("--fps", type=float, default=None, help="源视频帧率（只用于最短段长度），默认读 ffmpeg")
ap.add_argument("--step", type=float, default=0.1, help="相邻帧平均灰度差阈值")
ap.add_argument("--anchor", type=float, default=0.5, help="与段首帧平均灰度差阈值")
ap.add_argument("--pix", type=float, default=0.002, help="与段首帧相比，灰度差>12 的像素占比阈值（抓小字淡入）")
ap.add_argument("--min-sec", type=float, default=0.6, help="最短静止段（秒）")
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)
W, H = 96, 54
raw = os.path.join(a.out, "frames_gray.raw")
ff = ffmpeg()
if not os.path.exists(raw):
    subprocess.run([ff, "-v", "error", "-y", "-i", a.video, "-vf", f"scale={W}:{H},format=gray",
                    "-f", "rawvideo", raw], check=True)
fps = a.fps
if fps is None:
    r = subprocess.run([ff, "-hide_banner", "-i", a.video], capture_output=True, text=True).stderr
    import re
    m = re.search(r"([\d.]+) fps", r)
    fps = float(m.group(1)) if m else 60.0
fr = np.fromfile(raw, np.uint8).reshape(-1, H, W).astype(np.int16)
N = len(fr)
d = np.abs(np.diff(fr, axis=0)).mean(axis=(1, 2))
min_len = int(a.min_sec * fps)


def same(x, y):
    # 整帧平均差会把小字（如"结尾"两个字）的淡入稀释掉，所以同时看"变了的像素有多少"
    df = np.abs(x - y)
    return df.mean() < a.anchor and (df > 12).mean() < a.pix


holds = []
i = 0
while i < N:
    j = i
    while j + 1 < N and d[j] < a.step and same(fr[j + 1], fr[i]):
        j += 1
    if j - i + 1 >= min_len:
        mid = fr[(i + j) // 2]
        holds.append({"i": len(holds), "start": i, "end": j + 1, "frames": j + 1 - i,
                      "mean": round(float(mid.mean()), 1), "std": round(float(mid.std()), 2),
                      "blank": bool(mid.std() < 0.5)})
    i = j + 1
# 同一页中间偶尔有一两帧闪动会被切成两段：间隔很短且画面相同的相邻段合并
merged = []
for h in holds:
    p = merged[-1] if merged else None
    if p and h["start"] - p["end"] <= 8 and same(fr[p["end"] - 1], fr[h["start"]]):
        p["end"] = h["end"]; p["frames"] = p["end"] - p["start"]
    else:
        merged.append(h)
for k, h in enumerate(merged):
    h["i"] = k
holds = merged
json.dump({"video": a.video, "fps": fps, "frames": N, "holds": holds},
          open(os.path.join(a.out, "holds.json"), "w"), ensure_ascii=False, indent=1)
for h in holds:
    print(f"{h['i']:3d}  {h['start']:6d}-{h['end']:6d}  {h['frames']/fps:5.1f}s  "
          f"{'BLANK' if h['blank'] else ''}  std={h['std']}")

# 缩略图总表：取每段中点，一次解码全部选出（逐张 select 在 4K 视频上会很慢），提亮，带编号
thumbs = os.path.join(a.out, "thumbs"); os.makedirs(thumbs, exist_ok=True)
sel = "+".join(f"eq(n\\,{(h['start'] + h['end']) // 2})" for h in holds)
base = f"select='{sel}',scale=480:270,eq=brightness=0.12:contrast=1.3"
txt = ",drawtext=text='%{n}':x=8:y=8:fontsize=40:fontcolor=red:box=1"  # select 之后 n 就是 hold 编号
cmd = lambda vf: [ff, "-v", "error", "-y", "-i", a.video, "-vf", vf, "-vsync", "0",
                  "-start_number", "0", os.path.join(thumbs, "%03d.jpg")]
if subprocess.run(cmd(base + txt), stderr=subprocess.DEVNULL).returncode != 0:  # 有的 ffmpeg 没字体，退回无编号
    subprocess.run(cmd(base), check=True)
for k in range(0, len(holds), 20):
    subprocess.run([ff, "-v", "error", "-y", "-start_number", str(k), "-i", os.path.join(thumbs, "%03d.jpg"),
                    "-frames:v", "1", "-vf", "tile=5x4", os.path.join(a.out, f"holds_sheet_{k//20+1}.jpg")], check=True)
print(f"{len(holds)} 段, 总表: {a.out}/holds_sheet_*.jpg")
