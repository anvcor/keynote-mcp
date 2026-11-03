"""Read duration / size / frame rate from a .mov / .m4v / .mp4 file (stdlib only)."""
import struct

_CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts"}


def _atoms(f, start, end):
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        hdr = f.read(8)
        if len(hdr) < 8:
            return
        size, kind = struct.unpack(">I4s", hdr)
        hlen = 8
        if size == 1:
            size = struct.unpack(">Q", f.read(8))[0]
            hlen = 16
        elif size == 0:
            size = end - pos
        if size < hlen:
            return
        yield kind, pos + hlen, pos + size
        pos += size


def movie_info(path):
    """Return {'duration': seconds, 'width', 'height', 'frames', 'fps'} for the first video track."""
    info = {"duration": None, "width": None, "height": None, "frames": None, "fps": None, "codec": None}
    with open(path, "rb") as f:
        f.seek(0, 2)
        total = f.tell()
        for kind, s, e in _atoms(f, 0, total):
            if kind != b"moov":
                continue
            for k2, s2, e2 in _atoms(f, s, e):
                if k2 == b"mvhd":
                    f.seek(s2)
                    ver = f.read(1)[0]
                    f.read(3)
                    if ver == 1:
                        f.read(16)
                        ts, dur = struct.unpack(">IQ", f.read(12))
                    else:
                        f.read(8)
                        ts, dur = struct.unpack(">II", f.read(8))
                    info["duration"] = dur / ts if ts else None
                elif k2 == b"trak" and info["frames"] is None:
                    _read_trak(f, s2, e2, info)
    if info["frames"] and info["duration"]:
        info["fps"] = round(info["frames"] / info["track_duration"], 3) if info.get("track_duration") else None
    info.pop("track_duration", None)
    return info


def _read_trak(f, s, e, info):
    tk = {}

    def walk(a, b):
        for kind, s2, e2 in _atoms(f, a, b):
            if kind in _CONTAINERS:
                walk(s2, e2)
            elif kind == b"tkhd":
                f.seek(s2)
                ver = f.read(1)[0]
                f.seek(e2 - 8)
                w, h = struct.unpack(">II", f.read(8))
                tk["width"], tk["height"] = w >> 16, h >> 16
            elif kind == b"hdlr" and "handler" not in tk:
                f.seek(s2 + 8)
                tk["handler"] = f.read(4)
            elif kind == b"mdhd":
                f.seek(s2)
                ver = f.read(1)[0]
                f.read(3)
                if ver == 1:
                    f.read(16)
                    ts, dur = struct.unpack(">IQ", f.read(12))
                else:
                    f.read(8)
                    ts, dur = struct.unpack(">II", f.read(8))
                tk["track_duration"] = dur / ts if ts else None
            elif kind == b"stsd":
                f.seek(s2 + 12)
                tk["codec"] = f.read(4).decode("latin-1")
            elif kind == b"stts":
                f.seek(s2 + 4)
                n = struct.unpack(">I", f.read(4))[0]
                frames = 0
                for _ in range(n):
                    c, _d = struct.unpack(">II", f.read(8))
                    frames += c
                tk["frames"] = frames

    walk(s, e)
    if tk.get("handler") == b"vide":
        for k in ("width", "height", "frames", "track_duration", "codec"):
            info[k] = tk.get(k)


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        print(p, movie_info(p))
