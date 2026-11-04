"""Surface datasheet page of a lens (Windows + PowerPoint + CODE V), copied from the 85MIX2 page "Prototype Datasheet".

    python tools/datasheet_page.py LENS.len DECK.pptx --after 32 --title "SONY FE 35mm F1.4 GM" \
        --svg 结构图/SONY_FE35GM_layout_ppt.svg [--template-deck 85MIX2 --template-slide 24]

The template slide (table of surfaces on the left, lens drawing + aspheric coefficient table on the right) is copied into the deck;
the surface table, the aspheric table, the titles and the drawing are replaced. Data come from CODE V (position 1 = INF):
radius / thickness / glass from LIS, semi-diameters from the VIE drawing (the clear height CODE V draws), K and A..F (A4..A14) of ASP surfaces.
The dummy first surface is dropped and surfaces are numbered from 1 (the STOP counts, the object does not); the last row is the image plane.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from keynote_mcp import lens_layout as LL  # noqa: E402

NUM = r"[-+]?\d*\.?\d+(?:[EeDd][-+]?\d+)?"


def parse_lis(lis):
    """-> surfaces [dict(label, r, t, glass, asp)] in file order (OBJ skipped), image row last"""
    S, cur = [], None
    for ln in lis.splitlines():
        m = re.match(r"^\s*>?\s*(\d+|STO|IMG|OBJ):\s+(\S+)\s+(\S+)(?:\s+(\S+))?", ln)
        if m:
            lab, r, t, g = m.group(1), m.group(2), m.group(3), m.group(4)
            if lab == "OBJ":
                cur = None
                continue
            glass = None
            if g and not re.fullmatch(NUM, g) and g not in ("PIK", "OAL", "REFL", "TIR", "AIR"):
                glass = g.split("_")[0]
            cur = dict(label=lab, r=None if r.upper() == "INFINITY" else float(r), t=None if t.upper() == "INFINITY" else float(t), glass=glass, asp=None)
            S.append(cur)
            continue
        if cur is not None and ln.strip().startswith("ASP:"):
            cur["asp"] = {}
        elif cur is not None and cur["asp"] is not None and ":" in ln:
            for k, v in re.findall(r"\b([KABCDEFGHJ])\s*:\s*(" + NUM + ")", ln):
                cur["asp"][k] = float(v.replace("D", "E").replace("d", "e"))
        elif cur is not None and cur["asp"] is not None and not ln.strip():
            cur = cur if False else cur
        if ln.startswith("SPECIFICATION DATA"):
            break
    return S


def half_image(lis):
    m = re.search(r"^\s+YRI\s+(.*?)^\s+WTF", lis, re.S | re.M)
    return max(float(x) for x in re.findall(NUM, m.group(1))) if m else None


def fmt_r(v):
    return "Infinity" if v is None else "{:,.4f}".format(v)


def fmt_c(v):
    if v == 0:
        return "0"
    return "{:.4f}".format(v) if 1e-3 <= abs(v) < 1e5 else "{:.4E}".format(v)


def build_rows(g, lis):
    S = parse_lis(lis)
    # semi-diameters from the drawing: element i has its front on surface surf[i], back on surf[i]+1
    k = LL.IN_MM / g["scale"]
    semi = {}
    for i, (f, b) in enumerate(g["elems"]):
        s0 = g["surf"][i]
        semi[s0] = abs(f[-1][1] - f[0][1]) / 2 * k
        semi[s0 + 1] = abs(b[-1][1] - b[0][1]) / 2 * k
    rows, asph, n = [], [], 0
    for s in S:
        if s["label"] == "IMG":
            rows.append(("Image", "", "Infinity", "-", "", "{:.2f}".format(half_image(lis))))
            continue
        if s["label"] not in ("STO",) and s["r"] is None and not s["glass"] and (s["t"] or 0) == 0 and int(s["label"]) == 1:
            continue                                   # dummy first surface
        n += 1
        num = int(s["label"]) if s["label"] != "STO" else None
        typ = "STOP" if s["label"] == "STO" else ("Even Asphere" if s["asp"] is not None else "Standard")
        sd = "-" if s["label"] == "STO" else "{:.2f}".format(semi.get(num, 0.0))
        rows.append((typ, str(n), fmt_r(s["r"]), "{:.2f}".format(s["t"] or 0.0), s["glass"] or "", sd))
        if s["asp"] is not None:
            a = s["asp"]
            asph.append([str(n), fmt_c(a.get("K", 0.0))] + [fmt_c(a.get(c, 0.0)) for c in "ABCDEF"])
    return rows, asph


def fill(cell, text, size=None):
    tr = cell.Shape.TextFrame.TextRange
    tr.Text = text
    if size:
        tr.Font.Size = size


def build(a):
    import win32com.client as w
    lens = os.path.abspath(a.lens)
    run = LL.run_codev([lens])[0]
    g = LL.attach_glass(LL.parse_layout(run["dxf"]), run["lis"])
    rows, asph = build_rows(g, run["lis"])
    app = w.GetActiveObject("PowerPoint.Application")
    pres = next(p for p in app.Presentations if p.Name == os.path.basename(a.deck))
    tdeck = next(p for p in app.Presentations if p.Name.startswith(a.template_deck))
    tdeck.Slides(a.template_slide).Copy()
    s = pres.Slides.Paste(a.after + 1)(1)
    t1 = next(sh for sh in s.Shapes if sh.HasTable and sh.Table.Columns.Count == 6)
    t2 = next(sh for sh in s.Shapes if sh.HasTable and sh.Table.Columns.Count == 8)
    grp = next(sh for sh in s.Shapes if sh.Type == 6)
    titles = [sh for sh in s.Shapes if sh.HasTextFrame and sh.TextFrame.HasText and sh.TextFrame.TextRange.Font.Size >= 45]
    titles.sort(key=lambda sh: sh.Top)
    # ----- surface table -----
    t = t1.Table
    need = len(rows) + 1
    while t.Rows.Count < need:
        t.Rows.Add()
    while t.Rows.Count > need:
        t.Rows(t.Rows.Count).Delete()
    avail = 990.0 - 30.0
    rh = avail / len(rows)
    size = 24 if rh >= 33 else (22 if rh >= 30 else 20)
    hdr = ["Surface Type", "No.", "Radius", "Thickness", "Material", "Semi-Dia"]
    for j, h in enumerate(hdr, 1):
        fill(t.Cell(1, j), h)
    for i, r in enumerate(rows, 2):
        for j, v in enumerate(r, 1):
            c = t.Cell(i, j)
            fill(c, v, size if j != 1 or len(v) < 10 else size - 2)
            c.Shape.TextFrame.MarginTop = c.Shape.TextFrame.MarginBottom = 1
    t.Rows(1).Height = 30
    for i in range(2, need + 1):
        t.Rows(i).Height = rh
    t1.Top = 10
    # ----- aspheric table -----
    t = t2.Table
    need2 = len(asph) + 1
    while t.Rows.Count < need2:
        t.Rows.Add()
    while t.Rows.Count > need2:
        t.Rows(t.Rows.Count).Delete()
    fill(t.Cell(1, 1), "ASPH\nSurface")
    for j, h in enumerate(["Conic", "A4", "A6", "A8", "A10", "A12", "A14"], 2):
        fill(t.Cell(1, j), h)
    bsize = 22 if len(asph) <= 3 else (16 if len(asph) <= 5 else 14)
    nsize = 34 if len(asph) <= 3 else (22 if len(asph) <= 5 else 17)       # surface number column
    for i, r in enumerate(asph, 2):
        for j, v in enumerate(r, 1):
            c = t.Cell(i, j)
            fill(c, v, nsize if j == 1 else bsize)
            c.Shape.TextFrame.MarginTop = c.Shape.TextFrame.MarginBottom = 1
    top2, bottom = 700.0, 1000.0
    t.Rows(1).Height = 56
    body = (bottom - top2 - 56) / len(asph)
    for i in range(2, need2 + 1):
        t.Rows(i).Height = body
    t2.Top = top2
    # ----- drawing -----
    grp.Delete()
    pic = s.Shapes.AddPicture(os.path.abspath(a.svg), False, True, 0, 0)
    k = a.draw_scale
    pic.LockAspectRatio = 0
    pic.Width, pic.Height = pic.Width * k, pic.Height * k
    pic.Left = 1445 - pic.Width / 2
    pic.Top = 455 - pic.Height / 2
    pic.Name = "!!%s datasheet layout" % a.prefix
    # ----- titles -----
    for sh, txt in zip(titles, (a.title, "Surface Datasheet")):
        sh.TextFrame.WordWrap = 0
        sh.TextFrame.TextRange.Text = txt
        sh.Left = 1445 - sh.Width / 2
    pres.Save()
    return s.SlideIndex, len(rows), len(asph)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lens")
    ap.add_argument("deck")
    ap.add_argument("--after", type=int, required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--svg", required=True, help="the cropped *_layout_ppt.svg")
    ap.add_argument("--template-deck", default="85MIX2")
    ap.add_argument("--template-slide", type=int, default=24)
    ap.add_argument("--draw-scale", type=float, default=0.47)
    ap.add_argument("--prefix", default="lens")
    a = ap.parse_args()
    print("datasheet slide / rows / aspheric surfaces:", build(a))


if __name__ == "__main__":
    main()
