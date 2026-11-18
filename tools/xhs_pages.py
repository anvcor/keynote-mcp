"""把视频文稿 docx 排成小红书图文（竖版长图），见 图文发布.md。

用法:
  python tools/xhs_pages.py 文稿.docx 输出目录 --cover 封面.png --title "星曜35APO" "｜光学设计" "及实拍讨论" \
         [--notes 3] [--chapters 镜头外观,光学设计,实拍样张,结尾] [--number] [--font NotoSansSC-VF.ttf]

版式照作者的《MTF，自己算？｜Lens Bench网页镜头仿真》：
- 每张 1440×1920（3:4）。小红书按 3:4 显示：3:5 的图在发布页预览左右出白边，在网页 / App 里被裁掉上下（11-18 实测）。
- 深色底 #111111，左右 / 上边距 96（1080 宽设计稿里是 72），配图通栏不跨页；正文 #CCCCCC 约 58 px，行距 98，
  两端对齐（英文多的行改左对齐，不拉大空），标点避头；段落连续往下排，句子可以跨页断开。
- 章节标题（docx 的标题样式或 --chapters 列出的行）另起一页；--number 给前几章加「一 二 三」。
- 第一段文字是文稿名，不排进正文。
- 一篇最多 18 张图：正文按 --notes 篇拆，每篇 ≤17 页 + 1 张封面；在所有可行切法里优先切在章节开头，其次各篇页数接近。
  每篇封面 = 顶部横线 + 大标题（每行垫深灰底条，最后一行加（上）（中）（下））+ 视频封面图。
输出: 输出目录/part1/01.png …（01 是封面），按文件名顺序上传。
"""
import argparse, io, itertools, os, re
import docx
from PIL import Image, ImageDraw, ImageFont

ap = argparse.ArgumentParser()
ap.add_argument("docx"); ap.add_argument("out")
ap.add_argument("--cover", required=True)
ap.add_argument("--title", nargs="+", required=True, help="封面标题，每个参数一行")
ap.add_argument("--notes", type=int, default=0, help="拆成几篇；0 = 自动（最少篇数）")
ap.add_argument("--chapters", default="", help="当作章节标题的行（逗号分隔），docx 标题样式会自动识别")
ap.add_argument("--number", action="store_true", help="章节名前加 一 二 三…（名为「结尾」「总结」的不加）")
ap.add_argument("--font", default=r"C:\Windows\Fonts\NotoSansSC-VF.ttf")
a = ap.parse_args()

K = 4 / 3
W, H = round(1080 * K), round(1440 * K)
BG, FG, HEAD_FG = (17, 17, 17), (204, 204, 204), (235, 235, 235)
M = round(72 * K)
BOTTOM = H - round(68 * K)
CW = W - 2 * M
FS, LH = round(44 * K), round(73.5 * K)
GAP_IMG, GAP_PARA = round(50 * K), round(46 * K)
MAX_IMAGES = 18
NO_START = set("，。、；：？！）」』》”’…—·%,.;:!?)")
NO_END = set("（「『《“‘(")
CN_NUM = "一二三四五六七八九十"


def font(sz, weight="Regular"):
    f = ImageFont.truetype(a.font, sz)
    try:
        f.set_variation_by_name(weight)
    except Exception:
        pass
    return f


F_BODY = font(FS)
D0 = ImageDraw.Draw(Image.new("RGB", (1, 1)))


def tokens(text):
    return re.findall(r"[A-Za-z0-9][A-Za-z0-9./°%+]*-?|\s|.", text)


def wrap(text):
    lines, cur = [], []
    for t in tokens(text):
        if cur and D0.textlength("".join(cur) + t, font=F_BODY) > CW:
            if t in NO_START or cur[-1] in NO_END:
                carry = [cur.pop()]
                lines.append(cur); cur = carry + [t]
            else:
                lines.append(cur); cur = [] if t.isspace() else [t]
        else:
            cur.append(t)
    if cur:
        lines.append(cur)
    return lines


def draw_line(d, toks, y, justify, fill):
    toks = list(toks)
    while toks and toks[-1].isspace():
        toks.pop()
    w = [d.textlength(t, font=F_BODY) for t in toks]
    extra = (CW - sum(w)) / (len(toks) - 1) if justify and len(toks) > 1 else 0
    if extra > FS * 0.12:
        extra = 0
    x = M
    for t, tw in zip(toks, w):
        d.text((x, y), t, font=F_BODY, fill=fill)
        x += tw + extra


# ---- docx -> 块 ----
heads = {h.strip() for h in a.chapters.split(",") if h.strip()}
doc = docx.Document(a.docx)
items, seen_title, chap_no = [], False, 0
for p in doc.paragraphs:
    for blip in p._p.xpath(".//a:blip"):
        rid = blip.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
        items.append(("img", doc.part.related_parts[rid].blob))
        items.append(("gap", GAP_IMG))
    t = p.text.strip()
    if not t:
        continue
    style = p.style.name if p.style is not None else ""
    if not seen_title:
        seen_title = True
        continue
    if t in heads or style.startswith("Heading") or style.startswith("标题"):
        label = t
        if a.number and t not in ("结尾", "总结", "后记"):
            label = CN_NUM[chap_no] + " " + t; chap_no += 1
        items += [("chapter", t), ("line", tokens(label), False, HEAD_FG), ("gap", GAP_PARA)]
        continue
    ls = wrap(t)
    items += [("line", l, i < len(ls) - 1, FG) for i, l in enumerate(ls)]
    items.append(("gap", GAP_PARA))


def img_h(blob):
    im = Image.open(io.BytesIO(blob))
    return round(CW * im.height / im.width)


def height(it):
    return {"img": lambda: img_h(it[1]), "line": lambda: LH, "gap": lambda: it[1]}.get(it[0], lambda: 0)()


pages, cur, y = [], [], M
for it in items:
    has = any(c[0][0] in ("img", "line") for c in cur)
    if it[0] == "gap":
        if cur:
            cur.append((it, y)); y += it[1]
        continue
    if it[0] == "chapter":
        if has:
            pages.append(cur); cur, y = [], M
        cur.append((it, y)); continue
    h = height(it)
    if y + h > BOTTOM and has:
        pages.append(cur); cur, y = [], M
    cur.append((it, y)); y += h
if cur:
    pages.append(cur)

n, cap = len(pages), MAX_IMAGES - 1
notes = a.notes or -(-n // cap)
chap = {i for i, p in enumerate(pages) if any(it[0] == "chapter" for it, _ in p)}
best = None
for cuts in itertools.combinations(range(1, n), notes - 1):
    b = (0,) + cuts + (n,)
    sizes = [b[i + 1] - b[i] for i in range(notes)]
    if max(sizes) > cap:
        continue
    score = (sum(c not in chap for c in cuts), max(sizes) - min(sizes))
    if best is None or score < best[0]:
        best = (score, b)
if not best:
    raise SystemExit(f"{n} 页正文排不进 {notes} 篇（每篇最多 {cap} 页正文）")
bounds = best[1]


def render_page(page):
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    for it, y in page:
        if it[0] == "img":
            src = Image.open(io.BytesIO(it[1])).convert("RGB")
            im.paste(src.resize((CW, img_h(it[1])), Image.LANCZOS), (M, y))
        elif it[0] == "line":
            draw_line(d, it[1], y + round((LH - FS) * 0.32), it[2], it[3])
    return im


def render_cover(lines):
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    d.rectangle((M, round(100 * K), W - M, round(104 * K)), fill=(84, 84, 84))
    ft = font(round(124 * K), "Bold")
    y = round(160 * K)
    for t in lines:
        tw = d.textlength(t, font=ft)
        base = y + round(118 * K)
        d.rectangle((M, base, min(W - M, M + tw + round(8 * K)), base + round(20 * K)), fill=(40, 40, 40))
        d.text((M, y), t, font=ft, fill=(236, 236, 236))
        y += round(168 * K)
    cov = Image.open(a.cover).convert("RGB")
    im.paste(cov.resize((CW, round(CW * cov.height / cov.width)), Image.LANCZOS), (M, y + round(30 * K)))
    return im


tags = {2: "上下", 3: "上中下"}.get(notes)
for k in range(notes):
    folder = os.path.join(a.out, f"part{k + 1}")
    os.makedirs(folder, exist_ok=True)
    for f in os.listdir(folder):
        if f.endswith(".png"):
            os.remove(os.path.join(folder, f))
    lines = list(a.title)
    if notes > 1:
        lines[-1] += f"（{tags[k] if tags else k + 1}）"
    render_cover(lines).save(os.path.join(folder, "01.png"), optimize=True)
    for i, p in enumerate(pages[bounds[k]:bounds[k + 1]], 2):
        render_page(p).save(os.path.join(folder, f"{i:02d}.png"), optimize=True)
    print(f"part{k + 1}: 正文第 {bounds[k] + 1}-{bounds[k + 1]} 页 + 封面 = {bounds[k + 1] - bounds[k] + 1} 张")
print("正文共", n, "页")
