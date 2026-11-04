"""The three structure-figure pages of a lens (Windows + PowerPoint + CODE V), following 结构图规范.md.

    python tools/layout_pages.py LENS.len DECK.pptx --after 21 --name "SIGMA 35mm F1.4 DG II | Art" \
        --svg 结构图/SIGMA_35DG2_layout.svg --template-slide 13 --groups 1-1 2-3 4-9 10-11 12-15 \
        --focus 2:left 4:left --prefix sigma

Pages (each is a copy of the one before, so the same-named objects morph; no blank page between them):
  A  structure  : lens name (56 pt) + SVG drawing + group brackets and names
  B  focus      : an elbow arrow under every focus group's bracket (direction given as group:left|right), group name moves into the corner
  C  material   : title removed, one arrow + glass name per lens element (all elbows on one row), aspheric elements brightened
The template slide (a finished structure page of another lens) only supplies the title and group-name text formats.
Positions are the ones fixed with the author on the Sony 35GM pages; all numbers are in 结构图规范.md.
"""
import argparse
import math
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from keynote_mcp import lens_layout as LL  # noqa: E402

WHITE = 0xFFFFFF
ROW_Y = 877.0          # bracket row (axis on y=540)


def aspheric_elements(lis, g):
    asp, cur = set(), None
    for ln in lis.splitlines():
        m = re.match(r"^\s*(\d+):\s", ln)
        if m:
            cur = int(m.group(1))
        elif ln.strip().startswith("ASP:") and cur:
            asp.add(cur)
    return [i for i, sf in enumerate(g["surf"]) if sf in asp or sf + 1 in asp]


def element_extent(g, tf, i):
    pts = [tf(*p) for p in LL.elem_poly(*g["elems"][i])]
    return pts


def group_extent(g, tf, a, b, first):
    pts = [p for i in range(a - 1, b) for p in element_extent(g, tf, i)]
    right = max(p[0] for p in pts)
    left = min(p[0] for p in pts)
    if first:                                    # first group: the bottom-left corner of its first element (rays crowd the front)
        low = max(p[1] for p in pts)
        corner = min(p[0] for p in pts if p[1] > low - 3)
        if right - corner > 80:                  # a thin crescent has no usable corner: keep its full width then
            left = corner
    else:
        left = min(p[0] for p in pts)
    return left, right


def pick_labels(g, tf):
    """label anchors, all elbows on one row; the arrows are as short as the page allows so the longest text stays on the slide"""
    for lead in (42.0, 38.0, 34.0, 30.0, 26.0, 24.0):
        L = LL.label_positions(g, tf, align=True, lead=lead)
        top = min(l["ytop"] - l["lead"] - l["extra"] - 12 - l["n"] * 1.06 * math.sin(math.radians(50)) - 16 for l in L)
        if top >= 6:
            break
    return L


def clone_text(slide, tmpl, text, name):
    """a new text box with the template shape's geometry and text format (no clipboard: it can be busy)"""
    tb = slide.Shapes.AddTextbox(1, tmpl.Left, tmpl.Top, tmpl.Width, tmpl.Height)
    tb.Name = name
    f, tf_ = tb.TextFrame, tmpl.TextFrame
    f.WordWrap, f.AutoSize, f.VerticalAnchor = tf_.WordWrap, tf_.AutoSize, tf_.VerticalAnchor
    f.MarginLeft, f.MarginRight, f.MarginTop, f.MarginBottom = tf_.MarginLeft, tf_.MarginRight, tf_.MarginTop, tf_.MarginBottom
    r, tr = f.TextRange, tf_.TextRange
    r.Text = text
    r.Font.Name, r.Font.Size, r.Font.Color.RGB = tr.Font.Name, tr.Font.Size, tr.Font.Color.RGB
    r.ParagraphFormat.Alignment = tr.ParagraphFormat.Alignment
    tb.Left, tb.Top, tb.Width, tb.Height = tmpl.Left, tmpl.Top, tmpl.Width, tmpl.Height
    return tb


def line(slide, x1, y1, x2, y2, name):
    ln = slide.Shapes.AddLine(x1, y1, x2, y2)
    ln.Name = name
    ln.Line.ForeColor.RGB = WHITE
    ln.Line.Weight = 1.5
    return ln


def build(a):
    import win32com.client as w
    lens = os.path.abspath(a.lens)
    run = LL.run_codev([lens])[0]
    g = LL.attach_glass(LL.parse_layout(run["dxf"]), run["lis"])
    g["ray_layers"] = sorted(l for l in g["rays"] if g["rays"][l])
    tf = LL.Frame(g)
    app = w.GetActiveObject("PowerPoint.Application")
    pres = next(p for p in app.Presentations if p.Name == os.path.basename(a.deck))
    tmpl = pres.Slides(a.template_slide)
    title_t = max((sh for sh in tmpl.Shapes if sh.HasTextFrame and sh.TextFrame.HasText and sh.TextFrame.TextRange.Font.Size >= 50),
                  key=lambda sh: sh.TextFrame.TextRange.Font.Size)
    glab_t = next(sh for sh in tmpl.Shapes if sh.HasTextFrame and sh.TextFrame.HasText and re.fullmatch(r"G\d", sh.TextFrame.TextRange.Text))
    blank = pres.Slides(a.after)
    # ---------- page A ----------
    A = blank.Duplicate()(1)
    pic = A.Shapes.AddPicture(os.path.abspath(a.svg), False, True, 0, 0, 1920, 1080)
    pic.Name = "!!%s layout" % a.prefix
    pic.ZOrder(1)
    clone_text(A, title_t, a.name, "!!%s title" % a.prefix)
    ranges = [tuple(int(x) for x in s.split("-")) for s in a.groups]
    gl = {}
    for k, (ea, eb) in enumerate(ranges, 1):
        left, right = group_extent(g, tf, ea, eb, k == 1)
        line(A, left, ROW_Y - 13, left, ROW_Y, "!!%s G%d tick L" % (a.prefix, k))
        line(A, left, ROW_Y, right, ROW_Y, "!!%s G%d bar" % (a.prefix, k))
        line(A, right, ROW_Y - 13, right, ROW_Y, "!!%s G%d tick R" % (a.prefix, k))
        lab = clone_text(A, glab_t, "G%d" % k, "!!%s G%d" % (a.prefix, k))
        lab.Left = (left + right) / 2 - lab.Width / 2
        gl[k] = (left, right, lab)
    # ---------- page B ----------
    B = A.Duplicate()(1)
    foc = {}
    for spec in a.focus:
        k, d = spec.split(":")
        foc[int(k)] = d
    for k, d in foc.items():
        left, right, _ = gl[k]
        cx = (left + right) / 2
        sgn = -1 if d == "left" else 1
        fb = B.Shapes.BuildFreeform(0, cx, ROW_Y)
        fb.AddNodes(0, 0, cx, ROW_Y + 75)
        fb.AddNodes(0, 0, cx + sgn * 120, ROW_Y + 75)
        ar = fb.ConvertToShape()
        ar.Name = "!!%s G%d focus arrow" % (a.prefix, k)
        ar.Fill.Visible = 0
        ar.Line.ForeColor.RGB = WHITE
        ar.Line.Weight = 3
        ar.Line.EndArrowheadStyle = 2
        ar.Line.EndArrowheadLength = 3
        ar.Line.EndArrowheadWidth = 3
        lab = B.Shapes("!!%s G%d" % (a.prefix, k))
        lab.Left = cx + sgn * 60 - lab.Width / 2            # centred on the arrow's horizontal part
        lab.Top = lab.Top - 18.5                         # Sony: 890 -> 871.5 (middle of bracket row and arrow row)
    # ---------- page C ----------
    C = B.Duplicate()(1)
    C.Shapes("!!%s title" % a.prefix).Delete()
    for k in foc:                                            # G label stays where page B put it
        pass
    L = pick_labels(g, tf)
    C_ = math.cos(math.radians(50))
    S_ = math.sin(math.radians(50))
    NX, NY = -S_, -C_
    K, GAP = 20.5, 16.0
    for l in L:
        i = l["i"] + 1
        x, ya = l["x"], l["ytop"]
        yb = ya - l["lead"] - l["extra"]
        D = (x + 22 * C_, yb - 22 * S_)
        fb = C.Shapes.BuildFreeform(0, x, ya)
        fb.AddNodes(0, 0, x, yb)
        fb.AddNodes(0, 0, D[0], D[1])
        ar = fb.ConvertToShape()
        ar.Name = "!!mat arrow E%02d" % i
        ar.Fill.Visible = 0
        ar.Line.ForeColor.RGB = WHITE
        ar.Line.Weight = 3
        ar.Line.BeginArrowheadStyle = 2
        ar.Line.BeginArrowheadLength = 3
        ar.Line.BeginArrowheadWidth = 3
        tb = C.Shapes.AddTextbox(1, 0, 0, 200, 42)
        tb.Name = "!!mat label E%02d %s" % (i, l["name"])
        f = tb.TextFrame
        f.WordWrap = 0
        f.AutoSize = 0
        f.VerticalAnchor = 3
        f.MarginLeft = f.MarginRight = f.MarginTop = f.MarginBottom = 0
        r = f.TextRange
        r.Text = l["name"]
        r.Font.Name = "Helvetica Neue"
        r.Font.Size = 29
        r.Font.Color.RGB = WHITE
        r.ParagraphFormat.Alignment = 1
        wd = r.BoundWidth + 1
        ht = 40.0
        tb.Width, tb.Height = wd, ht
        px, py = D[0] + GAP * C_ + K * NX, D[1] - GAP * S_ + K * NY
        cx, cy = px + (wd / 2) * C_, py - (wd / 2) * S_
        tb.Left, tb.Top = cx - wd / 2, cy - ht / 2
        tb.Rotation = 310
    for i in aspheric_elements(run["lis"], g):
        pts = [tf(*p) for p in LL.elem_poly(*g["elems"][i])]
        fb = C.Shapes.BuildFreeform(0, pts[0][0], pts[0][1])
        for x, y in pts[1:]:
            fb.AddNodes(0, 0, x, y)
        fb.AddNodes(0, 0, pts[0][0], pts[0][1])
        ov = fb.ConvertToShape()
        ov.Name = "!!asph overlay E%02d" % (i + 1)
        ov.Line.Visible = 0
        ov.Fill.Visible = -1
        ov.Fill.Solid()
        ov.Fill.ForeColor.RGB = WHITE
        ov.Fill.Transparency = 0.72
    pres.Save()
    return A.SlideIndex, B.SlideIndex, C.SlideIndex


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lens")
    ap.add_argument("deck")
    ap.add_argument("--after", type=int, required=True, help="a blank slide; the three pages go after it")
    ap.add_argument("--name", required=True)
    ap.add_argument("--svg", required=True)
    ap.add_argument("--template-slide", type=int, required=True)
    ap.add_argument("--groups", nargs="+", required=True, help="element ranges per group, e.g. 1-7 8-13 14-14")
    ap.add_argument("--focus", nargs="+", required=True, help="group:direction, e.g. 2:left")
    ap.add_argument("--prefix", default="lens")
    a = ap.parse_args()
    print("pages:", build(a))


if __name__ == "__main__":
    main()
