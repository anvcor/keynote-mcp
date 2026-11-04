"""MTF vs Field page from a CODE V lens file, in the style of the author's 老蛙 AF35MC page 26 (Windows + PowerPoint + CODE V).

    python tools/mtf_vs_field_page.py LENS.len DECK.pptx --after 16 --png-dir MTF图 --name SONY_FE35GM \
        --freqs 10 30 80 --spectrum 656.3:10 587.6:27 546.1:29 486.1:23 435.8:11 --ref-wl 3

1. CODE V (COM) runs the macro mtfvsfld.seq at the first zoom position under the given spectrum (default = the author's
   saved spectrum "VOD"; `SPE name` only opens the spectrum option, so wavelengths and weights are set with WL / WTW / REF)
   and exports CODE V's own chart with `WGF PNG` (2400x1350). The data table is saved next to it as text.
2. A page is added to DECK after slide --after, copying the neighbouring page's dark background: a full-bleed white
   rectangle and the CODE V chart filling the page (老蛙 page 26: "Rectangle" + "MTF_INF.jpg", no other text).
"""
import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from keynote_mcp import lens_layout as LL  # noqa: E402  (CodeV engine wrapper)

MACRO = r"E:\CODEV2026\macro\mtfvsfld.seq"


def run_mtf_png(lens, freqs, spectrum, ref_wl, png, table, size=(2400, 1350), npts=11):
    eng = LL.CodeV()
    try:
        shutil.copyfile(lens, os.path.join(eng.dir, "s.len"))
        eng.cmd("RES s")
        eng.cmd("WL " + " ".join(w for w, _ in spectrum))
        eng.cmd("WTW " + " ".join(wt for _, wt in spectrum))
        eng.cmd("REF %d" % ref_wl)
        eng.cmd("WGF PNG mtfchart %d %d" % size)          # export the UCH chart the macro draws
        eng.cmd("DCS NO", check=False)
        out = eng.cmd('in "%s" %s 1 %d' % (MACRO, " ".join(str(f) for f in freqs), npts), check=False)
        src = os.path.join(eng.dir, "mtfchart.png")
        if not os.path.isfile(src):
            raise SystemExit("CODE V 没有导出图表：\n" + out[-600:])
        shutil.copyfile(src, png)
    finally:
        eng.close()
    spec = " ".join("%s:%s" % (w, wt) for w, wt in spectrum)
    with open(table, "w", encoding="utf-8") as f:
        f.write("光谱 %s（参考波长第 %d 个）\n" % (spec, ref_wl) + out[out.index("MTF Results") - 20:])


def add_page(deck, after, png):
    import win32com.client as w
    app = w.GetActiveObject("PowerPoint.Application")
    pres = next(p for p in app.Presentations if p.Name == os.path.basename(deck))
    prev = pres.Slides(after)
    s = prev.Duplicate()(1) if prev.Shapes.Count == 0 else pres.Slides.AddSlide(after + 1, prev.CustomLayout)
    s.FollowMasterBackground = prev.FollowMasterBackground
    if not prev.FollowMasterBackground:
        s.Background.Fill.Solid()
        s.Background.Fill.ForeColor.RGB = prev.Background.Fill.ForeColor.RGB
    rect = s.Shapes.AddShape(1, -23, -16.4, 1986, 1132.9)
    rect.Name = "Rectangle"
    rect.Fill.ForeColor.RGB = 0xFFFFFF
    rect.Line.Visible = 0
    pic = s.Shapes.AddPicture(os.path.abspath(png), False, True, 0, 0, 1920, 1080)
    pic.Name = os.path.basename(png)
    pres.Save()
    return s.SlideIndex


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lens")
    ap.add_argument("deck")
    ap.add_argument("--after", type=int, required=True, help="insert after this slide")
    ap.add_argument("--png-dir", required=True)
    ap.add_argument("--name", required=True, help="file name prefix, e.g. SONY_FE35GM")
    ap.add_argument("--freqs", type=float, nargs="+", default=[10, 30, 80])
    ap.add_argument("--spectrum", nargs="+", default=["656.3:10", "587.6:27", "546.1:29", "486.1:23", "435.8:11"])
    ap.add_argument("--ref-wl", type=int, default=3)
    a = ap.parse_args()
    os.makedirs(a.png_dir, exist_ok=True)
    png = os.path.join(a.png_dir, "%s_MTF_vs_Field_INF.png" % a.name)
    table = os.path.join(a.png_dir, "%s_MTF_vs_Field_INF_数据.txt" % a.name)
    run_mtf_png(os.path.abspath(a.lens), a.freqs, [tuple(x.split(":")) for x in a.spectrum], a.ref_wl, png, table)
    print("MTF page is slide", add_page(a.deck, a.after, png))


if __name__ == "__main__":
    main()
