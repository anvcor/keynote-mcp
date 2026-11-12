"""MCP tools for Windows: the same tool set as tools.py, driving Microsoft PowerPoint over COM.

Keynote does not run on Windows. Here the deck is a .pptx made in PowerPoint; on the Mac,
import_pptx turns it into a .key (Morph -> magic move, notes kept) for the final animation work.

Needs pywin32 (COM) and, for images, Pillow. Coordinates are points with the origin at the
top-left, decks are 1920x1080 pt (26.667 x 15 in), the same numbers as the Keynote decks.
PowerPoint stores a transition on the slide it moves INTO (Keynote: on the slide it leaves).
"""
import base64
import glob
import json
import os
import re
import shutil
import tempfile
import time
import uuid
import zipfile

from .pptx_info import ANIM_RE
from .script_md import parse as parse_script, tag_of

try:
    import pythoncom
    import pywintypes
    import win32com.client
except ImportError:  # the Mac backend never imports this module
    pythoncom = pywintypes = win32com = None

# ----------------------------------------------------------------- house style --

SLIDE_W, SLIDE_H = 1920, 1080
HOUSE_BG = "#2C2C2C"
HOUSE_RED = "#FC4B3E"
HOUSE_DURATION = 0.5
HOUSE_FONT = "Helvetica Neue"  # keep the Mac font name; Windows substitutes it, Keynote uses it again
HOUSE_FONT_EA = "PingFang SC"  # what Keynote uses for Chinese
HOUSE_TEXT_SIZE = 34
SUBTITLE_Y = 1000  # the author's horizontal guide: subtitles live below this line

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_TEMPLATE = os.path.join(PROJECT_DIR, "templates", "KEYNOTE动画模板-PPT.pptx")
REF_DIR = os.path.join(PROJECT_DIR, "references")
MATERIALS_PPTX = os.path.join(PROJECT_DIR, "materials", "pptx")

# PpEntryEffect codes (checked against what PowerPoint writes into the .pptx)
EFFECTS = {"magic move": 3954, "morph_word": 3955, "morph_char": 3956, "dissolve": 3849,
           "dissolve_pixels": 1537, "fade_through_black": 1793, "cut": 257, "none": 0}
EFFECT_ALIASES = {"morph": "magic move", "magic_move": "magic move", "magicmove": "magic move", "神奇移动": "magic move",
                  "平滑": "magic move", "fade": "dissolve", "溶解": "dissolve", "淡入淡出": "dissolve",
                  "fade_through_color": "fade_through_black", "no transition effect": "none", "无": "none", "无转场": "none"}
EFFECT_NAMES = {v: k for k, v in EFFECTS.items()}
EFFECT_XML = {"magic move": "p159:morph byObject", "morph_word": "p159:morph byWord", "morph_char": "p159:morph byChar",
              "dissolve": "p:fade（Keynote 导入后是溶解）", "dissolve_pixels": "p:dissolve", "fade_through_black": "p:fade thruBlk",
              "cut": "p:cut", "none": "无"}

TYPE_NAMES = {1: "shape", 3: "chart", 5: "shape", 6: "group", 7: "equation", 9: "line", 11: "image", 13: "image",
              14: "placeholder", 16: "movie", 17: "text item", 19: "table", 24: "graphic", 28: "graphic"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".gif", ".bmp", ".svg", ".webp", ".emf", ".wmf"}

# MsoAnimEffect / trigger codes
ANIM_WIPE, ANIM_FLY, ANIM_DISSOLVE, ANIM_FADE = 22, 2, 9, 10
ANIM_LEFT = 4
TRIGGERS = {"click": 1, "with": 2, "after": 3}
TRIGGER_NAMES = {1: "click", 2: "with", 3: "after"}
BUILD_DEFAULT_DURATION = {"line_draw": 2.0, "fade_move": 1.5}

MSO_TRUE, MSO_FALSE = -1, 0
PP_SAVE_PPTX = 24
RPC_GONE = {-2147023174, -2147417848, -2147023170, -2147220995}


class PptError(RuntimeError):
    pass


# --------------------------------------------------------------------- helpers --

def num(v, name, lo=None, hi=None):
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
    return f


def slide_no(v, name="slide"):
    return int(round(num(v, name, lo=1)))


def as_list(v):
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return list(v)
    return [v]


def r1(v):
    try:
        return round(float(v), 1)
    except (TypeError, ValueError):
        return None


def rgb(v):
    """'#RRGGBB' or [r,g,b] -> PowerPoint's BGR integer."""
    if isinstance(v, str):
        h = v.strip().lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) != 6:
            raise ValueError("颜色格式应为 #RRGGBB：%s" % v)
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    elif isinstance(v, (list, tuple)) and len(v) == 3:
        r, g, b = (max(0, min(255, int(round(float(x))))) for x in v)
    else:
        raise ValueError("颜色格式应为 #RRGGBB 或 [r,g,b]：%r" % (v,))
    return r + (g << 8) + (b << 16)


def hexc(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return None
    if n < 0:
        return None
    return "#%02X%02X%02X" % (n & 255, (n >> 8) & 255, (n >> 16) & 255)


def uname(base):
    """Unique object name with PowerPoint's '!!' prefix, so Morph always pairs the copies of an object."""
    base = re.sub(r"\s+", " ", str(base or "obj")).strip()[:24] or "obj"
    return "!!%s#%s" % (base, uuid.uuid4().hex[:4])


def _com_msg(e):
    try:
        info = e.excepinfo
        if info and info[2]:
            return str(info[2]).strip()
    except Exception:
        pass
    try:
        return str(e.strerror)
    except Exception:
        return str(e)


def _hresult(e):
    try:
        return e.hresult
    except Exception:
        return None


def _to_ppt(text):
    return str(text).replace("\r\n", "\n").replace("\n", "\r")


def _from_ppt(text):
    return str(text or "").replace("\r", "\n").replace("\x0b", "\n")


def _fit(w, h, max_w, max_h):
    k = min(max_w / float(w), max_h / float(h))
    return w * k, h * k


# --------------------------------------------------------------------- context --

class Context:
    """One PowerPoint COM connection per server process."""

    def __init__(self, cfg):
        self.cfg = cfg
        self._app = None
        self.last_doc = None  # FullName of the deck the tools used last
        self.dirty = None  # deck to save after the current tool call
        self.jobs = {}
        if pythoncom is not None:
            pythoncom.CoInitialize()

    def app(self, start=True):
        if win32com is None:
            raise PptError("缺少 pywin32：请运行 python -m pip install pywin32")
        if self._app is not None:
            try:
                self._app.Version
                return self._app
            except Exception:
                self._app = None
        if start:
            try:
                self._app = win32com.client.Dispatch("PowerPoint.Application")
            except Exception as e:
                raise PptError("启动不了 PowerPoint（需要安装 Microsoft PowerPoint）：%s" % e)
        else:
            try:
                self._app = win32com.client.GetActiveObject("PowerPoint.Application")
            except Exception:
                return None
        return self._app

    def show(self):
        try:
            self.app().Visible = MSO_TRUE
        except Exception:
            pass

    def running_job(self):
        return None


def _docs(ctx):
    app = ctx.app()
    return [app.Presentations.Item(i) for i in range(1, app.Presentations.Count + 1)]


def doc_path(d):
    try:
        return d.FullName if d.Path else None
    except Exception:
        return None


def find_doc(ctx, spec=None):
    docs = _docs(ctx)
    if not docs:
        raise ValueError("PowerPoint 里没有打开的文稿。先用 create_presentation 或 open_presentation。")
    if spec:
        s = str(spec).strip()
        if os.path.isabs(s) or "\\" in s or "/" in s:
            key = os.path.normcase(os.path.abspath(ctx.cfg.to_local(s)))
            for d in docs:
                p = doc_path(d)
                if p and os.path.normcase(p) == key:
                    ctx.last_doc = d.FullName
                    return d
            raise ValueError("这份文稿没有在 PowerPoint 里打开：%s（先用 open_presentation）" % s)
        for d in docs:
            if d.Name == s or os.path.splitext(d.Name)[0] == s:
                ctx.last_doc = d.FullName
                return d
        raise ValueError("没有找到文稿「%s」。打开着的：%s" % (s, "、".join(d.Name for d in docs)))
    if ctx.last_doc:
        for d in docs:
            if d.FullName == ctx.last_doc:
                return d
    try:
        d = ctx.app().ActivePresentation
    except Exception:
        d = docs[-1]
    ctx.last_doc = d.FullName
    return d


def guard(ctx, d):
    """Refuse to modify decks outside the allowed folders; remember the deck for autosave."""
    p = doc_path(d)
    if p and not ctx.cfg.in_roots(p):
        raise PermissionError("这份文稿不在允许修改的目录里（%s），为保护原文件拒绝修改。"
                              "请用 open_presentation 打开它，工具会先复制一份副本。" % p)
    ctx.dirty = d
    return d


def doc_json(ctx, d, layouts=False):
    p = doc_path(d)
    j = {"name": d.Name, "path": p, "slides": d.Slides.Count,
         "width": r1(d.PageSetup.SlideWidth), "height": r1(d.PageSetup.SlideHeight),
         "saved": bool(d.Saved), "protected": bool(p) and not ctx.cfg.in_roots(p)}
    if layouts:
        cl = d.SlideMaster.CustomLayouts
        j["layouts"] = [cl.Item(i).Name for i in range(1, cl.Count + 1)]
    return j


def get_slide(d, n):
    c = d.Slides.Count
    if not 1 <= n <= c:
        raise ValueError("第 %d 页不存在（共 %d 页）" % (n, c))
    return d.Slides.Item(n)


def get_item(s, k):
    c = s.Shapes.Count
    if not 1 <= k <= c:
        raise ValueError("对象 %d 不存在（这一页共 %d 个对象）" % (k, c))
    return s.Shapes.Item(k)


# ----------------------------------------------------------------- shape info --

def _has_text(sh):
    try:
        return bool(sh.HasTextFrame) and bool(sh.TextFrame.HasText)
    except Exception:
        return False


def _first_run(sh):
    tr = sh.TextFrame.TextRange
    try:
        if tr.Runs().Count:
            return tr.Runs(1)
    except Exception:
        pass
    return tr


def _opacity(sh):
    t = sh.Type
    try:
        if t == 9:
            return int(round((1 - sh.Line.Transparency) * 100))
        if t in (17, 14) and _has_text(sh):
            v = sh.TextFrame2.TextRange.Font.Fill.Transparency
            if 0 <= v <= 1:
                return int(round((1 - v) * 100))
        if t in (1, 5):
            if sh.Fill.Visible:
                return int(round((1 - sh.Fill.Transparency) * 100))
            if sh.Line.Visible:
                return int(round((1 - sh.Line.Transparency) * 100))
    except Exception:
        pass
    return None


def item_json(sh, k, full=True):
    t = sh.Type
    j = {"item": k, "type": TYPE_NAMES.get(t, "other"), "name": sh.Name,
         "x": r1(sh.Left), "y": r1(sh.Top), "width": r1(sh.Width), "height": r1(sh.Height)}
    if t == 14:
        try:
            j["type"] = "image" if sh.PlaceholderFormat.ContainedType == 13 else "text item"
            j["placeholder"] = True
        except Exception:
            pass
    if t == 5:
        j["freeform"] = True
    try:
        if sh.Rotation:
            j["rotation"] = r1(sh.Rotation)
    except Exception:
        pass
    if t == 6:
        try:
            j["group_items"] = sh.GroupItems.Count
        except Exception:
            pass
    if _has_text(sh):
        j["text"] = _from_ppt(sh.TextFrame.TextRange.Text)
        if full:
            try:
                run = _first_run(sh)
                j["font"] = run.Font.Name
                j["size"] = r1(run.Font.Size)
                j["color"] = hexc(run.Font.Color.RGB)
            except Exception:
                pass
    if j["type"] == "image":
        try:
            f = sh.Tags("KMCP_FILE")
            if f:
                j["file"] = f
        except Exception:
            pass
        try:
            if sh.AlternativeText:
                j["description"] = sh.AlternativeText
        except Exception:
            pass
    op = _opacity(sh)
    if op is not None and op < 100:
        j["opacity"] = op
    try:
        if sh.Locked:
            j["locked"] = True
    except Exception:
        pass
    return j


def slide_items(s, full=True):
    return [item_json(s.Shapes.Item(k), k, full) for k in range(1, s.Shapes.Count + 1)]


def transition_json(s):
    t = s.SlideShowTransition
    code = t.EntryEffect
    j = {"effect": EFFECT_NAMES.get(code, "ppt:%d" % code), "duration": round(float(t.Duration), 3),
         "on_click": t.AdvanceOnClick == MSO_TRUE}
    if t.AdvanceOnTime == MSO_TRUE:
        j["advance_after"] = round(float(t.AdvanceTime), 3)
    return j


def notes_shape(s, create=False):
    shp = s.NotesPage.Shapes
    for i in range(1, shp.Count + 1):
        x = shp.Item(i)
        try:
            if x.PlaceholderFormat.Type == 2:
                return x
        except Exception:
            continue
    if create:
        try:
            return shp.AddPlaceholder(2)
        except Exception:
            return None
    return None


def get_notes(s):
    ns = notes_shape(s)
    if ns is None:
        return ""
    try:
        return _from_ppt(ns.TextFrame.TextRange.Text)
    except Exception:
        return ""


def set_notes_text(s, text):
    ns = notes_shape(s, create=True)
    if ns is None:
        raise PptError("这一页没有备注区，写不了备注")
    ns.TextFrame.TextRange.Text = _to_ppt(text)


def _title_of(s):
    for i in range(1, s.Shapes.Count + 1):
        sh = s.Shapes.Item(i)
        try:
            if sh.Type == 14 and sh.PlaceholderFormat.Type in (1, 3) and _has_text(sh):
                return _from_ppt(sh.TextFrame.TextRange.Text)[:60]
        except Exception:
            continue
    for i in range(1, s.Shapes.Count + 1):
        sh = s.Shapes.Item(i)
        if _has_text(sh):
            return _from_ppt(sh.TextFrame.TextRange.Text)[:40]
    return None


# --------------------------------------------------------------- slide making --

def _bg_color(s):
    try:
        f = s.Background.Fill
        return int(f.ForeColor.RGB)
    except Exception:
        return rgb(HOUSE_BG)


def _blank_layout(d):
    cl = d.SlideMaster.CustomLayouts
    for want in ("Blank", "空白"):
        for i in range(1, cl.Count + 1):
            if cl.Item(i).Name == want:
                return cl.Item(i)
    if d.Slides.Count:
        return d.Slides.Item(1).CustomLayout
    return cl.Item(cl.Count)


def _layout_named(d, name):
    cl = d.SlideMaster.CustomLayouts
    names = []
    for i in range(1, cl.Count + 1):
        names.append(cl.Item(i).Name)
        if cl.Item(i).Name == name:
            return cl.Item(i)
    raise ValueError("没有这个版式：%s。可用：%s" % (name, "、".join(names)))


def blank_slide(d, index, like=None, layout=None):
    """Insert an empty slide at `index` with the dark grey background (copied from `like`)."""
    lay = layout or (like.CustomLayout if like is not None else _blank_layout(d))
    s = d.Slides.AddSlide(index, lay)
    if layout is None:
        for i in range(s.Shapes.Count, 0, -1):
            s.Shapes.Item(i).Delete()
    color = _bg_color(like) if like is not None else rgb(HOUSE_BG)
    s.FollowMasterBackground = MSO_FALSE
    s.Background.Fill.Solid()
    s.Background.Fill.ForeColor.RGB = color
    return s


def apply_transition(s, effect=None, duration=None, delay=None, automatic=None):
    t = s.SlideShowTransition
    if effect is not None:
        t.EntryEffect = effect_code(effect)
    if duration is not None:
        t.Duration = num(duration, "duration", lo=0.01, hi=59)
    if automatic is not None:
        t.AdvanceOnTime = MSO_TRUE if automatic else MSO_FALSE
        t.AdvanceOnClick = MSO_FALSE if automatic else MSO_TRUE
    if delay is not None:
        t.AdvanceTime = num(delay, "delay", lo=0, hi=3600)


def effect_code(name):
    n = str(name).strip()
    key = EFFECT_ALIASES.get(n.lower(), EFFECT_ALIASES.get(n, n.lower().replace("_", " ")))
    if key not in EFFECTS:
        key = key.replace(" ", "_")
    if key not in EFFECTS:
        raise ValueError("不支持的转场：%s。可用：%s" % (name, ", ".join(EFFECTS)))
    return EFFECTS[key]


def _house_entry(s, effect="magic move", duration=None):
    apply_transition(s, effect or "magic move", HOUSE_DURATION if duration is None else duration)


# -------------------------------------------------------------- object editing --

def _set_font(tr, font=None, size=None, color=None):
    f = tr.Font
    if font:
        f.Name = font
        f.NameFarEast = font
    if size is not None:
        f.Size = num(size, "size", lo=1, hi=2000)
    if color is not None:
        f.Color.RGB = rgb(color)


def _highlight(sh, hl):
    """Colour the first occurrence of a substring (the red F-number digits)."""
    out = []
    for h in as_list(hl):
        if isinstance(h, str):
            h = {"text": h}
        needle = h.get("text")
        if not needle:
            continue
        r = sh.TextFrame.TextRange.Find(needle)
        if r is None or not getattr(r, "Length", 0):
            out.append("没找到要标红的文字：%s" % needle)
            continue
        r.Font.Color.RGB = rgb(h.get("color", HOUSE_RED))
    return out


def _set_opacity(sh, op):
    t = 1 - num(op, "opacity", lo=0, hi=100) / 100.0
    typ = sh.Type
    if typ in (13, 11):
        raise ValueError("PowerPoint 的脚本接口设不了图片透明度。要让图片在平滑里淡出，就在下一页删掉它（remove_items）。")
    done = False
    if typ == 9:
        sh.Line.Transparency = t
        return
    if _has_text(sh):
        sh.TextFrame2.TextRange.Font.Fill.Transparency = t
        done = True
    try:
        if sh.Fill.Visible:
            sh.Fill.Transparency = t
            done = True
        if sh.Line.Visible:
            sh.Line.Transparency = t
            done = True
    except Exception:
        pass
    if not done:
        raise ValueError("这个对象（%s）没有能设透明度的填充、线条或文字" % sh.Name)


def _z_to(sh, pos):
    """Move a shape to z-order position `pos` (1 = bottom)."""
    for _ in range(400):
        cur = sh.ZOrderPosition
        if cur == pos:
            return
        sh.ZOrder(3 if cur > pos else 2)  # msoSendBackward / msoBringForward


def _effects_of(s, sh):
    """Animation effects on a shape, so they can be re-created after a picture is replaced."""
    seq = s.TimeLine.MainSequence
    out = []
    for i in range(1, seq.Count + 1):
        e = seq.Item(i)
        try:
            same = e.Shape.Name == sh.Name and e.Shape.Id == sh.Id
        except Exception:
            same = False
        if same:
            try:
                direction = e.EffectParameters.Direction
            except Exception:
                direction = None
            out.append({"index": i, "type": e.EffectType, "trigger": e.Timing.TriggerType,
                        "duration": e.Timing.Duration, "delay": e.Timing.TriggerDelayTime, "direction": direction})
    return out


def _replace_picture(s, sh, path):
    """PowerPoint cannot swap a picture's file: add the new one in the same place, size, order, name."""
    left, top, w = sh.Left, sh.Top, sh.Width
    pos, name, alt, rot = sh.ZOrderPosition, sh.Name, sh.AlternativeText, sh.Rotation
    effects = _effects_of(s, sh)
    new = s.Shapes.AddPicture(path, MSO_FALSE, MSO_TRUE, left, top)
    try:
        sh.PickUp()  # format painter: carries the picture transparency (the invisible Zemax base image), border, effects
        new.Apply()
    except Exception:
        pass
    new.LockAspectRatio = MSO_TRUE
    new.Width = w
    new.Left, new.Top = left, top
    new.Rotation = rot
    sh.Delete()
    new.Name = name
    new.AlternativeText = alt or ""
    new.Tags.Add("KMCP_FILE", path)
    _z_to(new, pos)
    seq = s.TimeLine.MainSequence
    for e in effects:
        ne = seq.AddEffect(new, e["type"], 0, e["trigger"], min(e["index"], seq.Count + 1))
        ne.Timing.Duration = e["duration"]
        ne.Timing.TriggerDelayTime = e["delay"]
        if e["direction"] is not None:
            try:
                ne.EffectParameters.Direction = e["direction"]
            except Exception:
                pass
    return new


def apply_changes(ctx, s, sh, ch):
    """Apply a dict of property changes (the update_item keys) to a shape. Returns (shape, warnings)."""
    warn = []
    if ch.get("locked") is False:
        try:
            sh.Locked = MSO_FALSE
        except Exception:
            pass
    if ch.get("image"):
        p = os.path.normpath(ctx.cfg.to_local(ch["image"]))
        if not os.path.isfile(p):
            raise ValueError("图片不存在：%s" % ch["image"])
        if sh.Type not in (13, 11):
            raise ValueError("对象 %s 不是图片，不能换图" % sh.Name)
        sh = _replace_picture(s, sh, p)
    if ch.get("text") is not None:
        if not sh.HasTextFrame:
            raise ValueError("对象 %s 不能放文字" % sh.Name)
        sh.TextFrame.TextRange.Text = _to_ppt(ch["text"])
    if any(ch.get(k) is not None for k in ("font", "size", "color")):
        if not sh.HasTextFrame:
            raise ValueError("对象 %s 没有文字" % sh.Name)
        _set_font(sh.TextFrame.TextRange, ch.get("font"), ch.get("size"), ch.get("color"))
    if ch.get("description") is not None:
        sh.AlternativeText = str(ch["description"])
    if ch.get("width") is not None:
        w = num(ch["width"], "width", lo=1)
        if sh.Type == 17:
            sh.TextFrame.WordWrap = MSO_TRUE
        sh.Width = w
    if ch.get("height") is not None:
        sh.Height = num(ch["height"], "height", lo=1)
    if ch.get("x") is not None:
        sh.Left = num(ch["x"], "x")
    if ch.get("y") is not None:
        sh.Top = num(ch["y"], "y")
    if ch.get("rotation") is not None:
        sh.Rotation = num(ch["rotation"], "rotation") % 360
    if ch.get("opacity") is not None:
        _set_opacity(sh, ch["opacity"])
    if ch.get("highlight") is not None:
        warn += _highlight(sh, ch["highlight"])
    if ch.get("locked") is True:
        try:
            sh.Locked = MSO_TRUE
        except Exception:
            warn.append("这个版本的 PowerPoint 不支持锁定对象")
    return sh, warn


ITEM_CHANGE_KEYS = ("x", "y", "width", "height", "rotation", "opacity", "locked", "text", "font",
                    "size", "color", "image", "description", "highlight")


def _place(sh, x, y):
    sh.Left = num(x, "x") if x is not None else (SLIDE_W - sh.Width) / 2.0
    sh.Top = num(y, "y") if y is not None else (SLIDE_H - sh.Height) / 2.0


def new_text(s, text, x=None, y=None, width=None, font=None, size=None, color=None, align=None, name=None):
    tb = s.Shapes.AddTextbox(1, 0, 0, float(width) if width else 400.0, 60.0)
    tf = tb.TextFrame
    tf.WordWrap = MSO_TRUE if width else MSO_FALSE
    tf.AutoSize = 1  # shape to fit text
    tr = tf.TextRange
    tr.Text = _to_ppt(text)
    tr.Font.Name = font or HOUSE_FONT
    tr.Font.NameFarEast = font or HOUSE_FONT_EA
    tr.Font.Size = num(size if size is not None else HOUSE_TEXT_SIZE, "size", lo=1, hi=2000)
    tr.Font.Color.RGB = rgb(color or "#FFFFFF")
    if align:
        tr.ParagraphFormat.Alignment = {"left": 1, "center": 2, "right": 3}[align]
    tb.Name = name or uname(str(text).splitlines()[0] if str(text).strip() else "text")
    _place(tb, x, y)
    return tb


def new_picture(s, path, x=None, y=None, width=None, height=None, fit=None, name=None):
    pic = s.Shapes.AddPicture(path, MSO_FALSE, MSO_TRUE, 0, 0)
    pic.LockAspectRatio = MSO_TRUE
    if width is not None:
        pic.Width = num(width, "width", lo=1)
    elif height is not None:
        pic.Height = num(height, "height", lo=1)
    else:
        mw, mh = fit or (SLIDE_W, SLIDE_H)
        if pic.Width > mw or pic.Height > mh:
            w, _h = _fit(pic.Width, pic.Height, mw, mh)
            pic.Width = w
    pic.Name = name or uname(os.path.basename(path))
    pic.Tags.Add("KMCP_FILE", path)
    _place(pic, x, y)
    return pic


def _new_item_result(s, sh, warn=None):
    k = sh.ZOrderPosition
    it = item_json(sh, k, True)
    if warn:
        it["warnings"] = warn
    return it


# ------------------------------------------------------------------- the tools --

def h_status(ctx, a):
    app = ctx.app(start=False)
    out = {"backend": "PowerPoint (Windows)", "workspace": ctx.cfg.workspace,
           "writable_roots": sorted(set(x.rstrip("\\/") for x in ctx.cfg.roots))}
    if app is None:
        out["running"] = False
        return out
    out["running"] = True
    out["version"] = app.Version
    out["documents"] = [doc_json(ctx, d) for d in _docs(ctx)]
    out["current"] = None
    for d in _docs(ctx):
        if d.FullName == ctx.last_doc:
            out["current"] = d.Name
    return out


def _template_catalog(tpl):
    j = os.path.splitext(tpl)[0] + ".json"
    if os.path.exists(j):
        try:
            with open(j, encoding="utf-8") as f:
                return json.load(f)
        except ValueError:
            return None
    return None


def _default_template():
    t = os.environ.get("KEYNOTE_MCP_PPT_TEMPLATE") or PROJECT_TEMPLATE
    return t if os.path.exists(t) else None


def _open(ctx, path, read_only=False):
    app = ctx.app()
    key = os.path.normcase(os.path.abspath(path))
    for d in _docs(ctx):
        p = doc_path(d)
        if p and os.path.normcase(p) == key:
            ctx.last_doc = d.FullName
            return d
    ctx.show()
    d = app.Presentations.Open(os.path.normpath(path), MSO_TRUE if read_only else MSO_FALSE, MSO_FALSE, MSO_TRUE)
    ctx.last_doc = d.FullName
    return d


def _pptx_path(ctx, path):
    path = os.path.normpath(ctx.cfg.to_local(path))
    if not path.lower().endswith(".pptx"):
        path += ".pptx"
    return path


def h_create(ctx, a):
    cfg = ctx.cfg
    path = a.get("path")
    if not path:
        name = a.get("name") or time.strftime("新文稿-%Y%m%d-%H%M%S")
        path = os.path.join(cfg.ensure_workspace(), name)
    path = _pptx_path(ctx, path)
    cfg.check_writable(path)
    if os.path.exists(path) and not a.get("overwrite"):
        raise ValueError("文件已存在：%s（要覆盖请传 overwrite=true）" % path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tpl = None
    if a.get("template") not in (None, "", "none"):
        tpl = os.path.normpath(cfg.to_local(a["template"]))
        if not os.path.isfile(tpl):
            raise ValueError("模板不存在：%s" % a["template"])
    elif a.get("template") != "none":
        tpl = _default_template()
    if os.path.exists(path):
        for d in _docs(ctx):
            if doc_path(d) and os.path.normcase(doc_path(d)) == os.path.normcase(path):
                d.Close()
        os.remove(path)
    if tpl:
        shutil.copy2(tpl, path)
        os.utime(path, None)
        d = _open(ctx, path)
        r = doc_json(ctx, d, layouts=True)
        r["template"] = tpl
        r["note"] = ("已从模板 %s 新建并保存。坐标单位是点，原点在左上角，页面 %sx%s（和 Keynote 文稿一致）。"
                     % (os.path.basename(tpl), r["width"], r["height"]))
        cat = _template_catalog(tpl)
        if cat:
            r["template_guide"] = {k: cat[k] for k in ("how_to_use", "slides", "editable_text_items", "red") if k in cat}
        return r
    w = num(a.get("width", SLIDE_W), "width", lo=100, hi=10000)
    h = num(a.get("height", SLIDE_H), "height", lo=100, hi=10000)
    ctx.show()
    d = ctx.app().Presentations.Add(MSO_TRUE)
    d.PageSetup.SlideWidth = w
    d.PageSetup.SlideHeight = h
    s = blank_slide(d, 1)
    _house_entry(s)
    d.SaveAs(path, PP_SAVE_PPTX)
    ctx.last_doc = d.FullName
    r = doc_json(ctx, d, layouts=True)
    r["note"] = "已新建并保存（深灰底空白页）。坐标单位是点，原点在左上角，页面 %dx%d。" % (w, h)
    return r


def h_open(ctx, a):
    cfg = ctx.cfg
    src = os.path.normpath(cfg.to_local(a["path"]))
    if not os.path.exists(src):
        raise ValueError("文件不存在：%s" % a["path"])
    low = src.lower()
    if low.endswith(".key"):
        raise ValueError("Keynote 文稿只能在 Mac 上打开。Windows 上请用 .pptx（参考成品的 .pptx 版见 list_references）。")
    if not low.endswith((".pptx", ".ppt", ".potx", ".pptm")):
        raise ValueError("只能打开 PowerPoint 文稿（.pptx/.ppt/.potx）：%s" % src)
    target, copied = src, False
    if not cfg.in_roots(src):
        size = os.path.getsize(src)
        if cfg.free_bytes(cfg.ensure_workspace()) < size * 1.2 + (1 << 30):
            raise RuntimeError("磁盘空间不足，无法复制 %.0f MB 的副本" % (size / 1e6))
        stem, ext = os.path.splitext(os.path.basename(src))
        if ext.lower() in (".ppt", ".potx"):
            ext = ext.lower()
        dest_dir = os.path.join(cfg.workspace, "副本")
        os.makedirs(dest_dir, exist_ok=True)
        target = os.path.join(dest_dir, "%s-副本%s%s" % (stem, time.strftime("%Y%m%d-%H%M%S"), ext))
        shutil.copy2(src, target)
        copied = True
    d = _open(ctx, target)
    r = doc_json(ctx, d, layouts=True)
    if copied:
        r["note"] = "原文件在允许修改的目录之外，已复制一份副本再打开，原文件没有改动：%s" % src
        r["original"] = src
    return r


def h_save(ctx, a):
    d = find_doc(ctx, a.get("document"))
    path = a.get("path")
    if path:
        path = _pptx_path(ctx, path)
        ctx.cfg.check_writable(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        d.SaveAs(path, PP_SAVE_PPTX)  # PowerPoint keeps working on the new file (Save As)
        ctx.last_doc = d.FullName
    else:
        guard(ctx, d)
        if not doc_path(d):
            raise ValueError("这份文稿还没有保存位置，请传 path。")
        d.Save()
    ctx.dirty = None
    return doc_json(ctx, d)


def h_close(ctx, a):
    d = find_doc(ctx, a.get("document"))
    name = d.Name
    if a.get("save", True):
        guard(ctx, d)
        if not doc_path(d):
            raise ValueError("这份文稿还没有保存位置。先 save_presentation 指定 path，或传 save=false。")
        d.Save()
    else:
        d.Saved = MSO_TRUE
    d.Close()
    ctx.dirty = None
    if ctx.last_doc and ctx.last_doc.endswith(name):
        ctx.last_doc = None
    return {"closed": name}


def h_get_deck(ctx, a):
    d = find_doc(ctx, a.get("document"))
    mode = a.get("notes", "short")
    rows = []
    for i in range(1, d.Slides.Count + 1):
        s = d.Slides.Item(i)
        nt = get_notes(s)
        row = {"slide": i, "layout": s.CustomLayout.Name, "skipped": s.SlideShowTransition.Hidden == MSO_TRUE,
               "items": s.Shapes.Count, "title": _title_of(s), "fig": tag_of(nt),
               "transition_in": transition_json(s), "builds": s.TimeLine.MainSequence.Count}
        try:
            tags = s.Tags("KMCP_SAMPLE")
            if tags:
                row["sample"] = tags
        except Exception:
            pass
        if mode == "full":
            row["notes"] = nt
        elif mode != "none":
            row["notes"] = nt[:60] + "…" if len(nt) > 60 else nt
        rows.append(row)
    return {"document": doc_json(ctx, d, layouts=True), "slides": rows,
            "note": "transition_in 是切入这一页的转场（PowerPoint 的记法）；Keynote 记在前一页上，Mac 上 import_pptx 会自动换算。"}


def h_get_slide(ctx, a):
    d = find_doc(ctx, a.get("document"))
    n = slide_no(a["slide"])
    s = get_slide(d, n)
    return {"slide": n, "layout": s.CustomLayout.Name, "skipped": s.SlideShowTransition.Hidden == MSO_TRUE,
            "notes": get_notes(s), "transition_in": transition_json(s), "items": slide_items(s, True),
            "builds": _builds_of(s)}


def _cache_dir(ctx):
    d = os.path.join(ctx.cfg.ensure_workspace(), ".cache")
    os.makedirs(d, exist_ok=True)
    return d


def _jpeg(png_path, max_px, quality=82):
    try:
        from PIL import Image
    except ImportError:
        return open(png_path, "rb").read(), "image/png"
    im = Image.open(png_path).convert("RGB")
    im.thumbnail((max_px, max_px))
    out = png_path[:-4] + ".jpg"
    im.save(out, quality=quality)
    return open(out, "rb").read(), "image/jpeg"


def h_view_slide(ctx, a):
    d = find_doc(ctx, a.get("document"))
    n = slide_no(a["slide"])
    s = get_slide(d, n)
    max_px = int(num(a.get("max_size", 1280), "max_size", lo=200, hi=4000))
    W, H = d.PageSetup.SlideWidth, d.PageSetup.SlideHeight
    tmp = tempfile.mkdtemp(prefix="view-", dir=_cache_dir(ctx))
    try:
        png = os.path.join(tmp, "view.png")
        k = max_px / float(max(W, H))
        s.Export(png, "PNG", int(round(W * k)), int(round(H * k)))
        data, mime = _jpeg(png, max_px)
        saved = None
        if a.get("save_to"):
            dest = os.path.normpath(ctx.cfg.to_local(a["save_to"]))
            ctx.cfg.check_writable(dest)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            s.Export(dest, "PNG", int(W), int(H))
            saved = dest
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    summary = {"slide": n}
    if saved:
        summary["saved_png"] = saved
    if a.get("with_items", True):
        items = []
        for it in slide_items(s, False):
            it = {k: v for k, v in it.items() if k in ("item", "type", "name", "x", "y", "width", "height", "text", "file")}
            if isinstance(it.get("text"), str) and len(it["text"]) > 40:
                it["text"] = it["text"][:40] + "…"
            items.append(it)
        summary["items"] = items
        summary["transition_in"] = transition_json(s)
    return {"__content__": [{"type": "image", "data": base64.b64encode(data).decode("ascii"), "mimeType": mime}],
            "summary": summary}


def _after_index(d, a):
    """Slide index for a new slide: after=N (0 = first), default end."""
    after = a.get("after")
    c = d.Slides.Count
    if after is None or after == "end":
        return c + 1
    k = int(round(num(after, "after", lo=0)))
    return min(k, c) + 1


def h_add_slide(ctx, a):
    d = guard(ctx, find_doc(ctx, a.get("document")))
    idx = _after_index(d, a)
    like = d.Slides.Item(idx - 1) if idx > 1 else (d.Slides.Item(1) if d.Slides.Count else None)
    layout = None
    if a.get("layout") not in (None, "", "Blank", "空白") or a.get("inherit_background") is False:
        layout = _layout_named(d, a["layout"]) if a.get("layout") not in (None, "", "Blank", "空白") else _blank_layout(d)
    s = blank_slide(d, idx, like=like, layout=layout)
    eff = a.get("transition", "magic move")
    apply_transition(s, eff, HOUSE_DURATION if a.get("duration") is None else a["duration"])
    if a.get("title") is not None:
        new_text(s, a["title"], x=51, y=48, size=50, name=uname("title"))
    if a.get("body") is not None:
        new_text(s, a["body"], x=51, y=160, width=1818, size=HOUSE_TEXT_SIZE, name=uname("body"))
    if a.get("notes") is not None:
        set_notes_text(s, a["notes"])
    return {"slide": idx, "items": s.Shapes.Count, "total": d.Slides.Count,
            "transition_in": transition_json(s)}


def h_duplicate_slide(ctx, a):
    d = guard(ctx, find_doc(ctx, a.get("document")))
    n = slide_no(a["slide"])
    s = get_slide(d, n)
    count = int(num(a.get("count", 1), "count", lo=1, hi=50))
    for _ in range(count):
        s.Duplicate()
    return {"total": d.Slides.Count, "new_slides": list(range(n + 1, n + 1 + count))}


def h_delete_slides(ctx, a):
    d = guard(ctx, find_doc(ctx, a.get("document")))
    nums = sorted({slide_no(x) for x in as_list(a["slides"])}, reverse=True)
    for n in nums:
        get_slide(d, n).Delete()
    return {"total": d.Slides.Count, "deleted": sorted(nums)}


def h_move_slide(ctx, a):
    d = guard(ctx, find_doc(ctx, a.get("document")))
    n, to = slide_no(a["slide"]), slide_no(a["to"], "to")
    get_slide(d, to)
    get_slide(d, n).MoveTo(to)
    return {"slide": to}


def h_set_slide(ctx, a):
    d = guard(ctx, find_doc(ctx, a.get("document")))
    n = slide_no(a["slide"])
    s = get_slide(d, n)
    if a.get("layout"):
        s.CustomLayout = _layout_named(d, a["layout"])
    if a.get("skipped") is not None:
        s.SlideShowTransition.Hidden = MSO_TRUE if a["skipped"] else MSO_FALSE
    for key, types, y, size in (("title", (1, 3), 48, 50), ("body", (2, 7), 160, HOUSE_TEXT_SIZE)):
        if a.get(key) is None:
            continue
        target = None
        for i in range(1, s.Shapes.Count + 1):
            sh = s.Shapes.Item(i)
            try:
                if sh.Type == 14 and sh.PlaceholderFormat.Type in types:
                    target = sh
                    break
            except Exception:
                continue
        if target is not None:
            target.TextFrame.TextRange.Text = _to_ppt(a[key])
        else:
            new_text(s, a[key], x=51, y=y, width=None if key == "title" else 1818, size=size, name=uname(key))
    for key in ("title_showing", "body_showing"):
        if a.get(key) is False:
            types = (1, 3) if key == "title_showing" else (2, 7)
            for i in range(s.Shapes.Count, 0, -1):
                sh = s.Shapes.Item(i)
                try:
                    if sh.Type == 14 and sh.PlaceholderFormat.Type in types:
                        sh.Delete()
                except Exception:
                    continue
    return {"slide": n, "layout": s.CustomLayout.Name, "skipped": s.SlideShowTransition.Hidden == MSO_TRUE,
            "items": slide_items(s, False)}


def h_add_text(ctx, a):
    d = guard(ctx, find_doc(ctx, a.get("document")))
    s = get_slide(d, slide_no(a["slide"]))
    tb = new_text(s, a["text"], a.get("x"), a.get("y"), a.get("width"), a.get("font"), a.get("size"), a.get("color"),
                  a.get("align"))
    warn = []
    if a.get("rotation") is not None:
        tb.Rotation = num(a["rotation"], "rotation") % 360
    if a.get("opacity") is not None:
        _set_opacity(tb, a["opacity"])
    if a.get("highlight") is not None:
        warn += _highlight(tb, a["highlight"])
    return _new_item_result(s, tb, warn)


def h_add_image(ctx, a):
    d = guard(ctx, find_doc(ctx, a.get("document")))
    s = get_slide(d, slide_no(a["slide"]))
    p = os.path.normpath(ctx.cfg.to_local(a["path"]))
    if not os.path.isfile(p):
        raise ValueError("图片不存在：%s" % a["path"])
    if os.path.splitext(p)[1].lower() == ".pdf":
        raise ValueError("PowerPoint 不能直接放 PDF，请先导出成 PNG。")
    pic = new_picture(s, p, a.get("x"), a.get("y"), a.get("width"), a.get("height"))
    if a.get("rotation") is not None:
        pic.Rotation = num(a["rotation"], "rotation") % 360
    if a.get("description") is not None:
        pic.AlternativeText = str(a["description"])
    if a.get("opacity") not in (None, 100):
        raise ValueError("PowerPoint 的脚本接口设不了图片透明度（图片已放上）。要淡出就在下一页删掉它。")
    return _new_item_result(s, pic)


def h_add_shape(ctx, a):
    d = guard(ctx, find_doc(ctx, a.get("document")))
    s = get_slide(d, slide_no(a["slide"]))
    w = num(a.get("width", 400), "width", lo=1)
    h = num(a.get("height", 300), "height", lo=1)
    sh = s.Shapes.AddShape(5 if a.get("rounded") else 1, 0, 0, w, h)
    sh.Fill.Solid()
    sh.Fill.ForeColor.RGB = rgb(a.get("fill", "#5E5E5E"))
    sh.Line.Visible = MSO_FALSE
    sh.Name = uname("shape")
    if a.get("text") is not None:
        sh.TextFrame.TextRange.Text = _to_ppt(a["text"])
        sh.TextFrame.TextRange.Font.Name = a.get("font") or HOUSE_FONT
        sh.TextFrame.TextRange.Font.NameFarEast = a.get("font") or HOUSE_FONT_EA
        _set_font(sh.TextFrame.TextRange, None, a.get("size", 26), a.get("color", "#FFFFFF"))
    _place(sh, a.get("x"), a.get("y"))
    if a.get("rotation") is not None:
        sh.Rotation = num(a["rotation"], "rotation") % 360
    if a.get("opacity") is not None:
        _set_opacity(sh, a["opacity"])
    return _new_item_result(s, sh)


def h_add_line(ctx, a):
    d = guard(ctx, find_doc(ctx, a.get("document")))
    s = get_slide(d, slide_no(a["slide"]))
    ln = s.Shapes.AddLine(*[num(a[k], k) for k in ("x1", "y1", "x2", "y2")])
    ln.Line.ForeColor.RGB = rgb(a.get("color", "#FFFFFF"))
    ln.Line.Weight = num(a.get("weight", 2), "weight", lo=0.25, hi=100)
    if a.get("dash"):
        ln.Line.DashStyle = 4  # msoLineDash
    ln.Name = uname("line")
    return _new_item_result(s, ln)


def h_add_table(ctx, a):
    d = guard(ctx, find_doc(ctx, a.get("document")))
    s = get_slide(d, slide_no(a["slide"]))
    rows = a["rows"]
    if not rows or not all(isinstance(r, list) for r in rows):
        raise ValueError("rows 应该是二维数组，例如 [[\"焦距\",\"MTF50\"],[\"50mm\",\"0.62\"]]")
    nr, nc = len(rows), max(len(r) for r in rows)
    if nr * nc > 600:
        raise ValueError("表格太大（最多 600 个单元格）")
    w = num(a.get("width", min(1800, 160 * nc)), "width", lo=10)
    h = num(a.get("height", 56 * nr), "height", lo=10)
    shp = s.Shapes.AddTable(nr, nc, 0, 0, w, h)
    tbl = shp.Table
    try:
        tbl.ApplyStyle("{5940675A-B579-460E-94D1-54222C63F5DA}", False)  # "No Style, Table Grid"
    except Exception:
        pass
    hr = int(num(a.get("header_rows", 1), "header_rows", lo=0, hi=5))
    hc = int(num(a.get("header_columns", 0), "header_columns", lo=0, hi=5))
    size = num(a.get("size", 24), "size", lo=6, hi=200)
    for i in range(1, nr + 1):
        for k in range(1, nc + 1):
            cell = tbl.Cell(i, k)
            tr = cell.Shape.TextFrame.TextRange
            v = rows[i - 1][k - 1] if k - 1 < len(rows[i - 1]) else ""
            tr.Text = _to_ppt("" if v is None else v)
            tr.Font.Name = HOUSE_FONT
            tr.Font.NameFarEast = HOUSE_FONT_EA
            tr.Font.Size = size
            tr.Font.Color.RGB = rgb("#FFFFFF")
            tr.Font.Bold = MSO_TRUE if (i <= hr or k <= hc) else MSO_FALSE
            tr.ParagraphFormat.Alignment = 2
            for b in (1, 2, 3, 4):  # top, left, bottom, right
                bd = cell.Borders(b)
                bd.Visible = MSO_TRUE
                bd.ForeColor.RGB = rgb("#FFFFFF")
                bd.Weight = 1
    shp.Name = uname("table")
    _place(shp, a.get("x"), a.get("y"))
    return _new_item_result(s, shp)


def h_update_item(ctx, a):
    d = guard(ctx, find_doc(ctx, a.get("document")))
    s = get_slide(d, slide_no(a["slide"]))
    sh = get_item(s, int(num(a["item"], "item", lo=1)))
    sh, warn = apply_changes(ctx, s, sh, {k: a.get(k) for k in ITEM_CHANGE_KEYS})
    return _new_item_result(s, sh, warn)


def h_delete_items(ctx, a):
    d = guard(ctx, find_doc(ctx, a.get("document")))
    s = get_slide(d, slide_no(a["slide"]))
    ks = sorted({int(num(x, "item", lo=1)) for x in as_list(a["items"])}, reverse=True)
    for k in ks:
        get_item(s, k).Delete()
    return {"remaining": s.Shapes.Count, "deleted": sorted(ks),
            "note": "删除后，后面对象的编号会前移，继续编辑前用 get_slide 重新查看。"}


def h_set_notes(ctx, a):
    d = guard(ctx, find_doc(ctx, a.get("document")))
    n = slide_no(a["slide"])
    s = get_slide(d, n)
    text = a.get("text", "")
    if a.get("append"):
        old = get_notes(s)
        text = old + "\n" + text if old else text
    set_notes_text(s, text)
    return {"slide": n, "notes": get_notes(s)}


def h_set_transition(ctx, a):
    d = guard(ctx, find_doc(ctx, a.get("document")))
    sl = a.get("slides", "all")
    idx = range(1, d.Slides.Count + 1) if sl == "all" else sorted({slide_no(x) for x in as_list(sl)})
    out = []
    for i in idx:
        s = get_slide(d, i)
        apply_transition(s, a.get("effect"), a.get("duration"), a.get("delay"), a.get("automatic"))
        out.append({"slide": i, "transition_in": transition_json(s)})
    return {"slides": out}


def h_magic_move_step(ctx, a):
    d = guard(ctx, find_doc(ctx, a.get("document")))
    n = slide_no(a["slide"])
    s = get_slide(d, n)
    s2 = s.Duplicate().Item(1)
    dur = HOUSE_DURATION if a.get("duration") is None else a["duration"]
    apply_transition(s2, "magic move", dur)
    if a.get("automatic") is not None or a.get("delay") is not None:
        apply_transition(s, None, None, a.get("delay"), a.get("automatic"))
    warn = []
    # resolve all items first: numbers refer to the original slide even after a picture is replaced
    targets = []
    for ch in as_list(a.get("changes")):
        if "item" not in ch:
            raise ValueError("changes 里每一项都要有 item（对象编号）")
        targets.append((get_item(s2, int(num(ch["item"], "item", lo=1))), ch))
    removes = [get_item(s2, int(num(x, "item", lo=1))) for x in sorted(set(as_list(a.get("remove_items"))))]
    for sh, ch in targets:
        _sh, w = apply_changes(ctx, s2, sh, {k: ch.get(k) for k in ITEM_CHANGE_KEYS})
        warn += w
    for sh in removes:
        sh.Delete()
    r = {"from_slide": n, "new_slide": n + 1,
         "note": "第 %d 页复制成第 %d 页，第 %d 页的切入转场设为平滑 %s 秒（Keynote 导入后是第 %d→%d 页的神奇移动）。"
                 % (n, n + 1, n + 1, dur, n, n + 1),
         "items": slide_items(s2, False)}
    if warn:
        r["warnings"] = warn
    return r


CHAPTER_NUM_RE = re.compile(r"^\s*([一二三四五六七八九十]+|[0-9]+)\s*[ 　、.．]\s*(.+)$")


def h_chapter_card(ctx, a):
    """Chapter card as in the author's decks: blank -> numeral (76 pt) above title (71 pt), white,
    centred -> blank, every slide entered with a 0.5 s Morph."""
    d = guard(ctx, find_doc(ctx, a.get("document")))
    title = str(a["title"]).strip()
    number = a.get("number")
    if number is None:
        m = CHAPTER_NUM_RE.match(title)
        if m:
            number, title = m.group(1), m.group(2).strip()
    eff = a.get("transition", "magic move")
    dur = a.get("duration", HOUSE_DURATION)
    color = a.get("color", "#FFFFFF")
    font = a.get("font")
    idx = _after_index(d, a)
    made = []

    def blank_at(i):
        like = d.Slides.Item(i - 1) if i > 1 else (d.Slides.Item(1) if d.Slides.Count else None)
        s = blank_slide(d, i, like=like)
        apply_transition(s, eff, dur)
        made.append(i)
        return s

    if a.get("blank_before", True):
        blank_at(idx)
        idx += 1
    c = blank_at(idx)
    W = d.PageSetup.SlideWidth
    t2 = new_text(c, title, x=0, y=0, width=W, font=font, size=a.get("title_size", 71), color=color, align="center",
                  name=uname("chapter-title"))
    if number:
        t1 = new_text(c, number, x=0, y=0, width=W, font=font, size=a.get("number_size", 76), color=color,
                      align="center", name=uname("chapter-number"))
        t1.Left, t1.Top = 0, num(a.get("number_y", 407), "number_y")
        t2.Left, t2.Top = 0, num(a.get("title_y", 566), "title_y")
    else:
        t2.Left, t2.Top = 0, (d.PageSetup.SlideHeight - t2.Height) / 2.0
    if a.get("notes") is not None:
        set_notes_text(c, a["notes"])
    if a.get("blank_after", True):
        blank_at(idx + 1)
    return {"slides": made, "number": number, "title": title,
            "note": "章节卡：空白 → 编号 + 标题 → 空白（编号 76 号、标题 71 号、白色居中，文本框占满页宽、居中对齐，"
                    "换字体后仍居中），每页切入转场 %s %s 秒。" % (eff, dur)}


def h_image_slides(ctx, a):
    d = guard(ctx, find_doc(ctx, a.get("document")))
    paths = [os.path.normpath(ctx.cfg.to_local(p)) for p in as_list(a["paths"])]
    for p in paths:
        if not os.path.isfile(p):
            raise ValueError("图片不存在：%s" % p)
    first = d.Slides.Count + 1
    W, H = d.PageSetup.SlideWidth, d.PageSetup.SlideHeight
    for p in paths:
        i = d.Slides.Count + 1
        like = d.Slides.Item(i - 1) if i > 1 else None
        s = blank_slide(d, i, like=like)
        _house_entry(s)
        top = 0
        if a.get("set_titles"):
            t = new_text(s, os.path.splitext(os.path.basename(p))[0], x=51, y=48, size=50)
            top = t.Top + t.Height + 10
        pic = new_picture(s, p, fit=(W, min(SUBTITLE_Y, H) - top))
        pic.Left = (W - pic.Width) / 2.0
        pic.Top = top + (min(SUBTITLE_Y, H) - top - pic.Height) / 2.0
    return {"first": first, "last": d.Slides.Count, "note": "新页加在文稿末尾，图片缩放到字幕线以上居中。"}


def h_sync_notes(ctx, a):
    sp = os.path.normpath(ctx.cfg.to_local(a["script_path"]))
    if not os.path.isfile(sp):
        raise ValueError("找不到文稿：%s" % a["script_path"])
    with open(sp, "r", encoding="utf-8-sig") as f:
        parsed = parse_script(f.read())
    figs = parsed["figures"]
    if not figs:
        raise ValueError("文稿里没有找到 [图N：…] 标记")
    d = find_doc(ctx, a.get("document"))
    total = d.Slides.Count
    notes = {i: get_notes(d.Slides.Item(i)) for i in range(1, total + 1)}
    plan, unmatched = {}, []
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
        for i in range(1, total + 1):
            t = tag_of(notes[i])
            if t:
                by_tag.setdefault(t, []).append(i)
        for fig in figs:
            targets = by_tag.get(str(fig["fig"]))
            if not targets:
                unmatched.append(fig["fig"])
                continue
            plan[targets[0]] = (fig["fig"], body(fig, True))
    elif strategy == "order":
        content = [i for i in range(1, total + 1)
                   if d.Slides.Item(i).SlideShowTransition.Hidden != MSO_TRUE and d.Slides.Item(i).Shapes.Count > 0]
        for fig, sn in zip(figs, content):
            plan[sn] = (fig["fig"], body(fig, True))
        unmatched = [f["fig"] for f in figs[len(content):]]
    else:
        raise ValueError("strategy 只能是 tag 或 order")

    def with_anim(i, text):
        keep = [ln for ln in notes[i].splitlines() if ANIM_RE.match(ln)]
        return text + ("\n" + "\n".join(keep) if keep else "")

    result = {"script_title": parsed["title"], "figures": len(figs),
              "planned": [{"slide": s, "fig": f, "notes_head": t[:50]} for s, (f, t) in sorted(plan.items())],
              "unmatched_figures": unmatched}
    if not plan:
        result["note"] = ("没有对上任何一页。strategy=tag 需要备注以 [图N] 开头；"
                          "也可以传 mapping，例如 {\"1\": 1, \"2\": [3, 4]}。")
        return result
    if a.get("dry_run"):
        result["dry_run"] = True
        return result
    guard(ctx, d)
    for s, (_f, t) in sorted(plan.items()):
        set_notes_text(d.Slides.Item(s), with_anim(s, t))
    result["written"] = len(plan)
    result["note"] = "备注里原有的 [动画] 行保留在段落后面。"
    return result


# ------------------------------------------------------------------- builds --

def _builds_of(s):
    seq = s.TimeLine.MainSequence
    out = []
    for i in range(1, seq.Count + 1):
        e = seq.Item(i)
        try:
            sh = e.Shape
            item = sh.ZOrderPosition
            name = sh.Name
            tag = sh.Tags("KMCP_BUILD")
        except Exception:
            item, name, tag = None, None, ""
        et = e.EffectType
        if tag:
            kind = tag
        elif et in (ANIM_WIPE, ANIM_DISSOLVE):
            kind = "line_draw"  # Keynote exports its line draw as Dissolve In
        elif et == ANIM_FLY:
            kind = "fade_move"  # Keynote exports fade-and-move as Fly In
        else:
            kind = "ppt:%d" % et
        exit_ = False
        try:
            exit_ = bool(e.Exit)
        except Exception:
            pass
        out.append({"order": i, "item": item, "name": name, "effect": kind, "ppt_effect": et, "exit": exit_,
                    "duration": round(float(e.Timing.Duration), 3), "start": TRIGGER_NAMES.get(e.Timing.TriggerType, e.Timing.TriggerType),
                    "delay": round(float(e.Timing.TriggerDelayTime), 3)})
    return out


def h_add_builds(ctx, a):
    d = guard(ctx, find_doc(ctx, a.get("document")))
    builds = as_list(a.get("builds"))
    if not builds:
        raise ValueError("builds 不能为空")
    first_on_slide = set()
    added = []
    for b in builds:
        n = slide_no(b["slide"])
        s = get_slide(d, n)
        sh = get_item(s, int(num(b["item"], "item", lo=1)))
        eff = b.get("effect", "line_draw")
        if eff not in BUILD_DEFAULT_DURATION:
            raise ValueError("effect 只能是 line_draw 或 fade_move")
        start = b.get("start")
        if start is None:
            start = "click" if n not in first_on_slide else "with"
        first_on_slide.add(n)
        seq = s.TimeLine.MainSequence
        e = seq.AddEffect(sh, ANIM_WIPE if eff == "line_draw" else ANIM_FLY, 0, TRIGGERS[start])
        try:
            e.EffectParameters.Direction = ANIM_LEFT
        except Exception:
            pass
        if eff == "fade_move" and _has_text(sh):
            try:
                e = seq.ConvertToTextUnitEffect(e, 1)  # by character, like Keynote's 逐字
            except Exception:
                pass
        e.Timing.Duration = num(b.get("duration", BUILD_DEFAULT_DURATION[eff]), "duration", lo=0.05, hi=60)
        if b.get("delay") is not None:
            e.Timing.TriggerDelayTime = num(b["delay"], "delay", lo=0, hi=60)
        sh.Tags.Add("KMCP_BUILD", eff)
        added.append({"slide": n, "item": sh.ZOrderPosition, "name": sh.Name, "effect": eff, "start": start})
    return {"added": added,
            "note": "PowerPoint 里没有 Keynote 的线条描绘和逐字淡入移动：line_draw 写成「擦除（自左）」，"
                    "fade_move 写成「飞入（自左，按字）」，只作预览。到 Mac 上 import_pptx 之后，用 add_builds 重做成 Keynote 的动画；"
                    "对象上记了 KMCP_BUILD 标签，list_builds 会按原意显示。"}


def h_list_builds(ctx, a):
    d = find_doc(ctx, a.get("document"))
    if a.get("slide"):
        n = slide_no(a["slide"])
        return {"slides": [{"slide": n, "builds": _builds_of(get_slide(d, n))}]}
    out = []
    for i in range(1, d.Slides.Count + 1):
        b = _builds_of(d.Slides.Item(i))
        if b:
            out.append({"slide": i, "builds": b})
    return {"slides": out}


# ---------------------------------------------------------------- MTF reading --

def h_read_zemax_mtf(ctx, a):
    from . import curves as C
    img = os.path.normpath(ctx.cfg.to_local(a["image"]))
    if not os.path.isfile(img):
        raise ValueError("找不到 Zemax 图：%s" % a["image"])
    csv_path = None
    if a.get("csv_path"):
        csv_path = os.path.normpath(ctx.cfg.to_local(a["csv_path"]))
        ctx.cfg.check_writable(csv_path)
    return C.read_image(img, x_max=num(a.get("x_max", 21.6), "x_max", lo=0.1, hi=1000),
                        fields=a.get("fields"), csv_path=csv_path)


# ------------------------------------------------------------ lens layout (CODE V) --

def h_export_lens_layout(ctx, a):
    from . import lens_layout as LL
    lens = a["lens_files"]
    if isinstance(lens, str):
        lens = [lens]
    lens = [os.path.normpath(ctx.cfg.to_local(p)) for p in lens]
    out = os.path.normpath(ctx.cfg.to_local(a["out_dir"]))
    ctx.cfg.check_writable(os.path.join(out, "x"))
    fmts = tuple(a.get("formats") or ("svg", "dxf"))
    bad = [f for f in fmts if f not in ("svg", "dxf", "png", "pdf")]
    if bad:
        raise ValueError("formats 只能是 svg / dxf / png / pdf：%s" % bad)
    try:
        return LL.export_layouts(lens, out, fields=a.get("fields"), zoom=int(a.get("zoom", 1)),
                                 px_per_mm=num(a.get("px_per_mm", 14.3), "px_per_mm", lo=3, hi=40),
                                 labeled=bool(a.get("labeled", True)), formats=fmts, names=a.get("names"))
    except LL.LayoutError as e:
        raise PptError(str(e))


# --------------------------------------------------------------- product card --

def _transparency(path):
    """True if the image has a transparent background (Photoshop cut-out), False if opaque, None if unknown."""
    try:
        from PIL import Image
        im = Image.open(path)
        if im.mode not in ("RGBA", "LA") and not (im.mode == "P" and "transparency" in im.info):
            return False
        im = im.convert("RGBA")
        w, h = im.size
        corners = [im.getpixel((x, y))[3] for x, y in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
        return sum(1 for v in corners if v < 16) >= 3
    except Exception:
        return None


def _sample_slide(d, kind):
    for i in range(1, d.Slides.Count + 1):
        try:
            if d.Slides.Item(i).Tags("KMCP_SAMPLE") == kind:
                return i
        except Exception:
            continue
    return 0


def _roles(s):
    out = {}
    for i in range(1, s.Shapes.Count + 1):
        sh = s.Shapes.Item(i)
        try:
            r = sh.Tags("KMCP_ROLE")
        except Exception:
            r = ""
        if r:
            out[r] = sh
    return out


def _fit_text_width(sh):
    tf = sh.TextFrame
    tf.WordWrap = MSO_FALSE
    tf.AutoSize = 1


def h_product_card(ctx, a):
    prods = as_list(a.get("products"))
    if not 1 <= len(prods) <= 2:
        raise ValueError("products 放 1 或 2 款产品，例如 [{\"image\": \"…png\", \"name\": \"…\", \"badge\": \"Since 2025.05.20\"}]")
    for p in prods:
        if not p.get("name"):
            raise ValueError("每款产品都要有 name")
        if p.get("image"):
            p["image"] = os.path.normpath(ctx.cfg.to_local(p["image"]))
            if not os.path.isfile(p["image"]):
                raise ValueError("图片不存在：%s" % p["image"])
    d = guard(ctx, find_doc(ctx, a.get("document")))
    sample = _sample_slide(d, "product_card")
    if not sample:
        raise ValueError("文稿里找不到产品介绍样例页（模板第 18 页）。用 create_presentation 从模板新建的文稿才有。")
    two = len(prods) == 2
    arrow = a.get("arrow", two)
    s = d.Slides.Item(sample).Duplicate().Item(1)
    s.Tags.Delete("KMCP_SAMPLE")
    total = d.Slides.Count
    target = total
    after = a.get("after")
    if after is not None and after != "end":
        k = int(round(num(after, "after", lo=0)))
        target = max(1, min(k + (1 if k < sample + 1 else 0), total))
    if target != sample + 1:
        s.MoveTo(target)
    n = s.SlideIndex
    R = _roles(s)
    for role in ("imgL", "imgR"):
        if role in R:
            R.pop(role).Delete()
    drop = set()
    if not two:
        drop |= {"nameR", "badgeR", "badgeR_text"}
    if not arrow:
        drop.add("arrow")
    if not prods[0].get("badge"):
        drop |= {"badgeL", "badgeL_text"}
    if two and not prods[1].get("badge"):
        drop |= {"badgeR", "badgeR_text"}
    for role in drop:
        if role in R:
            R.pop(role).Delete()
    cx_l, cx_r = (537.5, 1321.5) if two else (960.0, None)
    warn = []

    def slot(prod, cx, side):
        t = R.get("name" + side)
        if t is not None:
            t.TextFrame.TextRange.Text = _to_ppt(prod["name"])
            _fit_text_width(t)
            if prod.get("highlight"):
                warn.extend(_highlight(t, prod["highlight"]))
            t.Left = cx - t.Width / 2.0
        if prod.get("badge") and R.get("badge" + side + "_text") is not None:
            bt, b = R["badge" + side + "_text"], R.get("badge" + side)
            bt.TextFrame.TextRange.Text = _to_ppt(prod["badge"])
            _fit_text_width(bt)
            bt.Left = cx - bt.Width / 2.0
            if b is not None:
                bw = max(120.0, bt.Width + 22)
                b.Width = bw
                b.Left = cx - bw / 2.0

    slot(prods[0], cx_l, "L")
    if two:
        slot(prods[1], cx_r, "R")
    max_w, max_h, bottom = (560, 600, 690) if two else (900, 620, 700)
    images = []
    for prod, cx, side in ((prods[0], cx_l, "L"), (prods[1] if two else None, cx_r, "R")):
        if not prod or not prod.get("image"):
            continue
        pic = new_picture(s, prod["image"], x=0, y=0, name=uname("product" + side))
        w, h = _fit(pic.Width, pic.Height, max_w, max_h)
        pic.Width = w
        pic.Left = cx - pic.Width / 2.0
        pic.Top = bottom - pic.Height
        it = item_json(pic, pic.ZOrderPosition, False)
        if _transparency(prod["image"]) is False:
            it["warning"] = ("这张图不是透明背景（%s），放上去会带着白底。请先在 Photoshop 里抠好、存成透明 PNG 放进项目文件夹，"
                             "再用 update_item 的 image 换掉。" % os.path.basename(prod["image"]))
        images.append(it)
    # white rule between the photos and the names, as on every product page of 35之月 (added by hand there):
    # 5 pt, y 770; one product 597–1323, two products 140.9–1779.1
    x1, x2 = (140.9, 1779.1) if two else (597.0, 1323.0)
    ln = s.Shapes.AddLine(x1, 770.0, x2, 770.0)
    ln.Line.ForeColor.RGB = rgb("#FFFFFF")
    ln.Line.Weight = 5
    ln.Name = uname("line")
    r = {"slide": n, "items": slide_items(s, False), "images": images,
         "note": "产品介绍页已按样例页（模板第 18 页）的版式生成：产品图底边对齐、y=770 一条 5 磅白色横线、产品名 38 号、灰色标签按文字长短自动加宽，"
                 "全部在 y=%d 的字幕参考线以上。Windows 没有 Helvetica Neue，标签宽度按替代字体算，到 Mac 上可能要微调。" % SUBTITLE_Y}
    if warn:
        r["warnings"] = warn
    return r


# ----------------------------------------------------------------- references --

def _ref_catalog():
    p = os.path.join(REF_DIR, "catalog.json")
    if not os.path.exists(p):
        return {"decks": []}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _ref_pptx(deck):
    """The deck's .pptx version, unpacked from materials/pptx/<project>.zip on first use."""
    stem = os.path.splitext(os.path.basename(deck["file"]))[0]
    dest = os.path.join(REF_DIR, "_pptx", stem + ".pptx")
    if os.path.exists(dest):
        return dest
    src = os.path.join(MATERIALS_PPTX, deck["project"] + ".zip")
    if not os.path.exists(src):
        return None
    with zipfile.ZipFile(src) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".pptx") and "/" not in n.strip("/")]
        if not names:
            return None
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + ".part"
        with z.open(names[0]) as fin, open(tmp, "wb") as fout:
            shutil.copyfileobj(fin, fout)
        os.replace(tmp, dest)
    return dest


def h_list_references(ctx, a):
    decks = _ref_catalog().get("decks", [])
    q = (a.get("project") or "").strip()
    kind = (a.get("kind") or "").strip()
    absp = lambda rel: os.path.normpath(os.path.join(REF_DIR, rel))
    if not q:
        out = {"reference_dir": REF_DIR, "decks": [
            {"project": d["project"], "pptx": os.path.join(REF_DIR, "_pptx", os.path.splitext(os.path.basename(d["file"]))[0] + ".pptx"),
             "modified": d["modified"], "slides": d["slides"],
             "kinds": {k: len(v) for k, v in d["by_kind"].items()}, "contact_sheet": absp(d["contact_sheet"])}
            for d in sorted(decks, key=lambda d: d["modified"], reverse=True)],
            "note": "作者近期的成品文稿（Keynote 导出的 .pptx 版，页号和 .key 一致，图片压缩过）。传 project 看逐页清单，"
                    "kind 只看某类页面，with_sheet=true 返回缩略图目录。细看某页就 open_presentation 打开 pptx（会自动复制一份）。"}
        if kind:
            out["matches"] = [{"project": d["project"], "slides": d["by_kind"].get(kind, [])} for d in decks if d["by_kind"].get(kind)]
        return out
    hits = [d for d in decks if q in d["project"] or q in d["file"]]
    if not hits:
        raise ValueError("references 里没有和「%s」匹配的课题。可选：%s" % (q, "、".join(d["project"] for d in decks)))
    d = hits[0]
    rows = d["slide_list"] if not kind else [x for x in d["slide_list"] if x["kind"] == kind]
    rows = [x for x in rows if x["kind"] != "空白页"] if not kind else rows
    pptx = _ref_pptx(d)
    out = {"project": d["project"], "pptx": pptx, "slides": d["slides"], "kinds": d["by_kind"],
           "slide_list": rows, "contact_sheet": absp(d["contact_sheet"]),
           "note": "slide_list 省略了空白页。要复用某页：open_presentation 打开 pptx（自动复制），get_slide / view_slide 细看。"}
    if pptx is None:
        out["note"] += " 没找到这份的 .pptx 压缩包（materials/pptx）。"
    if a.get("with_sheet"):
        sheet = absp(d["contact_sheet"])
        if os.path.exists(sheet):
            tmp = tempfile.mkdtemp(prefix="sheet-", dir=_cache_dir(ctx))
            try:
                png = os.path.join(tmp, "sheet.png")
                shutil.copy2(sheet, png)
                data, mime = _jpeg(png, int(num(a.get("max_size", 1600), "max_size", lo=200, hi=4000)), 85)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            out["__content__"] = [{"type": "image", "data": base64.b64encode(data).decode("ascii"), "mimeType": mime}]
    return out


# --------------------------------------------------------------------- export --

FILTERS = {"png": "PNG", "jpeg": "JPG", "tiff": "TIF"}
EXTS = {"png": ".png", "jpeg": ".jpg", "tiff": ".tif", "pdf": ".pdf", "pptx": ".pptx"}


def _subset_copy(ctx, d, slides):
    """Open a hidden temporary copy that only has `slides` (for partial PDF / PPTX export)."""
    tmp = tempfile.mkdtemp(prefix="export-", dir=_cache_dir(ctx))
    p = os.path.join(tmp, "subset.pptx")
    d.SaveCopyAs(p)
    c = ctx.app().Presentations.Open(p, MSO_FALSE, MSO_FALSE, MSO_FALSE)
    keep = set(slides)
    for i in range(c.Slides.Count, 0, -1):
        if i not in keep:
            c.Slides.Item(i).Delete()
    return c, tmp


def h_export(ctx, a):
    d = find_doc(ctx, a.get("document"))
    fmt = a.get("format")
    if fmt == "movie":
        raise ValueError("Windows 上不导出视频：做完 .pptx 拿到 Mac 上 import_pptx，在 Keynote 里审核后手动导出。")
    if fmt not in EXTS:
        raise ValueError("format 只能是 pdf / png / jpeg / tiff / pptx")
    if a.get("with_notes"):
        raise ValueError("Windows 版导不出带备注的 PDF（PowerPoint 的这个接口在 pywin32 里调不通）。备注可以用 get_deck notes=full 读出来。")
    slides = sorted({slide_no(x) for x in as_list(a.get("slides"))})
    for n in slides:
        get_slide(d, n)
    stem = os.path.splitext(d.Name)[0]
    path = a.get("path")
    if path:
        path = os.path.normpath(ctx.cfg.to_local(path))
    else:
        base = os.path.dirname(doc_path(d)) if doc_path(d) and ctx.cfg.in_roots(doc_path(d)) else ctx.cfg.workspace
        path = os.path.join(base, stem + ("" if fmt in FILTERS else EXTS[fmt]))
    ctx.cfg.check_writable(path)
    if fmt in FILTERS:
        os.makedirs(path, exist_ok=True)
        W, H = d.PageSetup.SlideWidth, d.PageSetup.SlideHeight
        scale = num(a.get("scale", 1), "scale", lo=0.1, hi=4)
        files = []
        for n in (slides or range(1, d.Slides.Count + 1)):
            f = os.path.join(path, "%s.%03d%s" % (stem, n, EXTS[fmt]))
            if os.path.exists(f) and not a.get("overwrite"):
                raise ValueError("文件已存在：%s（要覆盖请传 overwrite=true）" % f)
            d.Slides.Item(n).Export(f, FILTERS[fmt], int(W * scale), int(H * scale))
            files.append(f)
        return {"format": fmt, "folder": path, "files": len(files), "first": files[0] if files else None}
    if os.path.exists(path) and not a.get("overwrite"):
        raise ValueError("文件已存在：%s（要覆盖请传 overwrite=true）" % path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    src, tmp = d, None
    if slides:
        src, tmp = _subset_copy(ctx, d, slides)
    try:
        if fmt == "pdf":
            # ppSaveAsPDF; ExportAsFixedFormat (needed for notes pages) cannot be called through pywin32
            src.SaveCopyAs(path, 32)
        else:
            src.SaveCopyAs(path)
    finally:
        if tmp:
            src.Saved = MSO_TRUE
            src.Close()
            shutil.rmtree(tmp, ignore_errors=True)
    return {"format": fmt, "path": path, "size_mb": round(os.path.getsize(path) / 1e6, 2)}


# ------------------------------------------------------------------- registry --

DOC = {"type": "string", "description": "目标文稿：文稿名或完整路径。不填 = 最近一次用工具打开/新建的文稿（没有则是 PowerPoint 当前窗口的文稿）。"}
SLIDE = {"type": "integer", "minimum": 1, "description": "页号，从 1 开始"}
ITEM = {"type": "integer", "minimum": 1, "description": "对象编号（get_slide 返回的 item，按叠放顺序，最底层是 1）"}
NUM = {"type": "number"}
COLOR = {"type": "string", "description": "颜色 #RRGGBB"}

CHANGE_PROPS = {
    "x": NUM, "y": NUM, "width": NUM, "height": NUM,
    "rotation": {"type": "number", "description": "角度 0-359"},
    "opacity": {"type": "number", "description": "不透明度 0-100（文字、形状、线条；图片不支持）"},
    "text": {"type": "string"}, "font": {"type": "string", "description": "字体名；不填时新文字用 Helvetica Neue（中文 PingFang SC），Windows 显示替代字体，到 Mac 上还原"},
    "size": {"type": "number", "description": "字号"}, "color": COLOR,
    "image": {"type": "string", "description": "换成另一张图片（文件路径）；位置、宽度、叠放顺序、名字和动画保持不变"},
    "description": {"type": "string", "description": "图片的替代文字（可用作标签）"},
    "locked": {"type": "boolean"},
    "highlight": {"description": "把文字里的一段标成红色（F 值红 #FC4B3E）：字符串，或 {text, color}，或它们的数组",
                  "anyOf": [{"type": "string"}, {"type": "object"}, {"type": "array"}]},
}


def _schema(props, required=()):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


TOOLS = [
    ("keynote_status", "查看 PowerPoint（Windows 上代替 Keynote）是否在运行、打开了哪些文稿（路径、页数、是否受保护），以及允许写入的目录。",
     _schema({}), h_status),
    ("create_presentation", "新建一份 PowerPoint 文稿（.pptx）并立即保存（默认放在工作目录）。默认从 KEYNOTE动画模板 的 PowerPoint 版新建"
                            "（18 页：1–11 深灰空白页，12–18 纵向像差 / 色焦移标注 / MTF / 封面 / 结构图 / 镜片形状 / 产品介绍页样例，"
                            "和 Keynote 模板页号、对象编号一致），返回 template_guide。",
     _schema({"path": {"type": "string", "description": "保存路径（.pptx）。不填则用 name 放进工作目录"},
              "name": {"type": "string", "description": "不给 path 时的文件名"},
              "width": {"type": "integer", "description": "不用模板时的页宽，默认 1920"},
              "height": {"type": "integer", "description": "不用模板时的页高，默认 1080"},
              "template": {"type": "string", "description": "从这份 .pptx 模板新建；默认 templates/KEYNOTE动画模板-PPT.pptx；none = 空白深灰文稿"},
              "overwrite": {"type": "boolean"}}), h_create),
    ("open_presentation", "打开一份 .pptx 文稿。文件不在允许修改的目录里时（比如参考成品、移动硬盘上的原稿），会先复制一份再打开，原文件不会被改动。"
                          ".key 只能在 Mac 上打开。",
     _schema({"path": {"type": "string"}}, ["path"]), h_open),
    ("save_presentation", "保存文稿；给 path 则另存到新位置（之后接着编辑新文件）。工具每次修改后也会自动保存。",
     _schema({"document": DOC, "path": {"type": "string"}}), h_save),
    ("close_presentation", "关闭文稿，默认先保存。", _schema({"document": DOC, "save": {"type": "boolean"}}), h_close),
    ("get_deck", "读取整份文稿的结构：每页的版式、标题（或第一段文字）、对象数、备注（含 [图N] 标签）、是否隐藏、切入转场、动画数、样例页标记。",
     _schema({"document": DOC, "notes": {"type": "string", "enum": ["short", "full", "none"], "description": "备注显示方式，默认 short"}}),
     h_get_deck),
    ("get_slide", "读取某一页的所有对象：编号、类型、名字、位置、尺寸、文字、字体字号颜色、图片文件、透明度、旋转，以及备注、切入转场和动画。",
     _schema({"document": DOC, "slide": SLIDE}, ["slide"]), h_get_slide),
    ("view_slide", "把某一页渲染成图片给你看（检查排版和平滑前后两页的效果），同时附上对象列表。Windows 上 Helvetica Neue 显示为替代字体。",
     _schema({"document": DOC, "slide": SLIDE, "max_size": {"type": "integer", "description": "图片最长边像素，默认 1280"},
              "save_to": {"type": "string", "description": "另存一份 1920x1080 PNG 到这个路径"},
              "with_items": {"type": "boolean"}}, ["slide"]), h_view_slide),
    ("add_slide", "加一页深灰空白页（沿用前一页的背景），默认加在末尾，切入转场默认 0.5 秒平滑（你的习惯）。可同时写标题、正文和演讲者备注。",
     _schema({"document": DOC, "layout": {"type": "string", "description": "版式名（get_deck 返回的 layouts）；不填 = 空白页"},
              "after": {"type": "integer", "minimum": 0, "description": "插在第几页之后；0 = 最前面；不填 = 末尾"},
              "title": {"type": "string"}, "body": {"type": "string"}, "notes": {"type": "string"},
              "transition": {"type": "string", "description": "切入这一页的转场，默认 magic move（平滑）；none = 无"},
              "inherit_background": {"type": "boolean", "description": "默认 true：沿用前一页的版式和背景"},
              "duration": {"type": "number", "description": "转场时长，默认 0.5"}}), h_add_slide),
    ("duplicate_slide", "复制一页（副本紧跟在原页后面）。", _schema({"document": DOC, "slide": SLIDE, "count": {"type": "integer"}}, ["slide"]),
     h_duplicate_slide),
    ("delete_slides", "删除一页或多页。", _schema({"document": DOC, "slides": {"type": "array", "items": {"type": "integer"}}}, ["slides"]),
     h_delete_slides),
    ("move_slide", "把一页移到新位置（to = 移动后的页号）。", _schema({"document": DOC, "slide": SLIDE, "to": SLIDE}, ["slide", "to"]),
     h_move_slide),
    ("set_slide", "修改一页：版式、是否隐藏（skipped）、标题/正文（有占位符就填进去，没有就加文本框）。",
     _schema({"document": DOC, "slide": SLIDE, "layout": {"type": "string"}, "skipped": {"type": "boolean"},
              "title": {"type": "string"}, "body": {"type": "string"},
              "title_showing": {"type": "boolean"}, "body_showing": {"type": "boolean"}}, ["slide"]), h_set_slide),
    ("add_text", "在一页上加文本框。x/y 是左上角坐标（点），不填则居中。width 固定宽度后会自动换行。默认白色 34 号 Helvetica Neue。",
     _schema({"document": DOC, "slide": SLIDE, "text": {"type": "string"}, "x": NUM, "y": NUM, "width": NUM,
              "font": CHANGE_PROPS["font"], "size": CHANGE_PROPS["size"], "color": COLOR,
              "align": {"type": "string", "enum": ["left", "center", "right"]},
              "opacity": CHANGE_PROPS["opacity"], "rotation": CHANGE_PROPS["rotation"],
              "highlight": CHANGE_PROPS["highlight"]}, ["slide", "text"]), h_add_text),
    ("add_image", "在一页上放图片（png/jpg/tif 等，PDF 先转成 PNG）。只给 width 或 height，另一边按比例；都不给时按原尺寸（大于页面则缩到页面内）；不给位置则居中。",
     _schema({"document": DOC, "slide": SLIDE, "path": {"type": "string"}, "x": NUM, "y": NUM, "width": NUM, "height": NUM,
              "rotation": CHANGE_PROPS["rotation"], "description": CHANGE_PROPS["description"]}, ["slide", "path"]), h_add_image),
    ("add_shape", "加一个矩形（rounded=true 为圆角），默认灰色 #5E5E5E 填充、无边框，可带文字。",
     _schema({"document": DOC, "slide": SLIDE, "x": NUM, "y": NUM, "width": NUM, "height": NUM, "text": {"type": "string"},
              "fill": COLOR, "rounded": {"type": "boolean"},
              "font": CHANGE_PROPS["font"], "size": CHANGE_PROPS["size"], "color": COLOR,
              "opacity": CHANGE_PROPS["opacity"], "rotation": CHANGE_PROPS["rotation"]}, ["slide"]), h_add_shape),
    ("add_line", "加一条直线（起点 x1,y1 到终点 x2,y2），默认白色 2 点。",
     _schema({"document": DOC, "slide": SLIDE, "x1": NUM, "y1": NUM, "x2": NUM, "y2": NUM, "color": COLOR,
              "weight": {"type": "number", "description": "线宽（点），默认 2"}, "dash": {"type": "boolean", "description": "虚线"}},
             ["slide", "x1", "y1", "x2", "y2"]), h_add_line),
    ("add_table", "加一个表格，rows 是二维数组（第一行默认作表头，加粗）。白字、白色细边框、无底色，默认 24 号。",
     _schema({"document": DOC, "slide": SLIDE, "rows": {"type": "array", "items": {"type": "array"}},
              "header_rows": {"type": "integer"}, "header_columns": {"type": "integer"}, "size": {"type": "number"},
              "x": NUM, "y": NUM, "width": NUM, "height": NUM}, ["slide", "rows"]), h_add_table),
    ("update_item", "修改一页上某个对象：位置、尺寸、旋转、透明度、文字及其字体字号颜色、换图、锁定。只改你给出的属性。",
     _schema(dict({"document": DOC, "slide": SLIDE, "item": ITEM}, **CHANGE_PROPS), ["slide", "item"]), h_update_item),
    ("delete_items", "删除一页上的一个或多个对象。",
     _schema({"document": DOC, "slide": SLIDE, "items": {"type": "array", "items": {"type": "integer"}}}, ["slide", "items"]),
     h_delete_items),
    ("set_notes", "写演讲者备注（视频文稿段落放这里，第一行 [图N：…]）。append=true 则追加。",
     _schema({"document": DOC, "slide": SLIDE, "text": {"type": "string"}, "append": {"type": "boolean"}}, ["slide", "text"]),
     h_set_notes),
    ("set_transition", "设置转场。注意 PowerPoint 的记法：effect / duration 是「切入这一页」的转场（第 N 页上的平滑 = 从第 N-1 页切到第 N 页）；"
                       "delay / automatic 是这一页停留 delay 秒后自动切走。effect 可用 magic move（平滑，Keynote 导入后是神奇移动）、"
                       "dissolve（Keynote 导入后是溶解）、morph_word、morph_char、fade_through_black、cut、none。",
     _schema({"document": DOC, "slides": {"description": "页号、页号数组，或 \"all\""},
              "effect": {"type": "string"}, "duration": {"type": "number", "description": "转场时长（秒）"},
              "delay": {"type": "number", "description": "自动切换前的停留秒数"},
              "automatic": {"type": "boolean"}}), h_set_transition),
    ("magic_move_step", "做一步神奇移动（PowerPoint 的平滑）：复制第 slide 页放到它后面，对新页上的对象做 changes（移动、缩放、旋转、淡化、改字、换图），"
                        "删掉 remove_items（平滑时淡出），新页的切入转场设为平滑。连续调用就能做出多步动画。对象编号沿用原页的编号。"
                        "到 Mac 上 import_pptx 后就是第 slide 页到下一页的神奇移动。",
     _schema({"document": DOC, "slide": SLIDE,
              "changes": {"type": "array", "items": _schema(dict({"item": ITEM}, **CHANGE_PROPS), ["item"])},
              "remove_items": {"type": "array", "items": {"type": "integer"}, "description": "新页上要删掉的对象（平滑时淡出）"},
              "duration": {"type": "number", "description": "平滑时长，默认 0.5 秒"},
              "delay": {"type": "number", "description": "原页停留几秒后自动切到新页"}, "automatic": {"type": "boolean"}}, ["slide"]),
     h_magic_move_step),
    ("chapter_card", "按你文稿里的章节卡样式插入三页：空白页 → 编号（76 号）+ 标题（71 号）两个白色居中文本框 → 空白页，每页切入 0.5 秒平滑。"
                     "title 可以直接写「二 性能仿真与测量」，会自动拆出编号。",
     _schema({"document": DOC, "title": {"type": "string", "description": "如「二 性能仿真与测量」或只写标题"},
              "number": {"type": "string", "description": "编号（一、二…）；不填则从 title 开头拆"},
              "after": {"type": "integer", "minimum": 0, "description": "插在第几页之后，不填 = 末尾"},
              "number_size": {"type": "number", "description": "默认 76"}, "title_size": {"type": "number", "description": "默认 71"},
              "font": CHANGE_PROPS["font"], "color": {"type": "string", "description": "默认 #FFFFFF"},
              "transition": {"type": "string", "description": "默认 magic move（平滑）"}, "duration": {"type": "number", "description": "默认 0.5"},
              "blank_before": {"type": "boolean"}, "blank_after": {"type": "boolean"}, "notes": {"type": "string"}},
             ["title"]), h_chapter_card),
    ("add_builds", "给对象加进入动画，记下 Keynote 构件动画的意图：line_draw（线条描绘：曲线、坐标轴、光路）在 PowerPoint 里写成「擦除（自左）」，"
                   "fade_move（逐字淡入移动：图例文字、色块）写成「飞入（自左，按字）」。PowerPoint 里只作预览，到 Mac 上 import_pptx 后用 add_builds 重做。"
                   "start：click 点按 / with 与上一个同时 / after 在上一个之后；不填时每页第一个新动画点按、其余与上一个同时（你的习惯）。",
     _schema({"document": DOC,
              "builds": {"type": "array", "items": _schema({
                  "slide": SLIDE, "item": ITEM,
                  "effect": {"type": "string", "enum": ["line_draw", "fade_move"]},
                  "duration": {"type": "number", "description": "秒；默认 line_draw 2.0、fade_move 1.5（你常用 1.5–3）"},
                  "start": {"type": "string", "enum": ["click", "with", "after"]},
                  "delay": {"type": "number"}}, ["slide", "item"])}}, ["builds"]), h_add_builds),
    ("list_builds", "列出进入/退出动画：每页哪些对象有什么动画、时长、出场方式。Keynote 导出的「溶解」「飞入」和本工具写的「擦除」都按原意显示为 line_draw / fade_move。",
     _schema({"document": DOC, "slide": SLIDE}), h_list_builds),
    ("read_zemax_mtf", "只读：从 Zemax「FFT MTF vs. Field」导出图里读出各频率子午/弧矢的 MTF 数值（不改文稿），可存成 CSV，写稿引用数据时用。",
     _schema({"image": {"type": "string"}, "x_max": {"type": "number"},
              "fields": {"type": "array", "items": {"type": "number"}},
              "csv_path": {"type": "string", "description": "把整条曲线的数据存成 CSV"}}, ["image"]), h_read_zemax_mtf),
    ("product_card", "做产品介绍页（你视频前段那种）：产品图、产品名、灰色圆角标签（发布日期 Since… 或价格），"
                     "1 款居中，2 款左右并排、中间一条箭头；产品图和产品名之间有一条 5 磅白色横线（y=770）。"
                     "版式照搬模板第 18 页，全部在 y=1000 字幕参考线以上。"
                     "产品图用作者在 Photoshop 抠好的透明 PNG（在项目文件夹的「白底图」「白底产品样图」等文件夹里），工具不做去背景；"
                     "找不到抠好的图就先问作者，不要用白底原图。",
     _schema({"document": DOC,
              "products": {"type": "array", "items": _schema({
                  "image": {"type": "string", "description": "Photoshop 抠好的透明 PNG（项目文件夹里）"},
                  "name": {"type": "string", "description": "如 VILTROX AF 35mm F1.4 Pro"},
                  "badge": {"type": "string", "description": "标签文字：Since 2026.07.08 或价格；不填则不要标签"},
                  "highlight": {"type": "string", "description": "名字里要标红的部分，可选"}}, ["name"])},
              "after": {"type": "integer", "minimum": 0, "description": "放在第几页之后，不填 = 末尾"},
              "arrow": {"type": "boolean", "description": "两款之间的箭头，两款时默认 true"}},
             ["products"]), h_product_card),
    ("export_lens_layout", "用 CODE V 把 .len 镜头文件导出 2D 结构图（无穷远位、3 个视场各上/主/下 3 条光线，含渐晕），"
                           "照美科 85MIX2 那页的样式重画成矢量图：SVG（每片镜片、每条光线单独 id，Illustrator / Keynote 可直接用）、"
                           "带玻璃名标注的 SVG、1:1 毫米 DXF（分图层）和 CODE V 原始 DXF；可选 png / pdf。"
                           "一次可给多只镜头（同一个 CODE V 会话，同一比例、像面对齐，方便对比）。只在 Windows + CODE V 上能用；不改 .len。",
     _schema({"lens_files": {"type": "array", "items": {"type": "string"}, "minItems": 1, "description": "CODE V .len 文件路径"},
              "out_dir": {"type": "string", "description": "输出文件夹（需在允许目录内）"},
              "fields": {"type": "array", "items": {"type": "integer"}, "description": "视场号（最多 3 个）；不填 = 轴上、最接近最大像高 55% 的那个、最大视场"},
              "zoom": {"type": "integer", "minimum": 1, "description": "对焦 / 变焦位置，默认 1（无穷远）"},
              "formats": {"type": "array", "items": {"type": "string", "enum": ["svg", "dxf", "png", "pdf"]}, "description": "默认 svg + dxf"},
              "labeled": {"type": "boolean", "description": "同时出带玻璃名标注的版本，默认 true"},
              "px_per_mm": {"type": "number", "description": "1920x1080 画布上每毫米多少像素，默认 14.3（长镜头调小）"},
              "names": {"type": "array", "items": {"type": "string"}, "description": "输出文件名前缀，默认用 .len 文件名"}},
             ["lens_files", "out_dir"]), h_export_lens_layout),
    ("list_references", "查看作者近期成品文稿（Keynote 导出的 .pptx 版，页号和原稿一致）：每份的页数、各类页面（章节卡、MTF 图、纵向像差图、"
                        "频响图、产品介绍页……）在第几页、每页的主要文字，以及缩略图目录。做新页前可以先找找他以前怎么做的。",
     _schema({"project": {"type": "string", "description": "课题名的一部分，如 85MIX2、松若85；不填则列出全部"},
              "kind": {"type": "string", "description": "只看某类页面：章节卡 / FFT MTF vs Field 图 / 纵向像差图 / 频响图 / 产品介绍页 / 曲线/结构图 / 图片页 / 图文页 / 文字页"},
              "with_sheet": {"type": "boolean", "description": "把缩略图目录作为图片返回（需要 project）"}}), h_list_references),
    ("image_slides", "用一批图片直接生成一组新页（加在末尾，图片缩放到字幕线以上居中），适合仿真截图、MTF 图。",
     _schema({"document": DOC, "paths": {"type": "array", "items": {"type": "string"}},
              "set_titles": {"type": "boolean", "description": "用文件名作标题"}}, ["paths"]), h_image_slides),
    ("sync_notes_from_script", "读 lens-video-script 的 文稿.md，把每个 [图N：…] 后面的段落写进对应页的演讲者备注（备注里原有的 [动画] 行保留）。"
                               "对应方式：mapping（{\"图号\": 页号或页号数组}）优先；否则 strategy=tag 按备注里已有的 [图N] 标签；"
                               "strategy=order 按顺序对应非空白页。先用 dry_run=true 预览。",
     _schema({"document": DOC, "script_path": {"type": "string"}, "mapping": {"type": "object"},
              "strategy": {"type": "string", "enum": ["tag", "order"]}, "include_tag": {"type": "boolean"},
              "dry_run": {"type": "boolean"}}, ["script_path"]), h_sync_notes),
    ("export", "导出文稿：pdf、png/jpeg/tiff（逐页图片，存进一个文件夹，可直接当文稿配图）、pptx（副本）。可以只导出指定页。"
               "默认放在文稿旁边。Windows 上不导出视频，也不导出带备注的 PDF。",
     _schema({"document": DOC, "format": {"type": "string", "enum": ["pdf", "png", "jpeg", "tiff", "pptx"]},
              "path": {"type": "string", "description": "输出路径；图片格式时是文件夹"},
              "slides": {"type": "array", "items": {"type": "integer"}, "description": "只导出这些页"},
              "scale": {"type": "number", "description": "图片相对 1920x1080 的倍数，默认 1；2 = 3840x2160"},
              "overwrite": {"type": "boolean"}}, ["format"]), h_export),
]

READ_ONLY = {"keynote_status", "get_deck", "get_slide", "view_slide", "read_zemax_mtf", "list_builds", "list_references"}
# Tools after which the deck is saved automatically (PowerPoint has no autosave for local files).
NO_AUTOSAVE = READ_ONLY | {"save_presentation", "close_presentation", "create_presentation", "open_presentation", "export", "export_lens_layout"}
SUBTITLE_CHECKED = {"add_text", "add_image", "add_shape", "add_line", "add_table", "update_item"}
HANDLERS = {name: h for name, _d, _s, h in TOOLS}


def tool_list(cfg):
    return [{"name": name, "description": desc, "inputSchema": schema, "annotations": {"readOnlyHint": name in READ_ONLY}}
            for name, desc, schema, _h in TOOLS]


def _subtitle_warning(it):
    try:
        bottom = float(it.get("y", 0)) + float(it.get("height", 0))
    except (TypeError, ValueError):
        return it
    if bottom > SUBTITLE_Y + 0.5:
        it["warning"] = "这个对象伸到了字幕参考线（y=%d）以下，底边在 y=%.0f，视频里会和字幕重叠。" % (SUBTITLE_Y, bottom)
    return it


def _run(ctx, h, args):
    try:
        return h(ctx, args)
    except Exception as e:
        if pywintypes is not None and isinstance(e, pywintypes.com_error):
            if _hresult(e) in RPC_GONE:
                ctx._app = None  # PowerPoint was closed or restarted; reconnect once
                try:
                    return h(ctx, args)
                except Exception as e2:
                    if isinstance(e2, pywintypes.com_error):
                        raise PptError("PowerPoint 报错：%s" % _com_msg(e2))
                    raise
            raise PptError("PowerPoint 报错：%s（如果 PowerPoint 弹出了对话框，先手动关掉）" % _com_msg(e))
        raise


def call(ctx, name, args):
    h = HANDLERS.get(name)
    if not h:
        raise ValueError("没有这个工具：%s" % name)
    ctx.dirty = None
    r = _run(ctx, h, args or {})
    if name not in NO_AUTOSAVE and ctx.dirty is not None:
        d, ctx.dirty = ctx.dirty, None
        try:
            if doc_path(d):
                d.Save()
        except Exception as e:
            if isinstance(r, dict):
                r["autosave"] = "自动保存失败：%s" % (_com_msg(e) if pywintypes and isinstance(e, pywintypes.com_error) else e)
    if name in SUBTITLE_CHECKED and isinstance(r, dict):
        r = _subtitle_warning(r)
    if name == "get_slide" and isinstance(r, dict):
        below = [i["item"] for i in r.get("items", []) if "warning" in _subtitle_warning(dict(i))]
        if below:
            r["below_subtitle_line"] = {"y": SUBTITLE_Y, "items": below}
    return r


INSTRUCTIONS = """\
这台是 Windows：用这个服务器控制本机的 Microsoft PowerPoint（COM），代替 Mac 上的 Keynote。工具名和参数与 Mac 版一致。要点：
- 产物是 .pptx。作者之后在 Mac 上用 import_pptx 把它转成 .key（平滑→神奇移动，备注保留），在 Keynote 里精修动画、审核、手动导出视频。
  所以这里做的是主体：版式、文字、图片、平滑步骤、章节卡、备注。不导出视频。
- 坐标单位是点，原点在页面左上角；页面 1920x1080（26.667x15 英寸），数字和 Keynote 文稿一致。页号、对象编号从 1 开始（按叠放顺序）。
  先 get_deck / get_slide 了解现状，改完用 view_slide 看效果再继续。删除对象后编号会前移。
- 转场记法和 Keynote 相反：PowerPoint 的转场记在「切入的那一页」上（第 N 页上的平滑 = 第 N-1 页切到第 N 页）。
  magic_move_step、add_slide、chapter_card 已经按这个处理；单独用 set_transition 时要注意。
- 动画：转场用 magic_move_step（复制当前页、改新页上的对象、新页切入设为 0.5 秒平滑）。工具新建的对象名字以 !! 开头，平滑一定能配对。
  构件动画用 add_builds 记意图（line_draw 写成擦除、fade_move 写成飞入），只是预览，到 Mac 上要用 add_builds 重做成线条描绘 / 逐字淡入移动。
  图片透明度脚本设不了：要淡出就在下一页删掉它（remove_items）。
- 字体：新文字用 Helvetica Neue（中文 PingFang SC）。Windows 没有这两个字体，显示的是替代字体、宽度略有出入；不要改成微软雅黑，回到 Mac 上会还原。
- 视频文稿：每段对应的 [图N：…] 写进演讲者备注（set_notes 或 sync_notes_from_script），第一行必须是 [图N：…]。
- 每次修改后工具会自动保存文稿。只能在允许的目录里保存和导出；不在这些目录里的文稿会被拒绝修改，open_presentation 会先复制一份副本再打开。
- 如果 PowerPoint 弹出了对话框（比如字体、修复提示），所有操作都会卡住，请用户先手动关掉。

用户的版式习惯（从他 78 份 Keynote 文稿统计得出，照着做）：
- 新文稿默认是他每期初始文件「KEYNOTE动画模板」的 PowerPoint 版（create_presentation 返回 template_guide），页号和对象编号与 Keynote 模板一致：
  1–11 页是深灰 #2C2C2C 空白页，直接用；12 纵向像差图、13 色焦移标注、14 FFT MTF vs Field、15 开场封面、16 光学结构图、17 镜片形状素材、18 产品介绍页。
  做同类页时 duplicate_slide 复制样例页、move_slide 挪到位置、update_item 改文字（F 值数字用 highlight 标红 #FC4B3E）；
  图表页底图换成新的 Zemax 图用 update_item 的 image（位置、宽度、叠放顺序不变）。曲线形状脚本改不了（Windows 上没有 trace_mtf），由他自己描或到 Mac 上 trace_mtf；
  read_zemax_mtf 可以读出数值写进文稿。做完删掉没用的样例页和多余空白页。
  结构图（镜片 + 3 个视场的光线）：export_lens_layout 直接从 CODE V 的 .len 导出 SVG / DXF（可一次给多只镜头，同比例）；要放进结构图页就在 formats 里加 png，再用 add_image 放 PNG 版。
- 95% 的转场是 0.5 秒神奇移动（其次 0.8 秒），全部点按换页。add_slide 和 chapter_card 默认已按此设置。
- 两页内容之间通常夹一张空白页（视频里表现为淡出再淡入）。
- 章节卡：空白页 → 编号「一」(76 号) + 标题 (71 号) 两个白色居中文本框 → 空白页，用 chapter_card。
- 他大量使用对象构件动画：曲线、坐标轴、光路用「线条描绘」（2–3 秒），图例文字和色块用「逐字淡入移动」（1.5–3 秒）；
  通常每页第一个动画点按出场，其余与上一个同时。用 add_builds 按这个习惯记下来。
- 字幕区：y=1000 以下是字幕位置，任何内容的底边都不要低于 y=1000（工具结果里出现 warning 就要挪上去）。
- 视频前段的产品介绍页用 product_card（产品图 + 产品名 + 灰色标签写发布日期或价格，1–2 款）。
  产品图用他在 Photoshop 抠好的透明 PNG，在项目文件夹的「白底图」「白底产品样图」等文件夹里找；不要自动去背景，找不到就先问他。
- 参考素材：list_references 列出他近期的成品文稿（Keynote 导出的 .pptx 版、每页类型、缩略图目录）。做某类页面前先看看他以前怎么做的；
  要细看就 open_presentation 打开 pptx（会自动复制）。
"""
