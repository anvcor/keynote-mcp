"""End-to-end test of the Windows backend against the real PowerPoint on this PC.

Creates test/测试文稿.pptx from the PowerPoint template, edits it through the MCP server like
Claude would while co-writing a script, renders slides to test/out_win/, exports PDF / PNG / PPTX,
then reads the result with pptx_info (the parser import_pptx uses on the Mac).

    python test/e2e_test_win.py
"""
import json
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
from mcp_client import Client  # noqa: E402
from keynote_mcp import pptx_info  # noqa: E402

OUT = os.path.join(HERE, "out_win")
DECK = os.path.join(HERE, "测试文稿.pptx")
IMG = os.path.join(HERE, "素材")
ZEMAX = os.path.join(IMG, "85MIX inf FFTMTFvsField.jpg")


def _make_product_pngs():
    from PIL import Image, ImageDraw
    cut = os.path.join(OUT, "产品抠图.png")
    im = Image.new("RGBA", (600, 900), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    dr.rounded_rectangle((120, 80, 480, 860), radius=40, fill=(40, 40, 40, 255))
    dr.ellipse((170, 100, 430, 360), fill=(90, 90, 90, 255))
    im.save(cut)
    white = os.path.join(OUT, "产品白底.png")
    Image.new("RGB", (600, 900), (255, 255, 255)).save(white)
    return cut, white


def main():
    os.makedirs(OUT, exist_ok=True)
    c = Client(["--root", HERE])
    init = c.initialize()
    failures = []

    def step(name, tool, args=None, expect_error=False, image=None, show=260, check=None):
        err, data = c.call(tool, args, save_image=image)
        ok = err == expect_error
        if ok and check is not None:
            try:
                ok = bool(check(data))
            except Exception as e:  # noqa: BLE001
                ok = False
                data = {"check_failed": str(e), "data": data}
        mark = "OK " if ok else "FAIL"
        text = json.dumps(data, ensure_ascii=False) if not isinstance(data, str) else data
        print("[%s] %-30s %s" % (mark, name, text[:show].replace("\n", " ")))
        if not ok:
            failures.append(name)
        return data

    print("instructions mention Windows:", "Windows" in init.get("instructions", ""))
    tools = c.request("tools/list")["tools"]
    print("tools:", len(tools), ", ".join(t["name"] for t in tools))

    step("status", "keynote_status", check=lambda d: d.get("backend", "").startswith("PowerPoint"))
    d = step("create from template", "create_presentation", {"path": DECK, "overwrite": True},
             check=lambda d: d["slides"] == 18 and d["width"] == 1920 and "template_guide" in d)
    deck = step("get_deck", "get_deck", {"notes": "none"},
                check=lambda d: [s.get("sample") for s in d["slides"][11:]] ==
                ["longitudinal", "longitudinal_annotated", "mtf_field", "cover", "structure", "shapes", "product_card"])

    # 图1: title page
    step("title text + red F", "add_text", {"slide": 1, "text": "AF 35mm F1.4 测试", "x": 51, "y": 48, "size": 50,
                                            "highlight": "1.4"}, check=lambda d: d["item"] == 1 and d["font"] == "Helvetica Neue")
    step("notes 图1", "set_notes", {"slide": 1, "text": "[图1：标题页]\n开场。"})

    # 图2-3: structure picture, then a magic move step
    pic = step("structure image", "add_image", {"slide": 2, "path": os.path.join(IMG, "结构图.png"), "width": 1200},
               check=lambda d: d["type"] == "image" and abs(d["width"] - 1200) < 1 and d["name"].startswith("!!"))
    step("caption", "add_text", {"slide": 2, "text": "P8s 光学结构（示例图）", "y": 820, "size": 32})
    step("notes 图2", "set_notes", {"slide": 2, "text": "[图2：镜头结构图]\n旧段落"})
    mm = step("magic_move_step", "magic_move_step",
              {"slide": 2, "changes": [{"item": 1, "x": 60, "y": 200, "width": 900}], "remove_items": [2]},
              check=lambda d: d["new_slide"] == 3 and len(d["items"]) == 1)
    step("spec text on slide 3", "add_text", {"slide": 3, "text": "4 组 7 片，尾部一片非球面", "x": 1020, "y": 420, "size": 40})
    step("slide 3 transition", "get_slide", {"slide": 3},
         check=lambda d: d["transition_in"]["effect"] == "magic move" and d["transition_in"]["duration"] == 0.5
         and d["items"][0]["name"] == pic["name"])
    step("notes 图3", "set_notes", {"slide": 3, "text": "[图3：结构图左移，右侧出现说明]\n[动画] 结构图左移缩小"})

    # chapter card after slide 3
    cc = step("chapter_card", "chapter_card", {"title": "二 性能仿真与测量", "after": 3},
              check=lambda d: d["slides"] == [4, 5, 6] and d["number"] == "二")
    step("chapter card items", "get_slide", {"slide": 5},
         check=lambda d: [i["text"] for i in d["items"]] == ["性能仿真与测量", "二"] and d["items"][0]["width"] == 1920)

    # 图4: MTF chart from the template's sample page (now slide 17 after inserting 1 + 3 slides)
    deck = step("get_deck 2", "get_deck", {"notes": "none"})
    mtf = [s["slide"] for s in deck["slides"] if s.get("sample") == "mtf_field"][0]
    step("duplicate MTF sample", "duplicate_slide", {"slide": mtf}, check=lambda d: d["new_slides"] == [mtf + 1])
    step("move it to slide 7", "move_slide", {"slide": mtf + 1, "to": 7})
    step("edit @INF text", "update_item", {"slide": 7, "item": 48, "text": "@INF F1.47", "highlight": "1.47"},
         check=lambda d: d["text"] == "@INF F1.47")
    base = step("replace base image", "update_item", {"slide": 7, "item": 1, "image": ZEMAX},
                check=lambda d: d["item"] == 1 and d["file"].endswith("FFTMTFvsField.jpg"))
    step("anim kept on chart", "list_builds", {"slide": 7}, check=lambda d: len(d["slides"][0]["builds"]) == 16)
    step("notes 图4", "set_notes", {"slide": 7, "text": "[图4：MTF 曲线]"})
    step("read_zemax_mtf", "read_zemax_mtf", {"image": ZEMAX}, check=lambda d: len(d["table"]) > 0)

    # builds on the structure slide
    step("add_builds", "add_builds", {"builds": [{"slide": 3, "item": 1, "effect": "line_draw"},
                                                 {"slide": 3, "item": 2, "effect": "fade_move"}]},
         check=lambda d: [b["start"] for b in d["added"]] == ["click", "with"])
    step("list_builds", "list_builds", {"slide": 3},
         check=lambda d: [b["effect"] for b in d["slides"][0]["builds"]] == ["line_draw", "fade_move"])

    # product card: one cut-out, one opaque (warning expected)
    cut, white = _make_product_pngs()
    pc = step("product_card", "product_card",
              {"products": [{"image": cut, "name": "VILTROX AF 35mm F1.4 Pro", "badge": "Since 2025.11.20", "highlight": "1.4"},
                            {"image": white, "name": "ZHONGYI AF 35mm F1.4", "badge": "¥2999"}], "after": 1},
              check=lambda d: d["slide"] == 2 and len(d["images"]) == 2 and "warning" in d["images"][1])
    step("product slide not a sample", "get_deck", {"notes": "none"},
         check=lambda d: d["slides"][1].get("sample") is None
         and sum(1 for s in d["slides"] if s.get("sample") == "product_card") == 1)

    # misc objects on a fresh slide, subtitle-line warning
    ns = step("add_slide", "add_slide", {"notes": "[图5：杂项]"})
    n = ns["slide"]
    step("add_line", "add_line", {"slide": n, "x1": 100, "y1": 500, "x2": 1800, "y2": 500, "dash": True})
    step("add_shape", "add_shape", {"slide": n, "x": 100, "y": 100, "width": 300, "height": 80, "text": "标签", "rounded": True})
    step("add_table", "add_table", {"slide": n, "rows": [["物距", "MTF50"], ["INF", "【待填】"]], "x": 100, "y": 600})
    step("subtitle warning", "add_text", {"slide": n, "text": "太低了", "x": 100, "y": 1010},
         check=lambda d: "warning" in d)
    step("image opacity refused", "update_item", {"slide": 3, "item": 1, "opacity": 50}, expect_error=True)
    step("dissolve transition", "set_transition", {"slides": [n], "effect": "dissolve", "duration": 0.8},
         check=lambda d: d["slides"][0]["transition_in"]["effect"] == "dissolve")

    # notes from 文稿.md
    script = os.path.join(OUT, "文稿.md")
    with open(script, "w", encoding="utf-8") as f:
        f.write("# 测试稿\n\n[图1：标题页]\n这是开场段落。\n\n## 一 光学设计\n\n[图2：镜头结构图]\n新段落。\n\n"
                "[图3：结构图左移，右侧出现说明]\n结构图左移之后，右边出现一行说明文字。\n\n[图4：MTF 曲线]\n曲线段落。\n\n[图9：没有页]\n无。\n")
    step("sync dry run", "sync_notes_from_script", {"script_path": script, "dry_run": True},
         check=lambda d: d["unmatched_figures"] == ["9"] and len(d["planned"]) == 4)
    step("sync write", "sync_notes_from_script", {"script_path": script}, check=lambda d: d["written"] == 4)
    step("[动画] line kept", "get_slide", {"slide": 4},
         check=lambda d: d["notes"].startswith("[图3：") and "[动画] 结构图左移缩小" in d["notes"])

    # look at it
    step("view_slide 4", "view_slide", {"slide": 4, "save_to": os.path.join(OUT, "slide4.png")},
         image=os.path.join(OUT, "view4.jpg"), check=lambda d: d["summary"]["slide"] == 4)
    step("view product", "view_slide", {"slide": 2}, image=os.path.join(OUT, "view2.jpg"))
    step("view chapter", "view_slide", {"slide": 6}, image=os.path.join(OUT, "view6.jpg"))
    step("view MTF", "view_slide", {"slide": 8}, image=os.path.join(OUT, "view8.jpg"))

    # exports
    step("export png", "export", {"format": "png", "path": os.path.join(OUT, "png"), "slides": [1, 2], "overwrite": True},
         check=lambda d: d["files"] == 2)
    step("export pdf", "export", {"format": "pdf", "path": os.path.join(OUT, "测试.pdf"), "overwrite": True})
    step("export pptx subset", "export", {"format": "pptx", "path": os.path.join(OUT, "前三页.pptx"), "slides": [1, 2, 3],
                                          "overwrite": True})

    # protection
    step("save outside roots refused", "save_presentation", {"path": "C:\\Windows\\Temp\\x.pptx"}, expect_error=True)
    step("bad slide number", "get_slide", {"slide": 999}, expect_error=True)
    refs = step("list_references", "list_references", check=lambda d: len(d["decks"]) == 8)
    r85 = step("references 85MIX2", "list_references", {"project": "MIX II", "kind": "章节卡", "with_sheet": True},
               image=os.path.join(OUT, "sheet85.jpg"), check=lambda d: d["pptx"] and os.path.exists(d["pptx"]))
    op = step("open reference (copied)", "open_presentation", {"path": r85["pptx"]},
              check=lambda d: "original" in d and "副本" in d["path"])
    if op and op.get("path"):
        step("close reference copy", "close_presentation", {"document": op["path"], "save": False})
        try:
            os.remove(op["path"])
        except OSError:
            pass

    step("delete sample pages", "get_deck", {"notes": "none"})
    step("save", "save_presentation", {"document": DECK})
    step("close", "close_presentation", {"document": DECK})
    c.close()

    # what the Mac's import_pptx will see
    info = pptx_info.read(DECK)
    print("slide size:", info["slide_size"]["points"], "fonts:", info["fonts"])
    for s in info["slides"][:8]:
        print("  slide %d fig=%s in=%s builds=%s anim=%s" % (s["slide"], s["fig"], s["transition_in"], s["builds"],
                                                              s["animation_notes"]))
    morph = [s for s in info["slides"] if (s["transition_in"] or {}).get("effect") == "morph"]
    ok = (info["slide_size"]["points"] == [1920, 1080] and len(morph) >= 20
          and all(abs(s["transition_in"]["duration"] - 0.5) < 1e-6 for s in morph if s["slide"] <= 7)
          and info["slides"][3]["fig"] == "3" and info["slides"][3]["animation_notes"])
    print("[%s] pptx_info sees morph 0.5 s, [图N], [动画]" % ("OK " if ok else "FAIL"))
    if not ok:
        failures.append("pptx_info")
    import re
    with zipfile.ZipFile(DECK) as z:
        names = [n for n in z.namelist() if n.startswith("ppt/notesSlides/")]
        print("notes parts:", len([n for n in names if n.endswith(".xml")]))
        bases = []
        for n in z.namelist():
            if re.match(r"ppt/slides/slide\d+\.xml$", n):
                for blk in re.findall(r"<p:pic>.*?</p:pic>", z.read(n).decode("utf-8"), re.S):
                    if 'name="50GM INF FFTMTFvsField.jpg"' in blk:
                        bases.append('<a:alphaModFix amt="0"/>' in blk)
    ok = len(bases) == 2 and all(bases)
    print("[%s] replaced Zemax base image stays invisible (%s)" % ("OK " if ok else "FAIL", bases))
    if not ok:
        failures.append("base image transparency")
    print("\n%d failures%s" % (len(failures), (": " + ", ".join(failures)) if failures else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
