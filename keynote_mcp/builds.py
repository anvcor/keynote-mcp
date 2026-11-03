"""Add object build animations to a closed .key file.

Keynote's AppleScript dictionary has no builds, so we write them straight into the
document's IWA archives with keynote-parser (installed into this project's .venv).
The objects mirror what the user's own decks contain (measured 2026-11-03):

    line_draw  -> "com.apple.iWork.Keynote.LineDrawForLine"  (curves, axes, rays)
    fade_move  -> "apple:fade and move character"            (legend text and swatches)

For each build we add a KN.BuildArchive and a KN.BuildChunkArchive to the slide's
component, list them on the slide (builds / buildChunks) and in its header, and
register the build's UUID in Metadata.iwa — the same places Keynote uses.
"""
import copy
import glob
import os
import random
import shutil
import sys
import time
import warnings
import zipfile

EFFECTS = {
    "line_draw": "com.apple.iWork.Keynote.LineDrawForLine",
    "fade_move": "apple:fade and move character",
}
EFFECT_ALIASES = {"线条描绘": "line_draw", "line": "line_draw", "linedraw": "line_draw",
                  "淡入移动": "fade_move", "逐字淡入移动": "fade_move", "fade": "fade_move", "text": "fade_move"}
# Keynote's "Start Build" as stored in KN.BuildChunkArchive (measured in the user's decks)
START = {"click": (False, True), "with": (True, False), "after": (True, True)}
START_ALIASES = {"on_click": "click", "点按": "click", "with_previous": "with", "同时": "with",
                 "after_previous": "after", "之后": "after"}

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class BuildsUnavailable(RuntimeError):
    pass


def _import():
    try:
        import keynote_parser  # noqa: F401
    except ImportError:
        for sp in glob.glob(os.path.join(_HERE, ".venv", "lib", "python3*", "site-packages")):
            if sp not in sys.path:
                sys.path.append(sp)
        try:
            import keynote_parser  # noqa: F401
        except ImportError:
            raise BuildsUnavailable(
                "加构件动画需要 keynote-parser。在终端运行：\n"
                "/usr/bin/python3 -m venv %s/.venv && %s/.venv/bin/pip install keynote-parser" % (_HERE, _HERE))
    warnings.simplefilter("ignore")
    from keynote_parser.codec import IWAArchiveSegment, IWAFile, import_version
    return IWAArchiveSegment, IWAFile, import_version


def available():
    try:
        _import()
        return True
    except BuildsUnavailable:
        return False


def _enum(msg, field, name):
    return msg.DESCRIPTOR.fields_by_name[field].enum_type.values_by_name[name].number


def normalize(spec):
    eff = str(spec.get("effect", "line_draw")).strip()
    eff = EFFECT_ALIASES.get(eff.lower(), EFFECT_ALIASES.get(eff, eff.lower()))
    if eff not in EFFECTS:
        raise ValueError("不支持的动画：%s。可用：line_draw（线条描绘）、fade_move（逐字淡入移动）" % spec.get("effect"))
    start = spec.get("start")
    if start is not None:
        start = START_ALIASES.get(str(start), str(start))
        if start not in START:
            raise ValueError("start 只能是 click（点按）、with（与上一个同时）、after（在上一个之后）")
    dur = float(spec.get("duration", 2.0 if eff == "line_draw" else 1.5))
    if not 0.1 <= dur <= 60:
        raise ValueError("duration 要在 0.1 到 60 秒之间")
    delay = float(spec.get("delay", 0))
    return {"slide": int(spec["slide"]), "item": int(spec["item"]), "effect": eff, "start": start,
            "duration": dur, "delay": delay}


def inject(path, specs, backup_dir=None, expected_items=None):
    """Add builds to the .key at `path` (Keynote must not have it open). Returns a report."""
    IWAArchiveSegment, IWAFile, import_version = _import()
    msgs = import_version()[0]
    BuildArchive, BuildChunkArchive = msgs[8], msgs[153]

    z = zipfile.ZipFile(path)
    files, segs = {}, {}
    for n in z.namelist():
        if n.endswith(".iwa"):
            f = IWAFile.from_buffer(z.read(n), n)
            files[n] = f
            for ch in f.chunks:
                for seg in ch.archives:
                    segs[seg.header.identifier] = (n, seg)
    pm_name, pm_seg = next((n, seg) for n, seg in segs.values() if type(seg.objects[0]).__name__ == "PackageMetadata")
    pm = pm_seg.objects[0]
    show = next(seg.objects[0] for n, seg in segs.values() if type(seg.objects[0]).__name__ == "ShowArchive")
    nslides = len(show.slideTree.slides)
    next_id = pm.last_object_identifier + 1
    touched = {pm_name}
    report = []
    first_new_on_slide = {}
    for spec in specs:
        sn = spec["slide"]
        if not 1 <= sn <= nslides:
            raise ValueError("页号超出范围：%d（共 %d 页）" % (sn, nslides))
        node = segs[show.slideTree.slides[sn - 1].identifier][1].objects[0]
        slide_id = node.slide.identifier
        slide_file, slide_seg = segs[slide_id]
        slide = slide_seg.objects[0]
        zorder = [r.identifier for r in slide.drawables_z_order]
        if expected_items and sn in expected_items and expected_items[sn] != len(zorder):
            raise ValueError("第 %d 页的对象编号对不上（Keynote 里 %d 个，文件里 %d 个），可能是显示了标题/正文占位符。"
                             "先用 set_slide 关掉 title_showing / body_showing 再试。" % (sn, expected_items[sn], len(zorder)))
        k = spec["item"]
        if not 1 <= k <= len(zorder):
            raise ValueError("第 %d 页没有第 %d 个对象（共 %d 个）" % (sn, k, len(zorder)))
        drawable_id = zorder[k - 1]
        dobj = segs.get(drawable_id, (None, None))[1]
        dtype = type(dobj.objects[0]).__name__ if dobj else "?"
        existing = [b for b in slide.builds if segs[b.identifier][1].objects[0].drawable.identifier == drawable_id]
        if existing:
            report.append({"slide": sn, "item": k, "skipped": "这个对象已经有构件动画"})
            continue
        start = spec["start"]
        if start is None:
            had = len(slide.buildChunks) > 0 or first_new_on_slide.get(sn)
            start = "with" if had else "click"
        first_new_on_slide[sn] = True
        automatic, referent = START[start]
        build_id, chunk_id = next_id, next_id + 1
        next_id += 2
        lower, upper = random.getrandbits(64), random.getrandbits(64)

        b = BuildArchive()
        b.drawable.identifier = drawable_id
        b.delivery = "All at Once"
        b.duration = 0
        at = b.attributes
        at.eventTrigger = 1
        at.ChartRotation3D = 60
        aa = at.animationAttributes
        aa.animation_type = "In"
        aa.effect = EFFECTS[spec["effect"]]
        aa.duration = spec["duration"]
        aa.delay = 0
        aa.random_number_seed = random.getrandbits(31)
        aa.writing_direction_is_rtl = False
        if spec["effect"] == "line_draw":
            at.action_acceleration = _enum(at, "action_acceleration", "kEaseBoth")
            at.custom_include_endpoints = True
        else:
            at.custom_textDelivery = _enum(at, "custom_textDelivery", "kTextDeliveryByObject")
            at.custom_deliveryOption = _enum(at, "custom_deliveryOption", "kDeliveryOptionBackward")
            at.custom_travel_distance = 1
        b.chunk_id_seed = 1

        c = BuildChunkArchive()
        c.build.identifier = build_id
        c.delay = spec["delay"]
        c.duration = spec["duration"]
        c.automatic = automatic
        c.referent = referent
        c.build_chunk_identifier.build_id.lower = lower
        c.build_chunk_identifier.build_id.upper = upper
        c.build_chunk_identifier.build_chunk_id = 1
        c.build_id.lower = lower
        c.build_id.upper = upper

        versions = list(slide_seg.header.message_infos[0].version)
        bh = copy.deepcopy(slide_seg.header)
        bh.Clear()
        bh.identifier = build_id
        mi = bh.message_infos.add()
        mi.type = 8
        mi.version.extend(versions)
        ch = copy.deepcopy(bh)
        ch.identifier = chunk_id
        ch.message_infos[0].type = 153

        last_chunk = files[slide_file].chunks[-1]
        last_chunk.archives.append(IWAArchiveSegment(bh, [b]))
        last_chunk.archives.append(IWAArchiveSegment(ch, [c]))
        segs[build_id] = (slide_file, last_chunk.archives[-2])
        segs[chunk_id] = (slide_file, last_chunk.archives[-1])
        slide.builds.add().identifier = build_id
        slide.buildChunks.add().identifier = chunk_id
        slide_seg.header.message_infos[0].object_references.extend([build_id, chunk_id])
        comp = next(cc for cc in pm.components if cc.identifier == slide_id)
        e = comp.object_uuid_map_entries.add()
        e.identifier = build_id
        e.uuid.lower, e.uuid.upper = lower, upper
        touched.add(slide_file)
        report.append({"slide": sn, "item": k, "effect": spec["effect"], "duration": spec["duration"],
                       "start": start, "object": dtype.replace("Archive", "")})
    if next_id - 1 == pm.last_object_identifier:
        return {"added": 0, "builds": report}
    pm.last_object_identifier = next_id - 1

    backup = None
    if backup_dir:
        os.makedirs(backup_dir, exist_ok=True)
        backup = os.path.join(backup_dir, "%s-加动画前-%s.key" % (
            os.path.splitext(os.path.basename(path))[0], time.strftime("%Y%m%d-%H%M%S")))
        shutil.copy2(path, backup)
        olds = sorted(glob.glob(os.path.join(backup_dir, "*.key")), key=os.path.getmtime)
        for f in olds[:-10]:  # keep the 10 most recent backups
            try:
                os.remove(f)
            except OSError:
                pass
    tmp = path + ".builds-tmp"
    with zipfile.ZipFile(tmp, "w") as zo:
        for info in z.infolist():
            data = files[info.filename].to_buffer() if info.filename in touched else z.read(info.filename)
            zo.writestr(info, data)
    z.close()
    os.replace(tmp, path)
    return {"added": sum(1 for r in report if "effect" in r), "builds": report, "backup": backup}


def read_builds(path, slide_no=None):
    """List builds per slide (for checking)."""
    IWAArchiveSegment, IWAFile, import_version = _import()
    z = zipfile.ZipFile(path)
    objs = {}
    for n in z.namelist():
        if n.endswith(".iwa"):
            for ch in IWAFile.from_buffer(z.read(n), n).chunks:
                for seg in ch.archives:
                    objs[seg.header.identifier] = seg.objects[0]
    show = next(o for o in objs.values() if type(o).__name__ == "ShowArchive")
    out = []
    for i, ref in enumerate(show.slideTree.slides, 1):
        if slide_no and i != slide_no:
            continue
        s = objs[objs[ref.identifier].slide.identifier]
        z_ids = [r.identifier for r in s.drawables_z_order]
        rows = []
        for cref in s.buildChunks:
            cobj = objs[cref.identifier]
            bobj = objs[cobj.build.identifier]
            a = bobj.attributes.animationAttributes
            start = {(False, True): "click", (True, False): "with", (True, True): "after"}.get(
                (cobj.automatic, cobj.referent), "?")
            d = bobj.drawable.identifier
            rows.append({"item": z_ids.index(d) + 1 if d in z_ids else None,
                         "effect": a.effect.replace("com.apple.iWork.Keynote.", ""),
                         "duration": round(a.duration, 2), "start": start})
        if rows or slide_no:
            out.append({"slide": i, "builds": rows})
    return out
