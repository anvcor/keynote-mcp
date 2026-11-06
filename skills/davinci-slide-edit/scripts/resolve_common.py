"""连接 DaVinci Resolve 脚本 API 的公共函数（macOS 默认安装路径）。"""
import os, sys, shutil, subprocess, json

API = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
LIB = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
os.environ.setdefault("RESOLVE_SCRIPT_API", API)
os.environ.setdefault("RESOLVE_SCRIPT_LIB", LIB)
sys.path.append(os.path.join(os.environ["RESOLVE_SCRIPT_API"], "Modules"))


def connect():
    """返回 (resolve, project, mediapool)。"""
    import DaVinciResolveScript as dvr
    r = dvr.scriptapp("Resolve")
    if r is None:
        raise SystemExit("连不上 Resolve：确认已打开工程，且 偏好设置 > 系统 > 常规 > 外部脚本使用 = 本地(Local)")
    p = r.GetProjectManager().GetCurrentProject()
    return r, p, p.GetMediaPool()


def timelines(p):
    return {p.GetTimelineByIndex(i).GetName(): p.GetTimelineByIndex(i)
            for i in range(1, p.GetTimelineCount() + 1)}


def pool_clips(mp):
    """媒体池里所有片段（递归子文件夹），name -> MediaPoolItem。"""
    out = {}
    def walk(f):
        for c in f.GetClipList() or []:
            out.setdefault(c.GetName(), c)
        for s in f.GetSubFolderList() or []:
            walk(s)
    walk(mp.GetRootFolder())
    return out


def tl_fps(t):
    # 注意：project.GetSetting('timelineFrameRate') 可能和时间线本身不一致，一律读时间线的
    return float(t.GetSetting("timelineFrameRate"))


def ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "--user", "-q", "imageio-ffmpeg"], check=True)
        import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def media_duration(path):
    """用 ffmpeg 读时长（秒）。"""
    r = subprocess.run([ffmpeg(), "-hide_banner", "-i", path], capture_output=True, text=True)
    for line in r.stderr.splitlines():
        line = line.strip()
        if line.startswith("Duration:"):
            h, m, s = line.split(",")[0].split()[1].split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    raise RuntimeError("读不到时长: " + path)
