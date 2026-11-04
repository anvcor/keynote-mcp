"""MTF vs Field page from a CODE V lens file, in the style of the author's 老蛙 AF35MC page 26 (Windows + PowerPoint + CODE V).

    python tools/mtf_vs_field_page.py LENS.len DECK.pptx --after 16 --png-dir MTF图 --name SONY_FE35GM \
        --freqs 10 30 80 --spectrum 656.3:10 587.6:27 546.1:29 486.1:23 435.8:11 --ref-wl 3

1. CODE V (COM) runs the macro mtfvsfld.seq at the first zoom position under the given spectrum (default = the author's
   saved spectrum "VOD"; `SPE name` only opens the spectrum option, so wavelengths and weights are set with WL / WTW / REF)
   and exports CODE V's own chart with `WGF PNG` (1600x953: the 老蛙 page aspect 1.678, so the chart text has the same relative size). The data table is saved next to it as text.
2. A page is added to DECK after slide --after, copying the neighbouring page's dark background: a full-bleed white
   rectangle and the CODE V chart (plus the brand / lens model in black above the legend) at x 64.5, y 0.4, 1811.1 x 1079.3 (老蛙 page 26: "Rectangle" + "MTF_INF.jpg", no other text).
"""
import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from keynote_mcp import lens_layout as LL  # noqa: E402  (CodeV engine wrapper)

MACRO = r"E:\CODEV2026\macro\mtfvsfld.seq"


def trim_border(path, dark=60):
    """CODE V draws a thin black frame around the chart; cut it off so it does not show on the page"""
    from PIL import Image
    im = Image.open(path).convert("RGB")
    w, h = im.size
    def is_dark(px):
        return px[0] < dark and px[1] < dark and px[2] < dark
    n = 0
    while n < 12 and all(is_dark(im.getpixel((x, n))) for x in range(0, w, max(1, w // 40))):
        n += 1
    m = n + 2                                           # a little extra to swallow anti-aliasing
    im.crop((m, m, w - m, h - m)).save(path)


def run_mtf_png(lens, freqs, spectrum, ref_wl, png, table, size=(1600, 953), npts=11, zoom=1):
    eng = LL.CodeV()
    try:
        shutil.copyfile(lens, os.path.join(eng.dir, "s.len"))
        eng.cmd("RES s")
        eng.cmd("WL " + " ".join(w for w, _ in spectrum))
        eng.cmd("WTW " + " ".join(wt for _, wt in spectrum))
        eng.cmd("REF %d" % ref_wl)
        eng.cmd("WGF PNG mtfchart %d %d" % size)          # export the UCH chart the macro draws
        eng.cmd("DCS NO", check=False)
        out = eng.cmd('in "%s" %s %d %d' % (MACRO, " ".join(str(f) for f in freqs), zoom, npts), check=False)
        src = os.path.join(eng.dir, "mtfchart.png")
        if not os.path.isfile(src):
            raise SystemExit("CODE V 没有导出图表：\n" + out[-600:])
        shutil.copyfile(src, png)
        trim_border(png)
    finally:
        eng.close()
    spec = " ".join("%s:%s" % (w, wt) for w, wt in spectrum)
    with open(table, "w", encoding="utf-8") as f:
        f.write("光谱 %s（参考波长第 %d 个）\n" % (spec, ref_wl) + out[out.index("MTF Results") - 20:])


def add_page(deck, after, png, brand=None, model=None):
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
    pic = s.Shapes.AddPicture(os.path.abspath(png), False, True, 64.5, 0.4, 1811.1, 1079.3)   # same box as 老蛙 page 26
    pic.Name = os.path.basename(png)
    # lens model: two black lines above the chart legend (block centre on the MTF 0.7 grid line, y 344.5), left-aligned with the legend column, same font size and
    # line pitch as the legend (27 pt, 40 px). Boxes are middle-anchored, so the line centre is top + height / 2.
    for text, centre in ((brand, 324.5), (model, 364.5)):
        if text:
            tb = s.Shapes.AddTextbox(1, 1553, centre - 62, 262, 83)      # measured: the glyph centre lands 20.5 px below the box middle
            tb.Name = text
            f = tb.TextFrame
            f.WordWrap = -1
            f.VerticalAnchor = 3
            f.MarginLeft = f.MarginTop = f.MarginBottom = f.MarginRight = 4
            r = f.TextRange
            r.Text = text
            r.Font.Name = "Helvetica Neue"
            r.Font.Size = 27
            r.Font.Color.RGB = 0
            r.ParagraphFormat.Alignment = 1
            tb.Height = 83
            tb.Top = centre - 62
    pres.Save()
    return s.SlideIndex


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lens")
    ap.add_argument("deck")
    ap.add_argument("--after", type=int, required=True, help="insert after this slide")
    ap.add_argument("--png-dir", required=True)
    ap.add_argument("--name", required=True, help="file name prefix, e.g. SONY_FE35GM")
    ap.add_argument("--brand", help="line 1 above the legend, e.g. SONY")
    ap.add_argument("--model", help="line 2 above the legend, e.g. FE 35mm F1.4 GM")
    ap.add_argument("--zoom", type=int, default=1, help="CODE V zoom (focus) position: 1 = INF, 2 = next state ...")
    ap.add_argument("--tag", default="INF", help="file name tag, e.g. INF or Z2")
    ap.add_argument("--freqs", type=float, nargs="+", default=[10, 30, 80])
    ap.add_argument("--spectrum", nargs="+", default=["656.3:10", "587.6:27", "546.1:29", "486.1:23", "435.8:11"])
    ap.add_argument("--ref-wl", type=int, default=3)
    a = ap.parse_args()
    os.makedirs(a.png_dir, exist_ok=True)
    png = os.path.join(a.png_dir, "%s_MTF_vs_Field_%s.png" % (a.name, a.tag))
    table = os.path.join(a.png_dir, "%s_MTF_vs_Field_%s_数据.txt" % (a.name, a.tag))
    run_mtf_png(os.path.abspath(a.lens), a.freqs, [tuple(x.split(":")) for x in a.spectrum], a.ref_wl, png, table, zoom=a.zoom)
    print("MTF page is slide", add_page(a.deck, a.after, png, a.brand, a.model))


if __name__ == "__main__":
    main()
