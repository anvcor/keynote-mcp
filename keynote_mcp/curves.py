"""Redraw the MTF curves of a chart slide from a Zemax "FFT MTF vs. Field" export.

How the author's chart slides are built (measured in 85MIX2 and the template):
- the bottom-most object is the Zemax plot image, scaled so its axes sit on the
  Keynote chart's axes (the "底图");
- each curve is a pen-tool shape (editable Bezier) with stroke colour = frequency
  (#56c1ff 10, #88fa4e 20, #ee220c 40, #fff056 80, #ff95ca 160, #73fdea 320 lp/mm)
  and pattern = Tangential (solid) / Sagittal (dotted); most carry a Line Draw build.

So: calibrate the old base image -> the chart's data box on the slide; trace the
new Zemax image; map its data into that box; fit Bezier nodes; overwrite the path of
the matching existing curve shapes (style, identity and builds stay untouched).
Auto-traced curves were checked against the author's hand tracing on 85MIX2 slide 43:
0.7–1.8 pt mean difference for the solid curves on a 1920-pt-wide slide.
"""
import math
import os
import random
import tempfile
import zipfile

from . import builds as _b
from . import zemax_mtf as Z

PALETTE = [  # chart colour of Zemax Freq 1…6 in the author's decks
    ("#56c1ff", 10), ("#88fa4e", 20), ("#ee220c", 40), ("#fff056", 80), ("#ff95ca", 160), ("#73fdea", 320),
]


def _hex(c):
    return "#%02x%02x%02x" % (round(c.r * 255), round(c.g * 255), round(c.b * 255))


def _nearest_palette(hexc):
    r, g, b = int(hexc[1:3], 16), int(hexc[3:5], 16), int(hexc[5:7], 16)
    best = None
    for i, (p, _lp) in enumerate(PALETTE):
        pr, pg, pb = int(p[1:3], 16), int(p[3:5], 16), int(p[5:7], 16)
        d = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2
        if best is None or d < best[0]:
            best = (d, i)
    return best[1] if best and best[0] < 60 ** 2 else None


def _stroke(objs, shape):
    st = objs.get(shape.style.identifier)
    seen = 0
    while st is not None and seen < 8:
        seen += 1
        for cand in (st, getattr(st, "super", None)):
            if cand is not None and hasattr(cand, "shape_properties") \
                    and "stroke" in cand.shape_properties.DESCRIPTOR.fields_by_name \
                    and cand.shape_properties.HasField("stroke"):
                return cand.shape_properties.stroke
        base = st.super.super if hasattr(st.super, "super") else st.super
        par = base.parent if hasattr(base, "parent") and base.HasField("parent") else None
        st = objs.get(par.identifier) if par is not None else None
    return None


def _load(path):
    IWAArchiveSegment, IWAFile, import_version = _b._import()
    z = zipfile.ZipFile(path)
    files, segs = {}, {}
    for n in z.namelist():
        if n.endswith(".iwa"):
            f = IWAFile.from_buffer(z.read(n), n)
            files[n] = f
            for ch in f.chunks:
                for seg in ch.archives:
                    segs[seg.header.identifier] = (n, seg)
    return z, files, segs


def _slide(segs, slide_no):
    show = next(seg.objects[0] for n, seg in segs.values() if type(seg.objects[0]).__name__ == "ShowArchive")
    if not 1 <= slide_no <= len(show.slideTree.slides):
        raise ValueError("页号超出范围：%d" % slide_no)
    node = segs[show.slideTree.slides[slide_no - 1].identifier][1].objects[0]
    return segs[node.slide.identifier]


def chart_box(z, segs, slide, x_max):
    """Data box of the chart on the slide, from its bottom-most image (the Zemax base image)."""
    objs = {k: v[1].objects[0] for k, v in segs.items()}
    pm = next(o for o in objs.values() if type(o).__name__ == "PackageMetadata")
    datas = {d.identifier: d for d in pm.datas}
    for ref in slide.drawables_z_order:
        o = objs.get(ref.identifier)
        if type(o).__name__ != "ImageArchive":
            continue
        d = datas.get(o.data.identifier)
        if d is None:
            continue
        stem = os.path.splitext(d.file_name)[0]
        cand = [n for n in z.namelist() if n.startswith("Data/") and os.path.basename(n).startswith(stem)]
        if not cand:
            continue
        tmp = tempfile.NamedTemporaryFile(suffix=os.path.splitext(cand[0])[1], delete=False)
        tmp.write(z.read(cand[0]))
        tmp.close()
        try:
            Image = Z._pil()
            im = Image.open(tmp.name).convert("RGB")
            x0, x1, y0, y1 = Z.calibrate(im.load(), *im.size)
        except ValueError:
            continue
        finally:
            os.unlink(tmp.name)
        g = o.super.geometry
        s = g.size.width / im.size[0]
        return {"X0": g.position.x + x0 * s, "X1": g.position.x + x1 * s,
                "Y0": g.position.y + y0 * s, "Y1": g.position.y + y1 * s,
                "base_image": d.file_name, "base_item": [r.identifier for r in slide.drawables_z_order].index(ref.identifier) + 1}
    raise ValueError("这一页没有可以用来对齐的 Zemax 底图（最底层应该是一张 MTF vs Field 截图）")


def prepare_series(trace, x_max):
    """Clean, fill the on-axis gap of the dotted curve from the solid one, resample and smooth."""
    out = {}
    for fi, d in trace["series"].items():
        t = Z.clean(d["T"])
        s = Z.clean(d["S"])
        if t and s:
            t0 = Z.resample(t, x_max)
            s_lo, s_hi = s[0][0], s[-1][0]
            pre = [p for p in t0 if p[0] < s_lo - 1e-6]
            post = [p for p in t0 if p[0] > s_hi + 1e-6]
            if pre and abs(pre[-1][1] - s[0][1]) < 0.04:
                s = pre + s
            if post and abs(post[0][1] - s[-1][1]) < 0.04:
                s = s + post
        res = {}
        for k, v in (("T", t), ("S", s)):
            if not v:
                continue
            v = _extend_ends(sorted(v), x_max)
            res[k] = Z.smooth(Z.resample(v, x_max), k=5)
        out[int(fi)] = res
    return out


def _extend_ends(pts, x_max, reach=1.0):
    """Extend a curve that stops just short of field 0 or the edge (hidden under another curve) along its slope."""
    def slope(seg):
        (xa, ya), (xb, yb) = seg[0], seg[-1]
        return (yb - ya) / (xb - xa) if xb != xa else 0.0
    if pts and 0 < pts[0][0] <= reach:
        seg = [p for p in pts if p[0] <= pts[0][0] + 0.6]
        pts = [(0.0, pts[0][1] - slope(seg) * pts[0][0])] + pts
    if pts and x_max - reach <= pts[-1][0] < x_max:
        seg = [p for p in pts if p[0] >= pts[-1][0] - 0.6]
        pts = pts + [(x_max, pts[-1][1] + slope(seg) * (x_max - pts[-1][0]))]
    return pts


def write_curves(path, slide_no, zemax_image, x_max=21.6, tol=1.5, backup_dir=None):
    IWAArchiveSegment, IWAFile, import_version = _b._import()
    z, files, segs = _load(path)
    slide_file, slide_seg = _slide(segs, slide_no)
    slide = slide_seg.objects[0]
    objs = {k: v[1].objects[0] for k, v in segs.items()}
    box = chart_box(z, segs, slide, x_max)
    tr = Z.trace(zemax_image, x_max=x_max)
    series = prepare_series(tr, x_max)

    def to_slide(f, m):
        return (box["X0"] + f / x_max * (box["X1"] - box["X0"]), box["Y0"] - m * (box["Y0"] - box["Y1"]))

    report, used = [], set()
    zids = [r.identifier for r in slide.drawables_z_order]
    for k, did in enumerate(zids, 1):
        o = objs.get(did)
        if type(o).__name__ != "ShapeInfoArchive" or not o.super.pathsource.HasField("editable_bezier_path_source"):
            continue
        st = _stroke(objs, o.super)
        if st is None or not st.HasField("color"):
            continue
        fi = _nearest_palette(_hex(st.color))
        if fi is None:
            continue
        kind = "T" if (st.HasField("pattern") and st.pattern.type == 1) else "S"
        data = series.get(fi + 1, {}).get(kind)
        if not data:
            report.append({"item": k, "lp": PALETTE[fi][1], "kind": kind, "skipped": "Zemax 图里没有这条曲线"})
            continue
        pts = [to_slide(f, m) for f, m in data]
        nodes = Z.bezier_nodes(pts, tol=tol, max_nodes=18)
        err = Z.bezier_error(nodes, pts)
        allp = [p for n in nodes for p in n[:3]]
        minx, miny = min(p[0] for p in allp), min(p[1] for p in allp)
        maxx, maxy = max(p[0] for p in allp), max(p[1] for p in allp)
        w, h = max(maxx - minx, 1.0), max(maxy - miny, 1.0)
        sh = o.super
        g = sh.super.geometry
        g.position.x, g.position.y = minx, miny
        g.size.width, g.size.height = w, h
        g.angle = 0
        ps = sh.pathsource
        ps.horizontalFlip = False
        ps.verticalFlip = False
        eb = ps.editable_bezier_path_source
        eb.naturalSize.width, eb.naturalSize.height = w, h
        del eb.subpaths[:]
        sp = eb.subpaths.add()
        sp.closed = False
        for (p, pin, pout, typ) in nodes:
            nd = sp.nodes.add()
            nd.nodePoint.x, nd.nodePoint.y = p[0] - minx, p[1] - miny
            nd.inControlPoint.x, nd.inControlPoint.y = pin[0] - minx, pin[1] - miny
            nd.outControlPoint.x, nd.outControlPoint.y = pout[0] - minx, pout[1] - miny
            nd.type = typ
        used.add((fi + 1, kind))
        report.append({"item": k, "lp": PALETTE[fi][1], "kind": "子午" if kind == "T" else "弧矢",
                       "nodes": len(nodes), "fit_error_pt": round(err, 2)})
    missing = [{"lp": PALETTE[fi - 1][1] if fi <= len(PALETTE) else fi, "kind": kind}
               for fi, d in series.items() for kind in d if (fi, kind) not in used]
    if not any("nodes" in r for r in report):
        raise ValueError("这一页没有找到可以替换的曲线（按颜色对应 10/20/40/80/160/320 线对）")

    backup = None
    if backup_dir:
        import shutil
        import time
        os.makedirs(backup_dir, exist_ok=True)
        backup = os.path.join(backup_dir, "%s-描曲线前-%s.key" % (
            os.path.splitext(os.path.basename(path))[0], time.strftime("%Y%m%d-%H%M%S")))
        shutil.copy2(path, backup)
        import glob
        for f in sorted(glob.glob(os.path.join(backup_dir, "*.key")), key=os.path.getmtime)[:-10]:
            try:
                os.remove(f)
            except OSError:
                pass
    tmp = path + ".curves-tmp"
    with zipfile.ZipFile(tmp, "w") as zo:
        for info in z.infolist():
            data = files[info.filename].to_buffer() if info.filename == slide_file else z.read(info.filename)
            zo.writestr(info, data)
    z.close()
    os.replace(tmp, path)

    # where the new Zemax image should sit so that its axes land on the same chart box
    W, H = tr["calibration"]["image"]
    c = tr["calibration"]
    sc = (box["X1"] - box["X0"]) / (c["x1"] - c["x0"])
    placement = {"x": box["X0"] - c["x0"] * sc, "y": box["Y1"] - c["y1"] * sc, "width": W * sc}
    return {"curves": report, "not_on_slide": missing, "box": {k: round(v, 1) for k, v in box.items() if isinstance(v, float)},
            "base_item": box["base_item"], "new_base_placement": {k: round(v, 1) for k, v in placement.items()},
            "backup": backup, "series": series}


def mtf_table(series, x_max, fields=None):
    """MTF at chosen fields for each frequency, e.g. centre / 0.5 / 0.7 / edge."""
    fields = fields or [0.0, round(0.5 * x_max, 2), round(0.7 * x_max, 2), x_max]
    rows = []
    for fi in sorted(series):
        lp = PALETTE[fi - 1][1] if fi <= len(PALETTE) else fi
        for kind, name in (("T", "子午"), ("S", "弧矢")):
            pts = series[fi].get(kind)
            if not pts:
                continue
            vals = {}
            for f in fields:
                near = min(pts, key=lambda p: abs(p[0] - f))
                vals[str(f)] = round(near[1], 3) if abs(near[0] - f) <= x_max / 50 else None
            rows.append({"lp": lp, "kind": name, "mtf": vals})
    return {"fields_mm": fields, "rows": rows}


def read_image(zemax_image, x_max=21.6, fields=None, csv_path=None):
    tr = Z.trace(zemax_image, x_max=x_max)
    series = prepare_series(tr, x_max)
    out = {"calibration": {k: round(v, 1) if isinstance(v, float) else v for k, v in tr["calibration"].items()},
           "frequencies_note": "按 Zemax 默认颜色对应 Freq 1–6；作者常用的是 10/20/40/80/160/320 线对，若 Zemax 里设的频率不同，以图例为准。",
           "table": mtf_table(series, x_max, fields)}
    if csv_path:
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("freq_index,lp_mm,kind,field_mm,mtf\n")
            for fi in sorted(series):
                lp = PALETTE[fi - 1][1] if fi <= len(PALETTE) else ""
                for kind in ("T", "S"):
                    for fld, m in series[fi].get(kind, []):
                        f.write("%d,%s,%s,%.4f,%.4f\n" % (fi, lp, kind, fld, m))
        out["csv"] = csv_path
    return out
