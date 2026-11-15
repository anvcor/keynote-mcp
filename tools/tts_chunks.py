"""把视频文稿 docx 切成 TTS 小块（见 配音.md）。

用法: python tts_chunks.py 文稿.docx 项目/配音 --headings 镜头外观,光学设计,实拍样张,结尾
        [--max 160] [--replace 64mm=64毫米 --replace 61MP=6100万像素 ...]

- 标题行（--headings 列出的，或 Word 标题样式）不念，作为分章点；第一段之前的标题（文稿名）跳过。
- 每个正文段落记下它前面最近一张插图的序号（图号，从 1 数）。
- 段落超过 --max 字时在句末（。！？）切开。火山引擎一次合成的文字越长，后面越念越快，
  300 字就能听出来；一块只放一段、不超过 160 字，语速才前后一致。
- 输出: 配音/文本/<章号>_<章名>.txt（每行一段，tts_srt.py 要用）和 配音/chunks.json。
"""
import argparse, json, os, re
import docx

ap = argparse.ArgumentParser()
ap.add_argument("docx"); ap.add_argument("out")
ap.add_argument("--headings", default="")
ap.add_argument("--max", type=int, default=160)
ap.add_argument("--replace", action="append", default=[], help="合成用文字里的替换，如 64mm=64毫米")
ap.add_argument("--first", default="开场", help="第一个标题之前那一章的名字")
a = ap.parse_args()

heads = {h.strip() for h in a.headings.split(",") if h.strip()}
reps = [r.split("=", 1) for r in a.replace]
d = docx.Document(a.docx)
chapters, fig, started = [[a.first, []]], 0, False
for p in d.paragraphs:
    if p._p.xpath(".//pic:pic"):
        fig += len(p._p.xpath(".//pic:pic"))
    t = p.text.strip()
    if not t:
        continue
    style = p.style.name if p.style is not None else ""
    if t in heads or style.startswith("Heading") or style.startswith("标题"):
        if started:
            chapters.append([t, []])
        continue
    if not started and fig == 0:
        continue  # 文稿名
    started = True
    for x, y in reps:
        t = t.replace(x, y)
    chapters[-1][1].append((t, fig))

os.makedirs(os.path.join(a.out, "文本"), exist_ok=True)
chunks = []
for si, (name, paras) in enumerate(chapters, 1):
    with open(os.path.join(a.out, "文本", f"{si}_{name}.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(t for t, _ in paras) + "\n")
    for pi, (para, fg) in enumerate(paras, 1):
        sents = re.findall(r"[^。！？]*[。！？]|[^。！？]+$", para)
        cur, parts = "", []
        for s in sents:
            if cur and len(cur) + len(s) > a.max:
                parts.append(cur); cur = s
            else:
                cur += s
        if cur:
            parts.append(cur)
        for k, t in enumerate(parts):
            chunks.append({"seg": si, "chapter": name, "para": pi, "fig": fg, "part": k,
                           "last_in_para": k == len(parts) - 1, "text": t})
for i, c in enumerate(chunks, 1):
    c["id"] = f"c{i:02d}"
json.dump(chunks, open(os.path.join(a.out, "chunks.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"{len(chapters)} 章, {len(chunks)} 块, 最长 {max(len(c['text']) for c in chunks)} 字")
for si, (name, paras) in enumerate(chapters, 1):
    print(f"  {si} {name}: {len(paras)} 段, {sum(len(t) for t, _ in paras)} 字, "
          f"{sum(1 for c in chunks if c['seg'] == si)} 块")
