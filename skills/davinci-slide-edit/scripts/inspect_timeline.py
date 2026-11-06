"""打印当前工程/时间线的结构：帧率、每条轨道的片段（相对起点的位置、时长、源入点）、媒体池。
用法: python3 inspect_timeline.py [时间线名]"""
import sys
from resolve_common import connect, timelines, pool_clips, tl_fps

r, p, mp = connect()
t = timelines(p)[sys.argv[1]] if len(sys.argv) > 1 else p.GetCurrentTimeline()
st = t.GetStartFrame()
print(f"工程: {p.GetName()}  时间线: {t.GetName()}  fps={tl_fps(t)}  "
      f"分辨率={t.GetSetting('timelineResolutionWidth')}x{t.GetSetting('timelineResolutionHeight')}  "
      f"长度={(t.GetEndFrame()-st)/tl_fps(t):.2f}s")
for kind in ("video", "audio", "subtitle"):
    for i in range(1, t.GetTrackCount(kind) + 1):
        items = t.GetItemListInTrack(kind, i) or []
        print(f"== {kind} {i} '{t.GetTrackName(kind, i)}'  {len(items)} 个片段")
        for n, it in enumerate(items):
            mpi = it.GetMediaPoolItem()
            src = mpi.GetClipProperty("File Path") if mpi else ""
            print(f"  [{n}] rel={it.GetStart()-st} dur={it.GetDuration()} srcIn={it.GetLeftOffset()} "
                  f"'{it.GetName()}' {src}")
print("== 媒体池")
for name, c in pool_clips(mp).items():
    pr = c.GetClipProperty()
    print(f"  {name}  type={pr.get('Type')} fps={pr.get('FPS')} dur={pr.get('Duration')} "
          f"res={pr.get('Resolution')} ch={pr.get('Audio Ch')}  {pr.get('File Path')}")
print("== 所有时间线:", list(timelines(p)))
