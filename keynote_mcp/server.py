"""Minimal MCP server over stdio (JSON-RPC 2.0, one JSON message per line). Stdlib only."""
import json
import sys
import traceback

from . import __version__
from .config import Config
from .tools import Context, call, tool_list

SUPPORTED_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]

INSTRUCTIONS = """\
用这个服务器控制这台 Mac 上的 Apple Keynote（AppleScript）。要点：
- 坐标单位是点，原点在页面左上角；新文稿默认 1920x1080。页号、对象编号都从 1 开始。
- 先 get_deck / get_slide 了解现状，改完用 view_slide 看效果再继续。删除对象后编号会前移。
- 动画：转场用 set_transition / magic_move_step（复制当前页、改新页上的对象、原页转场设为神奇移动）。
  对象构件动画用 add_builds（line_draw 线条描绘、fade_move 逐字淡入移动），它会保存、关闭、写入、再重新打开文稿；
  一页上的多个动画尽量一次调用加完。list_builds 可以查看已有动画。
  章节卡用 chapter_card（空白→标题→空白，溶解）。
- 视频文稿：每段对应的 [图N：…] 写进演讲者备注（set_notes 或 sync_notes_from_script）。
- 视频不由你导出：做完后保存文稿，告诉用户在 Keynote 里审核效果、再手动导出视频。export 只用于 PDF / 逐页图片 / PPTX。
- 安全：只能在允许的目录里保存和导出；不在这些目录里的原稿会被拒绝修改，open_presentation 会自动复制一份本机副本再打开。
- 形状填充色不能用脚本设置；需要色块时用图片或模板里现成的形状。

用户的版式习惯（从他 78 份文稿、6535 页统计得出，照着做）：
- 新文稿默认是他每期的初始文件「KEYNOTE动画模板」的副本（create_presentation 返回 template_guide）：
  1–11 页是深灰 #2C2C2C 空白页，直接用；12 纵向像差图、13 色焦移标注、14 FFT MTF vs Field、15 开场封面、16 光学结构图、17 镜片形状素材。
  做同类页时 duplicate_slide 复制样例页、move_slide 挪到位置、update_item 改文字（F 值数字用 highlight 标红 #FC4B3E）；
  MTF 曲线用 trace_mtf：给 Zemax 导出的 FFT MTF vs Field 图，自动描进图表页现成的曲线并换好底图（他平时是照底图手描的）；
  read_zemax_mtf 可以只读出数值写进文稿。其它曲线（像差图、光线）暂时还是他自己描。做完删掉没用的样例页和多余空白页。
  脚本不能设背景色，新页靠「复制前一页再清空」继承深灰背景（add_slide 默认就这样做）。
  标题、正文都用 add_text 加在空白页上（标题 50 号，正文 24 / 32 / 34 号），不要用带占位符的主题版式，否则背景会变成纯黑。
- 1920x1080，字体 Helvetica Neue（中文自动用苹方）。
- 95% 的转场是 0.5 秒神奇移动（其次 0.8 秒），全部点按换页。add_slide 和 chapter_card 默认已按此设置。
- 两页内容之间通常夹一张空白页（Blank 版式、神奇移动），视频里表现为淡出再淡入。
- 章节卡：空白页 → 编号「一」(76 号) + 标题 (71 号) 两个白色居中文本框 → 空白页，用 chapter_card。
- 他大量使用对象构件动画：曲线、坐标轴、光路用「线条描绘」画出来（2–3 秒），图例文字和色块用「逐字淡入移动」（1.5–3 秒）。
  通常每页第一个动画点按出场，其余与上一个同时，所以一页上的曲线和图例会一起出现。用 add_builds 按这个习惯加。
- 他的文稿不写演讲者备注；备注只在需要记录 [图N] / [动画] 时用，可以留着，也可以在交付前清掉。
- 字幕区：页面上有一条 y=1000 的横向参考线，下面是字幕位置，任何内容的底边都不要低于 y=1000（工具结果里出现 warning 就要挪上去）。
- 视频前段的产品介绍页用 product_card（产品图 + 产品名 + 灰色标签写发布日期或价格，1–2 款）。
  产品图用他在 Photoshop 抠好的透明 PNG，在项目文件夹的「白底图」「白底产品样图」等文件夹里找；不要自动去背景（会留白边），
  找不到抠好的图就先问他。
- 参考素材：list_references 列出他近期的成品文稿（每页类型、文字、缩略图目录）。做某类页面前先看看他以前怎么做的；
  要细看就 open_presentation 打开（会自动复制，不改参考文稿）。
- 视频由他审核后手动导出：3840x2160、60fps、HEVC 或 H.264，文件名「<课题>动画1-<页数>.mov」。
"""


class Server:
    def __init__(self, cfg):
        self.cfg = cfg
        self.ctx = Context(cfg)
        self.out = sys.stdout.buffer

    def send(self, msg):
        data = json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.out.write(data + b"\n")
        self.out.flush()

    def log(self, *a):
        print("[keynote-mcp]", *a, file=sys.stderr, flush=True)

    def translate(self, obj):
        """Map Mac paths back to client paths (Windows) when a path map is configured."""
        if not self.cfg.path_map:
            return obj
        if isinstance(obj, str):
            return self.cfg.to_client(obj)
        if isinstance(obj, list):
            return [self.translate(x) for x in obj]
        if isinstance(obj, dict):
            return {k: (v if k == "data" else self.translate(v)) for k, v in obj.items()}
        return obj

    def handle(self, msg):
        mid = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}
        if method is None:
            return None  # a response from the client; we never send requests
        if mid is None:
            return None  # notification (initialized, cancelled, ...)
        try:
            if method == "initialize":
                want = params.get("protocolVersion")
                ver = want if want in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[1]
                result = {
                    "protocolVersion": ver,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "keynote", "title": "Keynote", "version": __version__},
                    "instructions": INSTRUCTIONS,
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": tool_list(self.cfg)}
            elif method == "tools/call":
                result = self.call_tool(params.get("name"), params.get("arguments") or {})
            elif method in ("resources/list", "resources/templates/list"):
                result = {"resources": []} if method == "resources/list" else {"resourceTemplates": []}
            elif method == "prompts/list":
                result = {"prompts": []}
            else:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "Method not found: %s" % method}}
            return {"jsonrpc": "2.0", "id": mid, "result": result}
        except Exception as e:  # protocol-level failure
            self.log(traceback.format_exc())
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}}

    def call_tool(self, name, args):
        if isinstance(args, dict):
            fixed = {}
            for k, v in args.items():
                if k in ("path", "script_path", "save_to", "save_as", "image") and isinstance(v, str):
                    v = self.cfg.to_local(v)
                elif k == "document":
                    v = self.cfg.map_in(v)
                fixed[k] = v
            args = fixed
        try:
            r = call(self.ctx, name, args)
        except Exception as e:
            self.log("tool %s failed: %s" % (name, e))
            if not isinstance(e, (ValueError, PermissionError, RuntimeError)) and e.__class__.__name__ != "KeynoteError":
                self.log(traceback.format_exc())
            return {"content": [{"type": "text", "text": "错误：%s" % e}], "isError": True}
        content = []
        if isinstance(r, dict) and "__content__" in r:
            content.extend(r.pop("__content__"))
        r = self.translate(r)
        content.insert(0, {"type": "text", "text": json.dumps(r, ensure_ascii=False, indent=1)})
        return {"content": content, "isError": False}

    def serve(self):
        self.log("started v%s, workspace %s" % (__version__, self.cfg.workspace))
        for raw in sys.stdin.buffer:
            line = raw.strip()
            if not line:
                continue
            try:
                msg = json.loads(line.decode("utf-8"))
            except ValueError:
                self.send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
                continue
            if isinstance(msg, list):
                replies = [r for r in (self.handle(m) for m in msg if isinstance(m, dict)) if r]
                if replies:
                    self.send(replies)
            elif isinstance(msg, dict):
                r = self.handle(msg)
                if r:
                    self.send(r)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--version" in argv:
        print(__version__)
        return
    cfg = Config.from_env(argv)
    cfg.ensure_workspace()
    Server(cfg).serve()
