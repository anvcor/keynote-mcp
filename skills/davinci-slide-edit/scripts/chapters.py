"""B 站章节：从剪辑方案里的章节卡 cue 和文稿的一级标题，生成「章节文本编辑器」要的文本。

用法: python3 chapters.py plan.json 文稿.docx [--first 开场] [--no-number]
输出（粘进 B 站投稿页 > 更多设置 > 章节管理 > 编辑章节 > 章节文本编辑器）:
  00:00 开场
  01:17 一 索尼35GM与适马35DG2简谈
  05:40 二 中一光学AF 35mm F1.4
  ...

- 章节卡 cue = plan.json 里 label 含「章节」的 cue（写 plan 时给章节卡 cue 加 "label": "章节卡"）
- 时间取 cue 的 t 向下取整到秒：章节从章节卡开始淡入的那一刻算
- 标题 = 文稿里样式为「标题 1」的段落，按顺序加「一 二 三…」；第一章之前的开场叫 --first
- B 站要求相邻章节间隔 >= 5 秒
"""
import argparse, json, re, zipfile

NUM = "一二三四五六七八九十"

ap = argparse.ArgumentParser()
ap.add_argument("plan")
ap.add_argument("docx")
ap.add_argument("--first", default="开场", help="第一个章节卡之前那段的名字")
ap.add_argument("--no-number", action="store_true", help="标题前不加 一 二 三")
a = ap.parse_args()

cues = [c for c in json.load(open(a.plan))["cues"] if "章节" in str(c.get("label", ""))]

z = zipfile.ZipFile(a.docx)
styles = z.read("word/styles.xml").decode("utf8")
h1 = {m.group(1) for m in re.finditer(r'<w:style [^>]*w:styleId="([^"]+)"[^>]*>\s*<w:name w:val="(?:heading 1|Heading 1|标题 1)"', styles)}
h1 |= {"Heading1", "1"} if not h1 else set()
xml = z.read("word/document.xml").decode("utf8")
heads = []
for p in re.findall(r"<w:p[ >].*?</w:p>", xml, re.S):
    st = re.search(r'<w:pStyle w:val="([^"]+)"', p)
    text = "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", p)).strip()
    if st and st.group(1) in h1 and text:
        heads.append(text)

if len(cues) != len(heads):
    print(f"!! 章节卡 cue {len(cues)} 个，文稿一级标题 {len(heads)} 个，对不上：")
    print("   cue:", [round(c["t"], 2) for c in cues])
    print("   标题:", heads)
    raise SystemExit(1)

def ts(sec):
    sec = int(sec)
    h, r = divmod(sec, 3600)
    return f"{h:02d}:{r // 60:02d}:{r % 60:02d}" if h else f"{r // 60:02d}:{r % 60:02d}"

rows = [(0, a.first)] + [(int(c["t"]), (h if a.no_number else f"{NUM[i]} {h}")) for i, (c, h) in enumerate(zip(cues, heads))]
for (t0, _), (t1, name) in zip(rows, rows[1:]):
    if t1 - t0 < 5:
        print(f"!! 「{name}」离上一章不到 5 秒，B 站会拒绝")
for t, name in rows:
    print(ts(t), name)
