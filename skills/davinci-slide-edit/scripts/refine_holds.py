"""把 holds.json 里每个静止段收紧到"和本段中间帧逐像素一样"的那部分（960x540 灰度，差值 <=6）。

用法: python3 refine_holds.py 幻灯片.mov --out 工作目录
      读 工作目录/holds.json，原文件另存为 holds_raw.json，结果写回 holds.json

为什么要做：analyze_slides.py 用 96x54 的小图判断静止，神奇移动是缓入缓出的，
下一页的转场刚开始时（比如 G2 字开始往对焦箭头的折角里滑）每帧只动一点，小图看不出来，
这几帧会被算进静止段。剪辑时静止画面要重复铺好几遍，每一遍的末尾都会"滑一下再跳回去"。
章节卡的字开始淡出也一样。收紧后，重复铺的每一段首尾帧都完全相同。
"""
import argparse, json, os, shutil, subprocess
import numpy as np
from resolve_common import ffmpeg

ap = argparse.ArgumentParser()
ap.add_argument("video")
ap.add_argument("--out", required=True)
ap.add_argument("--fps", type=float, default=60.0)
ap.add_argument("--thr", type=int, default=6, help="灰度差阈值（0-255），超过就算变了")
a = ap.parse_args()
ff = ffmpeg()
W, H = 960, 540
path = os.path.join(a.out, "holds.json")
raw_copy = os.path.join(a.out, "holds_raw.json")
if not os.path.exists(raw_copy):
    shutil.copy(path, raw_copy)
J = json.load(open(path))


def frames(start, n):
    out = subprocess.run([ff, "-v", "error", "-ss", "%.4f" % (start / a.fps), "-i", a.video, "-frames:v", str(n),
                          "-vf", f"scale={W}:{H},format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
    return np.frombuffer(out, np.uint8).reshape(-1, H, W).astype(np.int16)


for h in J["holds"]:
    s, e = h["start"], h["end"]
    mid = (s + e) // 2
    ref = frames(mid, 1)[0]
    same = lambda f: int((np.abs(f - ref) > a.thr).sum()) == 0
    A = frames(s - 12, mid - s + 12)          # s-12 .. mid-1
    B = frames(mid + 1, e - mid + 11)         # mid+1 .. e+10
    i = len(A)
    while i > 0 and same(A[i - 1]):
        i -= 1
    j = 0
    while j < len(B) and same(B[j]):
        j += 1
    ns, ne = s - 12 + i, mid + 1 + j
    if (ns, ne) != (s, e):
        print(f"hold {h['i']:3d}: {s}-{e} -> {ns}-{ne}  (头 {ns - s:+d}, 尾 {ne - e:+d})")
    h["start"], h["end"], h["frames"] = ns, ne, ne - ns
json.dump(J, open(path, "w"), indent=0)
print("已写回", path, "（原始结果在 holds_raw.json）")
