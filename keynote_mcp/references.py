"""Catalog of finished decks kept in references/ as reference material.

For every deck: slide count and, per slide, a guessed kind (chapter card, MTF chart,
longitudinal aberration, frequency response, product intro, blank …), its main texts,
image / curve / build counts, plus a contact sheet built from the thumbnails Keynote
stores inside the .key (so nothing has to be opened in Keynote).
"""
import glob
import json
import os
import re
import time
import zipfile

from . import builds as _b

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF_DIR = os.path.join(HERE, "references")
CATALOG = os.path.join(REF_DIR, "catalog.json")
NUMERAL = re.compile(r"^[一二三四五六七八九十]+$")


def _texts(objs, slide):
    out = []
    for r in slide.drawables_z_order:
        o = objs.get(r.identifier)
        if type(o).__name__ == "ShapeInfoArchive" and o.HasField("owned_storage"):
            st = objs.get(o.owned_storage.identifier)
            if st is not None:
                t = "".join(st.text).strip().replace("\n", " ")
                if t:
                    out.append(t)
    return out


def _kind(texts, n_items, n_images, n_curves, has_round_badge):
    joined = " | ".join(texts)
    if n_items == 0:
        return "空白页"
    if len(texts) == 2 and n_items <= 3 and (NUMERAL.match(texts[0]) or NUMERAL.match(texts[1])):
        return "章节卡"
    if "MTF vs Field" in joined:
        return "FFT MTF vs Field 图"
    if "Longitudinal Aberration" in joined:
        return "纵向像差图"
    if "Frequency Response" in joined:
        return "频响图"
    if "MTF" in joined and n_curves:
        return "MTF 图"
    if (has_round_badge or re.search(r"Since \d{4}|¥\s?\d", joined)) and n_images:
        return "产品介绍页"
    if n_curves >= 5 and n_images <= 1:
        return "曲线/结构图"
    if n_images and not texts:
        return "图片页"
    if n_images:
        return "图文页"
    return "文字页"


def scan_deck(path, sheet_path=None):
    IWAArchiveSegment, IWAFile, import_version = _b._import()
    z = zipfile.ZipFile(path)
    objs = {}
    for n in z.namelist():
        if n.endswith(".iwa") and ("Slide" in n or n in ("Index/Document.iwa", "Index/Metadata.iwa")):
            try:
                f = IWAFile.from_buffer(z.read(n), n)
            except Exception:
                continue
            for ch in f.chunks:
                for seg in ch.archives:
                    objs[seg.header.identifier] = seg.objects[0]
    show = next(o for o in objs.values() if type(o).__name__ == "ShowArchive")
    pm = next(o for o in objs.values() if type(o).__name__ == "PackageMetadata")
    datas = {d.identifier: d.file_name for d in pm.datas}
    names = z.namelist()
    slides, thumbs = [], []
    for i, ref in enumerate(show.slideTree.slides, 1):
        node = objs.get(ref.identifier)
        s = objs.get(node.slide.identifier) if node is not None else None
        if s is None:
            continue
        kinds = [type(objs.get(r.identifier)).__name__ for r in s.drawables_z_order]
        n_images = kinds.count("ImageArchive") + kinds.count("MovieArchive")
        n_curves = 0
        round_badge = False
        for r in s.drawables_z_order:
            o = objs.get(r.identifier)
            if type(o).__name__ == "ShapeInfoArchive":
                ps = o.super.pathsource
                if ps.HasField("editable_bezier_path_source"):
                    n_curves += 1
                if ps.HasField("scalar_path_source") and ps.scalar_path_source.type == 1 \
                        and o.super.super.geometry.size.height < 90:
                    round_badge = True
        texts = _texts(objs, s)
        a = s.transition.attributes.animationAttributes
        slides.append({
            "slide": i,
            "kind": _kind(texts, len(kinds), n_images, n_curves, round_badge),
            "texts": [t[:40] for t in texts if len(t) > 1][:6],
            "images": n_images, "curves": n_curves, "builds": len(s.builds),
            "transition": {"apple:magic-move-implied-motion-path": "神奇移动"}.get(a.effect, a.effect or "无"),
            "skipped": bool(getattr(node, "isSkipped", False)),
        })
        th = None
        for t in getattr(node, "thumbnails", []):
            fn = datas.get(t.identifier)
            if fn:
                stem = os.path.splitext(fn)[0]
                cand = [x for x in names if x.startswith("Data/") and os.path.basename(x).startswith(stem)]
                if cand:
                    th = cand[0]
                    break
        thumbs.append(th)
    if sheet_path:
        _contact_sheet(z, thumbs, sheet_path)
    by_kind = {}
    for s in slides:
        by_kind.setdefault(s["kind"], []).append(s["slide"])
    return {"slides": slides, "by_kind": by_kind}


def _contact_sheet(z, thumbs, out, cols=8, tw=240, th=135):
    from . import zemax_mtf as _z
    Image = _z._pil()
    from PIL import ImageDraw
    rows = (len(thumbs) + cols - 1) // cols
    pad, label = 6, 18
    sheet = Image.new("RGB", (cols * (tw + pad) + pad, rows * (th + label + pad) + pad), (20, 20, 20))
    dr = ImageDraw.Draw(sheet)
    for i, name in enumerate(thumbs):
        x = pad + (i % cols) * (tw + pad)
        y = pad + (i // cols) * (th + label + pad)
        dr.text((x + 2, y + 2), str(i + 1), fill=(230, 230, 230))
        if name:
            try:
                import io
                im = Image.open(io.BytesIO(z.read(name))).convert("RGB")
                im.thumbnail((tw, th))
                sheet.paste(im, (x, y + label))
            except Exception:
                pass
        else:
            dr.rectangle([x, y + label, x + tw, y + label + th], outline=(80, 80, 80))
    sheet.save(out, quality=85)


def build(ref_dir=REF_DIR):
    decks = sorted(glob.glob(os.path.join(ref_dir, "**", "*.key"), recursive=True))
    out = []
    for p in decks:
        folder = os.path.dirname(p)
        sheet = os.path.join(folder, "目录图.jpg")
        info = scan_deck(p, sheet)
        out.append({
            "project": os.path.basename(folder),
            "file": os.path.relpath(p, ref_dir),
            "mb": round(os.path.getsize(p) / 1e6),
            "modified": time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(p))),
            "slides": len(info["slides"]),
            "contact_sheet": os.path.relpath(sheet, ref_dir),
            "by_kind": info["by_kind"],
            "slide_list": info["slides"],
        })
    cat = {"built": time.strftime("%Y-%m-%d %H:%M"), "decks": out}
    with open(os.path.join(ref_dir, "catalog.json"), "w", encoding="utf-8") as f:
        json.dump(cat, f, ensure_ascii=False, indent=1)
    return cat


def load(rebuild=False):
    decks = glob.glob(os.path.join(REF_DIR, "**", "*.key"), recursive=True)
    if not decks:
        return {"decks": [], "note": "references/ 里还没有 .key 成品"}
    stale = not os.path.exists(CATALOG) or any(os.path.getmtime(d) > os.path.getmtime(CATALOG) for d in decks)
    if rebuild or stale:
        return build()
    with open(CATALOG, encoding="utf-8") as f:
        return json.load(f)
