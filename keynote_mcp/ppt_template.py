"""Build templates/KEYNOTE动画模板-PPT.pptx, the PowerPoint twin of the Keynote start file (Windows only).

    python -m keynote_mcp.ppt_template

Source: the Keynote-exported .pptx of 85MIX2 (materials/pptx). Its last pages are the sample pages
of KEYNOTE动画模板.key (slides 97-103) with the same object numbers, and slide 8 is the two-product
intro page. The result has the Keynote template's page order:

    1-11 blank (#2C2C2C)   12 longitudinal aberration   13 + colour-shift label   14 FFT MTF vs Field
    15 cover   16 optical layout   17 lens shapes   18 product intro (badge group ungrouped, roles tagged)
"""
import json
import os
import shutil
import sys
import tempfile

from . import ppt_tools as P

SOURCE_PROJECT = "MIX II"  # 又双APO？ 美科AF 85mm F1.4 MIX II仿真及测量
KEEP = [1, 97, 98, 99, 100, 102, 103, 8]
SAMPLES = {12: "longitudinal", 13: "longitudinal_annotated", 14: "mtf_field", 15: "cover",
           16: "structure", 17: "shapes", 18: "product_card"}
KEY_GUIDE = os.path.join(P.PROJECT_DIR, "templates", "KEYNOTE动画模板.json")


def _source():
    for d in P._ref_catalog().get("decks", []):
        if SOURCE_PROJECT in d["project"]:
            return P._ref_pptx(d)
    raise SystemExit("references/catalog.json 里没有 85MIX2")


def _tag_product(s):
    """Ungroup the left badge and tag each object of the product page with its role."""
    for i in range(s.Shapes.Count, 0, -1):
        sh = s.Shapes.Item(i)
        if sh.Type == 6:
            sh.Ungroup()
    roles = {}
    for i in range(1, s.Shapes.Count + 1):
        sh = s.Shapes.Item(i)
        side = "L" if sh.Left + sh.Width / 2.0 < 960 else "R"
        if sh.Type == 9:
            role = "arrow"
        elif sh.Type == 13:
            role = "img" + side
        elif sh.Type == 1:
            role = "badge" + side
        elif sh.Type == 17 and sh.Top < 900:
            role = "name" + side
        elif sh.Type == 17:
            role = "badge" + side + "_text"
        else:
            continue
        sh.Tags.Add("KMCP_ROLE", role)
        roles[role] = i
    need = {"arrow", "imgL", "imgR", "nameL", "nameR", "badgeL", "badgeR", "badgeL_text", "badgeR_text"}
    if set(roles) != need:
        raise SystemExit("产品介绍页的对象和预期不一样：%s" % sorted(roles))
    return roles


def build(out=P.PROJECT_TEMPLATE):
    ctx = P.Context(cfg=None)
    app = ctx.app()
    src = _source()
    tmp = tempfile.mkdtemp(prefix="kmcp-template-")
    work = os.path.join(tmp, "work.pptx")
    shutil.copy2(src, work)
    d = app.Presentations.Open(work, P.MSO_FALSE, P.MSO_FALSE, P.MSO_FALSE)
    try:
        keep = set(KEEP)
        for i in range(d.Slides.Count, 0, -1):
            if i not in keep:
                d.Slides.Item(i).Delete()
        d.Slides.Item(2).MoveTo(d.Slides.Count)  # old slide 8 (product page) goes last
        for _ in range(10):
            d.Slides.Item(1).Duplicate()
        for i in range(1, 12):
            P.apply_transition(d.Slides.Item(i), "magic move", P.HOUSE_DURATION)
        for i in range(1, d.Slides.Count + 1):
            s = d.Slides.Item(i)
            P.set_notes_text(s, "")
            if i in SAMPLES:
                s.Tags.Add("KMCP_SAMPLE", SAMPLES[i])
        roles = _tag_product(d.Slides.Item(18))
        if d.Slides.Count != 18:
            raise SystemExit("模板页数不对：%d" % d.Slides.Count)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        d.SaveAs(os.path.normpath(out), P.PP_SAVE_PPTX)
    finally:
        d.Close()
        shutil.rmtree(tmp, ignore_errors=True)
    _write_guide(out, roles)
    return out


def _write_guide(out, roles):
    with open(KEY_GUIDE, encoding="utf-8") as f:
        g = json.load(f)
    g["file"] = os.path.basename(out)
    g["source"] = ("KEYNOTE动画模板.key 的 PowerPoint 版：由 keynote_mcp/ppt_template.py 从 85MIX2 的 Keynote 导出 pptx 生成"
                   "（第 97–103 页就是模板样例页，第 8 页是两款产品介绍页）。样例图片压缩过（长边 2560）。")
    g["how_to_use"] = (
        "新文稿是这份模板的副本。第 1–11 页是深灰空白页，按顺序直接用作内容页或页间空白页（不够就 add_slide）。"
        "第 12–18 页是样例页（get_deck 里有 sample 标记）：需要同类图时用 duplicate_slide 复制样例页，再 move_slide 移到要用的位置，"
        "用 update_item 改文字（F 值数字用 highlight 标红），换底图用 update_item 的 image。曲线形状脚本改不了，由作者自己描"
        "（或到 Mac 上用 trace_mtf）。产品介绍页用 product_card。做完后删掉没用到的样例页和多余空白页。"
        "所有内容的底边不要低于 y=1000（字幕参考线）。PowerPoint 的转场记在切入的那一页上。")
    g["editable_text_items"]["18"] = {"note": "用 product_card 生成，不要手改；对象按 KMCP_ROLE 标签认（%s）" % "、".join(sorted(roles))}
    g["sample_tags"] = {str(k): v for k, v in SAMPLES.items()}
    with open(os.path.splitext(out)[0] + ".json", "w", encoding="utf-8") as f:
        json.dump(g, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    print(build(sys.argv[1] if len(sys.argv) > 1 else P.PROJECT_TEMPLATE))
