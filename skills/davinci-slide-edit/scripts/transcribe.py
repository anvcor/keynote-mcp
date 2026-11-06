"""用 Resolve Studio 自带的语音转字幕，给配音打时间码（句级）。
每个音频单独建一条临时时间线 → CreateSubtitlesFromAudio → 读字幕条 → 删掉临时时间线。
不会动用户的时间线。

用法: python3 transcribe.py --out 工作目录 --clips 1.mp3 2.mp3 [--import /路径/2.mp3 ...]
  --clips   媒体池里的片段名
  --import  媒体池里还没有的文件，先导入到根目录（导入是保留的，后面剪辑要用）
输出: 工作目录/asr_<片段名>.tsv  每行: 起(秒)  止(秒)  文本
注意: 识别结果可能是繁体、有同音错字（"仿真"→"紡針"），只用来对时间，别当字幕用。
"""
import argparse, os
from resolve_common import connect, pool_clips, timelines, tl_fps

ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True)
ap.add_argument("--clips", nargs="+", required=True)
ap.add_argument("--import", dest="imports", nargs="*", default=[])
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)
r, p, mp = connect()
orig = p.GetCurrentTimeline()
if a.imports:
    have = pool_clips(mp)
    todo = [f for f in a.imports if os.path.basename(f) not in have]
    if todo:
        mp.SetCurrentFolder(mp.GetRootFolder())
        print("导入:", todo, bool(mp.ImportMedia(todo)))
clips = pool_clips(mp)
for name in a.clips:
    tname = "tmp_asr_" + name
    old = timelines(p).get(tname)
    if old:
        mp.DeleteTimelines([old])
    tl = mp.CreateEmptyTimeline(tname)
    p.SetCurrentTimeline(tl)
    try:
        mp.AppendToTimeline([clips[name]])
        ok = tl.CreateSubtitlesFromAudio({})
        fps, st = tl_fps(tl), tl.GetStartFrame()
        items = tl.GetItemListInTrack("subtitle", 1) or []
        path = os.path.join(a.out, f"asr_{name}.tsv")
        with open(path, "w") as f:
            for it in items:
                f.write(f"{(it.GetStart()-st)/fps:.3f}\t{(it.GetEnd()-st)/fps:.3f}\t{it.GetName()}\n")
        print(f"{name}: 识别={ok}, {len(items)} 句 -> {path}")
    finally:
        p.SetCurrentTimeline(orig)
        mp.DeleteTimelines([tl])
