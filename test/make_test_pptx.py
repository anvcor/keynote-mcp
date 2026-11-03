"""Make a PowerPoint-style .pptx like Claude would produce on Windows (needs python-pptx).

    python -m pip install python-pptx
    python test/make_test_pptx.py test/素材/PPT导入测试.pptx

Contents: PowerPoint's default 16:9 size, a Morph (平滑) transition with !!-named
objects, an [动画] line in the notes, a Windows font (Microsoft YaHei), a fade
transition and one entrance animation.
"""
import os
import sys

from lxml import etree
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor

HERE = os.path.dirname(os.path.abspath(__file__))
IMG = os.path.join(HERE, "素材", "结构图.png")
NSMAP = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main"}

MORPH = """<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">
<mc:Choice xmlns:p159="http://schemas.microsoft.com/office/powerpoint/2015/09/main" Requires="p159">
<p:transition xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:p14="http://schemas.microsoft.com/office/powerpoint/2010/main" spd="slow" p14:dur="1500"><p159:morph option="byObject"/></p:transition>
</mc:Choice>
<mc:Fallback><p:transition xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" spd="slow"><p:fade/></p:transition></mc:Fallback>
</mc:AlternateContent>"""

FADE = """<p:transition xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" spd="med"><p:fade/></p:transition>"""

ENTRANCE = """<p:timing xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:tnLst><p:par><p:cTn id="1" dur="indefinite" restart="never" nodeType="tmRoot"><p:childTnLst><p:seq concurrent="1" nextAc="seek"><p:cTn id="2" dur="indefinite" nodeType="mainSeq"><p:childTnLst><p:par><p:cTn id="3" fill="hold"><p:stCondLst><p:cond delay="indefinite"/></p:stCondLst><p:childTnLst><p:par><p:cTn id="4" fill="hold"><p:stCondLst><p:cond delay="0"/></p:stCondLst><p:childTnLst><p:par><p:cTn id="5" presetID="10" presetClass="entr" presetSubtype="0" fill="hold" nodeType="clickEffect"><p:stCondLst><p:cond delay="0"/></p:stCondLst><p:childTnLst><p:set><p:cBhvr><p:cTn id="6" dur="1" fill="hold"><p:stCondLst><p:cond delay="0"/></p:stCondLst></p:cTn><p:tgtEl><p:spTgt spid="SPID"/></p:tgtEl><p:attrNameLst><p:attrName>style.visibility</p:attrName></p:attrNameLst></p:cBhvr><p:to><p:strVal val="visible"/></p:to></p:set><p:animEffect transition="in" filter="fade"><p:cBhvr><p:cTn id="7" dur="500"/><p:tgtEl><p:spTgt spid="SPID"/></p:tgtEl></p:cBhvr></p:animEffect></p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn><p:prevCondLst><p:cond evt="onPrev" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:prevCondLst><p:nextCondLst><p:cond evt="onNext" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:nextCondLst></p:seq></p:childTnLst></p:cTn></p:par></p:tnLst></p:timing>"""


def add_xml(slide, xml):
    slide._element.append(etree.fromstring(xml))


def text(slide, s, x, y, w, h, size=32, name=None, font=None, color=None):
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    if name:
        tb.name = name
    run = tb.text_frame.paragraphs[0].add_run()
    run.text = s
    run.font.size = Pt(size)
    if font:
        run.font.name = font
        rpr = run._r.get_or_add_rPr()
        ea = etree.SubElement(rpr, "{http://schemas.openxmlformats.org/drawingml/2006/main}ea")
        ea.set("typeface", font)
    if color:
        run.font.color.rgb = RGBColor.from_string(color)
    return tb


def main(out):
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)  # PowerPoint default 16:9
    blank = prs.slide_layouts[6]

    s1 = prs.slides.add_slide(prs.slide_layouts[0])
    s1.shapes.title.text = "PPTX 导入测试"
    s1.placeholders[1].text = "Windows 上用 PowerPoint 做的主体"
    s1.notes_slide.notes_text_frame.text = "[图1：标题页]\n这是开场。"

    s2 = prs.slides.add_slide(blank)
    pic = s2.shapes.add_picture(IMG, Inches(2.67), Inches(1.0), width=Inches(8.0))
    pic.name = "!!lens"
    text(s2, "P8s 光学结构（示例图）", 4.5, 6.3, 5.0, 0.6, size=24, name="!!caption")
    s2.notes_slide.notes_text_frame.text = "[图2：镜头结构图]\n这一页放结构图。"

    s3 = prs.slides.add_slide(blank)
    pic = s3.shapes.add_picture(IMG, Inches(0.4), Inches(1.6), width=Inches(6.0))
    pic.name = "!!lens"
    text(s3, "P8s 光学结构（示例图）", 8.0, 6.3, 5.0, 0.6, size=24, name="!!caption")
    text(s3, "4 组 7 片，尾部一片非球面", 7.0, 3.0, 5.8, 1.0, size=36, name="spec")
    add_xml(s3, MORPH)
    s3.notes_slide.notes_text_frame.text = ("[图3：结构图左移，右侧出现说明]\n"
                                            "[动画] 结构图左移缩小，说明淡出，右侧出现规格文字\n"
                                            "结构图左移之后，右边出现一行说明文字。")

    s4 = prs.slides.add_slide(blank)
    text(s4, "二 性能仿真与测量", 3.5, 3.0, 6.5, 1.2, size=54, font="微软雅黑")
    tb = text(s4, "MTF 曲线稍后出现（进入动画）", 3.5, 4.6, 6.5, 0.8, size=28, font="Calibri")
    add_xml(s4, FADE)
    add_xml(s4, ENTRANCE.replace("SPID", str(tb.shape_id)))
    s4.notes_slide.notes_text_frame.text = "[图4：章节与 MTF]\n[动画] 标题淡入，副标题稍后出现"

    prs.save(out)
    print("saved", out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "素材", "PPT导入测试.pptx"))
