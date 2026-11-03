"""Pack the reference decks (.key and exported .pptx) into split zip archives for GitHub.

    .venv/bin/python -m keynote_mcp.pack_materials

The decks are mostly JPEG photos, so lossless zipping saves ~1 %. Images inside a copy of
each deck are therefore recompressed first (long side <= 2560 px, JPEG quality 80, PNGs keep
their alpha and are re-optimised; small images and thumbnails untouched; names and formats
unchanged). The full-quality decks stay in references/.

Each deck becomes materials/<key|pptx>/<project>.zip, compressed at level 9 and split into
95 MB parts (<project>.zip.001, .002, …) because GitHub refuses files over 100 MB.
7-Zip on Windows opens the .001 part and joins the rest. Small archives stay as a single .zip.
"""
import glob
import io
import os
import sys
import tempfile
import zipfile

for _sp in glob.glob(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".venv", "lib", "python3*", "site-packages")):
    if _sp not in sys.path:
        sys.path.append(_sp)
from PIL import Image  # noqa: E402

MAX_SIDE = 2560
JPEG_Q = 80
MIN_BYTES = 200 * 1000


def _shrink(data, ext):
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        return data
    w, h = im.size
    if max(w, h) > MAX_SIDE:
        k = MAX_SIDE / float(max(w, h))
        im = im.resize((max(1, int(w * k)), max(1, int(h * k))), Image.LANCZOS)
    out = io.BytesIO()
    if ext in (".jpg", ".jpeg"):
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        im.save(out, "JPEG", quality=JPEG_Q, optimize=True, progressive=True)
    elif ext == ".png":
        im.save(out, "PNG", optimize=True)
    else:
        return data
    new = out.getvalue()
    return new if len(new) < len(data) else data


def recompress(src, dst):
    """Copy a .key/.pptx (both are zips) with its large images recompressed."""
    zin = zipfile.ZipFile(src)
    with zipfile.ZipFile(dst, "w") as zo:
        for info in zin.infolist():
            data = zin.read(info.filename)
            name = info.filename
            ext = os.path.splitext(name)[1].lower()
            base = os.path.basename(name)
            media = name.startswith("Data/") or name.startswith("ppt/media/")
            if media and ext in (".jpg", ".jpeg", ".png") and len(data) >= MIN_BYTES and not base.startswith("st-"):
                data = _shrink(data, ext)
            zo.writestr(info, data)
    return dst

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = os.path.join(HERE, "references")
OUT = os.path.join(HERE, "materials")
PART = 95 * 1000 * 1000


def pack(src, dest_zip):
    os.makedirs(os.path.dirname(dest_zip), exist_ok=True)
    for old in glob.glob(dest_zip + "*"):
        os.remove(old)
    tmpdir = tempfile.mkdtemp(prefix="pack-")
    small = recompress(src, os.path.join(tmpdir, os.path.basename(src)))
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.write(small, os.path.basename(src))
    os.remove(small)
    os.rmdir(tmpdir)
    size = os.path.getsize(dest_zip)
    if size <= PART:
        return [dest_zip]
    parts = []
    with open(dest_zip, "rb") as f:
        i = 1
        while True:
            chunk = f.read(PART)
            if not chunk:
                break
            p = "%s.%03d" % (dest_zip, i)
            with open(p, "wb") as o:
                o.write(chunk)
            parts.append(p)
            i += 1
    os.remove(dest_zip)
    return parts


def main():
    total = 0
    for kind, pattern in (("key", os.path.join(REF, "*", "*.key")), ("pptx", os.path.join(REF, "_pptx", "*", "*.pptx"))):
        for src in sorted(glob.glob(pattern)):
            project = os.path.basename(os.path.dirname(src))
            parts = pack(src, os.path.join(OUT, kind, project + ".zip"))
            size = sum(os.path.getsize(p) for p in parts)
            total += size
            print("%-5s %6.0f MB -> %6.0f MB in %d file(s)  %s" % (kind, os.path.getsize(src) / 1e6, size / 1e6, len(parts), project))
    print("total %.0f MB" % (total / 1e6))


if __name__ == "__main__":
    main()
