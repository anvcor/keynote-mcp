"""配音的静音段分析：给出每个停顿的起止（秒），用来 (a) 校准句子开口时刻 (b) 找能切开插停顿的位置。

用法: python3 audio_env.py 1.mp3 2.mp3 --out 工作目录 [--thr -38]
输出: 工作目录/silences_<文件名>.json  {"file","duration","silences":[[起,止],...]}
也可 import：onset(sil, t) / pause_at(sil, t)
"""
import argparse, json, os, subprocess
import numpy as np
from resolve_common import ffmpeg


def silences(path, thr=-38.0, min_ms=80):
    raw = subprocess.run([ffmpeg(), "-v", "error", "-i", path, "-f", "s16le", "-ac", "1", "-ar", "24000", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.int16).astype(float) / 32768
    w = 240  # 10 ms
    e = x[: len(x) // w * w].reshape(-1, w)
    db = 20 * np.log10(np.sqrt((e ** 2).mean(1)) + 1e-9)
    s = db < thr
    out, i = [], 0
    while i < len(s):
        if s[i]:
            j = i
            while j < len(s) and s[j]:
                j += 1
            if (j - i) * 10 >= min_ms:
                out.append([round(i / 100, 2), round(j / 100, 2)])
            i = j
        else:
            i += 1
    return {"file": path, "duration": round(len(x) / 24000, 3), "silences": out}


def onset(sil, t, before=0.7, after=0.4):
    """t 附近（通常是 ASR 给的句首）真正开口的时刻 = 最近一段静音的结束点；找不到就返回 t。
    ASR 句首本身通常已经很准，两者取较小值即可。"""
    best = None
    for a, b in sil["silences"]:
        if t - before <= b <= t + after:
            best = b
    return min(t, best) if best is not None else t


def pause_at(sil, t, win=0.8):
    """t 附近最长的一段静音 [a,b]，用于在这里把配音切开。"""
    c = [(b - a, a, b) for a, b in sil["silences"] if a - win <= t <= b + win]
    return max(c)[1:] if c else None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--thr", type=float, default=-38.0)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    for f in a.files:
        r = silences(f, a.thr)
        name = os.path.splitext(os.path.basename(f))[0]
        json.dump(r, open(os.path.join(a.out, f"silences_{name}.json"), "w"), indent=0)
        longp = [p for p in r["silences"] if p[1] - p[0] >= 0.35]
        print(f"{f}: {r['duration']}s, {len(r['silences'])} 个静音段, >=0.35s 的停顿 {len(longp)} 个")
