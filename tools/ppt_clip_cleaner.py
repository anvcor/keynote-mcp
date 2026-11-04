"""Keep PowerPoint copies pasteable in web pages such as Feishu.

Microsoft 365 PowerPoint puts web formats on the clipboard next to its own ones:
image/svg+xml, "Chromium Web Custom MIME Data Format" (shows up as ppt/slides in Chrome) and
application/x-moz-custom-clipdata. Feishu sees those and pastes nothing. This removes only them;
every other format stays, so pasting back into PowerPoint still gives editable slides / shapes,
and a browser sees just the picture (like a screenshot).

    pythonw ppt_clip_cleaner.py          # watch the clipboard in the background
    python  ppt_clip_cleaner.py --once   # clean what is on the clipboard now and exit
"""
import os
import sys
import time

import ctypes

import win32clipboard as cb
import win32con

_gdi32 = ctypes.windll.gdi32
_gdi32.SetEnhMetaFileBits.restype = ctypes.c_void_p
_gdi32.SetEnhMetaFileBits.argtypes = [ctypes.c_uint, ctypes.c_char_p]


def _set(f, d):
    """SetClipboardData; an EMF comes back from pywin32 as bytes and has to go in as a handle."""
    if f == win32con.CF_ENHMETAFILE and isinstance(d, (bytes, bytearray)):
        h = _gdi32.SetEnhMetaFileBits(len(d), bytes(d))
        if not h:
            raise OSError("SetEnhMetaFileBits failed")
        cb.SetClipboardData(f, h)
    else:
        cb.SetClipboardData(f, d)

WEB_FORMATS = ("image/svg+xml", "Chromium Web Custom MIME Data Format", "application/x-moz-custom-clipdata")
# only touch copies made in PowerPoint
TRIGGERS = ("PowerPoint 14.0 Slides Package", "PowerPoint 12.0 Internal Slides", "Art::GVML ClipFormat",
            "PowerPoint 12.0 Internal Shapes")
# OLE bookkeeping that points at the source app's live data object; meaningless once re-written
OLE_INTERNAL = ("DataObject", "Ole Private Data")
# GDI-handle formats Windows synthesizes again from EMF / DIB
SKIP_STD = {win32con.CF_BITMAP, win32con.CF_METAFILEPICT, win32con.CF_PALETTE, 2}
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ppt_clip_cleaner.log")


def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def fmt_ids(names):
    return {cb.RegisterClipboardFormat(n): n for n in names}


WEB_IDS = None
TRIGGER_IDS = None
OLE_IDS = None


def init_ids():
    global WEB_IDS, TRIGGER_IDS, OLE_IDS
    WEB_IDS, TRIGGER_IDS, OLE_IDS = fmt_ids(WEB_FORMATS), fmt_ids(TRIGGERS), fmt_ids(OLE_INTERNAL)


def needs_cleaning():
    if not any(cb.IsClipboardFormatAvailable(f) for f in TRIGGER_IDS):
        return False
    return any(cb.IsClipboardFormatAvailable(f) for f in WEB_IDS)


def open_clipboard(tries=20):
    for _ in range(tries):
        try:
            cb.OpenClipboard()
            return True
        except Exception:
            time.sleep(0.05)
    return False


def clean():
    """Rewrite the clipboard without the web formats. Returns a short report, or None if nothing to do."""
    if not needs_cleaning():
        return None
    if not open_clipboard():
        return "剪贴板被别的程序占用，跳过"
    kept, dropped, failed = [], [], []
    try:
        data = []
        f = 0
        while True:
            f = cb.EnumClipboardFormats(f)
            if not f:
                break
            name = cb.GetClipboardFormatName(f) if f >= 0xC000 else "CF_%d" % f
            if f in WEB_IDS or f in OLE_IDS or f in SKIP_STD:
                dropped.append(name)
                continue
            try:
                data.append((f, name, cb.GetClipboardData(f)))
            except Exception as e:  # some delay-rendered formats cannot be read; leave them out
                failed.append("%s(%s)" % (name, type(e).__name__))
        cb.EmptyClipboard()
        for f, name, d in data:
            try:
                _set(f, d)
                kept.append(name)
            except Exception as e:
                failed.append("%s(set %s)" % (name, type(e).__name__))
    finally:
        cb.CloseClipboard()
    rep = "保留 %d 种，去掉 %s%s" % (len(kept), ", ".join(dropped), ("；读写失败 " + ", ".join(failed)) if failed else "")
    log(rep)
    return rep


def watch():
    import win32api
    import win32event
    import winerror
    global _MUTEX  # keep the handle alive: one watcher per login session
    _MUTEX = win32event.CreateMutex(None, False, "Local\\ppt_clip_cleaner")
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        log("已经有一个在运行，退出")
        return
    log("开始监视剪贴板")
    last = cb.GetClipboardSequenceNumber()
    while True:
        time.sleep(0.25)
        seq = cb.GetClipboardSequenceNumber()
        if seq == last:
            continue
        time.sleep(0.2)  # let PowerPoint finish putting its formats
        try:
            clean()
        except Exception as e:
            log("出错：%r" % e)
        last = cb.GetClipboardSequenceNumber()


def main():
    init_ids()
    if "--once" in sys.argv:
        r = clean()
        print(r or "剪贴板里不是 PowerPoint 的复制内容，或已经没有网页格式，不用处理")
        return
    watch()


if __name__ == "__main__":
    main()
