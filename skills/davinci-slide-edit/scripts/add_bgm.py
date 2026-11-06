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
  ]
      at = 这一章开始的时间线秒（一般是章节卡所在处）；每首放到下一首的 at 为止，最后一首到 end
      skip = 跳过曲子开头几秒；不写就自动跳过开头的静音
      title = 生成文件名里的曲名；不写就用 src 的文件名
}
淡出在下一首开始时正好结束，所以章节之间没有重叠也没有空隙。

片段音量（-15 dB）API 改不了，铺完在界面里设，见 references/resolve-notes.md「音频界面操作」。
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


def render(P, base):
    ff = ffmpeg()
    out = os.path.join(base, P.get("out", "音乐"))
    os.makedirs(out, exist_ok=True)
    target, fi, fo = P.get("target_lufs", -21.7), P.get("fade_in", 1.5), P.get("fade_out", 2.5)
    pcs = P["pieces"]
    layout = []
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
        dst = os.path.join(out, "%d_%s_%s.flac" % (n, pc["name"], title))
        af = "volume=%sdB,afade=t=in:st=0:d=%s,afade=t=out:st=%.3f:d=%s" % (gain, fi, dur - fo, fo)
        subprocess.run([ff, "-v", "error", "-y", "-ss", "%.3f" % skip, "-i", src, "-vn", "-t", "%.3f" % dur, "-af", af,
                        "-ar", "48000", "-ac", "2", "-sample_fmt", "s32", "-c:a", "flac", dst], check=True)
        got, peak = loudness(ff, dst)
        warn = "  !! 峰值过高，可能削波" if peak is not None and peak > -0.1 else ""
        print("%-12s %7.2f-%7.2f s (%5.1f s)  跳过 %.2f s  原 %.1f LUFS %+5.1f dB -> %.1f LUFS  峰值 %.1f dBFS%s"
              % (pc["name"], t0, t1, dur, skip, lufs, gain, got, peak, warn))
        layout.append({"file": dst, "at": t0, "dur": dur, "src": src, "skip": round(skip, 2), "gain_db": gain, "lufs": got})
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
    print("保存工程:", r.GetProjectManager().SaveProject())
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
