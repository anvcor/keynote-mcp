"""Parse a lens-video-script style 文稿.md.

Format (from the lens-video-script skill):

    # 标题
    [图1：描述]
    段落……
    ## 一 节标题
    [图2：…]
    段落……

Each [图N：…] marker starts a figure. The paragraphs after it, up to the next
marker or heading, are what gets read over that slide.
"""
import re

FIG_RE = re.compile(r"^\s*[\[【]\s*图\s*([0-9]+[a-zA-Z]?)\s*(?:[：:]\s*(.*?))?\s*[\]】]\s*(.*)$")
HEAD_RE = re.compile(r"^\s*(#{1,6})\s+(.*\S)\s*$")
TAG_RE = re.compile(r"[\[【]\s*图\s*([0-9]+[a-zA-Z]?)")


def parse(text):
    """Return {"title", "figures": [{"fig", "caption", "text", "section"}], "sections": [...]}."""
    title = None
    section = None
    sections = []
    figures = []
    cur = None
    for raw in text.splitlines():
        line = raw.rstrip()
        h = HEAD_RE.match(line)
        if h:
            level, name = len(h.group(1)), h.group(2)
            if level == 1 and title is None:
                title = name
            else:
                section = name
                sections.append({"section": name, "before_fig": None})
            cur = None
            continue
        m = FIG_RE.match(line)
        if m:
            cur = {"fig": m.group(1), "caption": (m.group(2) or "").strip(), "paragraphs": [], "section": section}
            if sections and sections[-1]["before_fig"] is None:
                sections[-1]["before_fig"] = m.group(1)
            figures.append(cur)
            rest = m.group(3).strip()
            if rest:
                cur["paragraphs"].append(rest)
            continue
        if cur is not None and line.strip():
            cur["paragraphs"].append(line.strip())
    for f in figures:
        f["text"] = "\n".join(f.pop("paragraphs"))
    return {"title": title, "figures": figures, "sections": sections}


def tag_of(notes):
    """Return the figure id a slide's notes start with, e.g. '[图12：…]' -> '12'."""
    if not notes:
        return None
    m = TAG_RE.search(notes[:40])
    return m.group(1) if m else None
