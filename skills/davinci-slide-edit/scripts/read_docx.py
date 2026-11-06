"""读视频文稿 .docx：按顺序打印段落，标出每张插图出现的位置，并把插图拼成带编号的总表。
不依赖 python-docx（直接解 zip 读 XML）。

用法: python3 read_docx.py 文稿.docx --out 工作目录
输出: 终端打印  段号  文本  [图N]；工作目录/docx_images_sheet.jpg（图N 左上角编号）
"""
import argparse, os, re, subprocess, zipfile
from resolve_common import ffmpeg

ap = argparse.ArgumentParser()
ap.add_argument("docx")
ap.add_argument("--out", required=True)
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)
z = zipfile.ZipFile(a.docx)
xml = z.read("word/document.xml").decode("utf8")
rels = z.read("word/_rels/document.xml.rels").decode("utf8")
rmap = {}
for m in re.finditer(r"<Relationship [^>]*>", rels):
    tag = m.group(0)
    i, t = re.search(r'Id="([^"]+)"', tag), re.search(r'Target="([^"]+)"', tag)
    if i and t:
        rmap[i.group(1)] = t.group(1)
order = []
for n, para in enumerate(re.findall(r"<w:p[ >].*?</w:p>", xml, re.S)):
    text = "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", para)).strip()
    imgs = [rmap[r] for r in re.findall(r'r:embed="([^"]+)"', para) if r in rmap]
    tags = []
    for im in imgs:
        order.append(im)
        tags.append(f"[图{len(order)} {os.path.basename(im)}]")
    if text or tags:
        print(n, text, " ".join(tags))
# 插图按出现顺序拼总表
d = os.path.join(a.out, "docx_images"); os.makedirs(d, exist_ok=True)
ff = ffmpeg()
for k, im in enumerate(order, 1):
    src = os.path.join(d, f"src_{k:03d}" + os.path.splitext(im)[1])
    open(src, "wb").write(z.read("word/" + im))
    vf = "scale=480:270:force_original_aspect_ratio=decrease,pad=480:270:(ow-iw)/2:(oh-ih)/2"
    out = os.path.join(d, f"{k:03d}.jpg")
    if subprocess.run([ff, "-v", "error", "-y", "-i", src, "-vf",
                       vf + f",drawtext=text='{k}':x=8:y=8:fontsize=40:fontcolor=red:box=1", out],
                      stderr=subprocess.DEVNULL).returncode != 0:
        subprocess.run([ff, "-v", "error", "-y", "-i", src, "-vf", vf, out], check=True)
if order:
    subprocess.run([ff, "-v", "error", "-y", "-start_number", "1", "-i", os.path.join(d, "%03d.jpg"),
                    "-frames:v", "1", "-vf", "tile=6x5", os.path.join(a.out, "docx_images_sheet.jpg")], check=True)
    print(f"{len(order)} 张插图 -> {a.out}/docx_images_sheet.jpg")
