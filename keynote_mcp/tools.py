"""MCP tool definitions and their Keynote implementations."""
import base64
import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid

from . import osa
from .mp4info import movie_info
from .osa import KeynoteError
from .script_md import parse as parse_script, tag_of

# ---------------------------------------------------------------- keywords --

EFFECTS = [
    "no transition effect", "magic move", "shimmer", "sparkle", "swing", "object cube",
    "object flip", "object pop", "object push", "object revolve", "object zoom",
    "perspective", "clothesline", "confetti", "dissolve", "drop", "droplet",
    "fade through color", "grid", "iris", "move in", "push", "reveal", "switch", "wipe",
    "blinds", "color planes", "cube", "doorway", "fall", "flip", "flop", "mosaic",
    "page flip", "pivot", "reflection", "revolving door", "scale", "swap", "swoosh",
    "twirl", "twist", "fade and move",
]
EFFECT_ALIASES = {
    "none": "no transition effect", "无": "no transition effect", "无转场": "no transition effect",
    "magic_move": "magic move", "magicmove": "magic move", "神奇移动": "magic move",
    "fade": "dissolve", "溶解": "dissolve", "淡入淡出": "dissolve",
    "fade_through_color": "fade through color", "fade_and_move": "fade and move",
}
MOVIE_FORMATS = {"360p": "format360p", "540p": "format540p", "720p": "format720p",
                 "1080p": "format1080p", "2160p": "format2160p", "4k": "format2160p",
                 "native": "native size"}
CODECS = {"h264": "h264", "hevc": "HEVC", "h265": "HEVC", "prores422": "AppleProRes422",
          "prores4444": "AppleProRes4444", "prores422lt": "AppleProRes422LT",
          "prores422hq": "AppleProRes422HQ", "prores422proxy": "AppleProRes422Proxy"}
FPS = {"12": "FPS12", "23.98": "FPS2398", "24": "FPS24", "25": "FPS25", "29.97": "FPS2997",
       "30": "FPS30", "50": "FPS50", "59.94": "FPS5994", "60": "FPS60"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".gif", ".heic", ".bmp", ".pdf", ".svg", ".webp"}
MOVIE_OFF = ("视频不由工具导出：请让用户先在 Keynote 里审核效果，再手动导出（文件 > 导出到 > 影片）。"
             "确实需要自动导出时，在配置里设置 KEYNOTE_MCP_ALLOW_MOVIE=1。")
CLICK_SLIDE_SECONDS = 5.0
# House style measured from the user's 78 decks (6535 slides): Basic Black, 1920x1080,
# 95% of slides use magic move, mostly 0.5 s, all advanced on click.
HOUSE_THEME = "Basic Black"
HOUSE_RED = "#FC4B3E"  # the red used for F-number digits in the user's titles
HOUSE_DURATION = 0.5  # what Keynote's movie export uses for click-advance slides (measured on 14.4)


def _house_transition(effect="magic move", duration=None):
    """AppleScript record for a new slide's transition (default: the user's 0.5 s magic move)."""
    eff = effect_keyword(effect or "magic move")
    if eff == "no transition effect":
        return "{transition effect:no transition effect}"
    dur = HOUSE_DURATION if duration is None else duration
    return "{transition effect:%s, transition duration:%s}" % (eff, num(dur, "duration", lo=0.05, hi=60))


def effect_keyword(name):
    n = str(name).strip()
    key = EFFECT_ALIASES.get(n.lower(), EFFECT_ALIASES.get(n, n.lower().replace("_", " ")))
    if key not in EFFECTS:
        raise ValueError("不支持的转场效果：%s。可用：%s" % (name, ", ".join(EFFECTS)))
    return key


# ----------------------------------------------------------------- helpers --

def num(v, name, lo=None, hi=None, integer=False):
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError("%s 必须是数字（收到 %r）" % (name, v))
    if f != f or f in (float("inf"), float("-inf")):
        raise ValueError("%s 必须是有限数字" % name)
    if lo is not None and f < lo:
        raise ValueError("%s 不能小于 %s" % (name, lo))
    if hi is not None and f > hi:
        raise ValueError("%s 不能大于 %s" % (name, hi))
    if integer:
        return str(int(round(f)))
    return ("%.4f" % f).rstrip("0").rstrip(".") if not f.is_integer() else "%.1f" % f


def color16(v):
    """'#RRGGBB' or [r,g,b] (0-255) -> AppleScript 16-bit color literal."""
    if isinstance(v, str):
        h = v.strip().lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) != 6:
            raise ValueError("颜色格式应为 #RRGGBB：%s" % v)
        rgb = [int(h[i:i + 2], 16) for i in (0, 2, 4)]
    elif isinstance(v, (list, tuple)) and len(v) == 3:
        rgb = [int(round(float(x))) for x in v]
    else:
        raise ValueError("颜色格式应为 #RRGGBB 或 [r,g,b]：%r" % (v,))
    rgb = [max(0, min(255, x)) for x in rgb]
    return "{%d, %d, %d}" % tuple(x * 257 for x in rgb)


def hex_color(c):
    if isinstance(c, list) and len(c) == 3:
        try:
            return "#%02x%02x%02x" % tuple(int(round(float(x) / 257)) for x in c)
        except (TypeError, ValueError):
            return c
    return c


def _fix_item(it):
    if isinstance(it, dict) and "color" in it:
        it["color"] = hex_color(it["color"])
    return it


def as_list(v):
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return list(v)
    return [v]


def slide_no(v, name="slide"):
    return int(num(v, name, lo=1, integer=True))


class Script:
    """Builds one AppleScript call. Text goes to argv, never into the source."""

    def __init__(self, ctx, doc, guard):
        self.ctx = ctx
        self.args = [doc or "", ctx.cfg.roots_text()]
        self.lines = ["set d to my findDoc(item 1 of argv)"]
        if guard:
            self.lines.append("my guardDoc(d, item 2 of argv)")

    def arg(self, v):
        self.args.append("" if v is None else str(v))
        return "(item %d of argv)" % len(self.args)

    def add(self, *lines):
        self.lines.extend(lines)
        return self

    def run(self, timeout=120):
        return osa.run("\n".join(self.lines), self.args, timeout=timeout)


def change_lines(sc, var, ch, doc_var="d"):
    """AppleScript lines that apply a dict of property changes to item variable `var`."""
    L = []
    if ch.get("locked") is False:
        L.append("set locked of %s to false" % var)
    if ch.get("text") is not None:
        L.append("set object text of %s to %s" % (var, sc.arg(ch["text"])))
    if ch.get("font"):
        L.append("set font of object text of %s to %s" % (var, sc.arg(ch["font"])))
    if ch.get("size") is not None:
        L.append("set size of object text of %s to %s" % (var, num(ch["size"], "size", lo=1, hi=2000)))
    if ch.get("color") is not None:
        L.append("set color of object text of %s to %s" % (var, color16(ch["color"])))
    if ch.get("image"):
        p = sc.ctx.cfg.to_local(ch["image"])
        if not os.path.isfile(p):
            raise ValueError("图片不存在：%s" % ch["image"])
        L += [
            "set dsc to \"\"",
            "try",
            "set dsc to description of %s" % var,
            "end try",
            "set file name of %s to POSIX file %s" % (var, sc.arg(p)),
            "if dsc is not \"\" and dsc is not missing value then set description of %s to dsc" % var,
        ]
    if ch.get("description") is not None:
        L.append("set description of %s to %s" % (var, sc.arg(ch["description"])))
    if ch.get("width") is not None:
        L.append("set width of %s to %s" % (var, num(ch["width"], "width", lo=1, integer=True)))
    if ch.get("height") is not None:
        L.append("set height of %s to %s" % (var, num(ch["height"], "height", lo=1, integer=True)))
    if ch.get("x") is not None or ch.get("y") is not None:
        x = num(ch["x"], "x", integer=True) if ch.get("x") is not None else "(item 1 of pp)"
        y = num(ch["y"], "y", integer=True) if ch.get("y") is not None else "(item 2 of pp)"
        L += ["set pp to position of %s" % var, "set position of %s to {%s, %s}" % (var, x, y)]
    if ch.get("rotation") is not None:
        L.append("set rotation of %s to %s" % (var, num(float(ch["rotation"]) % 360, "rotation", integer=True)))
    if ch.get("opacity") is not None:
        L.append("set opacity of %s to %s" % (var, num(ch["opacity"], "opacity", lo=0, hi=100, integer=True)))
    for hl in as_list(ch.get("highlight")):
        if isinstance(hl, str):
            hl = {"text": hl}
        col = color16(hl.get("color", HOUSE_RED))
        L.append("my highlightText(%s, %s, %s)" % (var, sc.arg(hl["text"]), col))
    if ch.get("locked") is True:
        L.append("set locked of %s to true" % var)
    return L


def center_lines(var, x, y):
    """Position an item; missing x/y means centered on the slide."""
    xs = num(x, "x", integer=True) if x is not None else "((width of d) - (width of %s)) div 2" % var
    ys = num(y, "y", integer=True) if y is not None else "((height of d) - (height of %s)) div 2" % var
    return ["set position of %s to {%s, %s}" % (var, xs, ys)]


# ----------------------------------------------------------------- context --

class Context:
    def __init__(self, cfg):
        self.cfg = cfg
        self.jobs = {}

    def running_job(self):
        for j in self.jobs.values():
            if not j["done"]:
                return j
        return None


# ------------------------------------------------------------------- tools --

def h_status(ctx, a):
    job = ctx.running_job()
    if job:
        return {"running": True, "busy": "正在导出视频（job %s），请用 export_status 查看" % job["id"]}
    r = osa.run(r'''
if not (application "Keynote" is running) then return "{\"running\":false}"
tell application "Keynote"
	set v to version
	set L to {}
	repeat with dd in documents
		set end of L to my docJSON(contents of dd)
	end repeat
end tell
return "{\"running\":true,\"version\":" & my js(v) & ",\"documents\":[" & my joinList(L, ",") & "]}"
''')
    for d in r.get("documents", []):
        d["protected"] = bool(d.get("path")) and not ctx.cfg.in_roots(d["path"])
        d.pop("layouts", None)
    r["workspace"] = ctx.cfg.workspace
    r["writable_roots"] = sorted(set(x.rstrip("/") for x in ctx.cfg.roots))
    return r


def h_list_themes(ctx, a):
    return osa.run(r'''
tell application "Keynote" to set tn to name of every theme
set q to {}
repeat with x in tn
	set end of q to my js(contents of x)
end repeat
return "{\"themes\":[" & my joinList(q, ",") & "]}"
''')


PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_TEMPLATE = os.path.join(PROJECT_DIR, "templates", "KEYNOTE动画模板.key")


def _default_template(cfg):
    t = os.environ.get("KEYNOTE_MCP_TEMPLATE") or PROJECT_TEMPLATE
    return t if os.path.exists(t) else None


def _template_catalog(tpl):
    j = os.path.splitext(tpl)[0] + ".json"
    if os.path.exists(j):
        try:
            with open(j, encoding="utf-8") as f:
                return json.load(f)
        except ValueError:
            return None
    return None


def _create_from_template(ctx, tpl, path):
    shutil.copy2(tpl, path)
    os.utime(path, None)
    sc = Script(ctx, "", guard=False)
    sc.lines = []
    p = sc.arg(path)
    sc.add("set f to POSIX file %s" % p,
           'tell application "Keynote" to open f',
           "set d to missing value",
           "repeat 40 times",
           "try",
           "set d to my findDoc(%s)" % p,
           "exit repeat",
           "end try",
           "delay 0.5",
           "end repeat",
           "if d is missing value then error \"Keynote 没有打开新文稿（可能弹出了对话框）\" number 9012",
           "return my docJSON(d)")
    r = sc.run(timeout=300)
    r["template"] = tpl
    r["note"] = ("已从模板 %s 新建（深灰背景等样式来自模板）。坐标单位是点，原点在左上角，页面 %sx%s。"
                 "新加的空白页会复制相邻页的背景。" % (os.path.basename(tpl), r.get("width"), r.get("height")))
    cat = _template_catalog(tpl)
    if cat:
        r["template_guide"] = {k: cat[k] for k in ("how_to_use", "slides", "editable_text_items", "red") if k in cat}
    return r


def h_create(ctx, a):
    cfg = ctx.cfg
    path = a.get("path")
    if not path:
        name = a.get("name") or time.strftime("新文稿-%Y%m%d-%H%M%S")
        path = os.path.join(cfg.ensure_workspace(), name)
    path = cfg.to_local(path)
    if not path.lower().endswith(".key"):
        path += ".key"
    cfg.check_writable(path)
    if os.path.exists(path) and not a.get("overwrite"):
        raise ValueError("文件已存在：%s（要覆盖请传 overwrite=true）" % path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        _trash(path)
    tpl = None
    if a.get("template") not in (None, "", "none") :
        tpl = cfg.to_local(a["template"])
        if not os.path.isfile(tpl):
            raise ValueError("模板不存在：%s" % a["template"])
    elif a.get("template") != "none" and not a.get("theme"):
        tpl = _default_template(cfg)
    if tpl:
        return _create_from_template(ctx, tpl, path)
    w = num(a.get("width", 1920), "width", lo=100, hi=10000, integer=True)
    h = num(a.get("height", 1080), "height", lo=100, hi=10000, integer=True)
    theme = a.get("theme") or HOUSE_THEME
    sc = Script(ctx, "", guard=False)
    sc.lines = []  # no document yet
    t = sc.arg(theme)
    p = sc.arg(path)
    sc.add(
        'tell application "Keynote"',
        "set tn to name of every theme",
        "if tn does not contain %s then error \"没有这个主题：\" & %s & \"。可用主题：\" & my joinList(tn, \"、\") number 9007" % (t, t),
        "set d to make new document with properties {document theme:theme %s, width:%s, height:%s}" % (t, w, h),
        "set saved to false",
        "set lastErr to \"\"",
        "repeat with attempt from 1 to 4",
        "try",
        "save d in POSIX file %s" % p,
        "set saved to true",
        "exit repeat",
        "on error e",
        "set lastErr to e",
        "delay 1",
        "end try",
        "end repeat",
        "if not saved then",
        "close d saving no",
        "error \"新文稿保存失败：\" & lastErr number 9011",
        "end if",
        "set transition properties of slide 1 of d to %s" % _house_transition(),
        "save d",
        "end tell",
        "return my docJSON(d)",
    )
    r = sc.run()
    r["note"] = "已新建并保存。坐标单位是点，原点在左上角，页面 %sx%s。" % (w, h)
    return r


def _trash(path):
    """Move a file we are about to replace to the user's Trash (recoverable)."""
    trash = os.path.expanduser("~/.Trash")
    base = os.path.basename(path.rstrip("/"))
    dest = os.path.join(trash, "%s-%s" % (time.strftime("%H%M%S"), base))
    shutil.move(path, dest)
    return dest


def h_open(ctx, a):
    cfg = ctx.cfg
    src = cfg.to_local(a["path"])
    if not os.path.exists(src):
        raise ValueError("文件不存在：%s" % a["path"])
    if src.lower().endswith((".pptx", ".ppt")):
        raise ValueError("PowerPoint 文件请用 import_pptx：它会转成 .key 并列出需要重制的动画。")
    if not src.lower().endswith(".key"):
        raise ValueError("只能打开 .key 文稿：%s" % src)
    target, copied = src, False
    if not cfg.in_roots(src):
        size = _size(src)
        if cfg.free_bytes(cfg.ensure_workspace()) < size * 1.2 + (1 << 30):
            raise RuntimeError("本机磁盘空间不足，无法复制 %.0f MB 的副本" % (size / 1e6))
        stem = os.path.splitext(os.path.basename(src))[0]
        dest_dir = os.path.join(cfg.workspace, "副本")
        os.makedirs(dest_dir, exist_ok=True)
        target = os.path.join(dest_dir, "%s-副本%s.key" % (stem, time.strftime("%Y%m%d-%H%M%S")))
        if os.path.isdir(src):
            shutil.copytree(src, target)
        else:
            shutil.copy2(src, target)
        copied = True
    sc = Script(ctx, "", guard=False)
    sc.lines = []
    p = sc.arg(target)
    sc.add(
        "set f to POSIX file %s" % p,
        'tell application "Keynote" to open f',
        "set d to missing value",
        "repeat 40 times",
        "try",
        "set d to my findDoc(%s)" % p,
        "exit repeat",
        "end try",
        "delay 0.5",
        "end repeat",
        "if d is missing value then error \"Keynote 没有打开这份文稿（可能弹出了对话框）\" number 9012",
        "return my docJSON(d)",
    )
    r = sc.run(timeout=300)
    if copied:
        r["note"] = "原文件在允许修改的目录之外，已复制一份到本机再打开，原文件没有改动：%s" % src
        r["original"] = src
    return r


def _size(p):
    if os.path.isfile(p):
        return os.path.getsize(p)
    total = 0
    for root, _dirs, files in os.walk(p):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def h_save(ctx, a):
    path = a.get("path")
    sc = Script(ctx, a.get("document"), guard=not path)
    if path:
        path = ctx.cfg.to_local(path)
        if not path.lower().endswith(".key"):
            path += ".key"
        ctx.cfg.check_writable(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        p = sc.arg(path)
        sc.add("set oldPath to my docPath(d)",
               'tell application "Keynote" to save d in POSIX file %s' % p,
               "if oldPath is not missing value and oldPath is not %s then" % p,
               "-- Keynote wrote a copy; keep working on the copy (Save As semantics)",
               'tell application "Keynote" to close d saving no',
               "set f to POSIX file %s" % p,
               'tell application "Keynote" to open f',
               "set d to missing value",
               "repeat 40 times",
               "try",
               "set d to my findDoc(%s)" % p,
               "exit repeat",
               "end try",
               "delay 0.5",
               "end repeat",
               "if d is missing value then error \"另存成功，但没能打开新文件\" number 9014",
               "end if")
    else:
        sc.add(
            "if my docPath(d) is missing value then error \"这份文稿还没有保存位置，请传 path。\" number 9008",
            'tell application "Keynote" to save d',
        )
    sc.add("return my docJSON(d)")
    return sc.run(timeout=300)


def h_close(ctx, a):
    save = a.get("save", True)
    sc = Script(ctx, a.get("document"), guard=bool(save))
    if save:
        sc.add(
            "if my docPath(d) is missing value then error \"这份文稿还没有保存位置。先 save_presentation 指定 path，或传 save=false。\" number 9008",
            'tell application "Keynote" to set nm to name of d',
            'tell application "Keynote" to close d saving yes',
        )
    else:
        sc.add('tell application "Keynote" to set nm to name of d', 'tell application "Keynote" to close d saving no')
    sc.add('return "{\\"closed\\":" & my js(nm) & "}"')
    return sc.run(timeout=300)


def _deck(ctx, doc):
    sc = Script(ctx, doc, guard=False)
    sc.add(r'''
tell application "Keynote"
	set c to count of slides of d
	set skL to skipped of every slide of d
	set tpL to transition properties of every slide of d
	set lyL to name of base layout of every slide of d
	set tsL to title showing of every slide of d
end tell
set rows to {}
repeat with i from 1 to c
	tell application "Keynote"
		set s to slide i of d
		set ic to count of iWork items of s
		set nt to ""
		try
			set nt to (presenter notes of s) as text
		end try
		set ttl to missing value
		if item i of tsL then
			try
				set ttl to (object text of default title item of s) as text
			end try
		end if
	end tell
	set end of rows to "{\"slide\":" & i & ",\"layout\":" & my js(item i of lyL) & ",\"skipped\":" & my jb(item i of skL) & ",\"items\":" & ic & ",\"title\":" & my js(ttl) & ",\"notes\":" & my js(nt) & ",\"transition\":" & my tpJSON(item i of tpL) & "}"
end repeat
return "{\"document\":" & my docJSON(d) & ",\"slides\":[" & my joinList(rows, ",") & "]}"
''')
    return sc.run(timeout=300)


def h_get_deck(ctx, a):
    r = _deck(ctx, a.get("document"))
    mode = a.get("notes", "short")
    for s in r["slides"]:
        s["fig"] = tag_of(s.get("notes"))
        n = s.get("notes") or ""
        if mode == "none":
            s.pop("notes", None)
        elif mode != "full" and len(n) > 60:
            s["notes"] = n[:60] + "…"
    r["document"]["protected"] = bool(r["document"].get("path")) and not ctx.cfg.in_roots(r["document"]["path"])
    return r


def _slide_items(ctx, doc, n, full=True):
    sc = Script(ctx, doc, guard=False)
    sc.add(
        "set s to my getSlide(d, %d)" % n,
        'tell application "Keynote"',
        "set ic to count of iWork items of s",
        "set nt to \"\"",
        "try",
        "set nt to (presenter notes of s) as text",
        "end try",
        "set tp to transition properties of s",
        "set ly to name of base layout of s",
        "set sk to skipped of s",
        "end tell",
        "set L to {}",
        "repeat with k from 1 to ic",
        'tell application "Keynote" to set itm to iWork item k of s',
        "set end of L to my itemJSON(itm, k, %s)" % ("true" if full else "false"),
        "end repeat",
        'return "{\\"slide\\":%d,\\"layout\\":" & my js(ly) & ",\\"skipped\\":" & my jb(sk) & ",\\"notes\\":" & my js(nt) & ",\\"transition\\":" & my tpJSON(tp) & ",\\"items\\":[" & my joinList(L, ",") & "]}"' % n,
    )
    r = sc.run()
    r["items"] = [_fix_item(i) for i in r["items"]]
    return r


def h_get_slide(ctx, a):
    return _slide_items(ctx, a.get("document"), slide_no(a["slide"]))


def h_view_slide(ctx, a):
    n = slide_no(a["slide"])
    max_px = int(num(a.get("max_size", 1280), "max_size", lo=200, hi=4000, integer=True))
    tmp = tempfile.mkdtemp(prefix="view-", dir=_cache_dir(ctx))
    out_folder = os.path.join(tmp, "img")
    sc = Script(ctx, a.get("document"), guard=False)
    f = sc.arg(out_folder)
    sc.add(
        "set s to my getSlide(d, %d)" % n,
        "set canModify to true",
        "try",
        "my guardDoc(d, item 2 of argv)",
        "on error",
        "set canModify to false",
        "end try",
        'tell application "Keynote"',
        "set c to count of slides of d",
        "if canModify then",
        "set skL to skipped of every slide of d",
        "try",
        "repeat with i from 1 to c",
        "set want to (i is not %d)" % n,
        "if (item i of skL) is not want then set skipped of slide i of d to want",
        "end repeat",
        "export d to POSIX file %s as slide images with properties {image format:PNG, skipped slides:false}" % f,
        "on error e number en",
        "repeat with i from 1 to c",
        "if (skipped of slide i of d) is not (item i of skL) then set skipped of slide i of d to (item i of skL)",
        "end repeat",
        "error e number en",
        "end try",
        "repeat with i from 1 to c",
        "if (skipped of slide i of d) is not (item i of skL) then set skipped of slide i of d to (item i of skL)",
        "end repeat",
        # Keynote may have autosaved the temporary state; save the restored one so the file is never left with skips.
        "if my docPath(d) is not missing value then save d",
        "else",
        "export d to POSIX file %s as slide images with properties {image format:PNG, skipped slides:true}" % f,
        "end if",
        "end tell",
        'return "{\\"single\\":" & my jb(canModify) & "}"',
    )
    try:
        r = sc.run(timeout=600)
        files = sorted(glob.glob(os.path.join(out_folder, "*.png")))
        if not files:
            raise KeynoteError("导出页面图片失败")
        src = files[0] if r.get("single") else files[n - 1]
        jpg = os.path.join(tmp, "view.jpg")
        subprocess.run(["sips", "-Z", str(max_px), "-s", "format", "jpeg", "-s", "formatOptions", "82",
                        src, "--out", jpg], capture_output=True, check=True)
        data = open(jpg, "rb").read()
        saved = None
        if a.get("save_to"):
            dest = ctx.cfg.to_local(a["save_to"])
            ctx.cfg.check_writable(dest)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(src, dest)
            saved = dest
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    content = [{"type": "image", "data": base64.b64encode(data).decode("ascii"), "mimeType": "image/jpeg"}]
    summary = {"slide": n}
    if saved:
        summary["saved_png"] = saved
    if a.get("with_items", True):
        info = _slide_items(ctx, a.get("document"), n, full=False)
        summary["items"] = [{k: v for k, v in it.items() if k in ("item", "type", "x", "y", "width", "height", "text", "file")}
                            for it in info["items"]]
        for it in summary["items"]:
            if isinstance(it.get("text"), str) and len(it["text"]) > 40:
                it["text"] = it["text"][:40] + "…"
        summary["transition"] = info["transition"]
    return {"__content__": content, "summary": summary}


def _cache_dir(ctx):
    d = os.path.join(ctx.cfg.ensure_workspace(), ".cache")
    os.makedirs(d, exist_ok=True)
    return d


def _insert_where(sc, a):
    """AppleScript location for a new slide: after=N (0 = first), default end."""
    after = a.get("after")
    if after is None or after == "end":
        return "at end of slides of d"
    n = int(num(after, "after", lo=0, integer=True))
    if n == 0:
        return "at before slide 1 of d"
    return "at after (my getSlide(d, %d))" % n


def _blank_anchor(a):
    """Slide number to duplicate for a new blank slide, or None to use the theme's layout."""
    after = a.get("after")
    if a.get("layout") not in (None, "", "Blank", "空白") or a.get("inherit_background") is False:
        return None
    if after is None or after == "end":
        return "END"
    n = int(num(after, "after", lo=0, integer=True))
    return n if n >= 1 else None


def _new_slide_number(a):
    """AppleScript line setting n to the new slide's number. Keynote's own "slide number" can lag by one
    right after slides were duplicated, so derive it from where the slide was inserted."""
    after = a.get("after")
    if after is None or after == "end":
        return "set n to count of slides of d"
    k = int(num(after, "after", lo=0, integer=True))
    return "set n to %d" % (k + 1)


def h_add_slide(ctx, a):
    sc = Script(ctx, a.get("document"), guard=True)
    anchor = _blank_anchor(a)
    tr = _house_transition(a.get("transition", "magic move"), a.get("duration"))
    if anchor is not None:
        n = "(count of slides of d)" if anchor == "END" else str(anchor)
        # Right after a duplicate Keynote reports the new slide's "slide number" one too low, so count it ourselves.
        sc.add('tell application "Keynote" to set nAnchor to %s' % n,
               "my getSlide(d, nAnchor)",
               "set s to my blankAfter(d, nAnchor)",
               "set n to nAnchor + 1",
               'tell application "Keynote"',
               "set transition properties of s to %s" % tr)
    else:
        lay = sc.arg(a.get("layout") or "Blank")
        sc.add(
            "set lay to my layoutOf(d, %s)" % lay,
            'tell application "Keynote"',
            "set s to make new slide %s with properties {base layout:lay}" % _insert_where(sc, a),
            "set transition properties of s to %s" % tr,
            _new_slide_number(a),
        )
    if a.get("title") is not None:
        sc.add("set title showing of s to true", "set object text of default title item of s to %s" % sc.arg(a["title"]))
    if a.get("body") is not None:
        sc.add("set body showing of s to true", "set object text of default body item of s to %s" % sc.arg(a["body"]))
    if a.get("notes") is not None:
        sc.add("set presenter notes of s to %s" % sc.arg(a["notes"]))
    sc.add("set ic to count of iWork items of s", "set tot to count of slides of d", "end tell",
           'return "{\\"slide\\":" & n & ",\\"items\\":" & ic & ",\\"total\\":" & tot & "}"')
    return sc.run()


def h_duplicate_slide(ctx, a):
    n = slide_no(a["slide"])
    count = int(num(a.get("count", 1), "count", lo=1, hi=50, integer=True))
    sc = Script(ctx, a.get("document"), guard=True)
    sc.add(
        "set s to my getSlide(d, %d)" % n,
        "repeat %d times" % count,
        'tell application "Keynote" to duplicate s to after s',
        "end repeat",
        'tell application "Keynote" to set tot to count of slides of d',
        'return "{\\"total\\":" & tot & "}"',
    )
    r = sc.run()
    r["new_slides"] = list(range(n + 1, n + 1 + count))
    return r


def h_delete_slides(ctx, a):
    nums = sorted({slide_no(x) for x in as_list(a["slides"])}, reverse=True)
    sc = Script(ctx, a.get("document"), guard=True)
    for n in nums:
        sc.add("set s to my getSlide(d, %d)" % n, 'tell application "Keynote" to delete s')
    sc.add('tell application "Keynote" to set tot to count of slides of d', 'return "{\\"total\\":" & tot & "}"')
    r = sc.run()
    r["deleted"] = sorted(nums)
    return r


def h_move_slide(ctx, a):
    n = slide_no(a["slide"])
    to = slide_no(a["to"], "to")
    sc = Script(ctx, a.get("document"), guard=True)
    sc.add("set s to my getSlide(d, %d)" % n, "set t to my getSlide(d, %d)" % to)
    if to < n:
        sc.add('tell application "Keynote" to move s to before t')
    elif to > n:
        sc.add('tell application "Keynote" to move s to after t')
    sc.add('return "{\\"slide\\":%d}"' % to)
    return sc.run()


def h_set_slide(ctx, a):
    n = slide_no(a["slide"])
    sc = Script(ctx, a.get("document"), guard=True)
    sc.add("set s to my getSlide(d, %d)" % n)
    if a.get("layout"):
        sc.add("set lay to my layoutOf(d, %s)" % sc.arg(a["layout"]))
    sc.add('tell application "Keynote"')
    if a.get("layout"):
        sc.add("set base layout of s to lay")
    for key, prop in (("skipped", "skipped"), ("title_showing", "title showing"), ("body_showing", "body showing")):
        if a.get(key) is not None:
            sc.add("set %s of s to %s" % (prop, "true" if a[key] else "false"))
    if a.get("title") is not None:
        sc.add("set title showing of s to true", "set object text of default title item of s to %s" % sc.arg(a["title"]))
    if a.get("body") is not None:
        sc.add("set body showing of s to true", "set object text of default body item of s to %s" % sc.arg(a["body"]))
    sc.add("end tell")
    sc.lines.append("set n to %d" % n)
    sc.add('return "{\\"slide\\":" & n & "}"')
    sc.run()
    return _slide_items(ctx, a.get("document"), n, full=False)


def _new_item_return(sc):
    sc.add("set k to count of iWork items of s", "end tell", "return my itemJSON(t, k, true)")


def h_add_text(ctx, a):
    n = slide_no(a["slide"])
    sc = Script(ctx, a.get("document"), guard=True)
    sc.add("set s to my getSlide(d, %d)" % n, 'tell application "Keynote"',
           "tell s to set t to make new text item with properties {object text:%s}" % sc.arg(a["text"]))
    sc.lines += change_lines(sc, "t", {k: a.get(k) for k in ("font", "size", "color", "width", "opacity", "rotation", "highlight")})
    sc.lines += center_lines("t", a.get("x"), a.get("y"))
    _new_item_return(sc)
    return _fix_item(sc.run())


def h_add_image(ctx, a):
    n = slide_no(a["slide"])
    p = ctx.cfg.to_local(a["path"])
    if not os.path.isfile(p):
        raise ValueError("图片不存在：%s" % a["path"])
    sc = Script(ctx, a.get("document"), guard=True)
    sc.add("set s to my getSlide(d, %d)" % n, 'tell application "Keynote"',
           "tell s to set t to make new image with properties {file:(POSIX file %s)}" % sc.arg(p))
    ch = {k: a.get(k) for k in ("opacity", "rotation", "description")}
    if a.get("width") is not None:
        ch["width"] = a["width"]
    elif a.get("height") is not None:
        ch["height"] = a["height"]
    sc.lines += change_lines(sc, "t", ch)
    sc.lines += center_lines("t", a.get("x"), a.get("y"))
    _new_item_return(sc)
    return sc.run()


def h_add_shape(ctx, a):
    n = slide_no(a["slide"])
    sc = Script(ctx, a.get("document"), guard=True)
    props = "{width:%s, height:%s}" % (num(a.get("width", 400), "width", lo=1, integer=True),
                                       num(a.get("height", 300), "height", lo=1, integer=True))
    sc.add("set s to my getSlide(d, %d)" % n, 'tell application "Keynote"',
           "tell s to set t to make new shape with properties %s" % props)
    ch = {k: a.get(k) for k in ("text", "font", "size", "color", "opacity", "rotation")}
    sc.lines += change_lines(sc, "t", ch)
    sc.lines += center_lines("t", a.get("x"), a.get("y"))
    _new_item_return(sc)
    return _fix_item(sc.run())


def h_add_line(ctx, a):
    n = slide_no(a["slide"])
    pts = [num(a[k], k, integer=True) for k in ("x1", "y1", "x2", "y2")]
    sc = Script(ctx, a.get("document"), guard=True)
    sc.add("set s to my getSlide(d, %d)" % n, 'tell application "Keynote"',
           "tell s to set t to make new line with properties {start point:{%s, %s}, end point:{%s, %s}}" % tuple(pts))
    _new_item_return(sc)
    return sc.run()


def h_add_table(ctx, a):
    n = slide_no(a["slide"])
    rows = a["rows"]
    if not rows or not all(isinstance(r, list) for r in rows):
        raise ValueError("rows 应该是二维数组，例如 [[\"焦距\",\"MTF50\"],[\"50mm\",\"0.62\"]]")
    nr, nc = len(rows), max(len(r) for r in rows)
    if nr * nc > 600:
        raise ValueError("表格太大（最多 600 个单元格）")
    sc = Script(ctx, a.get("document"), guard=True)
    sc.add("set s to my getSlide(d, %d)" % n, 'tell application "Keynote"',
           "tell s to set t to make new table with properties {row count:%d, column count:%d}" % (nr, nc),
           "set header row count of t to %s" % num(a.get("header_rows", 1), "header_rows", lo=0, hi=5, integer=True),
           "set header column count of t to %s" % num(a.get("header_columns", 0), "header_columns", lo=0, hi=5, integer=True))
    for i, r in enumerate(rows, 1):
        for k, v in enumerate(r, 1):
            if v is None or v == "":
                continue
            sc.add("set value of cell %d of row %d of t to %s" % (k, i, sc.arg(v)))
    ch = {k: a.get(k) for k in ("width", "height")}
    sc.lines += change_lines(sc, "t", ch)
    sc.lines += center_lines("t", a.get("x"), a.get("y"))
    _new_item_return(sc)
    return sc.run(timeout=300)


ITEM_CHANGE_KEYS = ("x", "y", "width", "height", "rotation", "opacity", "locked", "text", "font",
                    "size", "color", "image", "description", "highlight")


def h_update_item(ctx, a):
    n = slide_no(a["slide"])
    k = int(num(a["item"], "item", lo=1, integer=True))
    sc = Script(ctx, a.get("document"), guard=True)
    sc.add("set s to my getSlide(d, %d)" % n, "set t to my getItem(s, %d)" % k, 'tell application "Keynote"')
    sc.lines += change_lines(sc, "t", {key: a.get(key) for key in ITEM_CHANGE_KEYS})
    sc.add("end tell", "return my itemJSON(t, %d, true)" % k)
    return _fix_item(sc.run())


def h_delete_items(ctx, a):
    n = slide_no(a["slide"])
    ks = sorted({int(num(x, "item", lo=1, integer=True)) for x in as_list(a["items"])}, reverse=True)
    sc = Script(ctx, a.get("document"), guard=True)
    sc.add("set s to my getSlide(d, %d)" % n)
    for k in ks:
        sc.add("set t to my getItem(s, %d)" % k, 'tell application "Keynote" to delete t')
    sc.add('tell application "Keynote" to set rem to count of iWork items of s', 'return "{\\"remaining\\":" & rem & "}"')
    r = sc.run()
    r["deleted"] = sorted(ks)
    r["note"] = "删除后，后面对象的编号会前移，继续编辑前用 get_slide 重新查看。"
    return r


def h_set_notes(ctx, a):
    n = slide_no(a["slide"])
    sc = Script(ctx, a.get("document"), guard=True)
    t = sc.arg(a.get("text", ""))
    sc.add("set s to my getSlide(d, %d)" % n, 'tell application "Keynote"')
    if a.get("append"):
        sc.add("set old to (presenter notes of s) as text",
               "if old is \"\" then",
               "set presenter notes of s to %s" % t,
               "else",
               "set presenter notes of s to old & linefeed & %s" % t,
               "end if")
    else:
        sc.add("set presenter notes of s to %s" % t)
    sc.add("set nt to (presenter notes of s) as text", "end tell",
           'return "{\\"slide\\":%d,\\"notes\\":" & my js(nt) & "}"' % n)
    return sc.run()


def _tp_record(a, prefix="tp"):
    """AppleScript record for transition properties; unspecified fields keep current values."""
    eff = effect_keyword(a["effect"]) if a.get("effect") else None
    parts = [
        "transition effect:%s" % (eff if eff else "(transition effect of %s)" % prefix),
        "transition duration:%s" % (num(a["duration"], "duration", lo=0, hi=60) if a.get("duration") is not None
                                     else "(transition duration of %s)" % prefix),
        "transition delay:%s" % (num(a["delay"], "delay", lo=0, hi=3600) if a.get("delay") is not None
                                  else "(transition delay of %s)" % prefix),
        "automatic transition:%s" % (("true" if a["automatic"] else "false") if a.get("automatic") is not None
                                      else "(automatic transition of %s)" % prefix),
    ]
    return "{" + ", ".join(parts) + "}"


def h_set_transition(ctx, a):
    sc = Script(ctx, a.get("document"), guard=True)
    rec = _tp_record(a)
    sl = a.get("slides", "all")
    if sl == "all":
        sc.add('tell application "Keynote" to set nL to every slide of d', "set idx to {}",
               "repeat with i from 1 to count of nL", "set end of idx to i", "end repeat")
    else:
        idx = sorted({slide_no(x) for x in as_list(sl)})
        sc.add("set idx to {%s}" % ", ".join(str(i) for i in idx))
    sc.add(
        "set out to {}",
        "repeat with i in idx",
        "set s to my getSlide(d, contents of i)",
        'tell application "Keynote"',
        "set tp to transition properties of s",
        "set transition properties of s to %s" % rec,
        "set tp to transition properties of s",
        "end tell",
        'set end of out to "{\\"slide\\":" & (contents of i) & ",\\"transition\\":" & my tpJSON(tp) & "}"',
        "end repeat",
        'return "[" & my joinList(out, ",") & "]"',
    )
    return {"slides": sc.run()}


def h_magic_move_step(ctx, a):
    n = slide_no(a["slide"])
    sc = Script(ctx, a.get("document"), guard=True)
    rec = _tp_record({"effect": "magic move", "duration": a.get("duration", HOUSE_DURATION),
                      "delay": a.get("delay"), "automatic": a.get("automatic")})
    sc.add(
        "set s to my getSlide(d, %d)" % n,
        'tell application "Keynote"',
        "duplicate s to after s",
        "set tp to transition properties of s",
        "set transition properties of s to %s" % rec,
        "end tell",
        "set s2 to my getSlide(d, %d)" % (n + 1),
    )
    for ch in as_list(a.get("changes")):
        if "item" not in ch:
            raise ValueError("changes 里每一项都要有 item（对象编号）")
        k = int(num(ch["item"], "item", lo=1, integer=True))
        sc.add("set t to my getItem(s2, %d)" % k, 'tell application "Keynote"')
        sc.lines += change_lines(sc, "t", {key: ch.get(key) for key in ITEM_CHANGE_KEYS})
        sc.add("end tell")
    for k in sorted({int(num(x, "item", lo=1, integer=True)) for x in as_list(a.get("remove_items"))}, reverse=True):
        sc.add("set t to my getItem(s2, %d)" % k, 'tell application "Keynote" to delete t')
    sc.add('return "{\\"ok\\":true}"')
    sc.run()
    info = _slide_items(ctx, a.get("document"), n + 1, full=False)
    return {"from_slide": n, "new_slide": n + 1,
            "note": "第 %d 页到第 %d 页已设为神奇移动（时长 %ss）。新页继承了原页原来的转场。" % (n, n + 1, a.get("duration", HOUSE_DURATION)),
            "items": info["items"]}


CHAPTER_NUM_RE = re.compile(r"^\s*([一二三四五六七八九十]+|[0-9]+)\s*[ 　、.．]\s*(.+)$")


def h_chapter_card(ctx, a):
    """User's chapter card (measured in their decks): numeral box (76 pt) above a title box (71 pt),
    both white and centred, with a blank slide before and after, all on 0.5 s magic move."""
    title = str(a["title"]).strip()
    number = a.get("number")
    if number is None:
        m = CHAPTER_NUM_RE.match(title)
        if m:
            number, title = m.group(1), m.group(2).strip()
    sc = Script(ctx, a.get("document"), guard=True)
    rec = _house_transition(a.get("transition", "magic move"), a.get("duration", HOUSE_DURATION))
    color = a.get("color", "#FFFFFF")
    after = a.get("after")
    sc.add("set made to {}")
    if after is None or after == "end":
        sc.add('tell application "Keynote" to set n to count of slides of d')
    else:
        sc.add("set n to %s" % num(after, "after", lo=0, integer=True))
    sc.add("if n < 1 then",
           "set lay to my layoutOf(d, \"Blank\")",
           'tell application "Keynote" to make new slide at before slide 1 of d with properties {base layout:lay}',
           "set n to 1",
           "set firstIsNew to true",
           "else",
           "set firstIsNew to false",
           "end if")
    if a.get("blank_before", True):
        sc.add("if firstIsNew then",
               'tell application "Keynote" to set b1 to slide 1 of d',
               "else",
               "set b1 to my blankAfter(d, n)",
               "set n to n + 1",
               "end if",
               'tell application "Keynote" to set transition properties of b1 to %s' % rec,
               "set end of made to n")
    sc.add("if firstIsNew and not %s then" % ("true" if a.get("blank_before", True) else "false"),
           'tell application "Keynote" to set c to slide 1 of d',
           "else",
           "set c to my blankAfter(d, n)",
           "set n to n + 1",
           "end if",
           "set end of made to n",
           'tell application "Keynote"',
           "set transition properties of c to %s" % rec)
    sc.add("tell c to set t2 to make new text item with properties {object text:%s}" % sc.arg(title))
    sc.lines += change_lines(sc, "t2", {"size": a.get("title_size", 71), "font": a.get("font"), "color": color})
    if number:
        sc.add("tell c to set t1 to make new text item with properties {object text:%s}" % sc.arg(number))
        sc.lines += change_lines(sc, "t1", {"size": a.get("number_size", 76), "font": a.get("font"), "color": color})
        # stack: numeral box top at 407, title box top at 566 (as in the user's decks), both centred
        sc.add("set position of t1 to {((width of d) - (width of t1)) div 2, %s}" % num(a.get("number_y", 407), "number_y", integer=True),
               "set position of t2 to {((width of d) - (width of t2)) div 2, %s}" % num(a.get("title_y", 566), "title_y", integer=True))
    else:
        sc.lines += center_lines("t2", None, None)
    if a.get("notes") is not None:
        sc.add("set presenter notes of c to %s" % sc.arg(a["notes"]))
    sc.add("end tell")
    if a.get("blank_after", True):
        sc.add("set b2 to my blankAfter(d, n)",
               'tell application "Keynote" to set transition properties of b2 to %s' % rec,
               "set end of made to n + 1")
    sc.add('return my jlist(made)')
    made = sc.run()
    return {"slides": made, "number": number, "title": title,
            "note": "章节卡：空白 → 编号 + 标题 → 空白，按你文稿里的样式（编号 76 号、标题 71 号、白色居中），转场 %s %s 秒。"
                    % (a.get("transition", "magic move"), a.get("duration", HOUSE_DURATION))}


def h_image_slides(ctx, a):
    paths = [ctx.cfg.to_local(p) for p in as_list(a["paths"])]
    for p in paths:
        if not os.path.isfile(p):
            raise ValueError("图片不存在：%s" % p)
    sc = Script(ctx, a.get("document"), guard=True)
    files = ", ".join("POSIX file %s" % sc.arg(p) for p in paths)
    sc.add('tell application "Keynote"', "set c0 to count of slides of d",
           "make image slides d files {%s} %s set titles" % (files, "with" if a.get("set_titles") else "without"),
           "set c1 to count of slides of d", "end tell",
           'return "{\\"first\\":" & (c0 + 1) & ",\\"last\\":" & c1 & "}"')
    r = sc.run(timeout=600)
    r["note"] = "新页加在文稿末尾。"
    return r


def h_sync_notes(ctx, a):
    sp = ctx.cfg.to_local(a["script_path"])
    if not os.path.isfile(sp):
        raise ValueError("找不到文稿：%s" % a["script_path"])
    with open(sp, "r", encoding="utf-8-sig") as f:
        parsed = parse_script(f.read())
    figs = parsed["figures"]
    if not figs:
        raise ValueError("文稿里没有找到 [图N：…] 标记")
    deck = _deck(ctx, a.get("document"))
    slides = deck["slides"]
    total = len(slides)
    plan = {}  # slide -> (fig, text)
    unmatched = []
    mapping = a.get("mapping")
    strategy = a.get("strategy", "tag")
    include_tag = a.get("include_tag", True)

    def body(fig, first=True):
        tag = "[图%s：%s]" % (fig["fig"], fig["caption"]) if fig["caption"] else "[图%s]" % fig["fig"]
        if not first:
            return "[图%s 续]" % fig["fig"]
        if include_tag:
            return tag + ("\n" + fig["text"] if fig["text"] else "")
        return fig["text"]

    if mapping:
        for fig in figs:
            targets = as_list(mapping.get(str(fig["fig"])))
            if not targets:
                unmatched.append(fig["fig"])
                continue
            for i, t in enumerate(targets):
                t = slide_no(t)
                if t > total:
                    raise ValueError("mapping 里的页号 %d 超出范围（共 %d 页）" % (t, total))
                plan[t] = (fig["fig"], body(fig, i == 0))
    elif strategy == "tag":
        by_tag = {}
        for s in slides:
            t = tag_of(s.get("notes"))
            if t:
                by_tag.setdefault(t, []).append(s["slide"])
        for fig in figs:
            targets = by_tag.get(str(fig["fig"]))
            if not targets:
                unmatched.append(fig["fig"])
                continue
            plan[targets[0]] = (fig["fig"], body(fig, True))
    elif strategy == "order":
        content = [s["slide"] for s in slides if not s["skipped"] and s["items"] > 0]
        for fig, sn in zip(figs, content):
            plan[sn] = (fig["fig"], body(fig, True))
        unmatched = [f["fig"] for f in figs[len(content):]]
    else:
        raise ValueError("strategy 只能是 tag 或 order")

    result = {
        "script_title": parsed["title"],
        "figures": len(figs),
        "planned": [{"slide": s, "fig": f, "notes_head": t[:50]} for s, (f, t) in sorted(plan.items())],
        "unmatched_figures": unmatched,
    }
    if not plan:
        result["note"] = ("没有对上任何一页。strategy=tag 需要幻灯片备注以 [图N] 开头；"
                          "也可以传 mapping，例如 {\"1\": 1, \"2\": [3, 4]}。")
        return result
    if a.get("dry_run"):
        result["dry_run"] = True
        return result
    sc = Script(ctx, a.get("document"), guard=True)
    for s, (_f, t) in sorted(plan.items()):
        sc.add("set s to my getSlide(d, %d)" % s,
               'tell application "Keynote" to set presenter notes of s to %s' % sc.arg(t))
    sc.add('return "{\\"ok\\":true}"')
    sc.run(timeout=300)
    result["written"] = len(plan)
    return result


# --------------------------------------------------------------- pptx import --

# Common Windows font names written in Chinese inside .pptx files.
FONT_ALIASES = {"微软雅黑": "microsoft yahei", "宋体": "simsun", "新宋体": "nsimsun", "黑体": "simhei",
                "等线": "dengxian", "楷体": "kaiti", "仿宋": "fangsong", "思源黑体": "source han sans sc",
                "思源宋体": "source han serif sc"}

_FONT_JS = r'''
ObjC.import('AppKit');
var fm = $.NSFontManager.sharedFontManager;
var fams = ObjC.deepUnwrap(fm.availableFontFamilies);
var out = fams.slice();
fams.forEach(function (f) {
  var loc = fm.localizedNameForFamilyFace(f, $());
  if (loc) out.push(ObjC.unwrap(loc));
});
JSON.stringify(out.concat(ObjC.deepUnwrap(fm.availableFonts)));
'''


def _installed_fonts():
    try:
        p = subprocess.run(["osascript", "-l", "JavaScript", "-e", _FONT_JS], capture_output=True, text=True, timeout=60)
        return {x.lower() for x in json.loads(p.stdout)}
    except Exception:
        return None


def _missing_fonts(used):
    have = _installed_fonts()
    if have is None:
        return None
    missing = []
    for f in used:
        key = f.lower()
        if key in have or FONT_ALIASES.get(f, "") in have or key.replace(" ", "") in {h.replace(" ", "") for h in have}:
            continue
        missing.append(f)
    return missing


def h_import_pptx(ctx, a):
    from .pptx_info import read as read_pptx, repair_for_keynote

    cfg = ctx.cfg
    src = cfg.to_local(a["path"])
    if not os.path.isfile(src):
        raise ValueError("文件不存在：%s" % a["path"])
    if not src.lower().endswith((".pptx", ".ppt")):
        raise ValueError("import_pptx 只接受 .pptx / .ppt：%s" % src)
    info = read_pptx(src) if src.lower().endswith(".pptx") else None
    stem = os.path.splitext(os.path.basename(src))[0]
    open_path, repairs, tmpdir = src, None, None
    if info:
        tmpdir = tempfile.mkdtemp(prefix="pptx-", dir=_cache_dir(ctx))
        fixed = os.path.join(tmpdir, os.path.basename(src))
        repairs = repair_for_keynote(src, fixed)
        if repairs:
            open_path = fixed
    if a.get("save_as"):
        dest = cfg.to_local(a["save_as"])
    elif cfg.in_roots(src):
        dest = os.path.join(os.path.dirname(src), stem + ".key")
    else:
        dest = os.path.join(cfg.ensure_workspace(), stem + ".key")
    if not dest.lower().endswith(".key"):
        dest += ".key"
    cfg.check_writable(dest)
    if os.path.exists(dest):
        if not a.get("overwrite"):
            raise ValueError("目标文件已存在：%s（要覆盖请传 overwrite=true，或用 save_as 换个名字）" % dest)
        _trash(dest)
    os.makedirs(os.path.dirname(dest), exist_ok=True)

    # Open the PowerPoint file (Keynote converts it into a new, unsaved document) and save it as .key at once,
    # so Keynote never autosaves it into iCloud Drive.
    sc = Script(ctx, "", guard=False)
    sc.lines = []
    p_src = sc.arg(open_path)
    p_dest = sc.arg(dest)
    sc.add(
        "set f to POSIX file %s" % p_src,
        'tell application "Keynote" to set beforeIds to id of every document',
        'tell application "Keynote" to open f',
        "set d to missing value",
        "repeat 120 times",
        'tell application "Keynote" to set nowIds to id of every document',
        "repeat with x in nowIds",
        "if beforeIds does not contain (contents of x) then",
        'tell application "Keynote" to set d to document id (contents of x)',
        "exit repeat",
        "end if",
        "end repeat",
        "if d is not missing value then exit repeat",
        "delay 0.5",
        "end repeat",
        "if d is missing value then error \"Keynote 没能导入这份 PowerPoint 文件。Keynote 很可能弹出了「无法导入 / 文件格式无效」的对话框，请在 Keynote 里点「好」关掉它，否则后续操作都会卡住。\" number 9013",
        "set saved to false",
        "set lastErr to \"\"",
        "repeat with attempt from 1 to 4",
        "try",
        'tell application "Keynote" to save d in POSIX file %s' % p_dest,
        "set saved to true",
        "exit repeat",
        "on error e",
        "set lastErr to e",
        "delay 1",
        "end try",
        "end repeat",
        "if not saved then",
        'tell application "Keynote" to close d saving no',
        "error \"转换后的 .key 保存失败：\" & lastErr number 9011",
        "end if",
        "return my docJSON(d)",
    )
    try:
        doc = sc.run(timeout=600)
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)

    deck = _deck(ctx, dest)
    kslides = deck["slides"]
    converted = []
    if info and a.get("morph_to_magic_move", True):
        todo = []
        for s in info["slides"]:
            t = s.get("transition_in") or {}
            k = s["slide"] - 1  # PowerPoint stores it on the slide it enters; Keynote on the slide it leaves
            if t.get("effect") == "morph" and 1 <= k <= len(kslides):
                if kslides[k - 1]["transition"]["effect"] != "magic move":
                    todo.append((k, t["duration"]))
                converted.append({"keynote_slide": k, "to_slide": k + 1, "duration": t["duration"],
                                  "already_magic_move": kslides[k - 1]["transition"]["effect"] == "magic move"})
        if todo:
            sc = Script(ctx, dest, guard=True)
            for k, dur in todo:
                sc.add("set s to my getSlide(d, %d)" % k,
                       'tell application "Keynote" to set transition properties of s to {transition effect:magic move, transition duration:%s}'
                       % num(min(max(dur, 0.1), 60), "duration"))
            sc.add('tell application "Keynote" to save d', 'return "{\\"ok\\":true}"')
            sc.run(timeout=300)
            deck = _deck(ctx, dest)
            kslides = deck["slides"]

    report = []
    pslides = info["slides"] if info else []
    for i, ks in enumerate(kslides, 1):
        ps = pslides[i - 1] if i - 1 < len(pslides) else {}
        notes = ks.get("notes") or ps.get("notes") or ""
        row = {
            "slide": i,
            "title": ks.get("title") or ps.get("title"),
            "fig": tag_of(notes),
            "notes_head": notes[:50] + ("…" if len(notes) > 50 else ""),
            "keynote_transition_to_next": ks["transition"]["effect"],
        }
        if ps.get("animation_notes"):
            row["animation_notes"] = ps["animation_notes"]
        if ps.get("transition_in") and ps["transition_in"].get("effect") not in ("none", None) and i > 1:
            row["ppt_transition_into_this_slide"] = ps["transition_in"]
        if ps.get("builds"):
            row["ppt_builds"] = ps["builds"]
        if ps.get("hidden"):
            row["hidden"] = True
        if ps.get("shapes"):
            row["shape_names"] = [x["name"] for x in ps["shapes"]][:8]
        report.append(row)

    result = {
        "document": {k: doc.get(k) for k in ("name", "path", "width", "height", "slides", "theme")},
        "source": src,
        "note": ("已转换成 .key 并保存，原 PowerPoint 文件没有改动。PowerPoint 把转场记在切入的那一页上，Keynote 记在切出的那一页上："
                 "PPT 第 N 页上的平滑 = Keynote 第 N-1 页的神奇移动。下面每页的 animation_notes 是备注里写的 [动画] 说明，"
                 "需要用 magic_move_step / set_transition / chapter_card 按说明重做；ppt_builds 是 PowerPoint 里的进入/退出动画，"
                 "Keynote 脚本接口不能设置，只能改成神奇移动或由人手动补。"),
        "magic_move": converted,
        "repairs": repairs or [],
        "slides": report,
    }
    if info:
        result["pptx_slide_size"] = info["slide_size"]
        if len(info["slides"]) != len(kslides):
            result["warning"] = "页数不一致：PPTX %d 页，Keynote 里 %d 页。" % (len(info["slides"]), len(kslides))
        result["fonts_used"] = info["fonts"]
        miss = _missing_fonts(info["fonts"])
        if miss:
            result["fonts_missing_on_mac"] = miss
            result["font_note"] = "这些字体这台 Mac 上可能没有，Keynote 会用其它字体代替。可以在 Mac 上装同名字体，或用 update_item 换字体。"
    return result


# ------------------------------------------------------------------ builds --

def _reopen(ctx, path):
    sc = Script(ctx, "", guard=False)
    sc.lines = []
    p = sc.arg(path)
    sc.add("set f to POSIX file %s" % p, 'tell application "Keynote" to open f',
           "set d to missing value", "repeat 40 times", "try", "set d to my findDoc(%s)" % p, "exit repeat",
           "end try", "delay 0.5", "end repeat",
           "if d is missing value then error \"Keynote 没能重新打开文稿（可能弹出了对话框）\" number 9015",
           "return my docJSON(d)")
    return sc.run(timeout=300)


def h_add_builds(ctx, a):
    from . import builds as B
    specs = [B.normalize(dict(x)) for x in as_list(a.get("builds"))]
    if not specs:
        raise ValueError("builds 不能为空，例如 [{\"slide\": 5, \"item\": 3, \"effect\": \"line_draw\"}]")
    if not B.available():
        B._import()  # raises with install instructions
    slides_needed = sorted({s["slide"] for s in specs})
    sc = Script(ctx, a.get("document"), guard=True)
    sc.add("set p to my docPath(d)",
           "if p is missing value then error \"这份文稿还没有保存位置，先用 save_presentation 指定 path。\" number 9008",
           "set counts to {}")
    for n in slides_needed:
        sc.add("set s to my getSlide(d, %d)" % n,
               'tell application "Keynote" to set end of counts to count of iWork items of s')
    sc.add('tell application "Keynote" to save d', 'tell application "Keynote" to close d saving yes',
           'return "{\\"path\\":" & my js(p) & ",\\"counts\\":" & my jlist(counts) & "}"')
    info = sc.run(timeout=300)
    path = info["path"]
    expected = dict(zip(slides_needed, info["counts"]))
    try:
        res = B.inject(path, specs, backup_dir=os.path.join(_cache_dir(ctx), "加动画前备份"), expected_items=expected)
    finally:
        doc = _reopen(ctx, path)
    res["document"] = {k: doc.get(k) for k in ("name", "path", "slides")}
    res["note"] = ("构件动画已写入文件并重新打开。第一个新动画默认「点按」出场，后面的默认「与上一个同时」（你的习惯）。"
                   "在 Keynote 的「动画」面板里可以看到和调整；改之前的文件备份在 backup。")
    return res


def h_list_builds(ctx, a):
    from . import builds as B
    if not B.available():
        B._import()
    sc = Script(ctx, a.get("document"), guard=False)
    sc.add("set p to my docPath(d)",
           "if p is missing value then error \"这份文稿还没有保存位置。\" number 9008",
           "set canSave to true", "try", "my guardDoc(d, item 2 of argv)", "on error", "set canSave to false", "end try",
           "if canSave then", 'tell application "Keynote" to save d', "end if",
           'return "{\\"path\\":" & my js(p) & ",\\"saved\\":" & my jb(canSave) & "}"')
    info = sc.run(timeout=300)
    slide = int(num(a["slide"], "slide", lo=1, integer=True)) if a.get("slide") else None
    out = {"slides": B.read_builds(info["path"], slide)}
    if not info["saved"]:
        out["note"] = "这份文稿受保护，读到的是磁盘上最后保存的版本。"
    return out


def h_trace_mtf(ctx, a):
    from . import builds as B
    from . import curves as C
    img = ctx.cfg.to_local(a["image"])
    if not os.path.isfile(img):
        raise ValueError("找不到 Zemax 图：%s" % a["image"])
    n = slide_no(a["slide"])
    x_max = float(num(a.get("x_max", 21.6), "x_max", lo=0.1, hi=1000))
    B._import()
    sc = Script(ctx, a.get("document"), guard=True)
    sc.add("set p to my docPath(d)",
           "if p is missing value then error \"这份文稿还没有保存位置，先用 save_presentation 指定 path。\" number 9008",
           "my getSlide(d, %d)" % n,
           'tell application "Keynote" to save d', 'tell application "Keynote" to close d saving yes',
           'return "{\\"path\\":" & my js(p) & "}"')
    path = sc.run(timeout=300)["path"]
    try:
        res = C.write_curves(path, n, img, x_max=x_max, tol=float(a.get("tolerance", 1.5)),
                             backup_dir=os.path.join(_cache_dir(ctx), "描曲线前备份"))
    finally:
        _reopen(ctx, path)
    if a.get("replace_base_image", True):
        pl = res["new_base_placement"]
        h_update_item(ctx, {"document": path, "slide": n, "item": res["base_item"], "image": img,
                            "x": pl["x"], "y": pl["y"], "width": pl["width"]})
        sc = Script(ctx, path, guard=True)
        sc.add('tell application "Keynote" to save d', 'return "ok"')
        sc.run()
    series = res.pop("series")
    res["mtf_at"] = C.mtf_table(series, x_max, a.get("fields"))
    res["note"] = ("已按 Zemax 图重画这一页的曲线（颜色、虚实、线条描绘动画不变），底图换成了新图并按坐标轴对齐（透明度不变）。"
                   "这一页没有对应曲线的频率列在 not_on_slide。横轴最大视场按 x_max=%s mm；镜头视场不同时记得改刻度文字。" % x_max)
    return res


def h_read_zemax_mtf(ctx, a):
    from . import curves as C
    img = ctx.cfg.to_local(a["image"])
    if not os.path.isfile(img):
        raise ValueError("找不到 Zemax 图：%s" % a["image"])
    csv_path = None
    if a.get("csv_path"):
        csv_path = ctx.cfg.to_local(a["csv_path"])
        ctx.cfg.check_writable(csv_path)
    return C.read_image(img, x_max=float(num(a.get("x_max", 21.6), "x_max", lo=0.1, hi=1000)),
                        fields=a.get("fields"), csv_path=csv_path)


# --------------------------------------------------------------- product card --

SUBTITLE_Y = 1000  # the author's horizontal guide: subtitles live below this line
PRODUCT_SIGNATURE = "line,shape,text item,text item,image,image,text item,shape,text item"


def _cutout(ctx, path):
    """Background-removed, subject-cropped PNG via macOS Vision (same as Keynote's Remove Background)."""
    import hashlib
    exe = os.path.join(PROJECT_DIR, ".build", "kn-cutout")
    src = os.path.join(PROJECT_DIR, "keynote_mcp", "cutout.swift")
    if not os.path.exists(exe) or os.path.getmtime(exe) < os.path.getmtime(src):
        os.makedirs(os.path.dirname(exe), exist_ok=True)
        p = subprocess.run(["swiftc", "-O", "-o", exe, src], capture_output=True, text=True, timeout=300)
        if p.returncode != 0:
            raise RuntimeError("编译抠图工具失败（需要 Xcode 命令行工具）：%s" % p.stderr[-300:])
    h = hashlib.md5((path + str(os.path.getmtime(path))).encode("utf-8")).hexdigest()[:8]
    out_dir = os.path.join(_cache_dir(ctx), "抠图")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "%s-%s.png" % (os.path.splitext(os.path.basename(path))[0], h))
    if os.path.exists(out):
        return out, True
    p = subprocess.run([exe, path, out], capture_output=True, text=True, timeout=120)
    if p.returncode != 0 or not os.path.exists(out):
        return path, False
    return out, True


def _find_product_sample(ctx, doc):
    sc = Script(ctx, doc, guard=False)
    sc.add(
        'tell application "Keynote" to set c to count of slides of d',
        "repeat with i from 1 to c",
        'tell application "Keynote"',
        "set s to slide i of d",
        "set L to {}",
        "repeat with itm in iWork items of s",
        "set end of L to (class of itm) as text",
        "end repeat",
        "end tell",
        'if my joinList(L, ",") is %s then return i' % sc.arg(PRODUCT_SIGNATURE),
        "end repeat",
        "return 0",
    )
    n = int(sc.run() or 0)
    if not n:
        raise ValueError("文稿里找不到产品介绍样例页（模板第 18 页）。用 create_presentation 从模板新建的文稿才有。")
    return n


def h_product_card(ctx, a):
    prods = as_list(a.get("products"))
    if not 1 <= len(prods) <= 2:
        raise ValueError("products 放 1 或 2 款产品，例如 [{\"image\": \"…png\", \"name\": \"…\", \"badge\": \"Since 2025.05.20\"}]")
    for p in prods:
        if not p.get("name"):
            raise ValueError("每款产品都要有 name")
        if p.get("image"):
            p["image"] = ctx.cfg.to_local(p["image"])
            if not os.path.isfile(p["image"]):
                raise ValueError("图片不存在：%s" % p["image"])
    two = len(prods) == 2
    arrow = a.get("arrow", two)
    doc = a.get("document")
    sample = _find_product_sample(ctx, doc)
    # duplicate the sample and move the copy where it belongs
    after = a.get("after")
    sc = Script(ctx, doc, guard=True)
    sc.add("set s to my getSlide(d, %d)" % sample, 'tell application "Keynote" to duplicate s to after s',
           'tell application "Keynote" to set c to count of slides of d', 'return "{\\"count\\":" & c & "}"')
    total = sc.run()["count"]
    new = sample + 1
    if after is not None and after != "end":
        target = int(num(after, "after", lo=0, integer=True)) + (1 if int(after) < new else 0)
        target = max(1, min(target, total))
    else:
        target = total
    if target != new:
        h_move_slide(ctx, {"document": doc, "slide": new, "to": target})
    n = target
    # original item order: 1 arrow, 2 badgeR, 3 badgeR text, 4 nameR, 5 imgL, 6 imgR, 7 nameL, 8 badgeL, 9 badgeL text
    left, right = prods[0], (prods[1] if two else None)
    delete = {5, 6}
    if not two:
        delete |= {2, 3, 4}
    if not arrow:
        delete.add(1)
    if not left.get("badge"):
        delete |= {8, 9}
    if two and not right.get("badge"):
        delete |= {2, 3}
    keep = [i for i in range(1, 10) if i not in delete]
    idx = {orig: keep.index(orig) + 1 for orig in keep}
    cx_l, cx_r = (537.5, 1321.5) if two else (960.0, None)
    sc = Script(ctx, doc, guard=True)
    sc.add("set s to my getSlide(d, %d)" % n)
    for k in sorted(delete, reverse=True):
        sc.add('tell application "Keynote" to delete iWork item %d of s' % k)
    sc.add('tell application "Keynote"')

    def slot(prod, cx, name_i, badge_i, text_i):
        sc.add("set t to iWork item %d of s" % idx[name_i], "set object text of t to %s" % sc.arg(prod["name"]))
        if prod.get("highlight"):
            sc.add("my highlightText(t, %s, %s)" % (sc.arg(prod["highlight"]), color16(HOUSE_RED)))
        sc.add("set position of t to {round (%s - (width of t) / 2), 830}" % num(cx, "cx"))
        if prod.get("badge"):
            sc.add("set t to iWork item %d of s" % idx[text_i], "set object text of t to %s" % sc.arg(prod["badge"]),
                   "set b to iWork item %d of s" % idx[badge_i],
                   "set bw to (width of t) + 22",
                   "if bw < 120 then set bw to 120",
                   "set width of b to round bw",
                   "set position of b to {round (%s - bw / 2), 926}" % num(cx, "cx"),
                   "set position of t to {round (%s - (width of t) / 2), 938}" % num(cx, "cx"))
    slot(left, cx_l, 7, 8, 9)
    if two:
        slot(right, cx_r, 4, 2, 3)
    sc.add("end tell", 'return "ok"')
    sc.run()
    # product images: background removed, scaled to fit, bottom-aligned above the names
    max_w, max_h, bottom = (560, 600, 690) if two else (900, 620, 700)
    images = []
    for prod, cx in ((left, cx_l), (right, cx_r)):
        if not prod or not prod.get("image"):
            continue
        img, cut = (_cutout(ctx, prod["image"]) if a.get("remove_background", True) else (prod["image"], False))
        sc = Script(ctx, doc, guard=True)
        sc.add("set s to my getSlide(d, %d)" % n, 'tell application "Keynote"',
               "tell s to set t to make new image with properties {file:(POSIX file %s)}" % sc.arg(img),
               "set w to width of t", "set h to height of t",
               "set k to %s / w" % num(max_w, "w"),
               "if (%s / h) < k then set k to %s / h" % (num(max_h, "h"), num(max_h, "h")),
               "set width of t to round (w * k)",
               "set position of t to {round (%s - (width of t) / 2), round (%s - (height of t))}" % (num(cx, "cx"), num(bottom, "b")),
               "set kk to count of iWork items of s", "end tell", "return my itemJSON(t, kk, false)")
        it = sc.run()
        it["background_removed"] = cut
        images.append(it)
    info = _slide_items(ctx, doc, n, full=False)
    return {"slide": n, "items": info["items"], "images": images,
            "note": "产品介绍页已按样例页（模板第 18 页）的版式生成：产品图底边对齐、产品名 38 号、灰色标签按文字长短自动加宽，"
                    "全部在 y=%d 的字幕参考线以上。产品图已用 macOS 的主体识别去掉背景。" % SUBTITLE_Y}


def _subtitle_warning(it):
    try:
        bottom = float(it.get("y", 0)) + float(it.get("height", 0))
    except (TypeError, ValueError):
        return it
    if bottom > SUBTITLE_Y + 0.5:
        it["warning"] = "这个对象伸到了字幕参考线（y=%d）以下，底边在 y=%.0f，视频里会和字幕重叠。" % (SUBTITLE_Y, bottom)
    return it


# ------------------------------------------------------------------ export --

def _default_export_path(ctx, deck, fmt):
    doc = deck["document"]
    stem = os.path.splitext(doc["name"])[0]
    base = os.path.dirname(doc["path"]) if doc.get("path") and ctx.cfg.in_roots(doc["path"]) else \
        os.path.join(ctx.cfg.ensure_workspace(), "导出")
    if fmt == "movie":
        return os.path.join(base, "%s1-%d.mov" % (stem, doc["slides"]))
    if fmt in ("png", "jpeg", "tiff"):
        return os.path.join(base, "%s_图片" % stem)
    return os.path.join(base, "%s.%s" % (stem, {"pdf": "pdf", "pptx": "pptx"}[fmt]))


def _timeline(deck, slide_seconds, fps):
    t = 0.0
    out = []
    for s in deck["slides"]:
        if s["skipped"]:
            continue
        tp = s["transition"]
        auto = tp.get("automatic")
        hold = float(tp["delay"]) if auto else float(slide_seconds or CLICK_SLIDE_SECONDS)
        trans = float(tp["duration"]) if tp["effect"] != "no transition effect" else 0.0
        e = {
            "slide": s["slide"],
            "fig": tag_of(s.get("notes")),
            "title": s.get("title"),
            "blank": s["items"] == 0,
            "hold": [round(t, 3), round(t + hold, 3)],
            "transition": {"effect": tp["effect"], "start": round(t + hold, 3), "end": round(t + hold + trans, 3)},
        }
        if fps:
            e["hold_frames"] = [int(round(t * fps)), int(round((t + hold) * fps))]
        notes = (s.get("notes") or "").strip()
        if notes:
            e["notes_head"] = notes[:40]
        out.append(e)
        t += hold + trans
    return out, round(t, 3)


def h_export(ctx, a):
    if ctx.running_job():
        raise RuntimeError("已有一个视频导出在进行中，请先用 export_status 查看")
    fmt = str(a.get("format", "pdf")).lower()
    if fmt in ("jpg",):
        fmt = "jpeg"
    if fmt in ("images", "image"):
        fmt = "png"
    if fmt in ("mov", "video", "quicktime"):
        fmt = "movie"
    if fmt not in ("pdf", "png", "jpeg", "tiff", "pptx", "movie"):
        raise ValueError("format 只能是 pdf / png / jpeg / tiff / pptx")
    if fmt == "movie" and not ctx.cfg.allow_movie:
        raise PermissionError(MOVIE_OFF)
    deck = _deck(ctx, a.get("document"))
    doc_key = deck["document"].get("path") or deck["document"]["name"]
    path = ctx.cfg.to_local(a["path"]) if a.get("path") else _default_export_path(ctx, deck, fmt)
    ctx.cfg.check_writable(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        if not a.get("overwrite"):
            raise ValueError("输出位置已存在：%s（要覆盖请传 overwrite=true）" % path)
        _trash(path)

    if fmt == "movie":
        return _export_movie(ctx, a, deck, doc_key, path)

    subset = sorted({slide_no(x) for x in as_list(a.get("slides"))})
    sc = Script(ctx, doc_key, guard=bool(subset))
    p = sc.arg(path)
    if fmt == "pdf":
        style = "SlideWithNotes" if a.get("with_notes") else "IndividualSlides"
        q = {"good": "Good", "better": "Better", "best": "Best"}.get(str(a.get("quality", "best")).lower(), "Best")
        cmd = "export d to POSIX file %s as PDF with properties {PDF image quality:%s, export style:%s, skipped slides:false}" % (p, q, style)
    elif fmt == "pptx":
        if not path.lower().endswith(".pptx"):
            raise ValueError("PPTX 输出路径要以 .pptx 结尾")
        cmd = "export d to POSIX file %s as Microsoft PowerPoint" % p
    else:
        props = "image format:%s, skipped slides:false" % {"png": "PNG", "jpeg": "JPEG", "tiff": "TIFF"}[fmt]
        if fmt == "jpeg":
            props += ", compression factor:%s" % num(a.get("quality", 0.9), "quality", lo=0, hi=1)
        cmd = "export d to POSIX file %s as slide images with properties {%s}" % (p, props)
    if subset:
        sc.add('tell application "Keynote"', "set c to count of slides of d", "set skL to skipped of every slide of d",
               "set want to {%s}" % ", ".join(str(i) for i in subset),
               "try",
               "repeat with i from 1 to c",
               "set sk to (want does not contain i)",
               "if (item i of skL) is not sk then set skipped of slide i of d to sk",
               "end repeat",
               cmd,
               "on error e number en",
               "repeat with i from 1 to c",
               "if (skipped of slide i of d) is not (item i of skL) then set skipped of slide i of d to (item i of skL)",
               "end repeat",
               "error e number en",
               "end try",
               "repeat with i from 1 to c",
               "if (skipped of slide i of d) is not (item i of skL) then set skipped of slide i of d to (item i of skL)",
               "end repeat",
               "if my docPath(d) is not missing value then save d",
               "end tell")
    else:
        sc.add('tell application "Keynote" to ' + cmd)
    sc.add('return "ok"')
    sc.run(timeout=1800)
    result = {"format": fmt, "path": path}
    if fmt in ("png", "jpeg", "tiff"):
        files = sorted(glob.glob(os.path.join(path, "*")))
        result["files"] = len(files)
        result["first"] = files[0] if files else None
        result["note"] = "文件按导出顺序编号（跳过的页不导出）。"
    else:
        result["bytes"] = os.path.getsize(path) if os.path.exists(path) else None
    return result


def _export_movie(ctx, a, deck, doc_key, path):
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".mov", ".m4v", ".mp4"):
        raise ValueError("视频路径要以 .mov / .m4v / .mp4 结尾")
    res = str(a.get("resolution", "1080p")).lower()
    if res not in MOVIE_FORMATS:
        raise ValueError("resolution 只能是 %s" % ", ".join(MOVIE_FORMATS))
    fps_key = ("%g" % float(a.get("fps", 60)))
    if fps_key not in FPS:
        raise ValueError("fps 只能是 %s" % ", ".join(FPS))
    codec = str(a.get("codec", "h264")).lower().replace(" ", "").replace("-", "")
    if codec not in CODECS:
        raise ValueError("codec 只能是 %s" % ", ".join(CODECS))
    if ext == ".mp4" and codec.startswith("prores"):
        raise ValueError("ProRes 请用 .mov")
    if codec != "h264" and res != "native":
        raise ValueError("Keynote 只在 resolution=native 时使用 %s 编码（预设分辨率一律是 H.264）。"
                         "要 4K 的 HEVC/ProRes，请把文稿尺寸设为 3840x2160 再用 resolution=native。" % codec)
    ss = a.get("slide_seconds")
    ss = float(num(ss, "slide_seconds", lo=0.1, hi=3600)) if ss is not None else None
    props = "movie format:%s, movie codec:%s, movie framerate:%s, skipped slides:false" % (
        MOVIE_FORMATS[res], CODECS[codec], FPS[fps_key])
    # Keynote's export only accepts a .m4v name; rename afterwards.
    tmp_path = os.path.splitext(path)[0] + ".keynote-export-%s.m4v" % uuid.uuid4().hex[:6]
    sc = Script(ctx, doc_key, guard=ss is not None)
    p = sc.arg(tmp_path)
    sc.add('tell application "Keynote"', "set c to count of slides of d",
           "set tpL to transition properties of every slide of d", "end tell", "set changed to {}")
    if ss is not None:
        sc.add("repeat with i from 1 to c",
               "set tp to item i of tpL",
               'tell application "Keynote"',
               "if (automatic transition of tp) is false then",
               "set transition properties of slide i of d to {automatic transition:true, transition delay:%s}" % num(ss, "slide_seconds"),
               "set end of changed to i",
               "end if",
               "end tell",
               "end repeat")
    sc.add("set errMsg to \"\"",
           "try",
           'tell application "Keynote" to export d to POSIX file %s as QuickTime movie with properties {%s}' % (p, props),
           "on error e",
           "set errMsg to e",
           "end try",
           "repeat with i in changed",
           'tell application "Keynote" to set transition properties of slide (contents of i) of d to (item (contents of i) of tpL)',
           "end repeat",
           "if (count of changed) > 0 and my docPath(d) is not missing value then",
           'tell application "Keynote" to save d',
           "end if",
           "if errMsg is not \"\" then error errMsg number 9010",
           'return "ok"')
    fps_val = float(fps_key)
    timeline, est = _timeline(deck, ss, fps_val)
    proc = osa.popen("\n".join(sc.lines), sc.args)
    job = {"id": uuid.uuid4().hex[:8], "proc": proc, "started": time.time(), "tmp": tmp_path,
           "path": path, "timeline": timeline, "estimated": est, "done": False, "result": None,
           "settings": {"resolution": res, "fps": fps_val, "codec": codec, "slide_seconds": ss or CLICK_SLIDE_SECONDS,
                        "slide_seconds_applied_to_click_slides": ss is not None},
           "document": deck["document"]["name"], "manifest": a.get("manifest", True)}
    ctx.jobs[job["id"]] = job
    wait = float(num(a.get("wait_seconds", 45), "wait_seconds", lo=0, hi=3000))
    deadline = time.time() + wait
    while time.time() < deadline and proc.poll() is None:
        time.sleep(0.5)
    return _job_status(ctx, job)


def _job_status(ctx, job):
    if job["done"]:
        return job["result"]
    proc = job["proc"]
    if proc.poll() is None:
        return {"job": job["id"], "status": "running", "elapsed": round(time.time() - job["started"], 1),
                "estimated_movie_seconds": job["estimated"], "path": job["path"],
                "note": "视频还在导出，过一会儿用 export_status 查看。导出期间不要操作 Keynote。"}
    out, err = osa.collect(proc)
    job["done"] = True
    if proc.returncode != 0:
        if os.path.exists(job["tmp"]):
            os.remove(job["tmp"])
        job["result"] = {"job": job["id"], "status": "failed", "error": osa.clean_error(err)}
        return job["result"]
    os.replace(job["tmp"], job["path"])
    info = movie_info(job["path"])
    res = {"job": job["id"], "status": "done", "path": job["path"], "seconds": round(time.time() - job["started"], 1),
           "movie": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in info.items()},
           "bytes": os.path.getsize(job["path"]), "settings": job["settings"]}
    want = {"h264": "avc1", "hevc": "hvc1", "h265": "hvc1"}.get(job["settings"]["codec"])
    if job["settings"]["codec"].startswith("prores"):
        want = "apc"
    if want and info.get("codec") and not info["codec"].startswith(want):
        res["codec_note"] = ("Keynote 只在 resolution=native（按文稿原始尺寸）时才使用指定的编码，这次实际是 %s。"
                             "要 4K 的 HEVC/ProRes，请把文稿尺寸设为 3840x2160 再用 native 导出。" % info["codec"])
    diff = round((info["duration"] or 0) - job["estimated"], 3)
    res["timing_check"] = {"estimated": job["estimated"], "actual": round(info["duration"] or 0, 3), "diff": diff,
                           "ok": abs(diff) < 0.25}
    if not res["timing_check"]["ok"]:
        res["timing_check"]["note"] = "实际时长和按转场设置推算的不一致，可能有对象构件动画；slides.json 里的时间只能作参考。"
    if job["manifest"]:
        man = os.path.splitext(job["path"])[0] + ".slides.json"
        with open(man, "w", encoding="utf-8") as f:
            json.dump({"movie": os.path.basename(job["path"]), "document": job["document"],
                       "fps": info["fps"], "width": info["width"], "height": info["height"],
                       "duration": info["duration"], "settings": job["settings"],
                       "timing_check": res["timing_check"], "slides": job["timeline"]},
                      f, ensure_ascii=False, indent=1)
        res["manifest"] = man
    job["result"] = res
    return res


def h_export_status(ctx, a):
    jid = a.get("job")
    if jid:
        job = ctx.jobs.get(jid)
        if not job:
            raise ValueError("没有这个导出任务：%s" % jid)
        return _job_status(ctx, job)
    if not ctx.jobs:
        return {"jobs": []}
    return {"jobs": [_job_status(ctx, j) for j in ctx.jobs.values()]}


def h_run_applescript(ctx, a):
    if not ctx.cfg.allow_script:
        raise PermissionError("run_applescript 默认关闭。需要时在配置里设置 KEYNOTE_MCP_ALLOW_SCRIPT=1。")
    return {"output": osa.run(a["code"], [], timeout=int(a.get("timeout", 120)), as_json=False)}


# ---------------------------------------------------------------- registry --

DOC = {"type": "string", "description": "目标文稿：文稿名或完整路径。不填 = Keynote 最前面的文稿。"}
SLIDE = {"type": "integer", "minimum": 1, "description": "页号，从 1 开始"}
ITEM = {"type": "integer", "minimum": 1, "description": "对象编号（get_slide 返回的 item），从 1 开始"}
NUM = {"type": "number"}
COLOR = {"type": "string", "description": "颜色 #RRGGBB"}

CHANGE_PROPS = {
    "x": NUM, "y": NUM, "width": NUM, "height": NUM,
    "rotation": {"type": "number", "description": "角度 0-359"},
    "opacity": {"type": "number", "description": "不透明度 0-100"},
    "text": {"type": "string"}, "font": {"type": "string", "description": "字体 PostScript 名，如 PingFangSC-Semibold"},
    "size": {"type": "number", "description": "字号"}, "color": COLOR,
    "image": {"type": "string", "description": "换成另一张图片（文件路径）"},
    "description": {"type": "string", "description": "图片的说明文字（可用作标签）"},
    "locked": {"type": "boolean"},
    "highlight": {"description": "把文字里的一段标成红色（你的 F 值红 #FC4B3E）：字符串，或 {text, color}，或它们的数组",
                  "anyOf": [{"type": "string"}, {"type": "object"}, {"type": "array"}]},
}


def _schema(props, required=()):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


TOOLS = [
    ("keynote_status", "查看 Keynote 是否在运行、打开了哪些文稿（路径、页数、是否受保护），以及允许写入的目录。",
     _schema({}), h_status),
    ("list_themes", "列出可用的 Keynote 主题名。", _schema({}), h_list_themes),
    ("create_presentation", "新建一份 Keynote 文稿并立即保存到本机（默认放在工作目录 ~/KeynoteMCP）。默认从你每期的初始文件 KEYNOTE动画模板 新建（17 页：1–11 空白页，12–17 纵向像差 / MTF / 封面 / 结构图样例），返回文稿信息和 template_guide（每页是什么、哪些文字可以改）。",
     _schema({"path": {"type": "string", "description": "保存路径（.key）。不填则用 name 放进工作目录"},
              "name": {"type": "string", "description": "不给 path 时的文件名"},
              "theme": {"type": "string", "description": "主题名，默认 Basic Black（你 78 份文稿里 65 份用它），白底用 Basic White"},
              "width": {"type": "integer", "description": "默认 1920"}, "height": {"type": "integer", "description": "默认 1080"},
              "template": {"type": "string", "description": "从这份 .key 模板新建；默认用项目里的 templates/KEYNOTE动画模板.key（你每期的初始文件）。传 none 或给 theme 则用 Keynote 主题新建"},
              "overwrite": {"type": "boolean"}}), h_create),
    ("open_presentation", "打开一份 .key 文稿。文件不在允许修改的目录里时（比如移动硬盘上的原稿），会先复制一份到本机再打开，原文件不会被改动。",
     _schema({"path": {"type": "string"}}, ["path"]), h_open),
    ("import_pptx", "把 PowerPoint 文件（.pptx/.ppt，比如 Windows 上做好的主体）转成 Keynote 文稿：Keynote 打开后立即另存为 .key（原文件不动），"
                    "PPT 里的「平滑」转场默认自动改成神奇移动（自动处理 PPT 第 N 页 = Keynote 第 N-1 页的换算），"
                    "并逐页列出备注里的 [图N]、[动画] 说明、PPT 转场和进入/退出动画、对象名，以及 Mac 上可能缺的字体，方便接着重制。",
     _schema({"path": {"type": "string", "description": ".pptx 或 .ppt 路径"},
              "save_as": {"type": "string", "description": "另存的 .key 路径；不填则放在工作目录（或 PPTX 旁边，若它在允许目录里）"},
              "morph_to_magic_move": {"type": "boolean", "description": "把平滑转成神奇移动，默认 true"},
              "overwrite": {"type": "boolean"}}, ["path"]), h_import_pptx),
    ("save_presentation", "保存文稿；给 path 则另存到新位置。", _schema({"document": DOC, "path": {"type": "string"}}), h_save),
    ("close_presentation", "关闭文稿，默认先保存。", _schema({"document": DOC, "save": {"type": "boolean"}}), h_close),
    ("get_deck", "读取整份文稿的结构：每页的版式、标题、对象数、备注（含 [图N] 标签）、是否跳过、转场设置。",
     _schema({"document": DOC, "notes": {"type": "string", "enum": ["short", "full", "none"], "description": "备注显示方式，默认 short"}}),
     h_get_deck),
    ("get_slide", "读取某一页的所有对象：编号、类型、位置、尺寸、文字、字体字号颜色、图片文件、透明度、旋转等，以及备注和转场。",
     _schema({"document": DOC, "slide": SLIDE}, ["slide"]), h_get_slide),
    ("view_slide", "把某一页渲染成图片给你看（用来检查排版和动画前后两页的效果），同时附上对象列表。",
     _schema({"document": DOC, "slide": SLIDE, "max_size": {"type": "integer", "description": "图片最长边像素，默认 1280"},
              "save_to": {"type": "string", "description": "另存一份原尺寸 PNG 到这个路径"},
              "with_items": {"type": "boolean"}}, ["slide"]), h_view_slide),
    ("add_slide", "加一页。默认加在末尾、用 Blank 版式，转场默认 0.5 秒神奇移动（你的习惯）。可同时写标题、正文和演讲者备注。",
     _schema({"document": DOC, "layout": {"type": "string", "description": "版式名，见 create_presentation / get_deck 返回的 layouts"},
              "after": {"type": "integer", "minimum": 0, "description": "插在第几页之后；0 = 最前面；不填 = 末尾"},
              "title": {"type": "string"}, "body": {"type": "string"}, "notes": {"type": "string"},
              "transition": {"type": "string", "description": "这一页切到下一页的转场，默认 magic move；none = 无"},
              "inherit_background": {"type": "boolean", "description": "空白页默认复制前一页再清空，沿用它的背景；false 则用主题的 Blank 版式"},
              "duration": {"type": "number", "description": "转场时长，默认 0.5"}}), h_add_slide),
    ("duplicate_slide", "复制一页（副本紧跟在原页后面）。", _schema({"document": DOC, "slide": SLIDE, "count": {"type": "integer"}}, ["slide"]),
     h_duplicate_slide),
    ("delete_slides", "删除一页或多页。", _schema({"document": DOC, "slides": {"type": "array", "items": {"type": "integer"}}}, ["slides"]),
     h_delete_slides),
    ("move_slide", "把一页移到新位置（to = 移动后的页号）。", _schema({"document": DOC, "slide": SLIDE, "to": SLIDE}, ["slide", "to"]),
     h_move_slide),
    ("set_slide", "修改一页的属性：版式、是否跳过、标题/正文占位符的文字和显示。",
     _schema({"document": DOC, "slide": SLIDE, "layout": {"type": "string"}, "skipped": {"type": "boolean"},
              "title": {"type": "string"}, "body": {"type": "string"},
              "title_showing": {"type": "boolean"}, "body_showing": {"type": "boolean"}}, ["slide"]), h_set_slide),
    ("add_text", "在一页上加文本框。x/y 是左上角坐标（点），不填则居中。width 固定宽度后会自动换行。",
     _schema({"document": DOC, "slide": SLIDE, "text": {"type": "string"}, "x": NUM, "y": NUM, "width": NUM,
              "font": CHANGE_PROPS["font"], "size": CHANGE_PROPS["size"], "color": COLOR,
              "opacity": CHANGE_PROPS["opacity"], "rotation": CHANGE_PROPS["rotation"],
              "highlight": CHANGE_PROPS["highlight"]}, ["slide", "text"]), h_add_text),
    ("add_image", "在一页上放图片（png/jpg/tiff/pdf 等）。只给 width 或 height，另一边按比例；不给位置则居中。",
     _schema({"document": DOC, "slide": SLIDE, "path": {"type": "string"}, "x": NUM, "y": NUM, "width": NUM, "height": NUM,
              "opacity": CHANGE_PROPS["opacity"], "rotation": CHANGE_PROPS["rotation"],
              "description": CHANGE_PROPS["description"]}, ["slide", "path"]), h_add_image),
    ("add_shape", "加一个矩形形状（可带文字）。注意：Keynote 脚本接口不能设置形状填充色，用的是主题默认样式。",
     _schema({"document": DOC, "slide": SLIDE, "x": NUM, "y": NUM, "width": NUM, "height": NUM, "text": {"type": "string"},
              "font": CHANGE_PROPS["font"], "size": CHANGE_PROPS["size"], "color": COLOR,
              "opacity": CHANGE_PROPS["opacity"], "rotation": CHANGE_PROPS["rotation"]}, ["slide"]), h_add_shape),
    ("add_line", "加一条直线（起点 x1,y1 到终点 x2,y2）。",
     _schema({"document": DOC, "slide": SLIDE, "x1": NUM, "y1": NUM, "x2": NUM, "y2": NUM}, ["slide", "x1", "y1", "x2", "y2"]),
     h_add_line),
    ("add_table", "加一个表格，rows 是二维数组（第一行默认作表头）。",
     _schema({"document": DOC, "slide": SLIDE, "rows": {"type": "array", "items": {"type": "array"}},
              "header_rows": {"type": "integer"}, "header_columns": {"type": "integer"},
              "x": NUM, "y": NUM, "width": NUM, "height": NUM}, ["slide", "rows"]), h_add_table),
    ("update_item", "修改一页上某个对象：位置、尺寸、旋转、透明度、文字及其字体字号颜色、换图、锁定。只改你给出的属性。",
     _schema(dict({"document": DOC, "slide": SLIDE, "item": ITEM}, **CHANGE_PROPS), ["slide", "item"]), h_update_item),
    ("delete_items", "删除一页上的一个或多个对象。",
     _schema({"document": DOC, "slide": SLIDE, "items": {"type": "array", "items": {"type": "integer"}}}, ["slide", "items"]),
     h_delete_items),
    ("set_notes", "写演讲者备注（视频文稿段落放这里）。append=true 则追加。",
     _schema({"document": DOC, "slide": SLIDE, "text": {"type": "string"}, "append": {"type": "boolean"}}, ["slide", "text"]),
     h_set_notes),
    ("set_transition", "设置转场（从这一页切到下一页的效果）。effect 可用 magic move（神奇移动）、dissolve（溶解）、none 等 40 多种；"
                       "automatic=true 表示 delay 秒后自动切换。没给的字段保持原值。",
     _schema({"document": DOC, "slides": {"description": "页号、页号数组，或 \"all\""},
              "effect": {"type": "string"}, "duration": {"type": "number", "description": "转场时长（秒）"},
              "delay": {"type": "number", "description": "自动切换前的停留秒数"},
              "automatic": {"type": "boolean"}}), h_set_transition),
    ("magic_move_step", "做一步神奇移动动画：复制第 slide 页放到它后面，对新页上的对象做 changes（移动、缩放、旋转、淡化、改字、换图），"
                        "删掉 remove_items，并把原页的转场设为神奇移动。连续调用就能做出多步动画。对象编号沿用原页的编号。",
     _schema({"document": DOC, "slide": SLIDE,
              "changes": {"type": "array", "items": _schema(dict({"item": ITEM}, **CHANGE_PROPS), ["item"])},
              "remove_items": {"type": "array", "items": {"type": "integer"}, "description": "新页上要删掉的对象（神奇移动时淡出）"},
              "duration": {"type": "number", "description": "神奇移动时长，默认 0.5 秒"},
              "delay": {"type": "number"}, "automatic": {"type": "boolean"}}, ["slide"]), h_magic_move_step),
    ("chapter_card", "按你文稿里的章节卡样式插入三页：空白页 → 编号（76 号）+ 标题（71 号）两个白色居中文本框 → 空白页，转场 0.5 秒神奇移动。"
                     "title 可以直接写「二 性能仿真与测量」，会自动拆出编号。",
     _schema({"document": DOC, "title": {"type": "string", "description": "如「二 性能仿真与测量」或只写标题"},
              "number": {"type": "string", "description": "编号（一、二…）；不填则从 title 开头拆"},
              "after": {"type": "integer", "minimum": 0, "description": "插在第几页之后，不填 = 末尾"},
              "number_size": {"type": "number", "description": "默认 76"}, "title_size": {"type": "number", "description": "默认 71"},
              "font": CHANGE_PROPS["font"], "color": {"type": "string", "description": "默认 #FFFFFF"},
              "transition": {"type": "string", "description": "默认 magic move"}, "duration": {"type": "number", "description": "默认 0.5"},
              "blank_before": {"type": "boolean"}, "blank_after": {"type": "boolean"}, "notes": {"type": "string"}},
             ["title"]), h_chapter_card),
    ("add_builds", "给对象加构件动画（Keynote 脚本接口本身做不到，这里直接写进文件）：line_draw = 线条描绘（曲线、坐标轴、光路），"
                   "fade_move = 逐字淡入移动（图例文字、色块）。文稿会被保存、关闭、写入后重新打开，改前自动备份。"
                   "start：click 点按 / with 与上一个同时 / after 在上一个之后；不填时每页第一个新动画点按、其余与上一个同时（你的习惯）。"
                   "item 用 get_slide 的对象编号；可以一次给多页。",
     _schema({"document": DOC,
              "builds": {"type": "array", "items": _schema({
                  "slide": SLIDE, "item": ITEM,
                  "effect": {"type": "string", "enum": ["line_draw", "fade_move"]},
                  "duration": {"type": "number", "description": "秒；默认 line_draw 2.0、fade_move 1.5（你常用 1.5–3）"},
                  "start": {"type": "string", "enum": ["click", "with", "after"]},
                  "delay": {"type": "number"}}, ["slide", "item"])}}, ["builds"]), h_add_builds),
    ("list_builds", "列出构件动画（脚本接口读不到，这里从文件里读）：每页哪些对象有什么动画、时长、出场方式。",
     _schema({"document": DOC, "slide": SLIDE}), h_list_builds),
    ("trace_mtf", "按 Zemax「FFT MTF vs. Field」导出图自动描曲线：识别图里各频率的子午（实线）和弧矢（点线）曲线，"
                  "写进这一页现成的曲线（按颜色对应 10/20/40/80/160/320 线对，样式和线条描绘动画不变），并把底图换成这张图、按坐标轴对齐。"
                  "这一页需要是 MTF vs Field 图表页（模板第 14 页或它的副本，最底层有一张旧的 Zemax 底图用来定位）。返回各视场位置的 MTF 数值。",
     _schema({"document": DOC, "slide": SLIDE, "image": {"type": "string", "description": "Zemax 导出的 FFT MTF vs Field 图（png/jpg）"},
              "x_max": {"type": "number", "description": "横轴最大视场（mm），默认 21.6"},
              "fields": {"type": "array", "items": {"type": "number"}, "description": "返回这些视场位置的 MTF，默认 0 / 0.5 / 0.7 / 最大"},
              "replace_base_image": {"type": "boolean", "description": "把底图换成这张 Zemax 图，默认 true"},
              "tolerance": {"type": "number", "description": "曲线拟合容差（点），默认 1.5；越小节点越多"}},
             ["slide", "image"]), h_trace_mtf),
    ("read_zemax_mtf", "只读：从 Zemax「FFT MTF vs. Field」导出图里读出各频率子午/弧矢的 MTF 数值（不改文稿），可存成 CSV，写稿引用数据时用。",
     _schema({"image": {"type": "string"}, "x_max": {"type": "number"},
              "fields": {"type": "array", "items": {"type": "number"}},
              "csv_path": {"type": "string", "description": "把整条曲线的数据存成 CSV"}}, ["image"]), h_read_zemax_mtf),
    ("product_card", "做产品介绍页（你视频前段那种）：白底产品图（自动去背景）、产品名、灰色圆角标签（发布日期 Since… 或价格），"
                     "1 款居中，2 款左右并排、中间一条箭头。版式照搬模板第 18 页（松若85 那页），全部在 y=1000 字幕参考线以上。",
     _schema({"document": DOC,
              "products": {"type": "array", "items": _schema({
                  "image": {"type": "string", "description": "产品图（白底或已去背景的 png/jpg）"},
                  "name": {"type": "string", "description": "如 SongRaw AF 85mm F1.2"},
                  "badge": {"type": "string", "description": "标签文字：Since 2026.07.08 或价格；不填则不要标签"},
                  "highlight": {"type": "string", "description": "名字里要标红的部分，可选"}}, ["name"])},
              "after": {"type": "integer", "minimum": 0, "description": "放在第几页之后，不填 = 末尾"},
              "arrow": {"type": "boolean", "description": "两款之间的箭头，两款时默认 true"},
              "remove_background": {"type": "boolean", "description": "用 macOS 主体识别去背景，默认 true"}},
             ["products"]), h_product_card),
    ("image_slides", "用一批图片直接生成一组新页（加在末尾），适合仿真截图、MTF 图。",
     _schema({"document": DOC, "paths": {"type": "array", "items": {"type": "string"}},
              "set_titles": {"type": "boolean", "description": "用文件名作标题"}}, ["paths"]), h_image_slides),
    ("sync_notes_from_script", "读 lens-video-script 的 文稿.md，把每个 [图N：…] 后面的段落写进对应页的演讲者备注。"
                               "对应方式：mapping（{\"图号\": 页号或页号数组}）优先；否则 strategy=tag 按备注里已有的 [图N] 标签；"
                               "strategy=order 按顺序对应非空白页。先用 dry_run=true 预览。",
     _schema({"document": DOC, "script_path": {"type": "string"}, "mapping": {"type": "object"},
              "strategy": {"type": "string", "enum": ["tag", "order"]}, "include_tag": {"type": "boolean"},
              "dry_run": {"type": "boolean"}}, ["script_path"]), h_sync_notes),
    ("export", "导出文稿：pdf、png/jpeg/tiff（逐页图片，存进一个文件夹）、pptx、movie（视频）。"
               "视频默认 1080p 60fps H.264，默认文件名「<文稿名>1-<页数>.mov」，并在旁边写一份 .slides.json（每页在视频里的起止时间和对应图号，给剪辑用）。"
               "slide_seconds = 点按换页的页在视频里停留几秒（不填则按 Keynote 默认 5 秒）。视频导出较慢，超过 wait_seconds 会返回任务号，用 export_status 查看。",
     _schema({"document": DOC, "format": {"type": "string", "enum": ["pdf", "png", "jpeg", "tiff", "pptx", "movie"]},
              "path": {"type": "string", "description": "输出路径；图片格式时是文件夹"},
              "slides": {"type": "array", "items": {"type": "integer"}, "description": "只导出这些页（pdf/图片/pptx）"},
              "with_notes": {"type": "boolean", "description": "PDF 带备注"}, "quality": {"description": "PDF: good/better/best；JPEG: 0-1"},
              "resolution": {"type": "string", "enum": list(MOVIE_FORMATS)},
              "fps": {"type": "number", "enum": [12, 23.98, 24, 25, 29.97, 30, 50, 59.94, 60]},
              "codec": {"type": "string", "enum": list(CODECS)},
              "slide_seconds": {"type": "number"}, "wait_seconds": {"type": "number"},
              "manifest": {"type": "boolean"}, "overwrite": {"type": "boolean"}}, ["format"]), h_export),
    ("export_status", "查看视频导出任务的进度和结果。", _schema({"job": {"type": "string"}}), h_export_status),
    ("run_applescript", "（默认关闭）直接运行一段 AppleScript，用于工具没覆盖到的操作。",
     _schema({"code": {"type": "string"}, "timeout": {"type": "integer"}}, ["code"]), h_run_applescript),
]

READ_ONLY = {"keynote_status", "list_themes", "get_deck", "get_slide", "view_slide", "export_status", "read_zemax_mtf", "list_builds"}


EXPORT_DESC_NO_MOVIE = ("导出文稿：pdf、png/jpeg/tiff（逐页图片，存进一个文件夹，可直接当文稿配图）、pptx。"
                        "默认放在文稿旁边。视频不在这里导出：做完后请用户在 Keynote 里审核，再手动导出。")


def tool_list(cfg):
    out = []
    for name, desc, schema, _h in TOOLS:
        if name == "run_applescript" and not cfg.allow_script:
            continue
        if name == "export_status" and not cfg.allow_movie:
            continue
        if name == "export" and not cfg.allow_movie:
            desc = EXPORT_DESC_NO_MOVIE
            props = {k: v for k, v in schema["properties"].items()
                     if k not in ("resolution", "fps", "codec", "slide_seconds", "wait_seconds", "manifest")}
            props["format"] = {"type": "string", "enum": ["pdf", "png", "jpeg", "tiff", "pptx"]}
            schema = dict(schema, properties=props)
        t = {"name": name, "description": desc, "inputSchema": schema}
        t["annotations"] = {"readOnlyHint": name in READ_ONLY}
        out.append(t)
    return out


HANDLERS = {name: h for name, _d, _s, h in TOOLS}


SUBTITLE_CHECKED = {"add_text", "add_image", "add_shape", "add_line", "add_table", "update_item"}


def call(ctx, name, args):
    h = HANDLERS.get(name)
    if not h:
        raise ValueError("没有这个工具：%s" % name)
    job = ctx.running_job()
    if job and name not in ("export_status", "keynote_status"):
        # finish bookkeeping if it just ended
        _job_status(ctx, job)
        if ctx.running_job():
            raise RuntimeError("正在导出视频（任务 %s），导出期间不能操作 Keynote。请用 export_status 查看进度。" % job["id"])
    r = h(ctx, args or {})
    if name in SUBTITLE_CHECKED and isinstance(r, dict):
        r = _subtitle_warning(r)
    if name == "get_slide" and isinstance(r, dict):
        below = [i["item"] for i in r.get("items", []) if "warning" in _subtitle_warning(dict(i))]
        if below:
            r["below_subtitle_line"] = {"y": SUBTITLE_Y, "items": below}
    return r
