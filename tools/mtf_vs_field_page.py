"""MTF vs Field page from a CODE V lens file, in the style of the author's 老蛙 AF35MC page (Windows + PowerPoint + CODE V).

    python tools/mtf_vs_field_page.py LENS.len DECK.pptx --after 16 --ref-deck 老蛙AF35MC动画.pptx --ref-slide 60 \
        --brand SONY --name "FE 35mm F1.4 GM" --fno 1.47 --freqs 10 30 80 \
        --spectrum 656.3:10 587.6:27 546.1:29 486.1:23 435.8:11 --ref-wl 3

1. CODE V (COM) runs the macro mtfvsfld.seq at the first zoom position with 21 field points under the given spectrum
   (the saved spectrum "VOD" = the default above); `SPE name` only opens the spectrum option, so wavelengths and weights
   are set with WL / WTW / REF.
2. The reference page (a slide of an author deck: dark background, grid, axis labels, orange MTF 50 % line, legend squares)
   is inserted into DECK after slide --after; its old curves, base screenshot and unused legend entries are removed.
3. Smooth freeform curves are drawn from the data: tangential solid, sagittal dotted, 2 pt, in the legend colours.
Axis mapping was fitted to the 老蛙 page grid: x = 317 + 62.4 * field(mm), y = 808.9 - 634.1 * MTF.
"""
import argparse
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from keynote_mcp import lens_layout as LL  # noqa: E402  (CodeV engine wrapper)

COLORS = ["#56C1FF", "#88FA4E", "#EE220D", "#FFF056"]       # legend colours of the reference page (10 / 20 / 40 / 80)
BY_FREQ = {10: 0, 20: 1, 30: 1, 40: 2, 60: 2, 80: 3}         # colour index by frequency (30 takes the 20 lp/mm green)


def color_for(freqs, k):
    i = BY_FREQ.get(int(freqs[k]), k)
    return COLORS[min(i, len(COLORS) - 1)]
LEGEND_X = [259.0, 494.0, 739.0, 984.0]                       # legend square left edges
MACRO = r"E:\CODEV2026\macro\mtfvsfld.seq"


def run_mtf(lens, freqs, spectrum, ref_wl, npts):
    eng = LL.CodeV()
    try:
        shutil.copyfile(lens, os.path.join(eng.dir, "s.len"))
        eng.cmd("RES s")
        eng.cmd("WL " + " ".join(w for w, _ in spectrum))
        eng.cmd("WTW " + " ".join(wt for _, wt in spectrum))
        eng.cmd("REF %d" % ref_wl)
        out = eng.cmd('in "%s" %s 1 %d' % (MACRO, " ".join(str(f) for f in freqs), npts), check=False)
    finally:
        eng.close()
    data, cur = {}, None
    for ln in out.splitlines():
        m = re.search(r"Frequency =>\s+([\d.]+)", ln)
        if m:
            cur = float(m.group(1))
            data[cur] = []
            continue
        m = re.match(r"^\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*$", ln)
        if m and cur is not None:
            data[cur].append([float(m.group(1)), float(m.group(2)), float(m.group(3))])
    if not data or any(not v for v in data.values()):
        raise SystemExit("CODE V 没有算出 MTF 表：\n" + out[-600:])
    return data


def rgb(h):
    return int(h[1:3], 16) | (int(h[3:5], 16) << 8) | (int(h[5:7], 16) << 16)


def bezier(pts):
    """Catmull-Rom through the points, as cubic Bezier segments (c1, c2, end)"""
    n, segs = len(pts), []
    for i in range(n - 1):
        p0, p1, p2 = pts[i - 1] if i else pts[i], pts[i], pts[i + 1]
        p3 = pts[i + 2] if i + 2 < n else pts[i + 1]
        segs.append(((p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6),
                     (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6), p2))
    return segs


def build(deck, after, ref_deck, ref_slide, data, brand, name, fno, freqs):
    import win32com.client as w
    app = w.GetActiveObject("PowerPoint.Application")
    pres = next(p for p in app.Presentations if p.Name == os.path.basename(deck))
    ref_path = os.path.abspath(ref_deck)
    pres.Slides.InsertFromFile(ref_path, after, ref_slide, ref_slide)
    s = pres.Slides(after + 1)
    prev = pres.Slides(after)                       # same dark background / layout as the neighbouring page
    s.CustomLayout = prev.CustomLayout
    s.FollowMasterBackground = prev.FollowMasterBackground
    if not prev.FollowMasterBackground:
        s.Background.Fill.Solid()
        s.Background.Fill.ForeColor.RGB = prev.Background.Fill.ForeColor.RGB
    legend_cols = [rgb(c) for c in COLORS]
    # remove the old curves (freeforms in a legend colour), the base screenshot, and legend entries we do not need
    drop = []
    for sh in s.Shapes:
        if sh.Type == 13 and sh.Width > 1000:
            drop.append(sh)
        elif sh.Type == 5 and sh.Line.ForeColor.RGB in legend_cols:
            drop.append(sh)
    squares = sorted([sh for sh in s.Shapes if sh.Name == "Square"], key=lambda q: q.Left)
    labels = sorted([sh for sh in s.Shapes if sh.HasTextFrame and sh.TextFrame.HasText and sh.TextFrame.TextRange.Text.endswith("lp/mm")],
                    key=lambda q: q.Left)
    keep = min(len(freqs), len(squares))
    for k in range(len(squares)):
        if k >= keep:
            drop += [squares[k], labels[k]]
    for sh in drop:
        sh.Delete()
    squares, labels = squares[:keep], labels[:keep]
    # legend: first entries keep their colours, text gets the new frequencies
    for k, f in enumerate(freqs[:keep]):
        labels[k].TextFrame.TextRange.Text = "%glp/mm" % f
        squares[k].Fill.ForeColor.RGB = rgb(color_for(freqs, k))
    for sh in s.Shapes:
        if sh.HasTextFrame and sh.TextFrame.HasText:
            t = sh.TextFrame.TextRange.Text
            if t.startswith("@INF"):
                sh.TextFrame.TextRange.Text = "@INF F%g" % fno
            elif t.startswith("FE ") or "mm F" in t and "lp" not in t and "@" not in t:
                sh.TextFrame.TextRange.Text = name
            elif t in ("SONY", "MEIKE", "LAOWA", "老蛙", "VILTROX"):
                sh.TextFrame.TextRange.Text = brand
    X = lambda f: 317.0 + 62.4 * f
    Y = lambda m: 808.9 - 634.1 * m
    for k, f in enumerate(freqs):
        for kind, col, dash in (("sagittal", 1, 11), ("tangential", 2, 1)):
            pts = [(X(r[0]), Y(r[col])) for r in data[f]]
            fb = s.Shapes.BuildFreeform(1, pts[0][0], pts[0][1])
            for c1, c2, p in bezier(pts):
                fb.AddNodes(1, 1, c1[0], c1[1], c2[0], c2[1], p[0], p[1])
            sh = fb.ConvertToShape()
            sh.Name = "!!MTF %glp %s" % (f, kind)
            sh.Fill.Visible = 0
            sh.Line.ForeColor.RGB = rgb(color_for(freqs, k))
            sh.Line.Weight = 2
            sh.Line.DashStyle = dash
    pres.Save()
    return after + 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lens")
    ap.add_argument("deck")
    ap.add_argument("--after", type=int, required=True, help="insert after this slide")
    ap.add_argument("--ref-deck", required=True)
    ap.add_argument("--ref-slide", type=int, default=60)
    ap.add_argument("--brand", default="SONY")
    ap.add_argument("--name", required=True)
    ap.add_argument("--fno", type=float, required=True)
    ap.add_argument("--freqs", type=float, nargs="+", default=[10, 30, 80])
    ap.add_argument("--spectrum", nargs="+", default=["656.3:10", "587.6:27", "546.1:29", "486.1:23", "435.8:11"])
    ap.add_argument("--ref-wl", type=int, default=3)
    ap.add_argument("--points", type=int, default=21)
    a = ap.parse_args()
    spec = [tuple(x.split(":")) for x in a.spectrum]
    data = run_mtf(os.path.abspath(a.lens), a.freqs, spec, a.ref_wl, a.points)
    n = build(a.deck, a.after, a.ref_deck, a.ref_slide, data, a.brand, a.name, a.fno, a.freqs)
    print("MTF page is slide", n)


if __name__ == "__main__":
    main()
