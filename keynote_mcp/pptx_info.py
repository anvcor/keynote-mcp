"""Read what matters for a Keynote remake out of a .pptx (stdlib only).

Per slide: texts, shape names, speaker notes ([图N] tag and [动画] lines),
the PowerPoint transition (which lives on the slide it transitions INTO),
how many build animations it has, and which fonts the file uses.
"""
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET

NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "p14": "http://schemas.microsoft.com/office/powerpoint/2010/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
P = "{%s}" % NS["p"]
A = "{%s}" % NS["a"]
R = "{%s}" % NS["r"]
P14 = "{%s}" % NS["p14"]
SPEED = {"slow": 1.0, "med": 0.75, "fast": 0.5}
ANIM_RE = re.compile(r"^\s*[\[【]\s*动画\s*[\]】]\s*(.*)$")
FIG_RE = re.compile(r"[\[【]\s*图\s*([0-9]+[a-zA-Z]?)")


def _rels(z, part):
    d, f = posixpath.split(part)
    rp = posixpath.join(d, "_rels", f + ".rels")
    out = {}
    if rp not in z.namelist():
        return out
    root = ET.fromstring(z.read(rp))
    for rel in root.findall("rel:Relationship", NS):
        target = rel.get("Target")
        if rel.get("TargetMode") == "External":
            continue
        out[rel.get("Id")] = (rel.get("Type", "").rsplit("/", 1)[-1], posixpath.normpath(posixpath.join(d, target)))
    return out


def _texts(el):
    out = []
    for para in el.iter(A + "p"):
        t = "".join(x.text or "" for x in para.iter(A + "t"))
        if t.strip():
            out.append(t.strip())
    return out


def _transition(sld):
    """Return the transition on this slide (= the one used to arrive at it)."""
    tr = None
    for ac in sld.findall("mc:AlternateContent", NS):
        ch = ac.find("mc:Choice", NS)
        if ch is not None and ch.find(P + "transition") is not None:
            tr = ch.find(P + "transition")
            break
    if tr is None:
        tr = sld.find(P + "transition")
    if tr is None:
        return None
    kids = [k for k in tr if not k.tag.endswith("}sndAc") and not k.tag.endswith("}extLst")]
    effect = kids[0].tag.split("}", 1)[-1] if kids else "none"
    option = kids[0].get("option") if kids else None
    if tr.get(P14 + "dur"):
        dur = int(tr.get(P14 + "dur")) / 1000.0
    elif effect == "morph":
        dur = 2.0  # PowerPoint's default morph duration
    else:
        dur = SPEED.get(tr.get("spd", "fast"), 0.5)
    t = {"effect": effect, "duration": dur}
    if option:
        t["option"] = option
    if tr.get("advTm") is not None:
        t["advance_after"] = int(tr.get("advTm")) / 1000.0
    if tr.get("advClick") == "0":
        t["on_click"] = False
    return t


def _builds(sld):
    timing = sld.find(P + "timing")
    if timing is None:
        return None
    counts = {}
    for ctn in timing.iter(P + "cTn"):
        cls = ctn.get("presetClass")
        if cls:
            counts[cls] = counts.get(cls, 0) + 1
    return counts or None


def _shapes(sld):
    out = []
    tree = sld.find(P + "cSld/" + P + "spTree")
    if tree is None:
        return out
    kinds = {"sp": "shape", "pic": "picture", "graphicFrame": "table/chart", "grpSp": "group", "cxnSp": "line"}
    for el in tree:
        kind = kinds.get(el.tag.split("}", 1)[-1])
        if not kind:
            continue
        nv = el.find(".//" + P + "cNvPr")
        name = nv.get("name") if nv is not None else ""
        txt = " ".join(_texts(el))[:40]
        item = {"type": kind, "name": name}
        if txt:
            item["text"] = txt
        out.append(item)
    return out


def _notes(z, notes_part):
    root = ET.fromstring(z.read(notes_part))
    for sp in root.iter(P + "sp"):
        ph = sp.find(".//" + P + "nvPr/" + P + "ph")
        if ph is not None and ph.get("type") == "body":
            return "\n".join(_texts(sp))
    return ""


def _fonts(z):
    fonts = set()
    for n in z.namelist():
        if not (n.startswith("ppt/") and n.endswith(".xml")):
            continue
        if not any(k in n for k in ("slides/", "slideLayouts/", "slideMasters/", "theme/")):
            continue
        for m in re.finditer(rb'<a:(?:latin|ea|cs) typeface="([^"]+)"', z.read(n)):
            f = m.group(1).decode("utf-8", "replace")
            if f and not f.startswith("+"):
                fonts.add(f)
    return sorted(fonts)


def read(path):
    z = zipfile.ZipFile(path)
    pres = ET.fromstring(z.read("ppt/presentation.xml"))
    prels = _rels(z, "ppt/presentation.xml")
    sz = pres.find(P + "sldSz")
    size = {"cx": int(sz.get("cx")), "cy": int(sz.get("cy"))} if sz is not None else None
    if size:
        size["inches"] = [round(size["cx"] / 914400, 3), round(size["cy"] / 914400, 3)]
        size["points"] = [round(size["cx"] / 12700), round(size["cy"] / 12700)]
    slides = []
    lst = pres.find(P + "sldIdLst")
    for i, sid in enumerate(lst if lst is not None else [], 1):
        rid = sid.get(R + "id")
        part = prels.get(rid, (None, None))[1]
        if not part:
            continue
        sld = ET.fromstring(z.read(part))
        srels = _rels(z, part)
        notes = ""
        for kind, target in srels.values():
            if kind == "notesSlide" and target in z.namelist():
                notes = _notes(z, target)
        texts = _texts(sld.find(P + "cSld"))
        info = {
            "slide": i,
            "hidden": sld.get("show") == "0",
            "title": texts[0][:60] if texts else None,
            "shapes": _shapes(sld),
            "notes": notes,
            "fig": (FIG_RE.search(notes[:40]).group(1) if FIG_RE.search(notes[:40] or "") else None),
            "animation_notes": [m.group(1).strip() for m in (ANIM_RE.match(l) for l in notes.splitlines()) if m],
            "transition_in": _transition(sld),
            "builds": _builds(sld),
        }
        slides.append(info)
    return {"slide_size": size, "slides": slides, "fonts": _fonts(z)}


def repair_for_keynote(src, dst):
    """Write a copy Keynote can import, or return None when nothing needs fixing.

    python-pptx (what Claude typically uses on Windows) adds a notes master when
    speaker notes are written, but does not list it in presentation.xml's
    <p:notesMasterIdLst>. PowerPoint tolerates that; Keynote rejects the whole
    file with "The file format is invalid".
    """
    z = zipfile.ZipFile(src)
    pres = z.read("ppt/presentation.xml").decode("utf-8")
    if "notesMasterIdLst" in pres:
        return None
    rels = _rels(z, "ppt/presentation.xml")
    rid = next((k for k, (kind, _t) in rels.items() if kind == "notesMaster"), None)
    if not rid or "</p:sldMasterIdLst>" not in pres:
        return None
    fixed = pres.replace("</p:sldMasterIdLst>",
                         '</p:sldMasterIdLst><p:notesMasterIdLst><p:notesMasterId r:id="%s"/></p:notesMasterIdLst>' % rid, 1)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as out:
        for item in z.infolist():
            data = z.read(item.filename)
            if item.filename == "ppt/presentation.xml":
                data = fixed.encode("utf-8")
            out.writestr(item, data)
    return ["presentation.xml 缺少 notesMasterIdLst（python-pptx 写备注时的常见问题），已在临时副本里补上"]
