# Keynote MCP

> 这是作者为自己的镜头评测视频（Keynote 动画 → 达芬奇剪辑）做的工具，文档里的路径都按作者的 Mac 写的，自己用时换成你的路径。许可证：GPL-3.0。

让 Claude（或其它支持 MCP 的 LLM）直接在这台 Mac 上的 Keynote 里新建、编辑文稿：加页、写字、放图、做神奇移动动画、做章节卡、写演讲者备注、导出 PDF / 逐页图片 / PPTX。

- 只支持 macOS + Keynote（App Store 版，已在 macOS 26.3.1 / Keynote 14.4 上实测）。
- 基本功能零依赖：只用系统自带的 `/usr/bin/python3`（3.9 即可）。加构件动画另外需要 keynote-parser，装在本文件夹的 `.venv` 里（见安装第 3 步）。
- **视频不由工具导出**：做完后由你在 Keynote 里审核效果，再手动导出（见「视频导出」一节）。

## 在写稿流程里的位置

| 步骤 | 谁来做 | 用到的工具 |
|---|---|---|
| 开题、逐帧陪写 `文稿.md` | lens-video-script skill | — |
| 按 `[图N]` 做 Keynote 页、动画 | Claude 调用本工具 | `add_slide` `add_image` `add_text` `magic_move_step` `chapter_card` |
| 把段落写进演讲者备注 | Claude | `set_notes` / `sync_notes_from_script` |
| 检查效果 | Claude 先自查，再由你审核 | `view_slide`（把页面渲染成图片给 Claude 看） |
| 文稿配图 | Claude | `export format=png` |
| 导出动画视频 | **你在 Keynote 里手动导出** | — |
| 达芬奇剪辑 | davinci-slide-edit skill | — |

## 安装

1. 文件夹放在一个固定位置，当前就在 `/Users/anvcor/Downloads/keynote-mcp`。挪了位置的话，把下面配置里的路径一起改掉。
2. 自检：

   ```bash
   /usr/bin/python3 /Users/anvcor/Downloads/keynote-mcp/server.py --version
   ```

   输出 `0.1.0` 就说明能跑。

3. （加构件动画用，已经装好）在本文件夹建 `.venv` 并装 keynote-parser，约 32 MB：

   ```bash
   /usr/bin/python3 -m venv /Users/anvcor/Downloads/keynote-mcp/.venv && /Users/anvcor/Downloads/keynote-mcp/.venv/bin/pip install keynote-parser
   ```

   服务器会自动用到它，Claude 的配置不用改。没装时其它工具照常可用，只有 `add_builds` / `list_builds` 会提示安装。
4. 第一次真正调用 Keynote 时，macOS 会弹窗问「是否允许 Claude（或终端）控制 Keynote」，点「允许」。点错了的话，到 系统设置 > 隐私与安全性 > 自动化 里把开关打开。不需要辅助功能权限。

### 接到 Claude Code

在终端运行（`--scope user` 表示所有项目都能用）：

```bash
claude mcp add keynote --scope user -- /usr/bin/python3 /Users/anvcor/Downloads/keynote-mcp/server.py
```

想改工作目录或允许修改的目录，加 `-e`：

```bash
claude mcp add keynote --scope user -e KEYNOTE_MCP_ROOTS=/Users/anvcor/KeynoteMCP -- /usr/bin/python3 /Users/anvcor/Downloads/keynote-mcp/server.py
```

装好后在 Claude Code 里输入 `/mcp`，能看到 `keynote` 且状态为 connected 即可。

### 接到 Claude Desktop

编辑 `~/Library/Application Support/Claude/claude_desktop_config.json`。这个文件里已经有 `preferences` 等内容，**不要整份替换**，只在最外层加一个 `mcpServers` 键：

```json
{
  "mcpServers": {
    "keynote": {
      "command": "/usr/bin/python3",
      "args": ["/Users/anvcor/Downloads/keynote-mcp/server.py"],
      "env": { "KEYNOTE_MCP_ROOTS": "/Users/anvcor/KeynoteMCP" }
    }
  },
  "preferences": { "…原来的内容保持不动…": true }
}
```

保存后完全退出并重开 Claude Desktop。

## 按你的习惯做了哪些默认设置

2026-11-03 只读统计了移动硬盘上的 78 份 Keynote 文稿（6535 页）和 47 个导出视频，工具的默认值按统计结果设置：

| 你的习惯（统计结果） | 工具里的对应 |
|---|---|
| 65 份用 Basic Black，背景单独改成深灰 #2C2C2C | 默认从你每期的初始文件 `KEYNOTE动画模板` 新建（见下一节）。脚本不能设背景色，新页靠「复制前一页再清空」继承深灰背景 |
| 全部 1920×1080，字体 Helvetica Neue；常用字号：标题 50，正文 24 / 32 / 34 | 说明里要求标题、正文用文本框加在空白页上 |
| 95% 的转场是神奇移动，时长以 0.5 秒为主（其次 0.8 秒），全部点按换页 | `add_slide`、`chapter_card` 默认 0.5 秒神奇移动；`magic_move_step` 默认 0.5 秒 |
| 两页内容之间通常夹一张空白页 | `add_slide` 默认就是加这种空白页 |
| 章节卡：编号「一」76 号、标题 71 号，两个白色居中文本框，前后夹空白页 | `chapter_card` 按这个样式做 |
| 4767 个对象构件动画，主要是线条描绘（2803 个）和文字逐字淡入移动（1827 个）；通常每页第一个点按、其余与上一个同时 | `add_builds` 直接把这两种动画写进文件，出场方式默认按你的习惯 |
| 演讲者备注：6535 页里一条都没有 | 备注只在需要记录 `[图N]`、`[动画]` 时用，可以在交付前清掉 |
| 导出视频：3840×2160、60fps、HEVC 或 H.264，文件名「<课题>动画1-<页数>.mov」，平均每页约 3.8 秒 | 由你手动导出 |
| 课题文件夹里放：.key、导出视频、`配音音频/`（或 `音频/`，1.mp3、2.mp3…）、`Cover/`、`产品样图/`、`白底图/`、`实拍样图/`、`zmx/`、专利 PDF；耳机评测另有 `LoopBack/` | 新课题建议在 `~/KeynoteMCP/<课题>/` 里按同样结构放 |

## 初始模板（KEYNOTE动画模板）

新文稿默认是你每期的初始文件的副本。项目里放的是 `templates/KEYNOTE动画模板.key`，从 `~/Documents/项目素材/KEYNOTE动画模板.key` 复制而来（2025-07-27 版），原文件没有改动。

| 页 | 内容 | Claude 怎么用 |
|---|---|---|
| 1–11 | 深灰空白页 | 直接当内容页或页间空白页用，不够就 `add_slide` |
| 12 | 纵向像差图（5 条波长曲线，线条描绘） | 复制这页，改 F 值、品牌、型号等文字 |
| 13 | 同一张图加色焦移宽度标注（如 67μm），和 12 之间神奇移动 | 同上 |
| 14 | FFT MTF vs Field（10/20/40/80lp/mm、子午/弧矢、MTF 50% 线；曲线线条描绘、图例逐字淡入移动） | 同上 |
| 15 | 开场封面：品牌标志 +「Or」+ 大字型号 + 镜头大图 | 改型号，F 值数字用 `highlight` 标红 |
| 16 | 光学结构图：镜片形状 + 光线（线条描绘） | 改标题 |
| 17 | 镜片形状素材 | 拼结构图或做神奇移动过渡 |

- `create_presentation` 会把 `templates/KEYNOTE动画模板.json` 里的这份说明（每页是什么、哪些文字对象可以改）一起返回给 Claude。
- 改文字时，F 值数字可以用 `highlight` 标成你的红色 #FC4B3E。
- 复制样例页时，页上的构件动画会一起保留。
- **改不了的**：曲线和光线是 Keynote 原生的贝塞尔形状，脚本只能改位置、大小、透明度，改不了形状，新曲线仍由你粘贴；标题里的「β=…」是公式图片，改不了数值。
- 做完后，Claude 会删掉没用到的样例页和多余的空白页。
- 你更新了初始文件以后，把新版复制到 `templates/KEYNOTE动画模板.key` 覆盖旧的；页的顺序有变化的话，同时改一下旁边的 `.json` 说明。也可以用环境变量 `KEYNOTE_MCP_TEMPLATE` 指向别的模板。

## 按 Zemax 图自动描 MTF 曲线

你平时的做法：把 Zemax 导出的「FFT MTF vs. Field」图当底图放在图表页最底层，对齐坐标轴、透明度设为 0，再用钢笔沿着曲线描。`trace_mtf` 把这一步自动化了：

1. 用这一页最底层的旧底图，加上它的位置和识别出的坐标轴，算出图表在页面上的「数据框」。
2. 识别新的 Zemax 图：
   - 按 Zemax 的默认颜色区分 6 个频率；
   - 连成长线段的是子午实线，零散小点是弧矢点线；
   - 视场 0 附近被实线盖住的点线，用实线的数值补上。
3. 把数据换算进数据框，拟合成平滑的贝塞尔曲线，每条约 7 到 18 个节点，和数据的最大偏差约 1 到 3 点。
4. 按颜色（10/20/40/80/160/320 线对）和虚实，写进这一页现成的曲线。颜色、粗细、虚实和线条描绘动画都不变。
5. 底图换成这张新图，按坐标轴对齐，透明度保持不变，方便你对照检查。

**核对过**：用 85MIX2 第 43 页的同一张 Zemax 图自动描，和你手描的曲线相比，子午实线平均差 0.7 到 1.8 点（整页宽 1920 点）。

**用法**：复制模板第 14 页（或任何一张已有的 MTF vs Field 图表页），对 Claude 说「用这张 Zemax 图描第 N 页」。

- 模板这页只有 10/20/40/80 四组曲线，160、320 会列在 `not_on_slide` 里跳过。
- 镜头的最大视场不是 21.6 mm 时，传 `x_max`，并把横轴刻度文字改掉。
- `read_zemax_mtf` 只读出数值（中心、0.5、0.7 视场和边缘，也可以存整条曲线的 CSV），写稿引用数据时用。
- 改之前自动备份到 `.cache/描曲线前备份/`。
- 目前只支持 Zemax 的 FFT MTF vs Field 图；纵向像差、光路图等还是手描。

### .key 文件里一条曲线是怎么存的

你用钢笔画的每条曲线，在 .key 里是一个「形状」对象，由四部分组成。以 85MIX2 第 43 页 80 线对子午黄线为例：

| 部分 | 内容 | 例子 |
|---|---|---|
| 外框 | 左上角位置、宽高、旋转角度。曲线先按未旋转的外框存，再绕外框中心转过去 | (293.7, 377.8)，1384.2 × 212.9，351.67° |
| 路径（可编辑贝塞尔） | 一串节点，每个节点 = 节点位置 + 进入控制柄 + 离开控制柄 + 类型（尖角或平滑）。坐标相对外框 | 13 个节点；第 2 个在 (159.8, 3.2)，控制柄 (106.6, −0.9) / (203.9, 6.6)，平滑 |
| 样式 | 线条颜色、粗细、虚实，存在可共用的样式对象里 | #fff056，2 点，实线 = 子午 |
| 构件动画 | 独立的动画对象，按编号指向这条曲线 | 线条描绘 |

`trace_mtf` 只改路径和外框（旋转归零），另外三部分原样保留。

## 文件放在哪、怎样保护原稿

- **工作目录**默认是 `~/KeynoteMCP`（`KEYNOTE_MCP_ROOTS` 里的第一个目录）。新建的文稿、没指定位置的导出都放在这里。
- **只能在允许的目录里保存和导出**。允许的目录由 `KEYNOTE_MCP_ROOTS` 设置，多个目录用冒号 `:` 分隔，默认只有 `~/KeynoteMCP`。
- **移动硬盘上的原稿不会被改**：
  - `open_presentation` 打开允许目录之外的 `.key`（比如 `/Volumes/移动硬盘/…`）时，会先复制到 `~/KeynoteMCP/副本/`，再打开副本。
  - 你自己在 Keynote 里开着的原稿，工具只能读、不能改。
  - 审核满意后，用不用副本替换原稿由你决定。
- 副本放在本机磁盘上，也就避开了 exFAT 盘上自动保存副本越堆越多的问题。复制前会检查剩余空间。
- 新建文稿时会立刻保存到指定路径。否则 Keynote 会自动把它存成 iCloud 云盘里的「Untitled.key」。

## 工具一览

| 工具 | 作用 |
|---|---|
| `keynote_status` | Keynote 是否在运行、开着哪些文稿、哪些受保护、允许写入的目录 |
| `list_themes` | 可用主题 |
| `create_presentation` | 新建并保存文稿：默认从你的初始模板 KEYNOTE动画模板 新建，也可以用 `theme` 指定 Keynote 主题 |
| `open_presentation` / `save_presentation` / `close_presentation` | 打开（必要时先复制）、保存或另存、关闭 |
| `import_pptx` | 把 .pptx 转成 .key（原文件不动）：平滑转成神奇移动，逐页列出 `[图N]`、`[动画]` 说明、PPT 转场和动画，以及 Mac 上缺的字体 |
| `get_deck` | 整份文稿：每页版式、标题、对象数、备注（含 `[图N]`）、转场 |
| `get_slide` | 某页所有对象：编号、类型、位置、尺寸、文字、字体字号颜色、图片文件、透明度 |
| `view_slide` | 把某页渲染成图片返回给 Claude，附对象列表 |
| `add_slide` / `duplicate_slide` / `delete_slides` / `move_slide` / `set_slide` | 页面操作。`add_slide` 默认复制前一页再清空，保留深灰背景 |
| `add_text` / `add_image` / `add_shape` / `add_line` / `add_table` | 加对象（不给位置就居中） |
| `update_item` / `delete_items` | 改对象（移动、缩放、旋转、透明度、改字、换图、锁定；`highlight` 把一段文字标红）、删对象 |
| `set_notes` | 写演讲者备注 |
| `sync_notes_from_script` | 读 `文稿.md`，把每个 `[图N：…]` 后的段落写进对应页的备注 |
| `set_transition` | 转场：神奇移动、溶解等 40 多种，时长、延迟、自动或点按 |
| `add_builds` | 加构件动画：线条描绘、逐字淡入移动；出场方式可选点按、与上一个同时、在上一个之后 |
| `list_builds` | 列出每页已有的构件动画 |
| `trace_mtf` | 按 Zemax FFT MTF vs Field 导出图自动描曲线，写进图表页现成的曲线并换好底图 |
| `read_zemax_mtf` | 只读出 Zemax MTF 图里的数值，可存 CSV |
| `magic_move_step` | 一步神奇移动：复制当前页 → 改新页上的对象 → 原页转场设为神奇移动 |
| `chapter_card` | 章节卡：空白 → 编号 + 标题（76 / 71 号，白色居中）→ 空白，0.5 秒神奇移动 |
| `image_slides` | 一批图片直接生成一组页 |
| `export` | PDF（可带备注）、逐页 PNG/JPEG/TIFF、PPTX；可以只导出指定页 |

坐标单位是点，原点在左上角；页号和对象编号都从 1 开始。删除对象后，后面对象的编号会前移。

## 用法示例（对 Claude 说）

- 「在 ~/KeynoteMCP 新建一份黑底的《美科50F1.4动画》，第一页写标题。」
- 「第 5 页放这张结构图 `/Users/anvcor/…/layout.png`，宽 1200，下面加一行白色说明。」
- 「把第 5 页做成神奇移动：结构图缩到左边，说明淡出，右边写『4 组 7 片，尾部一片非球面』。」
- 「在第 8 页后面插一个章节卡『二 性能仿真与测量』。」
- 「按 `美科50F1.4/文稿.md` 把每段写进对应页的备注，先预览对应关系。」
- 「导出第 5 到第 9 页的 PNG，给图文版当配图。」

lens-video-script 里每段前的 `[图N：…]`，可以直接作为页的备注开头。`sync_notes_from_script` 默认就是按备注里的 `[图N]` 标签去对应页。

## 动画：能做什么、不能做什么

- **能做**：所有转场，包括神奇移动和溶解；对象的位置、尺寸、旋转、透明度、文字、换图。你 95% 的转场是神奇移动，这部分都能用脚本完成。
- **构件动画**：Keynote 的脚本接口里没有这一项，而你用得很多（74 份文稿里 4767 个）。`add_builds` 绕开了这个限制，直接把动画写进 .key 文件：
  - 支持你最常用的两种：线条描绘（`line_draw`）和逐字淡入移动（`fade_move`），合计占你全部动画的 97%。
  - 写法照搬你自己文稿里的数据结构。实测 Keynote 能打开、重新保存后动画还在；导出的测试视频里，线条确实是一点点画出来的，文字从左侧淡入滑进来。
  - 过程是保存、关闭、写入、重新打开文稿，改之前自动备份到工作目录的 `.cache/加动画前备份/`，保留最近 10 份。
  - 其它效果（飞入、溶解、退出动画等）还没做，需要时在 Keynote 的「动画」面板里手动加。复制带动画的页时动画会保留。
  - 依赖 keynote-parser 对 Keynote 文件格式的解析，目前支持到 Keynote 14.5。Keynote 大版本更新后，要等 keynote-parser 跟进。
- **形状填充色**：脚本接口里是只读的，需要色块时用图片或模板里现成的形状。

## 视频导出

默认由你在 Keynote 里审核后手动导出：文件 > 导出到 > 影片。你过去的设置是 3840×2160、60fps；回放选「自动播放」，每页停留时间按习惯设（例如 3 秒）。

工具里保留了自动导出视频的代码，默认关闭。确实需要时，在配置的 `env` 里加 `KEYNOTE_MCP_ALLOW_MOVIE=1`，`export` 就会多出 `format=movie`。这台机器上实测过的结论：

- 自动转场的页停留时间等于转场延迟；点按换页的页停留时间由 `slide_seconds` 指定（不指定时 Keynote 默认 5 秒）；转场时长另外加上，最后一页的转场也算在内。按这个规则推算的时长和实际视频只差 1–2 帧。
- 自动导出时会在视频旁边写一份 `.slides.json`，记录每页在视频里的起止时间和对应的图号，可以给达芬奇对位用。
- Keynote 只在 `resolution=native`（按文稿原始尺寸导出）时使用指定的编码（HEVC、ProRes）；4K 预设一律是 H.264。

## Windows

**Keynote 没有 Windows 版**，也不能把 Keynote.app 拷到 Windows 上运行：它是 macOS 专用程序，依赖 80 多个 macOS 系统组件。几条替代路线的评估见 [方案.md](方案.md)。有两条可行的路：

**路线一：Windows 上用 PowerPoint 创作，Mac 上重制 .key**（推荐，不需要 SSH，详见 [方案.md 第 4.1 节](方案.md)）

1. 在 Windows 上用 PowerPoint 做主体内容，Claude 可以用 pptx skill 帮忙。每页备注写 `[图N：…]` 和文稿段落；动画用「平滑」转场表示，或者在备注里写一行 `[动画] …`。
2. 把 .pptx 放进 Mac 的 `~/KeynoteMCP/`。
3. 对 Claude 说「用 import_pptx 导入 xxx.pptx」。工具会转成 .key，平滑自动变成神奇移动，再按 `[动画]` 说明逐页重做、同步备注。
4. 人工精修后手动导出视频。

在这台 Mac 上实测过（测试文件由 `test/make_test_pptx.py` 用 python-pptx 生成，模拟 Windows 上 Claude 的做法）：

- **PowerPoint 的平滑导入后直接变成神奇移动**，对象真的会动（从测试视频里截取转场中间一帧确认过）。平滑的对象请在前后两页用同一个名字，名字以 `!!` 开头时强制匹配。
- 淡出变成溶解；进入动画变成 Keynote 的构件动画，导出视频时每个构件大约多 2.5 秒。
- 备注、`[图N]`、`[动画]` 都能保留。
- **python-pptx 写备注会让 Keynote 报「文件格式无效」并拒绝导入**（缺少 `notesMasterIdLst`）。`import_pptx` 会自动在临时副本里修好再导入。用 PowerPoint 另存一遍也能解决。
- PowerPoint 默认 16:9 页面导入后是 960×540，坐标是 Keynote 1920×1080 文稿的一半。想要 1920×1080，就在 PowerPoint 的「设计 > 幻灯片大小 > 自定义」里设成 26.667 × 15 英寸。按 4K 导出时都是矢量重绘，文字不会糊，图片清晰度取决于原图像素。
- 微软雅黑、Calibri 这些 Windows 字体，这台 Mac 上没有，会被替换。建议两边装同一套字体，例如思源黑体。`import_pptx` 会列出缺的字体。
- 如果 Keynote 弹出「无法导入」对话框，要在 Keynote 里点「好」关掉，否则后续所有操作都会卡住。

**路线二：Windows 上的 Claude 通过 SSH 远程调用这台 Mac 上的同一个服务**，功能和在 Mac 上用完全一样，但 Mac 要一直开着。

服务端已经支持路径映射，Windows 那边的 `Z:\导出\a.pdf` 会自动对应到 Mac 上的 `~/KeynoteMCP/导出/a.pdf`，返回结果里的路径也会换回 Windows 写法。在这台 Mac 上用模拟的 Windows 路径测试通过；**真实的 Windows 连接还没有实测**。步骤如下：

1. **Mac 上**（需要你本人操作）：
   - 系统设置 > 通用 > 共享，打开「远程登录」。
   - 再打开「文件共享」，把 `~/KeynoteMCP` 共享出去。
   - Mac 要保持开机、登录桌面、不睡眠。
2. **Windows 上**：
   - 把 Mac 的共享文件夹映射成网络驱动器，比如 `Z:`。
   - 在 PowerShell 里运行 `ssh-keygen`，把生成的 `id_ed25519.pub` 内容追加到 Mac 的 `~/.ssh/authorized_keys`。
   - 运行 `ssh anvcor@<Mac名>.local echo ok`，确认不用输密码就能登录。
3. **第一次授权**：在 Windows 上运行下面这条命令，同时有人在 Mac 前面。Mac 上会弹出允许控制 Keynote 的提示，点「允许」：

   ```bash
   ssh anvcor@<Mac名>.local "osascript -e 'tell application \"Keynote\" to get version'"
   ```

   如果没有弹窗、直接报 -1743，到 Mac 的 系统设置 > 隐私与安全性 > 自动化 里找 sshd 相关条目打开。
4. **Windows 上的 Claude Desktop** 配置（`%APPDATA%\Claude\claude_desktop_config.json`）：

   ```json
   {
     "mcpServers": {
       "keynote": {
         "command": "ssh",
         "args": ["-T", "anvcor@<Mac名>.local",
                  "/usr/bin/python3", "/Users/anvcor/Downloads/keynote-mcp/server.py",
                  "--path-map", "Z:=/Users/anvcor/KeynoteMCP"]
       }
     }
   }
   ```

   Windows 上的 Claude Code：

   ```bash
   claude mcp add keynote -- ssh -T anvcor@<Mac名>.local /usr/bin/python3 /Users/anvcor/Downloads/keynote-mcp/server.py --path-map Z:=/Users/anvcor/KeynoteMCP
   ```

   这里写成 `Z:` 而不是 `Z:\`，是为了避免反斜杠被远程 shell 吃掉。

## 排错

| 现象 | 处理 |
|---|---|
| 报错里有 -1743 / 没有自动化权限 | 系统设置 > 隐私与安全性 > 自动化，允许对应程序控制 Keynote |
| 报错里有 -1712 / 超时 | Keynote 里可能有对话框挡着（字体缺失、版本提示等），先手动关掉 |
| 提示「不在允许修改的目录里」 | 用 `open_presentation` 打开（会自动复制），或把目录加进 `KEYNOTE_MCP_ROOTS` |
| 提示「没有这个版式」 | 不同主题的版式名不同，看 `get_deck` / `create_presentation` 返回的 `layouts` |
| 想看出错时实际执行的 AppleScript | 配置里加 `KEYNOTE_MCP_DEBUG=1`，脚本会打印到 stderr |

## 测试

```bash
python3 /Users/anvcor/Downloads/keynote-mcp/test/e2e_test.py
```

这个测试会通过 MCP 协议驱动真实的 Keynote，一共 40 多个步骤：

- 在 `test/` 里新建 `测试文稿.key`，依次做标题页、章节卡、放图、神奇移动、表格。
- 从 `test/文稿.md` 同步备注，渲染页面图片。
- 检查三项拒绝：往允许目录之外写文件、超出范围的页号、不存在的转场效果。
- 导出 PDF、PNG、PPTX，结果都在 `test/out/`。
- 导入 `test/素材/PPT导入测试.pptx`，检查平滑是否变成了神奇移动。
- 给对象加线条描绘和逐字淡入移动，重新打开、另存、再保存后检查动画还在。
- 用 `test/素材/85MIX inf FFTMTFvsField.jpg`（85MIX2 的 Zemax 导出图）描模板第 14 页的 MTF 曲线。

加上 `--movie` 参数会额外测试默认关闭的视频导出。

## 文件结构

```
keynote-mcp/
  server.py              入口
  keynote_mcp/
    server.py            MCP 协议（stdio、JSON-RPC）
    tools.py             各个工具的实现
    osa.py               调用 osascript；用户文字只通过参数传入，不拼进脚本
    config.py            允许目录、Windows 路径映射
    script_md.py         解析 文稿.md 里的 [图N]
    pptx_info.py         读 .pptx（备注、转场、动画、字体），修 python-pptx 的备注问题
    builds.py            把构件动画写进 .key（需要 .venv 里的 keynote-parser）
    zemax_mtf.py         识别 Zemax FFT MTF vs Field 图里的曲线（需要 .venv 里的 Pillow）
    curves.py            把识别出的曲线拟合成贝塞尔，写进图表页的曲线
  .venv/                 keynote-parser（只给 add_builds / list_builds 用）
    mp4info.py           读视频时长、帧率（只在开启视频导出时用到）
  templates/             你的初始模板 KEYNOTE动画模板.key 和每页说明 .json
  test/                  真机测试脚本、测试素材和输出
  方案.md                 方案和 Windows 评估
```

## 许可证

GPL-3.0，见 [LICENSE](LICENSE)。
