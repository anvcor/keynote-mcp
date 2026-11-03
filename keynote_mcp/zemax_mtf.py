"""Read curves out of a Zemax OpticStudio "FFT MTF vs. Field" plot image.

The author traces these plots by hand in Keynote (the Zemax export sits under the
chart as a base image). This does the tracing automatically:

1. Calibrate the plot box from the grid: the dark left/bottom axes and the
   right-most / top-most light-grey grid lines (Zemax always draws them).
2. For each Zemax curve colour, keep pixels whose colour "direction" from white
   matches (so the lighter, anti-aliased dotted lines match too).
3. Long connected pieces are the solid Tangential curve, small pieces are the
   dots of the dotted Sagittal curve.
4. Convert to data: field = 0 … x_max (mm), MTF = 0 … 1.

Needs Pillow (installed in the project's .venv together with keynote-parser).
"""
import glob
import math
import os
import sys

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Zemax default plot colours for Freq 1…6 (measured on an OpticStudio 2024 R2 export)
ZEMAX_COLORS = [
    (0, 0, 255),      # 1  blue
    (0, 230, 0),      # 2  green
    (230, 0, 0),      # 3  red
    (180, 185, 25),   # 4  olive / yellow
    (230, 0, 230),    # 5  magenta
    (40, 225, 225),   # 6  cyan
]


def _pil():
    try:
        from PIL import Image
    except ImportError:
        for sp in glob.glob(os.path.join(_HERE, ".venv", "lib", "python3*", "site-packages")):
            if sp not in sys.path:
                sys.path.append(sp)
        from PIL import Image
    return Image


def _clusters(values, gap=3):
    out, cur = [], []
    for v in values:
        if cur and v - cur[-1] > gap:
            out.append(cur)
            cur = []
        cur.append(v)
    if cur:
        out.append(cur)
    return [sum(c) / len(c) for c in out]


def calibrate(px, W, H):
    """Return (x0, x1, y0, y1): pixel x of field 0 and field max, pixel y of MTF 0 and MTF 1."""
    def light(p):
        r, g, b = p
        return 195 <= r <= 245 and abs(r - g) < 10 and abs(g - b) < 10

    def dark(p):
        r, g, b = p
        return r < 100 and g < 100 and b < 100 and max(p) - min(p) < 30

    band_y = range(int(H * 0.03), int(H * 0.65))
    band_x = range(int(W * 0.1), int(W * 0.98))
    need_c = len(band_y) * 0.35
    need_r = len(band_x) * 0.35
    dark_cols = [x for x in range(W) if sum(1 for y in band_y if dark(px[x, y])) > need_c]
    dark_rows = [y for y in range(int(H * 0.2), int(H * 0.8)) if sum(1 for x in band_x if dark(px[x, y])) > need_r]
    grid_cols = [x for x in range(W) if sum(1 for y in band_y if light(px[x, y])) > need_c]
    grid_rows = [y for y in range(H) if sum(1 for x in band_x if light(px[x, y])) > need_r]
    if not dark_cols or not dark_rows or not grid_cols or not grid_rows:
        raise ValueError("没认出坐标轴和网格（这不像 Zemax 的 MTF vs Field 图？）")
    x0 = _clusters(dark_cols)[0]
    y0 = _clusters(dark_rows)[0]
    gc = [c for c in _clusters(grid_cols) if c > x0 + 5]
    gr = [r for r in _clusters(grid_rows) if r < y0 - 5]
    if not gc or not gr:
        raise ValueError("没找到右侧和顶部的网格线")
    return x0, max(gc), y0, min(gr)


def _matches(p, ref, min_strength=45, min_cos=0.975):
    v = (255 - p[0], 255 - p[1], 255 - p[2])
    d = (255 - ref[0], 255 - ref[1], 255 - ref[2])
    nv = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    if nv < min_strength:
        return False
    nd = math.sqrt(d[0] ** 2 + d[1] ** 2 + d[2] ** 2)
    return (v[0] * d[0] + v[1] * d[1] + v[2] * d[2]) / (nv * nd) > min_cos


def _components(mask):
    """8-connected components of a set of (x, y) pixels."""
    seen, comps = set(), []
    for start in mask:
        if start in seen:
            continue
        stack, comp = [start], []
        seen.add(start)
        while stack:
            x, y = stack.pop()
            comp.append((x, y))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    q = (x + dx, y + dy)
                    if q in mask and q not in seen:
                        seen.add(q)
                        stack.append(q)
        comps.append(comp)
    return comps


def _column_series(pixels):
    cols = {}
    for x, y in pixels:
        cols.setdefault(x, []).append(y)
    return sorted((x, sorted(ys)[len(ys) // 2]) for x, ys in cols.items())


def trace(path, x_max=21.6, freqs=None, solid_min_width=None):
    """Return {"calibration": …, "series": {freq_index: {"T": [(field, mtf)…], "S": […]}}}."""
    Image = _pil()
    im = Image.open(path).convert("RGB")
    W, H = im.size
    px = im.load()
    x0, x1, y0, y1 = calibrate(px, W, H)
    if solid_min_width is None:
        solid_min_width = max(25, int((x1 - x0) * 0.03))
    freqs = freqs or list(range(1, len(ZEMAX_COLORS) + 1))
    xa, xb, ya, yb = int(x0) + 3, int(x1) + 1, int(y1) - 2, int(y0) - 2
    out = {}
    for fi in freqs:
        ref = ZEMAX_COLORS[fi - 1]
        mask = {(x, y) for x in range(xa, xb) for y in range(ya, yb) if _matches(px[x, y], ref)}
        if not mask:
            continue
        comps = [c for c in _components(mask) if len(c) >= 3]
        widths = [max(p[0] for p in c) - min(p[0] for p in c) + 1 for c in comps]
        small = sorted(w for w in widths if w < solid_min_width)
        dot_w = small[len(small) // 2] if small else 6
        frag_min = max(12, 2.2 * dot_w)  # pieces of the solid line cut short where other curves cross it
        solid, dots = [], []
        for c, w in zip(comps, widths):
            if w >= solid_min_width or w > frag_min:
                solid.extend(c)
            else:
                dots.append(c)
        t = _column_series(solid)
        s = sorted((sum(p[0] for p in c) / len(c), sum(p[1] for p in c) / len(c)) for c in dots)

        def to_data(pts):
            return [(round((x - x0) / (x1 - x0) * x_max, 4), round((y0 - y) / (y0 - y1), 4)) for x, y in pts]
        out[fi] = {"T": to_data(t), "S": to_data(s)}
    return {"calibration": {"x0": x0, "x1": x1, "y0": y0, "y1": y1, "image": [W, H], "x_max": x_max},
            "series": out}


def resample(points, x_max, n=None, step=None):
    """Average duplicates, fill gaps by linear interpolation, return points on an even field grid."""
    if not points:
        return []
    pts = sorted(points)
    lo, hi = pts[0][0], pts[-1][0]
    step = step or (x_max / 200.0)
    grid, out = [], []
    x = max(0.0, lo)
    while x <= min(hi, x_max) + 1e-9:
        grid.append(x)
        x += step
    j = 0
    for gx in grid:
        while j < len(pts) - 2 and pts[j + 1][0] < gx:
            j += 1
        (xa, ya), (xb, yb) = pts[j], pts[min(j + 1, len(pts) - 1)]
        y = ya if xb == xa else ya + (yb - ya) * (gx - xa) / (xb - xa)
        out.append((round(gx, 4), round(y, 4)))
    return out


def clean(points, window=None, max_dev=None):
    """Drop stray pixels/dots: keep points within max_dev (MTF units) of a running median."""
    pts = sorted(points)
    if len(pts) < 5:
        return pts
    span = pts[-1][0] - pts[0][0]
    window = window or max(span / 40.0, 1e-6)
    max_dev = max_dev or 0.03
    out = []
    j0 = 0
    for i, (x, y) in enumerate(pts):
        while pts[j0][0] < x - window:
            j0 += 1
        j1 = i
        while j1 + 1 < len(pts) and pts[j1 + 1][0] <= x + window:
            j1 += 1
        ys = sorted(p[1] for p in pts[j0:j1 + 1])
        med = ys[len(ys) // 2]
        if abs(y - med) <= max_dev:
            out.append((x, y))
    return out


def smooth(points, k=2):
    """Light moving-average smoothing (k points each side) to remove pixel jitter."""
    out = []
    for i in range(len(points)):
        a, b = max(0, i - k), min(len(points), i + k + 1)
        out.append((points[i][0], sum(p[1] for p in points[a:b]) / (b - a)))
    return out


def _rdp(pts, eps):
    if len(pts) < 3:
        return list(pts)
    (x1, y1), (x2, y2) = pts[0], pts[-1]
    dx, dy = x2 - x1, y2 - y1
    norm = math.hypot(dx, dy) or 1e-9
    dmax, idx = 0, 0
    for i in range(1, len(pts) - 1):
        d = abs(dy * pts[i][0] - dx * pts[i][1] + x2 * y1 - y2 * x1) / norm
        if d > dmax:
            dmax, idx = d, i
    if dmax > eps:
        return _rdp(pts[:idx + 1], eps)[:-1] + _rdp(pts[idx:], eps)
    return [pts[0], pts[-1]]


def bezier_nodes(pts, tol=1.0, max_nodes=24):
    """Fit a smooth cubic Bezier through slide-space points (y down).

    Returns [(node, in_ctrl, out_ctrl, type)] with Catmull-Rom tangents; type 1 = sharp end, 3 = smooth.
    """
    eps = tol
    while True:
        keys = _rdp(pts, eps)
        if len(keys) <= max_nodes:
            break
        eps *= 1.5
    # keep at least a few nodes so long gentle curves stay smooth
    if len(keys) < 5:
        step = max(1, len(pts) // 6)
        keys = pts[::step] + ([pts[-1]] if pts[-1] != pts[::step][-1] else [])
    n = len(keys)
    out = []
    for i, p in enumerate(keys):
        prev = keys[i - 1] if i > 0 else p
        nxt = keys[i + 1] if i < n - 1 else p
        tx, ty = (nxt[0] - prev[0]) / 6.0, (nxt[1] - prev[1]) / 6.0
        if i == 0:
            tx, ty = (nxt[0] - p[0]) / 3.0, (nxt[1] - p[1]) / 3.0
            out.append((p, p, (p[0] + tx, p[1] + ty), 1))
        elif i == n - 1:
            tx, ty = (p[0] - prev[0]) / 3.0, (p[1] - prev[1]) / 3.0
            out.append((p, (p[0] - tx, p[1] - ty), p, 1))
        else:
            out.append((p, (p[0] - tx, p[1] - ty), (p[0] + tx, p[1] + ty), 3))
    return out


def bezier_error(nodes, pts, samples=16):
    """Max vertical distance between the Bezier and the dense points (slide units)."""
    curve = []
    for (p0, _i0, o0, _t0), (p3, i3, _o3, _t3) in zip(nodes, nodes[1:]):
        for s in range(samples + 1):
            t = s / samples
            u = 1 - t
            curve.append((u ** 3 * p0[0] + 3 * u * u * t * o0[0] + 3 * u * t * t * i3[0] + t ** 3 * p3[0],
                          u ** 3 * p0[1] + 3 * u * u * t * o0[1] + 3 * u * t * t * i3[1] + t ** 3 * p3[1]))
    curve.sort()
    err = 0
    j = 0
    for x, y in pts:
        while j < len(curve) - 2 and curve[j + 1][0] < x:
            j += 1
        (xa, ya), (xb, yb) = curve[j], curve[j + 1]
        yc = ya if xb == xa else ya + (yb - ya) * (x - xa) / (xb - xa)
        err = max(err, abs(yc - y))
    return err
