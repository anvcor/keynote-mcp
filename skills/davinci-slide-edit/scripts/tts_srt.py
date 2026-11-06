"""成片字幕：TTS 配音 + 合成用的原文 -> 成片的 SRT。不做语音识别，字幕文字就是原文，没有错字。

用法:
  python3 tts_srt.py 配音文件夹 --render 成片.mp4                  # 常用：配音位置从达芬奇时间线 A1 读，输出 成片.srt
  python3 tts_srt.py 配音文件夹 --offsets 0 80.1667 ... --out x.srt   # 不连达芬奇，手填每个 mp3 在成片里的起点

配音文件夹（TTS 流程的产物）里要有 1.mp3..N.mp3、文本/1_*.txt..N_*.txt（每行一段，和合成时一致）、
段落时间表.csv（文件,段落,图,开始秒,结束秒,开头）。

做法：每段的起止来自段落时间表；段内按标点切成短句，和音频里的语音段（停顿 >=120 ms 处断开）
做分组对齐（按音节数估长度）；一口气念完的几句按音节比例分时间，切点吸附到最安静的帧。
作者的字幕习惯（以往剪映导出的 SRT）：单行，句末去掉逗号句号、保留冒号分号，一般 12-16 字，
太长的在顿号、"…时 / …上"、"的"等处拆开；字幕提前 0.1 s 出现，停顿短于 0.3 s 时首尾相接。
--render 时用波形互相关量出成片里配音的实际位置（mp3 解码会差几十毫秒），并核对每段都对得上。
"""
import argparse, csv, glob, json, math, os, re, subprocess
import numpy as np
import imageio_ffmpeg

ap = argparse.ArgumentParser()
ap.add_argument("folder")
ap.add_argument("--render", help="导出的成片；默认输出到它旁边的同名 .srt")
ap.add_argument("--timeline", default="Timeline 1", help="从这条时间线的 A1 读每个 mp3 的位置")
ap.add_argument("--offsets", nargs="+", type=float, help="每个 mp3 在成片里的起点（秒），给了就不连达芬奇")
ap.add_argument("--delay", type=float, help="成片里声音比 ffmpeg 解码的 mp3 晚多少秒；给了 --render 时自动测")
ap.add_argument("--out")
ap.add_argument("--json", help="另存逐条明细（核对用）")
ap.add_argument("--maxw", type=float, default=24.0, help="一行最宽（汉字=1，字母数字=0.55）")
a = ap.parse_args()
FF = imageio_ffmpeg.get_ffmpeg_exe()
SR, HOP = 16000, 160                      # 10 ms 一帧

n_mp3 = len(glob.glob(os.path.join(a.folder, "[0-9]*.mp3")))
if a.offsets is None:
    from resolve_common import connect, timelines
    _r, _p, _mp = connect()
    _t = timelines(_p)[a.timeline]; _st = _t.GetStartFrame(); _fps = float(_t.GetSetting("timelineFrameRate"))
    pos = {x.GetName(): (x.GetStart() - _st) / _fps - x.GetLeftOffset() / _fps for x in _t.GetItemListInTrack("audio", 1)}
    a.offsets = [pos["%d.mp3" % k] for k in range(1, n_mp3 + 1)]
    print("A1 上的配音位置:", ", ".join("%d.mp3 %.3fs" % (k + 1, o) for k, o in enumerate(a.offsets)))
if a.render and a.delay is None:
    def pcm8k(path, ss, dur):
        b = subprocess.run([FF, "-v", "error", "-ss", str(max(ss, 0)), "-t", str(dur), "-i", path, "-ac", "1", "-ar", "8000",
                            "-f", "f32le", "-"], capture_output=True).stdout
        return np.frombuffer(b, np.float32)
    ds = []
    for k, off in enumerate(a.offsets, 1):
        x = pcm8k(os.path.join(a.folder, "%d.mp3" % k), 0, 20); w0 = max(off - 2, 0); y = pcm8k(a.render, w0, 24)
        m = len(x) + len(y)
        c = np.fft.irfft(np.fft.rfft(y, 2 * m) * np.conj(np.fft.rfft(x, 2 * m)), 2 * m)[: len(y)]
        ds.append(w0 + int(np.argmax(c)) / 8000 - off)
    a.delay = float(np.median(ds))
    print("成片里配音的偏差: " + ", ".join("%+.1f ms" % (1000 * d) for d in ds))
    if max(abs(d - a.delay) for d in ds) > 0.05:
        raise SystemExit("!! 各段偏差不一致：成片和时间线对不上（导出后又改过剪辑？），先确认成片是最新的")
a.delay = a.delay or 0.0
if not a.out:
    if not a.render:
        raise SystemExit("要给 --render 或 --out")
    a.out = os.path.splitext(a.render)[0] + ".srt"

# ---------- 音节估计（和 tts_check_speed.py 一致） ----------
def num_syl(tok):
    if "." in tok:
        x, y = tok.split(".", 1); return num_syl(x or "0") + 1 + len(y)
    v = int(tok)
    if len(tok) == 4 and 1900 <= v <= 2100: return 4
    if v < 11: return 1
    if v < 20: return 2
    if v < 100: return 2 if v % 10 == 0 else 3
    if v < 1000: return 3 + (0 if v % 100 == 0 else 2)
    return len(tok) * 2
SPECIAL = {"mm": 2, "lp": 2, "pro": 1, "gm": 2, "art": 1, "ii": 1, "dg": 2, "dn": 2, "za": 2, "spot": 1, "spt": 3, "zemax": 2,
           "claude": 1, "github": 2, "powerpoint": 3, "field": 1, "vs": 3, "lens": 1, "bench": 1, "mp": 2, "nd": 2, "vd": 2,
           "bfl": 3, "mcp": 3, "ed": 2, "fds": 3, "nbfd": 4, "cdgm": 4, "mnbfd": 5}
def eng_syl(w):
    lw = w.lower()
    if lw in SPECIAL: return SPECIAL[lw]
    if w.isupper() and len(w) <= 5: return len(w)
    return max(1, len(re.findall(r"[aeiouy]+", lw)))
def syl(t):
    n = 0
    for m in re.finditer(r"[一-鿿]|\d+(?:\.\d+)?|[A-Za-z]+", t):
        g = m.group(0)
        n += 1 if "一" <= g[0] <= "鿿" else (num_syl(g) if g[0].isdigit() else eng_syl(g))
    return n
def width(t):
    return sum(1.0 if ord(c) > 0x2E80 else 0.55 for c in t)

# ---------- 音频 -> 每 10 ms 的响度 ----------
def envelope(path):
    raw = subprocess.run([FF, "-v", "quiet", "-i", path, "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"], capture_output=True).stdout
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    n = len(x) // HOP
    rms = np.sqrt((x[: n * HOP].reshape(n, HOP) ** 2).mean(1) + 1e-12)
    return 20 * np.log10(rms + 1e-9)

def chunks_in(voiced, f0, f1, min_pause=12, min_len=6):
    """[f0,f1) 帧内的语音段（停顿 >= min_pause 帧才断开）。"""
    out, i = [], f0
    while i < f1:
        if not voiced[i]:
            i += 1; continue
        j = i
        while j < f1:
            if voiced[j]: j += 1; continue
            k = j
            while k < f1 and not voiced[k]: k += 1
            if k - j >= min_pause or k >= f1: break
            j = k
        if j - i >= min_len: out.append([i, j])
        i = j
    return out

PUNCT = "，。；：？！"
def phrases_of(par):
    ps = [p for p in re.findall(r"[^%s]+[%s]*" % (PUNCT, PUNCT), par) if p.strip()]
    return [p for p in ps if syl(p) > 0] or [par]

def align(ph, ch):
    """短句 <-> 语音段 的分组对齐（每组 1-4 : 1-4），返回 [(i0,i1,j0,j1)]。"""
    sy = [syl(p) for p in ph]
    sp = [c[1] - c[0] for c in ch]
    r0 = sum(sp) / max(1, sum(sy))
    INF = float("inf"); NP, NC = len(ph), len(ch)
    D = [[INF] * (NC + 1) for _ in range(NP + 1)]; B = [[None] * (NC + 1) for _ in range(NP + 1)]
    D[0][0] = 0
    for i in range(NP + 1):
        for j in range(NC + 1):
            if D[i][j] == INF: continue
            for x in range(1, 5):
                for y in range(1, 5):
                    if i + x > NP or j + y > NC: continue
                    s = sum(sy[i:i + x]); d = sum(sp[j:j + y])
                    c = math.log((d / max(s, 0.5)) / r0) ** 2 + 0.12 * (x - 1) + 0.2 * (y - 1)
                    if x == 1:   # 一个短句中间很少停 0.35 秒以上，长停顿多半是句与句之间
                        c += sum(2.0 * max(0, (ch[t + 1][0] - ch[t][1]) / 100 - 0.35) for t in range(j, j + y - 1))
                    if D[i][j] + c < D[i + x][j + y]:
                        D[i + x][j + y] = D[i][j] + c; B[i + x][j + y] = (x, y)
    if D[NP][NC] == INF:
        return None
    i, j, g = NP, NC, []
    while i or j:
        x, y = B[i][j]; g.append((i - x, i, j - y, j)); i -= x; j -= y
    return g[::-1]

def split_times(db, f0, f1, weights, thr):
    """把 [f0,f1) 按权重（音节数）分成几段，切点吸附到附近最安静的帧。"""
    if len(weights) == 1:
        return [(f0, f1)]
    seg = db[f0:f1]
    voiced_idx = np.cumsum(seg > thr)                # 按"有声帧"累计分配，停顿不占份额
    tot = voiced_idx[-1] if len(voiced_idx) else 1
    cuts, acc = [], 0
    for w in weights[:-1]:
        acc += w
        target = acc / sum(weights) * tot
        k = int(np.searchsorted(voiced_idx, target))
        lo, hi = max(1, k - 15), min(len(seg) - 1, k + 15)
        k = lo + int(np.argmin(seg[lo:hi])) if hi > lo else k
        cuts.append(f0 + k)
    edges = [f0] + cuts + [f1]
    return [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]

BREAK_AFTER = set("的了是在和与对把被而就也都还又让从向将给比并或及但")
POST = set("时后上中里下前外")          # 方位词、"…时"：断在它们后面
BREAK_BEFORE = set("这那但而并就却")
def split_long(text, maxw):
    """太长的句子在中间附近找个好断点拆成两半（不拆开字母数字）。"""
    if width(clean(text)) <= maxw:
        return [text]
    tokens = re.findall(r"[A-Za-z0-9.\-]+(?: [A-Za-z0-9.\-]+)*|.", text)
    best, bi, pos = None, None, 0
    half = width(text) / 2
    for i in range(1, len(tokens)):
        pos += width(tokens[i - 1])
        prev = tokens[i - 1]
        nxt = tokens[i]
        score = abs(pos - half)
        good = False
        if prev[-1] == "、": score -= 4; good = True
        if prev[-1] in POST: score -= 2.5; good = True
        elif prev[-1] in BREAK_AFTER: score -= 2.0; good = True
        if nxt[0] in BREAK_BEFORE: score -= 2.0; good = True
        if prev.isascii() or nxt.isascii(): score -= 1.0; good = True
        if not good: score += 1.5            # 两个普通汉字中间，多半在词中间
        if prev[-1] in POST and nxt.isascii(): score += 2.5    # "…上 | AF" 不如别处
        if prev in "“「（" or tokens[i] in "”」）、": score += 99
        if best is None or score < best: best, bi = score, i
    left, right = "".join(tokens[:bi]).strip(), "".join(tokens[bi:]).strip()
    return split_long(left, maxw) + split_long(right, maxw)

def clean(t):
    t = t.strip()
    while t and t[-1] in "，。、,.": t = t[:-1]
    return t

# ---------- 主流程 ----------
rows = list(csv.DictReader(open(os.path.join(a.folder, "段落时间表.csv"), encoding="utf-8-sig")))
subs, report = [], []
for k, off in enumerate(a.offsets, 1):
    mp3 = os.path.join(a.folder, "%d.mp3" % k)
    txt = glob.glob(os.path.join(a.folder, "文本", "%d_*.txt" % k))[0]
    pars = [l.strip() for l in open(txt, encoding="utf-8") if l.strip()]
    R = [r for r in rows if r["文件"] == "%d.mp3" % k]
    assert len(R) == len(pars), (k, len(R), len(pars))
    db = envelope(mp3)
    thr = max(-50.0, np.sort(db)[int(0.9 * len(db))] - 32)
    voiced = db > thr
    for pi, (par, r) in enumerate(zip(pars, R)):
        f0 = max(0, int(float(r["开始秒"]) * 100) - 10); f1 = min(len(db), int(float(r["结束秒"]) * 100) + 10)
        ph = phrases_of(par)
        ch = chunks_in(voiced, f0, f1)
        g = align(ph, ch)
        if g is None:
            raise SystemExit("对不上: %d.mp3 段 %d" % (k, pi + 1))
        for i0, i1, j0, j1 in g:
            span0, span1 = ch[j0][0], ch[j1 - 1][1]
            # 一组里多个短句：按音节分时间；再把超长短句拆开，同样按音节分
            pieces = []
            for p in ph[i0:i1]:
                for q in split_long(p, a.maxw):
                    pieces.append(q)
            times = split_times(db, span0, span1, [max(1, syl(q)) for q in pieces], thr)
            for q, (t0, t1) in zip(pieces, times):
                # 收紧到有声的部分
                while t0 < t1 - 1 and not voiced[t0]: t0 += 1
                while t1 > t0 + 1 and not voiced[t1 - 1]: t1 -= 1
                subs.append({"mp3": k, "par": pi + 1, "text": q, "t0": t0 / 100 + off + a.delay, "t1": t1 / 100 + off + a.delay,
                             "syl": syl(q)})

# ---------- 合并过短的相邻短句（同一句话里、停顿很短、合起来不长） ----------
merged = []
for s in subs:
    if merged:
        m = merged[-1]
        same = m["mp3"] == s["mp3"] and m["par"] == s["par"] and m["text"].rstrip()[-1:] not in "。？！"
        short = min(width(clean(m["text"])), width(clean(s["text"]))) <= 4 or width(clean(m["text"]) + clean(s["text"])) <= 10
        if same and short and s["t0"] - m["t1"] < 0.35 and width(m["text"] + s["text"]) <= a.maxw:
            m["text"] += s["text"]; m["t1"] = s["t1"]; m["syl"] += s["syl"]
            continue
    merged.append(dict(s))

# ---------- 定出入点：提前 0.1 s 出现，晚 0.15 s 消失；间隔小于 0.3 s 就首尾相接 ----------
for i, s in enumerate(merged):
    s["in"] = s["t0"] - 0.10
    if i:
        s["in"] = max(s["in"], merged[i - 1]["t1"] + 0.02)
    s["out"] = s["t1"] + 0.15
for i, s in enumerate(merged):
    if i + 1 < len(merged):
        nxt = merged[i + 1]["in"]
        if nxt - s["out"] < 0.3: s["out"] = nxt
        s["out"] = min(s["out"], nxt)
    if s["out"] - s["in"] < 0.7: s["out"] = s["in"] + 0.7
snap = lambda x: round(x * 60) / 60                       # 对齐到 60 fps 的帧
def ts(x):
    ms = int(round(x * 1000)); h, ms = divmod(ms, 3600000); m, ms = divmod(ms, 60000); s, ms = divmod(ms, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)
with open(a.out, "w", encoding="utf-8") as f:
    for n, s in enumerate(merged, 1):
        s["in"], s["out"] = snap(s["in"]), snap(s["out"])
        f.write("%d\n%s --> %s\n%s\n\n" % (n, ts(s["in"]), ts(s["out"]), clean(s["text"])))
if a.json:
    json.dump(merged, open(a.json, "w"), ensure_ascii=False, indent=0)
W = [width(clean(s["text"])) for s in merged]
rate = [s["syl"] / max(0.05, s["t1"] - s["t0"]) for s in merged]
print("字幕 %d 条，宽度 中位 %.0f p90 %.0f 最宽 %.0f；语速 中位 %.2f 音节/秒" % (
    len(merged), np.median(W), np.percentile(W, 90), max(W), np.median(rate)))
odd = [(s, r) for s, r in zip(merged, rate) if r > 8.0 or r < 3.5]
print("语速异常（可能对错了位置）: %d 条" % len(odd))
for s, r in odd[:30]:
    print("  %7.2f-%7.2f  %4.1f/s  %s" % (s["t0"], s["t1"], r, clean(s["text"])))
