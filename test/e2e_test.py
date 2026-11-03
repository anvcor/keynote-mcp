"""End-to-end test against the real Keynote on this Mac.

Creates test/测试文稿.key (local disk only), edits it through the MCP server,
renders a few slides to test/out/, and exports PDF / PNG / PPTX / movie.

    python3 test/e2e_test.py            # build + export PDF / PNG / PPTX
    python3 test/e2e_test.py --movie    # also test the (normally disabled) movie export
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from mcp_client import Client  # noqa: E402

OUT = os.path.join(HERE, "out")
DECK = os.path.join(HERE, "测试文稿.key")
IMG = os.path.join(HERE, "素材")


def main():
    os.makedirs(OUT, exist_ok=True)
    movie = "--movie" in sys.argv
    c = Client(["--root", HERE] + (["--allow-movie"] if movie else []))
    c.initialize()
    failures = []

    def step(name, tool, args=None, expect_error=False, image=None, show=300):
        err, data = c.call(tool, args, save_image=image)
        ok = err == expect_error
        mark = "OK " if ok else "FAIL"
        text = json.dumps(data, ensure_ascii=False) if not isinstance(data, str) else data
        print("[%s] %-28s %s" % (mark, name, text[:show].replace("\n", " ")))
        if not ok:
            failures.append(name)
        return data

    # 0. clean start
    st = step("status", "keynote_status")
    for d in st.get("documents", []):
        if d.get("path") == DECK:
            step("close old test deck", "close_presentation", {"document": DECK, "save": False})
    deck = step("create (template)", "create_presentation", {"path": DECK, "overwrite": True})
    second = os.path.join(OUT, "第二份.key")
    step("second deck, same template", "create_presentation", {"path": second, "overwrite": True})
    step("close second", "close_presentation", {"document": second})
    step("theme deck", "create_presentation", {"path": os.path.join(OUT, "主题新建.key"), "theme": "Basic White", "overwrite": True})
    step("close theme deck", "close_presentation", {"document": os.path.join(OUT, "主题新建.key")})
    print("     layouts:", deck.get("layouts"))

    # 1. title slide: the template has a black opener (1) and a grey blank (2); write the title on slide 2
    step("title text", "add_text", {"slide": 2, "text": "AFUL P8s 测试", "size": 116, "font": "HelveticaNeue-Medium"})
    step("notes slide 2", "set_notes", {"slide": 2, "text": "[图1：标题页]"})

    # 2. chapter card → slides 2-4
    step("chapter card", "chapter_card", {"title": "一 光学设计"})

    # 3. structure diagram slide → slide 5
    s = step("add blank slide", "add_slide", {"layout": "Blank", "notes": "[图2：镜头结构图]"})
    n = s["slide"]
    img = step("add image", "add_image", {"slide": n, "path": os.path.join(IMG, "结构图.png"), "width": 1200})
    cap = step("add caption", "add_text", {"slide": n, "text": "P8s 光学结构（示例图）", "y": 880, "size": 44,
                                          "color": "#FFFFFF", "font": "PingFangSC-Semibold"})
    step("view slide 5", "view_slide", {"slide": n}, image=os.path.join(OUT, "slide5.jpg"))

    # 4. magic move: push diagram left + shrink, fade caption, add a note on the right → slide 6
    mm = step("magic move step", "magic_move_step", {
        "slide": n, "duration": 1.2,
        "changes": [{"item": img["item"], "x": 80, "y": 290, "width": 900},
                    {"item": cap["item"], "opacity": 0}]})
    n2 = mm["new_slide"]
    step("text on new slide", "add_text", {"slide": n2, "text": "4 组 7 片\n尾部一片非球面", "x": 1080, "y": 420,
                                          "size": 56, "color": "#FFD400", "width": 700})
    step("notes slide 6", "set_notes", {"slide": n2, "text": "[图3：结构图左移，右侧出现说明]"})
    step("view slide 6", "view_slide", {"slide": n2}, image=os.path.join(OUT, "slide6.jpg"))

    # 5. second chapter + MTF slide with a table and a line
    step("chapter card 2", "chapter_card", {"title": "二 性能仿真与测量"})
    s = step("add MTF slide", "add_slide", {"layout": "Title Only", "title": "MTF（示例）", "notes": "[图4：MTF 曲线]"})
    m = s["slide"]
    step("add MTF image", "add_image", {"slide": m, "path": os.path.join(IMG, "MTF.png"), "x": 80, "y": 260, "width": 1000})
    step("add table", "add_table", {"slide": m, "rows": [["位置", "MTF50"], ["中心", "示例"], ["边缘", "示例"]],
                                    "x": 1180, "y": 300, "width": 640, "height": 300})
    step("add line", "add_line", {"slide": m, "x1": 80, "y1": 820, "x2": 1840, "y2": 820})
    step("view MTF slide", "view_slide", {"slide": m}, image=os.path.join(OUT, "slide_mtf.jpg"))

    # 6. edits on existing items
    sl = step("get slide", "get_slide", {"slide": m}, show=200)
    tbl = [i for i in sl["items"] if i["type"] == "table"][0]
    step("update table pos", "update_item", {"slide": m, "item": tbl["item"], "y": 330})
    step("transition dissolve", "set_transition", {"slides": [1], "effect": "dissolve", "duration": 0.8})
    step("save as + switch", "save_presentation", {"path": os.path.join(OUT, "另存测试.key")})
    st = step("status after save as", "keynote_status", {}, show=400)
    step("back to main deck", "close_presentation", {"document": os.path.join(OUT, "另存测试.key"), "save": False})
    step("reopen main deck", "open_presentation", {"path": DECK})

    # 6b. build animations (written into the file)
    b = step("add builds", "add_builds", {"builds": [
        {"slide": n2, "item": 3, "effect": "fade_move", "duration": 1.5},
        {"slide": m, "item": 5, "effect": "line_draw", "duration": 2.0}]}, show=400)
    lb = step("list builds", "list_builds", {}, show=400)
    got = {(x["slide"], y["item"], y["effect"]) for x in lb.get("slides", []) for y in x["builds"]} if isinstance(lb, dict) else set()
    if (n2, 3, "apple:fade and move character") not in got or (m, 5, "LineDrawForLine") not in got:
        failures.append("builds not found after reopen")
    step("bad build effect", "add_builds", {"builds": [{"slide": m, "item": 5, "effect": "explode"}]}, expect_error=True)

    # 6c. MTF curves traced from a Zemax "FFT MTF vs Field" export onto the template's MTF slide (14)
    tm = step("trace mtf", "trace_mtf", {"slide": 14, "image": os.path.join(IMG, "85MIX inf FFTMTFvsField.jpg")}, show=200)
    if not (isinstance(tm, dict) and sum(1 for x in tm.get("curves", []) if "nodes" in x) == 8):
        failures.append("trace_mtf did not write 8 curves")
    step("read zemax mtf", "read_zemax_mtf", {"image": os.path.join(IMG, "85MIX inf FFTMTFvsField.jpg"), "fields": [0, 15.12]}, show=200)

    # 6d. product intro slide (template slide 18 layout), one and two products
    pc = step("product card x2", "product_card", {"after": 2, "products": [
        {"image": os.path.join(IMG, "结构图.png"), "name": "Lens A 50mm F1.2", "badge": "Since 2025.05.20"},
        {"image": os.path.join(IMG, "MTF.png"), "name": "Lens B 85mm F1.2", "badge": "¥2599"}]}, show=150)
    if isinstance(pc, dict) and pc.get("slide"):
        g = step("product card layout", "get_slide", {"slide": pc["slide"]}, show=80)
        if isinstance(g, dict) and g.get("below_subtitle_line"):
            failures.append("product card crosses the subtitle line")
    step("product card x1", "product_card", {"products": [{"name": "Lens C 35mm F1.4", "badge": "Since 2026.01.01", "highlight": "1.4"}]}, show=80)
    step("subtitle warning", "add_text", {"slide": 2, "text": "too low", "y": 1010}, show=200)

    # 7. notes from 文稿.md by [图N] tags (dry run, then write)
    script = os.path.join(HERE, "文稿.md")
    step("sync notes dry", "sync_notes_from_script", {"script_path": script, "dry_run": True})
    step("sync notes", "sync_notes_from_script", {"script_path": script})

    # 8. structure check
    dk = step("get deck", "get_deck", {}, show=200)
    for s in dk["slides"]:
        print("     %2d %-12s items=%d fig=%-4s %s → %s" % (s["slide"], s["layout"], s["items"], s["fig"],
                                                        (s["notes"] or "")[:24].replace("\n", " "),
                                                        s["transition"]["effect"]))

    # 9. safety checks
    step("refuse write outside roots", "save_presentation", {"path": "/Volumes/移动硬盘/不应该出现.key"}, expect_error=True)
    step("bad slide number", "get_slide", {"slide": 99}, expect_error=True)
    step("bad effect", "set_transition", {"slides": [1], "effect": "explode"}, expect_error=True)

    step("save", "save_presentation", {})

    # 10. exports
    step("export pdf", "export", {"format": "pdf", "path": os.path.join(OUT, "测试文稿.pdf"), "overwrite": True})
    step("export png", "export", {"format": "png", "path": os.path.join(OUT, "测试文稿_图片"), "overwrite": True})
    step("export png subset", "export", {"format": "png", "path": os.path.join(OUT, "只导出5和6"), "slides": [5, 6],
                                         "overwrite": True})
    step("export pptx", "export", {"format": "pptx", "path": os.path.join(OUT, "测试文稿.pptx"), "overwrite": True})
    if not movie:
        step("movie export is off", "export", {"format": "movie"}, expect_error=True)
    else:
        r = step("export movie", "export", {"format": "movie", "path": os.path.join(OUT, "测试文稿1-%d.mov" % len(dk["slides"])),
                                            "resolution": "1080p", "fps": 60, "slide_seconds": 2, "overwrite": True,
                                            "wait_seconds": 600}, show=600)
        if isinstance(r, dict) and r.get("status") == "running":
            print("     movie still running, job", r["job"])

    step("save again", "save_presentation", {})
    lb2 = step("builds survive re-saves", "list_builds", {}, show=300)
    if not (isinstance(lb2, dict) and sum(len(x["builds"]) for x in lb2.get("slides", [])) >= 2):
        failures.append("builds lost after save")

    # 11. PowerPoint → Keynote (file made by test/make_test_pptx.py with python-pptx)
    imp = step("import pptx", "import_pptx", {"path": os.path.join(IMG, "PPT导入测试.pptx"),
                                              "save_as": os.path.join(OUT, "PPT导入测试.key"), "overwrite": True}, show=200)
    if isinstance(imp, dict) and imp.get("slides"):
        mm = [s["keynote_transition_to_next"] for s in imp["slides"]]
        print("     transitions after import:", mm, "| repairs:", imp.get("repairs"), "| missing fonts:", imp.get("fonts_missing_on_mac"))
        if mm[1] != "magic move":
            failures.append("morph -> magic move")
        step("close imported", "close_presentation", {"document": imp["document"]["path"]})
    c.close()
    print("\nfailures:", failures or "none")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
