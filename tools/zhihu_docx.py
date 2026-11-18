"""把视频文稿 docx 压成知乎「导入文档」用的 docx（见 图文发布.md）。

用法: python tools/zhihu_docx.py 文稿.docx 输出.docx [--chapters 镜头外观,光学设计] [--number] [--width 1600] [--quality 84]

- 飞书导出的 docx 配图是 3200×1800 PNG，整篇几百 MB；知乎导入上限 30 MB，浏览器扩展单次上传上限 10 MB。
  这里把配图转成 --width 宽的 JPEG（49 张约 6 MB），文字和顺序不变。
- 第一段（文稿名）去掉，知乎标题栏单独填；章节标题用标题 1（知乎导入后变成 H2）。--number 给章节加 一 二 三。
"""
import argparse, io
import docx
from docx.shared import Inches
from PIL import Image

ap = argparse.ArgumentParser()
ap.add_argument("src"); ap.add_argument("dst")
ap.add_argument("--width", type=int, default=1600); ap.add_argument("--quality", type=int, default=84)
ap.add_argument("--number", action="store_true")
ap.add_argument("--chapters", default="", help="当作章节标题的行（逗号分隔），飞书导出的 docx 标题常常没有标题样式")
a = ap.parse_args()
CN = "一二三四五六七八九十"
heads = {h.strip() for h in a.chapters.split(",") if h.strip()}
src, out = docx.Document(a.src), docx.Document()
first, k = True, 0
for p in src.paragraphs:
    for blip in p._p.xpath(".//a:blip"):
        blob = src.part.related_parts[blip.get(
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")].blob
        im = Image.open(io.BytesIO(blob)).convert("RGB")
        if im.width > a.width:
            im = im.resize((a.width, round(a.width * im.height / im.width)), Image.LANCZOS)
        buf = io.BytesIO(); im.save(buf, "JPEG", quality=a.quality, optimize=True, progressive=True); buf.seek(0)
        out.add_picture(buf, width=Inches(6.2))
    t = p.text.strip()
    if not t:
        continue
    if first:
        first = False
        continue
    style = p.style.name if p.style is not None else ""
    if t in heads or style.startswith("Heading") or style.startswith("标题"):
        if a.number and t not in ("结尾", "总结", "后记"):
            t = CN[k] + " " + t; k += 1
        out.add_heading(t, level=1)
    else:
        out.add_paragraph(t)
out.save(a.dst)
import os
print(a.dst, round(os.path.getsize(a.dst) / 1e6, 1), "MB")

