"""背景音乐：每章一首，裁到章节长度、对齐响度、加淡入淡出，铺到时间线的音乐轨（默认 A2）。

用法:
  python3 add_bgm.py --measure 曲库文件夹      # 量文件夹里每首的响度和平均值（定 target_lufs 用）
  python3 add_bgm.py bgm.json                 # 生成每章的 FLAC 并核对响度，不碰时间线
  python3 add_bgm.py bgm.json --apply         # 再导入媒体池「背景音乐」文件夹、铺到音乐轨、保存工程
  python3 add_bgm.py bgm.json --apply --replace   音乐轨上已有片段时先删掉再铺

bgm.json:
{
  "timeline": "Timeline 1",
  "track": 2,                  音乐轨；不够就新建立体声轨
  "target_lufs": -21.7,        作者曲库的平均响度：这样片段音量统一设 -15 dB 时，听感和他以前的配乐一样
  "out": "音乐",               生成的 FLAC 放哪（相对 bgm.json 所在目录）
  "fade_in": 1.5, "fade_out": 2.5,
  "end": 976.18,               最后一首停在哪（时间线秒），一般是 V1 的结尾
  "pieces": [
    {"at": 0,      "name": "开场", "src": "/路径/德彪西 - 阿拉伯风格曲 第1号.ogg"},
    {"at": 80.166, "name": "一索尼与适马", "src": "...", "skip": 1.8}
  ],
      at = 这一章开始的时间线秒（一般是章节卡所在处）；每首放到下一首的 at 为止，最后一首到 end
      skip = 跳过曲子开头几秒；不写就自动跳过开头的静音
      title = 生成文件名里的曲名；不写就用 src 的文件名
  "duck": {"voice_track": 1, "voice_db": -15, "open_db": 0, "ramp": 1.0, "margin": 0.2, "min_gap": 2.0}
      可选。把音量曲线直接做进文件：有配音的地方 voice_db，配音之间的空档（章节卡，>= min_gap 秒）
      回到 open_db（原来的响度），离开配音 margin 秒后用 ramp 秒升上去，下一句开口前 margin 秒降回来。
      配音区间默认从时间线的 A{voice_track} 读（要开着 Resolve）；也可以写 "voice": [[开始, 结束], ...]（时间线秒）。
      有 duck 时片段音量在界面里保持 0 dB，不要再设 -15。
}
淡出在下一首开始时正好结束，所以章节之间没有重叠也没有空隙。

不用 duck 时，片段音量（-15 dB）API 改不了，铺完在界面里设，见 references/resolve-notes.md「音频界面操作」。
"""
import argparse, json, os, re, subprocess
from resolve_common import ffmpeg, media_duration

AUDIO_EXT = (".mp3", ".flac", ".ogg", ".wav", ".m4a", ".aac", ".aif", ".aiff", ".opus")


def loudness(ff, path):
    """(响度 LUFS, 真峰值 dBFS)。-vn：mp3 的封面图会让 ebur128 出错。"""
    r = subprocess.run([ff, "-hide_banner", "-nostats", "-i", path, "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True).stderr
    i = re.findall(r"I:\s+(-?[\d.]+) LUFS", r)
    p = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", r)
    if not i:
        raise RuntimeError("量不出响度: " + path)
    return float(i[-1]), float(p[-1]) if p else None


def lead_silence(ff, path):
    r = subprocess.run([ff, "-hide_banner", "-nostats", "-i", path, "-vn", "-t", "20", "-af", "silencedetect=n=-50dB:d=0.3",
                        "-f", "null", "-"], capture_output=True, text=True).stderr
    m = re.search(r"silence_start: (-?[\d.]+)\n.*?silence_end: ([\d.]+)", r, re.S)
    return float(m.group(2)) if m and float(m.group(1)) <= 0.05 else 0.0


def measure(folder):
    ff = ffmpeg()
    vals = []
    for f in sorted(os.listdir(folder)):
        if f.lower().endswith(AUDIO_EXT) and not f.startswith("._"):
            i, p = loudness(ff, os.path.join(folder, f))
            vals.append(i)
            print("%6.1f LUFS  峰值 %5.1f dBFS  %s" % (i, p if p is not None else 0, f))
    if vals:
        print("共 %d 首，平均 %.1f LUFS（作 target_lufs）" % (len(vals), sum(vals) / len(vals)))


def voice_spans(P):
    """配音所在的时间线区间 [(开始秒, 结束秒)]，相邻的合并。"""
    D = P["duck"]
    if D.get("voice"):
        spans = sorted((float(a), float(b)) for a, b in D["voice"])
    else:
        from resolve_common import connect, timelines, tl_fps
        r, p, mp = connect()
        t = timelines(p)[P["timeline"]]
        fps, st = tl_fps(t), t.GetStartFrame()
        spans = sorted(((x.GetStart() - st) / fps, (x.GetEnd() - st) / fps)
                       for x in t.GetItemListInTrack("audio", D.get("voice_track", 1)) or [])
        if not spans:
            raise SystemExit("A%d 上没有配音片段，没法算压低区间" % D.get("voice_track", 1))
    merged = []
    for a, b in spans:
        if merged and a - merged[-1][1] < 0.05:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    return merged


def open_windows(spans, end, min_gap):
    """没有配音、且长于 min_gap 的空档（章节卡）。开头和结尾的空档也算。"""
    w, prev = [], 0.0
    for a, b in spans:
        if a - prev >= min_gap:
            w.append((prev, a))
        prev = max(prev, b)
    if end - prev >= min_gap:
        w.append((prev, end))
    return w


def duck_expr(D, windows, t0, t1, end):
    """这一段（时间线 t0-t1）的音量表达式，ffmpeg 的 t 从 0 开始 = 时间线 t0。返回 (表达式, 涉及的空档)。"""
    lo, hi = float(D.get("voice_db", -15)), float(D.get("open_db", 0))
    ramp, margin = float(D.get("ramp", 1.0)), float(D.get("margin", 0.2))
    terms, used = [], []
    for a, b in windows:
        if b <= t0 or a >= t1:
            continue
        # 开头的空档不用升（一开始就是原响度），结尾的空档不用降
        u = (a + margin) if a > 0 else (a - ramp - 1)
        d = (b - margin) if b < end - 1e-3 else (b + ramp + 1)
        r = min(ramp, max((d - u) / 2, 0.05))
        u, d = u - t0, d - t0
        terms.append("min(1,max(0,(t-(%.3f))/%.3f))*min(1,max(0,((%.3f)-t)/%.3f))" % (u, r, d, r))
        used.append((a, b))
    if not terms:
        return "%.2f" % lo, used
    return "%.2f+(%.2f)*min(1,%s)" % (lo, hi - lo, "+".join(terms)), used


def render(P, base):
    ff = ffmpeg()
    out = os.path.join(base, P.get("out", "音乐"))
    os.makedirs(out, exist_ok=True)
    target, fi, fo = P.get("target_lufs", -21.7), P.get("fade_in", 1.5), P.get("fade_out", 2.5)
    pcs = P["pieces"]
    layout = []
    D = P.get("duck")
    windows = []
    if D:
        windows = open_windows(voice_spans(P), P["end"], float(D.get("min_gap", 2.0)))
        print("压低：有配音处 %s dB，以下空档回到 %s dB（前后 %s s 渐变，离配音 %s s）："
              % (D.get("voice_db", -15), D.get("open_db", 0), D.get("ramp", 1.0), D.get("margin", 0.2)))
        for a, b in windows:
            print("   %8.2f - %8.2f s  (%.1f s)" % (a, b, b - a))
    for n, pc in enumerate(pcs):
        t0 = pc["at"]
        t1 = pcs[n + 1]["at"] if n + 1 < len(pcs) else P["end"]
        dur = round(t1 - t0, 3)
        src = pc["src"]
        skip = pc.get("skip")
        if skip is None:
            skip = lead_silence(ff, src)
        src_len = media_duration(src)
        if src_len - skip < dur:
            print("!! %s：曲子去掉开头后只有 %.1f 秒，这一章要 %.1f 秒，最后 %.1f 秒没有音乐（换一首，或这一章用两首）"
                  % (pc["name"], src_len - skip, dur, dur - (src_len - skip)))
        lufs, _ = loudness(ff, src)
        gain = round(target - lufs, 1)
        title = pc.get("title") or os.path.splitext(os.path.basename(src))[0]
        # 压低版换个文件名：同名文件会被媒体池里的旧片段（和它的缓存）顶替
        dst = os.path.join(out, "%d_%s_%s%s.flac" % (n, pc["name"], title, "_压低" if D else ""))
        af = "volume=%sdB" % gain
        used = []
        if D:
            expr, used = duck_expr(D, windows, t0, t1, P["end"])
            # 256 个采样一帧（约 5 ms）逐帧算音量，渐变不会有台阶
            af += ",asetnsamples=n=256:p=0,volume='pow(10,(%s)/20)':eval=frame" % expr
        af += ",afade=t=in:st=0:d=%s,afade=t=out:st=%.3f:d=%s" % (fi, dur - fo, fo)
        subprocess.run([ff, "-v", "error", "-y", "-ss", "%.3f" % skip, "-i", src, "-vn", "-t", "%.3f" % dur, "-af", af,
                        "-ar", "48000", "-ac", "2", "-sample_fmt", "s32", "-c:a", "flac", dst], check=True)
        got, peak = loudness(ff, dst)
        warn = "  !! 峰值过高，可能削波" if peak is not None and peak > -0.1 else ""
        print("%-12s %7.2f-%7.2f s (%5.1f s)  跳过 %.2f s  原 %.1f LUFS %+5.1f dB -> %.1f LUFS  峰值 %.1f dBFS%s%s"
              % (pc["name"], t0, t1, dur, skip, lufs, gain, got, peak, warn,
                 ("  （含压低，空档 %s）" % ", ".join("%.1f-%.1f" % w for w in used)) if used else
                 ("  （全段压低）" if D else "")))
        layout.append({"file": dst, "at": t0, "dur": dur, "src": src, "skip": round(skip, 2), "gain_db": gain, "lufs": got,
                       "open_windows": used})
    json.dump(layout, open(os.path.join(out, "bgm_layout.json"), "w"), ensure_ascii=False, indent=1)
    return layout


def apply(P, layout, replace):
    from resolve_common import connect, timelines, tl_fps
    r, p, mp = connect()
    t = timelines(p)[P["timeline"]]
    p.SetCurrentTimeline(t)
    fps, st, tr = tl_fps(t), t.GetStartFrame(), P.get("track", 2)
    while t.GetTrackCount("audio") < tr:
        t.AddTrack("audio", "stereo")
    old = t.GetItemListInTrack("audio", tr) or []
    if old:
        if not replace:
            raise SystemExit("A%d 上已有 %d 个片段；确认要换掉就加 --replace" % (tr, len(old)))
        print("删除 A%d 旧片段:" % tr, len(old), t.DeleteClips(old, False))
    root = mp.GetRootFolder()
    sub = [f for f in (root.GetSubFolderList() or []) if f.GetName() == "背景音乐"]
    folder = sub[0] if sub else mp.AddSubFolder(root, "背景音乐")
    have = {c.GetClipProperty("File Path"): c for c in (folder.GetClipList() or [])}
    need = [L["file"] for L in layout if L["file"] not in have]
    if need:
        mp.SetCurrentFolder(folder)
        for c in mp.ImportMedia(need) or []:
            have[c.GetClipProperty("File Path")] = c
        mp.SetCurrentFolder(root)
    infos = []
    for L in layout:
        it = have[L["file"]]
        cf = float(it.GetClipProperty("FPS") or 24)       # 音频片段的"帧"按它自己的 FPS 属性算
        infos.append({"mediaPoolItem": it, "startFrame": 0, "endFrame": int(L["dur"] * cf) - 1, "mediaType": 2,
                      "trackIndex": tr, "recordFrame": st + round(L["at"] * fps)})
    res = mp.AppendToTimeline(infos) or []
    print("铺入 %d/%d" % (len(res), len(infos)))
    for x in t.GetItemListInTrack("audio", tr) or []:
        print("  A%d %-40s %8.2f - %8.2f s" % (tr, x.GetName(), (x.GetStart() - st) / fps, (x.GetEnd() - st) / fps))
    # 媒体池「背景音乐」里本脚本以前生成、这次没用到的片段移出媒体池（磁盘上的文件不删）
    out_dir = os.path.dirname(layout[0]["file"]) if layout else None
    keep = {L["file"] for L in layout}
    stale = [c for c in (folder.GetClipList() or [])
             if out_dir and os.path.dirname(c.GetClipProperty("File Path") or "") == out_dir
             and c.GetClipProperty("File Path") not in keep]
    if stale and replace:
        print("媒体池移除旧版:", len(stale), mp.DeleteClips(stale))
    print("保存工程:", r.GetProjectManager().SaveProject())
    if P.get("duck"):
        print("音量已做进文件（有配音 %s dB、章节卡空档 %s dB）：A%d 片段在检查器里保持 0 dB，不要再设 -15"
              % (P["duck"].get("voice_db", -15), P["duck"].get("open_db", 0), tr))
    else:
        print("接着在界面里把 A%d 这几段的音量设为 -15 dB（多选后在检查器里改 Volume）" % tr)


ap = argparse.ArgumentParser()
ap.add_argument("plan", nargs="?")
ap.add_argument("--measure", metavar="文件夹")
ap.add_argument("--apply", action="store_true")
ap.add_argument("--replace", action="store_true")
a = ap.parse_args()
if a.measure:
    measure(a.measure)
elif a.plan:
    P = json.load(open(a.plan))
    lay = render(P, os.path.dirname(os.path.abspath(a.plan)))
    if a.apply:
        apply(P, lay, a.replace)
    else:
        print("只生成了音乐文件，没有改动时间线。确认无误后加 --apply。")
else:
    ap.print_help()
