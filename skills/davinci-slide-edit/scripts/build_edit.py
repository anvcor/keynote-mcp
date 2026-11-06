"""按剪辑计划 plan.json 把幻灯片视频 + 配音铺到 Resolve 时间线上。

用法:
  python3 build_edit.py plan.json            # 预演：只计算、检查、出预览图，不碰时间线
  python3 build_edit.py plan.json --apply    # 执行：备份时间线 → 删掉要重铺的片段 → 铺 → 校验 → 保存工程

plan.json 字段（时间 t/at 都是"相对时间线起点的秒"；src/帧号都是源视频帧）:
{
  "timeline": "Timeline 1",
  "video_clip": "xxx.mov",               媒体池片段名
  "holds": "holds.json",                 analyze_slides.py 的输出（相对 plan 所在目录）
  "keep_video_items": 22,                V1 前 N 个片段是用户已剪好的，原样保留
  "start": {"at_frame": 1197, "src": 1039, "slide": 5},
        从时间线相对帧 at_frame 开始铺，第一段从源帧 src 播到 slide 的静止画面
        （从头剪: {"at_frame":0,"src":0,"slide":1}）
  "cues": [{"t": 52.18, "slide": 7, "via": [6]}, ...],
        到 t 秒开始转场到 slide；via 是途经的空白/过渡 hold（每个只留 blank_keep*2 帧）
        跳页（文稿顺序和幻灯片顺序不同，目标页不挨着当前页）:
        {"t": 485.2, "slide": 59, "via": [54], "via_in": 58}
        via[0] = 当前页后面的空白页，via_in = 目标页前面的空白页：先淡出到前一张空白，
        再从后一张空白淡入目标页；两张空白画面一样，中间那一刀看不出来
  "end": {"t": 417.2, "fade_to": 48, "tail": 30},   最后一页停到 t，转到 fade_to 再留 tail 帧
  "margin": 3,           取静止画面时两头各让开几帧，避开转场残影
  "blank_keep": 8,
  "audio": {"track": 1, "replace": true,
            "segments": [{"clip": "1.mp3", "from": 0, "to": 125.5, "at": 0}, ...]}
        from/to 是音频文件内的秒（to=null 到文件尾）；at 是放到时间线上的秒。
        replace=true 时先清空该音频轨。注意：重铺音频会丢掉片段上的音量等设置，要重新设。
}
"""
import argparse, json, os, subprocess, datetime
from resolve_common import connect, timelines, pool_clips, tl_fps, ffmpeg, media_duration

ap = argparse.ArgumentParser()
ap.add_argument("plan")
ap.add_argument("--apply", action="store_true")
ap.add_argument("--no-preview", action="store_true")
a = ap.parse_args()
base = os.path.dirname(os.path.abspath(a.plan))
P = json.load(open(a.plan))
H = json.load(open(os.path.join(base, P.get("holds", "holds.json"))))
holds = {h["i"]: h for h in H["holds"]}
M, BK = P.get("margin", 3), P.get("blank_keep", 8)
s = lambda k: holds[k]["start"]
e = lambda k: holds[k]["end"]
ia = lambda k: s(k) + M          # 静止画面可用区间 [ia, ib)
ib = lambda k: e(k) - M

r, p, mp = connect()
t = timelines(p)[P["timeline"]]
fps, st = tl_fps(t), t.GetStartFrame()
if abs(fps - H["fps"]) > 0.01:
    print(f"!! 时间线 {fps}fps 与源视频 {H['fps']}fps 不同，源帧和时间线帧不再一一对应，本脚本假设相同")
F = lambda sec: round(sec * fps)

# ---------- 计算视频片段 ----------
clips = []          # (时间线相对帧, 源入, 源出[不含])
pos = P["start"]["at_frame"]
warn = []


def add(x, y):
    global pos
    if y <= x:
        raise SystemExit(f"源区间为空 {x}-{y}，检查 hold 编号/via")
    clips.append((pos, x, y)); pos += y - x


def hold(k, until):
    if until < pos:
        warn.append(f"hold {k}: 下一个转场({until/fps:.2f}s)早于本页转场结束({pos/fps:.2f}s)，被挤掉 {(pos-until)/fps:.2f}s")
        return
    while pos < until:   # 静止画面不够长就重复铺同一段（画面静止，接缝看不出）
        add(ia(k), min(ib(k), ia(k) + until - pos))


def block(A, via, B):
    x = ib(A)
    for K in via:
        add(x, s(K) + BK); x = e(K) - BK
    add(x, ia(B))


def jump(A, out_blank, in_blank, B):
    """不相邻的两页：淡出到 A 后面的空白页，切到 B 前面的空白页（画面相同），再淡入 B。"""
    for K in (out_blank, in_blank):
        if not holds[K]["blank"]:
            raise SystemExit(f"跳页要经过空白页，hold {K} 不是空白页")
    add(ib(A), s(out_blank) + BK)
    add(e(in_blank) - BK, ia(B))


add(P["start"]["src"], ia(P["start"]["slide"]))
cur = P["start"]["slide"]
for c in P["cues"]:
    hold(cur, F(c["t"]))
    if c.get("via_in") is not None:
        jump(cur, c["via"][0], c["via_in"], c["slide"])
    else:
        block(cur, c.get("via", []), c["slide"])
    cur = c["slide"]
end = P["end"]
hold(cur, F(end["t"]))
add(ib(cur), s(end["fade_to"]) + end.get("tail", 30))

# ---------- 检查 ----------
def src_at(rel):
    for q, x, y in clips:
        if q <= rel < q + y - x:
            return x + rel - q


print(f"视频: {len(clips)} 段, 从 {P['start']['at_frame']/fps:.2f}s 铺到 {pos/fps:.2f}s")
print(" cue    t(s)  slide  该页完整显示时段")
for n, c in enumerate(P["cues"]):
    k = c["slide"]
    nxt = F(P["cues"][n + 1]["t"]) if n + 1 < len(P["cues"]) else pos
    vis = [q for q in range(F(c["t"]), nxt) if ia(k) <= (src_at(q) or -1) < ib(k)]
    span = f"{vis[0]/fps:7.2f}-{vis[-1]/fps:7.2f}s ({len(vis)/fps:.2f}s)" if vis else "—— 没出现！"
    print(f" {n:3d} {c['t']:7.2f}  {k:4d}   {span}")
    if len(vis) < fps * 1.0:
        warn.append(f"cue {n} (slide {k}) 完整显示不足 1 秒")
json.dump(clips, open(os.path.join(base, "plan_clips.json"), "w"))

aud = P.get("audio")
ainfo = []
if aud:
    pc = pool_clips(mp)
    for sg in aud["segments"]:
        c = pc[sg["clip"]]
        cf = float(c.GetClipProperty("FPS") or 24)          # 音频片段的"帧"按它自己的 FPS 属性算（mp3 常见 24）
        to = sg["to"] if sg.get("to") is not None else media_duration(c.GetClipProperty("File Path"))
        ainfo.append({"mediaPoolItem": c, "startFrame": round(sg["from"] * cf), "endFrame": int(to * cf) - 1,
                      "mediaType": 2, "trackIndex": aud.get("track", 1), "recordFrame": st + F(sg["at"])})
        print(f"音频: {sg['clip']} {sg['from']:.2f}-{to:.2f}s  -> 时间线 {sg['at']:.2f}-{sg['at']+to-sg['from']:.2f}s")
    segs = sorted(aud["segments"], key=lambda g: g["at"])
for w in warn:
    print("!!", w)

v = t.GetItemListInTrack("video", 1) or []
keep = P["keep_video_items"]
cur_items = [(it.GetStart() - st, it.GetLeftOffset(), it.GetLeftOffset() + it.GetDuration()) for it in v[keep:]]
if len(v) >= keep and keep and v[keep - 1].GetEnd() - st != P["start"]["at_frame"]:
    print(f"!! 保留的第 {keep} 个片段结束于 {v[keep-1].GetEnd()-st}，和 start.at_frame={P['start']['at_frame']} 对不上")
print(f"V1 现有 {len(v)} 段：保留前 {keep} 段，将替换后面 {len(cur_items)} 段"
      + ("（与计划完全相同）" if cur_items == [list(x) for x in clips] or cur_items == clips else ""))

# ---------- 预览图：每个 cue 后 1.6 秒的画面 ----------
if not a.no_preview:
    frames = [src_at(F(c["t"] + 1.6)) for c in P["cues"]]
    sel = "+".join(f"eq(n\\,{f})" for f in sorted(set(x for x in frames if x is not None)))
    vid = pool_clips(mp)[P["video_clip"]].GetClipProperty("File Path")
    pv = os.path.join(base, "preview"); os.makedirs(pv, exist_ok=True)
    for f in os.listdir(pv):
        os.remove(os.path.join(pv, f))
    ff = ffmpeg()
    subprocess.run([ff, "-v", "error", "-y", "-i", vid, "-vf", f"select='{sel}',scale=384:216,eq=brightness=0.08",
                    "-vsync", "0", os.path.join(pv, "src_%03d.jpg")], check=True)
    # ffmpeg 按源帧顺序出图；有跳页时和 cue 顺序不同，按 cue 顺序重新排一遍再拼
    uniq = sorted(set(x for x in frames if x is not None))
    import shutil
    n = 0
    for f in frames:
        if f is None:
            continue
        n += 1
        shutil.copy(os.path.join(pv, "src_%03d.jpg" % (uniq.index(f) + 1)), os.path.join(pv, "%03d.jpg" % n))
    rows = max(1, -(-n // 6))
    subprocess.run([ff, "-v", "error", "-y", "-i", os.path.join(pv, "%03d.jpg"), "-vf", f"tile=6x{rows}", "-frames:v", "1",
                    os.path.join(base, "preview_cues.jpg")], check=True)
    print("预览:", os.path.join(base, "preview_cues.jpg"), "（按 cue 顺序，每格是转场后 1.6 秒的画面）")

if not a.apply:
    print("预演结束，没有改动时间线。确认无误后加 --apply。")
    raise SystemExit

# ---------- 执行 ----------
bk = P["timeline"] + " 备份"
if bk in timelines(p):
    bk += datetime.datetime.now().strftime(" %m%d-%H%M%S")
p.SetCurrentTimeline(t)
print("备份时间线:", t.DuplicateTimeline(bk).GetName())
p.SetCurrentTimeline(t)
dele = v[keep:]
if aud and aud.get("replace"):
    dele += t.GetItemListInTrack("audio", aud.get("track", 1)) or []
if dele:
    print("删除旧片段:", len(dele), t.DeleteClips(dele, False))
vc = pool_clips(mp)[P["video_clip"]]
info = [{"mediaPoolItem": vc, "startFrame": x, "endFrame": y - 1,   # endFrame 是闭区间
         "mediaType": 1, "trackIndex": 1, "recordFrame": st + q} for q, x, y in clips] + ainfo
res = mp.AppendToTimeline(info) or []
print(f"铺入 {len(res)}/{len(info)}")
v = t.GetItemListInTrack("video", 1)
got = [(it.GetStart() - st, it.GetLeftOffset(), it.GetLeftOffset() + it.GetDuration()) for it in v[keep:]]
gaps = sum(1 for i in range(1, len(v)) if v[i].GetStart() != v[i - 1].GetEnd())
print("校验: 与计划一致" if got == clips else "!! 校验: 与计划不一致", f"| V1 缝隙/重叠 {gaps} 处")
print("保存工程:", r.GetProjectManager().SaveProject())
