"""Lens layout drawings from CODE V (Windows): .len -> VIE (3 fields, real vignetted rays) -> DXF -> clean vector art.

One CODE V engine is started per call and used for every lens in it (start-up is ~10 s, each lens < 1 s).
Outputs per lens, in the same style as the Meike 85 MIX II structure page (#6C6C6C fill on #2C2C2C, thin light rays):
  <stem>_layout.svg            editable vector art, one path per element / ray (ids E01_FCD1, ray_F2_mid_1 ...)
  <stem>_layout_labeled.svg    + glass names with bent arrows (optional)
  <stem>_layout_mm.dxf         1:1 mm CAD drawing, layers LENS / CEMENT / RAYS_* / STOP / IMAGE_PLANE / AXIS / GLASS_TEXT
  <stem>_CODEV_<zoom>.dxf      the raw CODE V export (plot inches)
  .png / .pdf                  optional, need PyMuPDF (labels need matplotlib + Arial to become outlines)
Only the standard library is needed for SVG and DXF.
"""
import math
import os
import re
import shutil
import tempfile
import time

IN_MM = 25.4

try:  # pywin32 is only needed for the CODE V COM call
    import pythoncom
    import win32com.client
except Exception:  # pragma: no cover
    pythoncom = None
    win32com = None


class LayoutError(RuntimeError):
    pass


# ------------------------------------------------------------------ CODE V ---

def _com_engines():
    """PIDs of COM-launched CODE V engines (codevm.exe ... -com). The user's own GUI engine has no -com flag. Needs psutil."""
    try:
        import psutil
    except ImportError:
        return None
    out = {}
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            if (p.info["name"] or "").lower() == "codevm.exe" and " -com " in " ".join(p.info["cmdline"] or []) + " ":
                out[p.pid] = p
        except Exception:
            pass
    return out


class CodeV:
    """Short-lived CODE V COM engine working in an ASCII scratch folder (CODE V dislikes non-ASCII paths).
    StopCodeV() often leaves the codevm.exe engine running, so engines that appeared during this session are ended too."""

    def __init__(self):
        if win32com is None:
            raise LayoutError("缺少 pywin32：请运行 python -m pip install pywin32")
        base = tempfile.gettempdir()
        try:
            base.encode("ascii")
        except UnicodeEncodeError:
            base = r"C:\Users\Public"
        self.dir = tempfile.mkdtemp(prefix="kmcv_", dir=base)
        self.before = _com_engines()
        try:
            self.cv = win32com.client.Dispatch("CodeV.Command")
        except Exception as e:
            shutil.rmtree(self.dir, ignore_errors=True)
            raise LayoutError("启动不了 CODE V（COM 对象 CodeV.Command）：%s" % e)
        self.cv.SetStartingDirectory(self.dir)
        self.cv.StartCodeV()

    def cmd(self, text, check=True):
        r = str(self.cv.Command(text))
        if check and re.search(r"^\s*Error:", r, re.M):
            raise LayoutError("CODE V 命令失败：%s\n%s" % (text, r.strip()[:400]))
        return r

    def close(self):
        try:
            self.cv.StopCodeV()
        except Exception:
            pass
        self.cv = None
        after = _com_engines()
        if self.before is not None and after is not None:
            for pid, p in after.items():
                if pid not in self.before:
                    try:
                        p.terminate()
                    except Exception:
                        pass
        time.sleep(0.2)
        shutil.rmtree(self.dir, ignore_errors=True)


def _spec_numbers(lis, key):
    """numbers of a SPECIFICATION DATA row (e.g. YRI) that may continue on following lines"""
    vals, on = [], False
    for ln in lis.splitlines():
        t = ln.split()
        if not t:
            on = False
            continue
        if t[0] == key:
            on, t = True, t[1:]
        elif on and not re.match(r"^[-+0-9.]", t[0]):
            on = False
        if on:
            for x in t:
                try:
                    vals.append(float(x))
                except ValueError:
                    on = False
                    break
    return vals


def pick_fields(lis, fields=None):
    """-> (list of field numbers (1-based), heights or None). Default: on-axis, the field nearest 55 % of the maximum, the last."""
    heights = None
    for key in ("YRI", "YAN", "YOB", "YIM"):
        v = _spec_numbers(lis, key)
        n = len(_spec_numbers(lis, "XRI")) or len(v)
        if v and len(v) >= n >= 1:
            heights = [abs(x) for x in v[:n]]
            break
    n = len(heights) if heights else len(_spec_numbers(lis, "XRI"))
    if fields:
        sel = sorted(set(int(k) for k in fields))
    elif heights and n >= 3:
        top = max(heights)
        mid = min(range(1, n - 1), key=lambda i: abs(heights[i] - 0.55 * top))
        sel = sorted({1, mid + 1, n})
    elif n:
        sel = sorted({1, (n + 1) // 2, n})
    else:
        sel = [1]
    if len(sel) > 3:
        raise LayoutError("最多 3 个视场（给了 %s）" % sel)
    if n and any(k < 1 or k > n for k in sel):
        raise LayoutError("视场号超出范围 1..%d：%s" % (n, sel))
    return sel, n, heights


def glass_surfaces(lis):
    """[(surface, glass)] for every surface that starts a glass element, in order (from the LIS dump)"""
    out = []
    for ln in lis.splitlines():
        m = re.match(r"^\s*(\d+):\s+(\S+)\s+(\S+)\s+(\S+)", ln)
        if not m:
            continue
        g = m.group(4)
        if re.fullmatch(r"[+-]?\d+(\.\d*)?([eE][+-]?\d+)?", g) and not re.fullmatch(r"\d{6}", g):
            continue            # a number: CCY column, no glass on this surface
        if g in ("PIK", "OAL", "REFL", "TIR", "AIR"):
            continue
        out.append((int(m.group(1)), g))
    return out


def run_codev(lens_files, fields=None, zoom=1, ray_ext=12.0, log=None):
    """-> list of dict(dxf=<text>, lis=<text>, fields=[...]) for each .len, using one engine."""
    res = []
    eng = CodeV()
    try:
        for i, path in enumerate(lens_files):
            stem = "l%d" % i
            shutil.copyfile(path, os.path.join(eng.dir, stem + ".len"))
            eng.cmd("RES " + stem)
            lis = eng.cmd("LIS", check=False)
            sel, n, heights = pick_fields(lis, fields)
            if zoom == 1:
                eng.cmd("ZOO 1", check=False)          # only position 1 -> one DXF instead of one per focus state
            eng.cmd("GRA %s_v" % stem)
            eng.cmd("VIE")
            eng.cmd("ERI %g" % -abs(ray_ext))
            hide = [k for k in range(1, n + 1) if k not in sel]
            for k in hide:
                eng.cmd("FLD F%d N" % k)
            for k, col in zip(sel, ("RED", "GRE", "BLU")):
                eng.cmd("FLD F%d %s" % (k, col))
            eng.cmd("GO")
            eng.cmd("GRA t")
            eng.cmd("GCV DXF %s_v.plt" % stem)
            cands = [os.path.join(eng.dir, "%s_v.dxf" % stem), os.path.join(eng.dir, "%s_v_P%d.dxf" % (stem, zoom))]
            f = next((c for c in cands if os.path.isfile(c)), None)
            if not f:
                raise LayoutError("CODE V 没有生成 DXF（%s）" % path)
            res.append(dict(dxf=open(f, encoding="latin-1").read(), lis=lis, fields=sel, heights=heights, n_fields=n))
            if log:
                log("%s: fields %s" % (os.path.basename(path), sel))
    finally:
        eng.close()
    return res


# --------------------------------------------------------------------- DXF ---

def read_dxf(text):
    L = text.split("\n")
    ents, cur, d = [], None, {}
    i = 0
    while i < len(L) - 1:
        c, v = L[i].strip(), L[i + 1].strip()
        i += 2
        if c == "0":
            if cur in ("LINE", "TEXT"):
                ents.append(d)
            cur, d = v, {"type": v}
        else:
            d.setdefault(c, []).append(v)
    if cur in ("LINE", "TEXT"):
        ents.append(d)
    out = []
    for e in ents:
        if e["type"] == "LINE":
            out.append(("LINE", e["8"][0], (float(e["10"][0]), float(e["20"][0]), float(e["11"][0]), float(e["21"][0]))))
        else:
            out.append(("TEXT", e["8"][0], e["1"][0]))
    return out


def chain(lines, tol=1e-6):
    polys, cur = [], None
    for x1, y1, x2, y2 in lines:
        if cur and abs(cur[-1][0] - x1) < tol and abs(cur[-1][1] - y1) < tol:
            cur.append((x2, y2))
        else:
            cur = [(x1, y1), (x2, y2)]
            polys.append(cur)
    return polys


def parse_layout(dxf_text):
    """Raw CODE V VIE DXF -> elements, stop ticks, image plane, rays per field layer, axis row, scale (plot-in per lens-in)."""
    ents = read_dxf(dxf_text)
    P = chain([p for k, lay, p in ents if k == "LINE" and lay == "0"])

    def span(p):
        return abs(p[-1][1] - p[0][1])

    def V(p):
        return span(p) > 1e-4 and (max(a for a, b in p) - min(a for a, b in p)) < 0.6 * span(p)

    def H(p):
        return len(p) == 2 and abs(p[-1][1] - p[0][1]) < 1e-6

    def up(p):
        return p[::-1] if p[0][1] > p[-1][1] else p

    elems, ticks, rest, k = [], [], [], 0
    while k < len(P):
        if k + 3 < len(P) and V(P[k]) and V(P[k + 1]) and H(P[k + 2]) and H(P[k + 3]):
            elems.append((up(P[k]), up(P[k + 1])))     # front, back (edges are implied by the closed outline)
            k += 4
        elif V(P[k]):
            ticks.append(P[k])
            k += 1
        else:
            rest.append(P[k])
            k += 1
    if not elems or not rest:
        raise LayoutError("DXF 里认不出镜片轮廓（VIE 输出格式不对？）")
    axis_y = rest[0][0][1]
    half = max(abs(y - axis_y) for e in elems for y in (e[0][0][1], e[0][-1][1], e[1][0][1], e[1][-1][1]))
    ticks = [t for t in ticks if abs(t[0][1] - axis_y) <= half * 1.05]      # drops the page's scale-bar marks
    img = ticks.pop()
    rays = {}
    for lay in ("1", "2", "3"):
        rays[lay] = chain([p for kk, ll, p in ents if kk == "LINE" and ll == lay])
    texts = [t[2] for t in ents if t[0] == "TEXT"]
    try:
        scale = float(texts[texts.index("Scale:") + 1])
    except (ValueError, IndexError):
        raise LayoutError("DXF 里没有 Scale 标注，没法换算成毫米")
    return dict(elems=elems, stop=ticks, img=img, axis_y=axis_y, rays=rays, scale=scale)


def attach_glass(g, lis):
    gl = glass_surfaces(lis)
    if len(gl) != len(g["elems"]):
        raise LayoutError("镜片数对不上：LIS 里 %d 片玻璃，DXF 里 %d 个镜片（反射面 / 非序列系统暂不支持）" % (len(gl), len(g["elems"])))
    g["glass"] = [x[1].split("_")[0] for x in gl]
    g["surf"] = [x[0] for x in gl]
    g["cem"] = [gl[i + 1][0] == gl[i][0] + 1 for i in range(len(gl) - 1)]      # next glass on the very next surface
    g["plate"] = [len(f) == 2 and len(b) == 2 for f, b in g["elems"]]            # flat-flat = filter / cover glass: no label
    return g


# --------------------------------------------------------------------- SVG ---

def elem_poly(front, back):
    return list(front) + list(back)[::-1]


class Frame:
    """plot-inch -> canvas px. Same px/mm for every lens, image planes aligned at x_img, axis on row y0."""

    def __init__(self, g, px_per_mm=14.3, x_img=1832.0, y0=540.0):
        self.k = px_per_mm * IN_MM / g["scale"]
        self.ximg, self.ya, self.x_img, self.y0 = g["img"][0][0], g["axis_y"], x_img, y0

    def __call__(self, x, y):
        return (self.x_img + (x - self.ximg) * self.k, self.y0 - (y - self.ya) * self.k)


def _path(pts, tf, close=False):
    return " ".join(("M" if i == 0 else "L") + ("%.1f %.1f" % tf(*p)) for i, p in enumerate(pts)) + (" Z" if close else "")


def _text_outline(text, size=29.0):
    """glyph outlines (matplotlib + Arial) so PNG/PDF render without font substitution; None if unavailable"""
    try:
        from matplotlib.font_manager import FontProperties
        from matplotlib.textpath import TextPath
        fp = FontProperties(fname=os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "arial.ttf"))
        tp = TextPath((0, 0), text, size=size, prop=fp)
    except Exception:
        return None
    d = []
    for v, c in tp.iter_segments():
        if c == 1:
            d.append("M %.2f %.2f" % tuple(v))
        elif c == 2:
            d.append("L %.2f %.2f" % tuple(v))
        elif c == 3:
            d.append("Q %.2f %.2f %.2f %.2f" % tuple(v))
        elif c == 4:
            d.append("C %.2f %.2f %.2f %.2f %.2f %.2f" % tuple(v))
        elif c == 79:
            d.append("Z")
    return " ".join(d)


def _labels(g, tf, font, outline):
    """white text rotated -50 deg above each element, bent arrow down to its top edge; heights relaxed so nothing overlaps"""
    C, S, FS = math.cos(math.radians(50)), math.sin(math.radians(50)), 29.0
    L = []
    for i, (f, b) in enumerate(g["elems"]):
        if g["plate"][i]:
            continue
        poly = [tf(*p) for p in elem_poly(f, b)]
        top = min(poly, key=lambda q: q[1])
        near = [q[0] for q in poly if q[1] < top[1] + 3]
        L.append(dict(i=i, x=sum(near) / len(near), ytop=top[1] - 8, extra=0.0, name=g["glass"][i], n=len(g["glass"][i]) * FS * 0.62))

    def geom(l):
        yb = l["ytop"] - 34 - l["extra"]
        x = l["x"]
        lead = [(x, l["ytop"] - 14), (x, yb), (x + 16, yb - 16)]
        sx, sy = x + 22 - 7.7, yb - 12 - 6.4
        txt = [(sx + t * C, sy - t * S) for t in [k * l["n"] / 12 for k in range(13)]]
        lp = [(p[0] + (q[0] - p[0]) * k / 10, p[1] + (q[1] - p[1]) * k / 10) for p, q in zip(lead, lead[1:]) for k in range(11)]
        return lp, txt

    def hit(a, b, r):
        r2 = r * r
        return any((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 < r2 for p in a for q in b)

    G = [geom(l) for l in L]
    for _ in range(80):
        moved = False
        for i in range(len(L)):
            for j in range(len(L)):
                if i != j and (hit(G[i][1], G[j][0], 20) or (i < j and hit(G[i][1], G[j][1], 26))):
                    L[i]["extra"] += 34
                    G[i] = geom(L[i])
                    moved = True
        if not moved:
            break
    o = ['<g id="glass_labels" font-family="%s" font-size="29" fill="#FFFFFF">' % font]
    for l in L:
        x, ya = l["x"], l["ytop"]
        yb = ya - 34 - l["extra"]
        o.append('<g id="label_E%02d">' % (l["i"] + 1))
        o.append('<path d="M %.1f %.1f L %.1f %.1f L %.1f %.1f" fill="none" stroke="#FFFFFF" stroke-width="3"/>' % (x, ya - 14, x, yb, x + 16, yb - 16))
        o.append('<path d="M %.1f %.1f L %.1f %.1f L %.1f %.1f Z" fill="#FFFFFF"/>' % (x - 8, ya - 14, x + 8, ya - 14, x, ya))
        d = _text_outline(l["name"]) if outline else None
        if d:
            o.append('<path transform="translate(%.1f %.1f) rotate(-50) scale(1 -1)" d="%s"/>' % (x + 22, yb - 12, d))
        else:
            o.append('<text transform="translate(%.1f %.1f) rotate(-50)">%s</text>' % (x + 22, yb - 12, l["name"]))
        o.append("</g>")
    o.append("</g>")
    return "\n".join(o)


def make_svg(g, tf, labels=False, title="", outline=False):
    font = "Helvetica Neue, Helvetica, Arial, sans-serif"
    o = ['<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080">', "<title>%s</title>" % title,
         '<g id="lens_elements" fill="#6C6C6C" stroke="none">']
    for i, (f, b) in enumerate(g["elems"]):
        o.append('<path id="E%02d_%s" d="%s"/>' % (i + 1, g["glass"][i], _path(elem_poly(f, b), tf, True)))
    o.append("</g>")
    o.append('<g id="cemented_surfaces" fill="none" stroke="#FFFFFF" stroke-opacity="0.45" stroke-width="1.3" stroke-linejoin="round">')
    o += ['<path d="%s"/>' % _path(g["elems"][i][1], tf) for i, c in enumerate(g["cem"]) if c]
    o.append("</g>")
    x0 = min(p[0][0] for lay in g["rays"].values() for p in lay)
    a, e = tf(x0, g["axis_y"]), tf(g["img"][0][0], g["axis_y"])
    o.append('<g id="rays" fill="none" stroke="#E6E6E6" stroke-opacity="0.85" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round">')
    o.append('<path id="axis_ray" d="M %.1f %.1f L %.1f %.1f"/>' % (a[0], a[1], e[0], e[1]))
    for lay, nm in zip(g["ray_layers"], ("F1_on_axis", "F2_mid", "F3_full")):
        o += ['<path id="ray_%s_%d" d="%s"/>' % (nm, j + 1, _path(p, tf)) for j, p in enumerate(g["rays"][lay])]
    o.append("</g>")
    o.append('<g id="stop" fill="none" stroke="#FFFFFF" stroke-opacity="0.9" stroke-width="2">')
    o += ['<path d="%s"/>' % _path(t, tf) for t in g["stop"]]
    o.append("</g>")
    o.append('<g id="image_plane" fill="none" stroke="#E6E6E6" stroke-opacity="0.85" stroke-width="1.6"><path d="%s"/></g>' % _path(g["img"], tf))
    if labels:
        o.append(_labels(g, tf, font, outline))
    o.append("</svg>")
    return "\n".join(o)


# --------------------------------------------------------------------- DXF out ---

def make_dxf_mm(g):
    """1:1 mm drawing (x along the axis from the first surface vertex, y up) as an R12 ASCII DXF: opens in any CAD program."""
    k, ya = IN_MM / g["scale"], g["axis_y"]
    f0, xv = g["elems"][0][0], None
    for (x1, y1), (x2, y2) in zip(f0, f0[1:]):
        if (y1 - ya) * (y2 - ya) <= 0 and y1 != y2:
            xv = x1 + (x2 - x1) * (ya - y1) / (y2 - y1)
            break
    if xv is None:
        xv = f0[0][0]
    t = lambda p: ((p[0] - xv) * k, (p[1] - ya) * k)
    layers = (("LENS", 7), ("CEMENT", 8), ("RAYS_F1_ONAXIS", 1), ("RAYS_F2_MID", 3), ("RAYS_F3_FULL", 5),
              ("STOP", 2), ("IMAGE_PLANE", 6), ("AXIS", 8), ("GLASS_TEXT", 7))
    o = ["0", "SECTION", "2", "TABLES", "0", "TABLE", "2", "LAYER", "70", str(len(layers))]
    for n, c in layers:
        o += ["0", "LAYER", "2", n, "70", "0", "62", str(c), "6", "CONTINUOUS"]
    o += ["0", "ENDTAB", "0", "ENDSEC", "0", "SECTION", "2", "ENTITIES"]

    def poly(layer, pts, closed=False):
        o.extend(["0", "POLYLINE", "8", layer, "66", "1", "70", "1" if closed else "0"])
        for x, y in pts:
            o.extend(["0", "VERTEX", "8", layer, "10", "%.4f" % x, "20", "%.4f" % y, "30", "0"])
        o.extend(["0", "SEQEND", "8", layer])

    for i, (f, b) in enumerate(g["elems"]):
        pts = [t(p) for p in elem_poly(f, b)]
        poly("LENS", pts, True)
        top = max(pts, key=lambda q: q[1])
        o.extend(["0", "TEXT", "8", "GLASS_TEXT", "10", "%.4f" % top[0], "20", "%.4f" % (top[1] + 2), "30", "0",
                  "40", "1.8", "1", ("COVER " if g["plate"][i] else "") + g["glass"][i]])
    for i, c in enumerate(g["cem"]):
        if c:
            poly("CEMENT", [t(p) for p in g["elems"][i][1]])
    for lay, name in zip(g["ray_layers"], ("RAYS_F1_ONAXIS", "RAYS_F2_MID", "RAYS_F3_FULL")):
        for p in g["rays"][lay]:
            poly(name, [t(q) for q in p])
    x0 = min(p[0][0] for lay in g["rays"].values() for p in lay)
    poly("AXIS", [t((x0, ya)), t((g["img"][0][0], ya))])
    for s in g["stop"]:
        poly("STOP", [t(q) for q in s])
    poly("IMAGE_PLANE", [t(q) for q in g["img"]])
    o += ["0", "ENDSEC", "0", "EOF"]
    return "\n".join(o) + "\n", dict(ttl_mm=round((g["img"][0][0] - xv) * k, 2), half_image_mm=round(abs(g["img"][-1][1] - g["img"][0][1]) / 2 * k, 2))


def render(svg_text, png=None, pdf=None):
    """PNG (2x, transparent) / PDF from an SVG string with PyMuPDF; returns the formats written"""
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz
        except ImportError:
            return []
    doc = fitz.open(stream=svg_text.encode("utf-8"), filetype="svg")
    done = []
    if png:
        doc[0].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=True).save(png)
        done.append("png")
    if pdf:
        open(pdf, "wb").write(doc.convert_to_pdf())
        done.append("pdf")
    return done


# ------------------------------------------------------------------- driver ---

def export_layouts(lens_files, out_dir, fields=None, zoom=1, px_per_mm=14.3, labeled=True, formats=("svg", "dxf"),
                   names=None, log=None):
    t0 = time.time()
    lens_files = [os.path.normpath(p) for p in lens_files]
    for p in lens_files:
        if not os.path.isfile(p):
            raise LayoutError("找不到镜头文件：%s" % p)
    os.makedirs(out_dir, exist_ok=True)
    runs = run_codev(lens_files, fields=fields, zoom=zoom, log=log)
    t1 = time.time()
    results = []
    for i, (path, r) in enumerate(zip(lens_files, runs)):
        stem = (names[i] if names and i < len(names) else os.path.splitext(os.path.basename(path))[0])
        g = attach_glass(parse_layout(r["dxf"]), r["lis"])
        used = sorted(l for l in g["rays"] if g["rays"][l])
        if len(used) != len(r["fields"]):
            # an on-axis field loses its chief ray in VIE, so layers can only be missing when fields were hidden by CODE V itself
            raise LayoutError("DXF 里的视场层 %s 和选的视场 %s 对不上" % (used, r["fields"]))
        g["ray_layers"] = used
        tf = Frame(g, px_per_mm=px_per_mm)
        files = []

        def put(name, text, mode="w"):
            p = os.path.join(out_dir, name)
            with open(p, mode, encoding=None if "b" in mode else "utf-8", newline="") as f:
                f.write(text)
            files.append(p)
            return p

        info = {}
        raw = put("%s_CODEV_z%d.dxf" % (stem, zoom), r["dxf"])
        if "dxf" in formats:
            txt, info = make_dxf_mm(g)
            put("%s_layout_mm.dxf" % stem, txt)
        want_raster = [f for f in ("png", "pdf") if f in formats]
        for lab in ([False, True] if labeled else [False]):
            suf = "_labeled" if lab else ""
            svg = make_svg(g, tf, lab, stem + suf)
            if "svg" in formats:
                put("%s_layout%s.svg" % (stem, suf), svg)
            if want_raster:
                rs = make_svg(g, tf, lab, stem + suf, outline=True)
                png = os.path.join(out_dir, "%s_layout%s.png" % (stem, suf)) if "png" in want_raster else None
                pdf = os.path.join(out_dir, "%s_layout%s.pdf" % (stem, suf)) if "pdf" in want_raster else None
                for fmt, p in zip(render(rs, png, pdf), [x for x in (png, pdf) if x]):
                    files.append(p)
        results.append(dict(lens=os.path.basename(path), elements=len(g["elems"]), cemented_after_element=[i + 1 for i, c in enumerate(g["cem"]) if c],
                            fields=r["fields"], field_heights=[r["heights"][k - 1] for k in r["fields"]] if r["heights"] else None,
                            glass=g["glass"], vie_scale=g["scale"], files=files, **info))
    return dict(lenses=results, out_dir=out_dir, canvas="1920x1080, %.1f px/mm, image plane at x=1832, axis at y=540 (page centre line)" % px_per_mm,
                seconds=dict(codev=round(t1 - t0, 1), drawing=round(time.time() - t1, 1)))
