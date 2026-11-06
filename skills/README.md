# skills

整条流程里用到的两个 Claude skill。每个都有源文件夹和打包好的上传件（`.skill`）。

| skill | 做什么 | 源文件夹 | 上传件 |
|---|---|---|---|
| lens-video-script | 用作者的口吻陪写镜头评测 / 光学设计 / 行业评论视频文稿 | `lens-video-script/`（SKILL.md + references/ + evals/） | `lens-video-script.skill` |
| davinci-slide-edit | 用 DaVinci Resolve 脚本 API 按文稿和配音剪幻灯片视频，按章节铺背景音乐，成片导出后生成字幕和 B 站章节 | `davinci-slide-edit/`（SKILL.md + references/ + scripts/） | `davinci-slide-edit.skill` |

`lens-video-script-workspace/` 是 lens-video-script 的 skill-creator 评测迭代记录。

安装方法见仓库根目录 [README.md](../README.md) 的「两个 skill 怎么装」。

## 重新打包

以技能文件夹为顶层目录、正斜杠路径压成 zip，扩展名用 `.skill`。lens-video-script 不打包 `evals/`，davinci-slide-edit 不打包 `__pycache__/`。先删掉旧的 `.skill`，因为 zip 会往旧文件里追加：

```bash
cd skills
rm -f lens-video-script.skill && zip -r -X lens-video-script.skill lens-video-script -x 'lens-video-script/evals/*'
rm -f davinci-slide-edit.skill && zip -r -X davinci-slide-edit.skill davinci-slide-edit -x '*/__pycache__/*' '*.DS_Store'
```

## 更新记录

lens-video-script：

- 2026-09-25：默认口吻改为批判性评论（references/critical-voice.md），新增评论稿骨架（structure-essay.md）与飞书同步流程。
- 2026-11-03：新增「配合 Keynote」一节：能用 keynote 工具时，逐帧把画面和备注做进 Keynote，收尾统一同步备注；视频由作者在 Keynote 里审核后手动导出。

davinci-slide-edit：

- 2026-09-25：第一版，以 lens-bench 那期作者手剪的开头为样板。
- 2026-11-06（《35之月》）：
  - 跳页：cue 加 `via_in`，文稿顺序和幻灯片顺序不一致时画面跟着配音走。
  - `refine_holds.py`：收紧静止段，重复铺的静止画面末尾不再滑动。
  - 新建工程的设置写进流程；配音来自 TTS 时直接用段落时间表。
  - `add_bgm.py`：每章一首背景音乐，对齐响度、淡入淡出，铺到 A2。
  - `tts_srt.py`：成片导出后，按 TTS 配音的原文生成同名 SRT，代替剪映语音识别。
  - `chapters.py`：从章节卡 cue 和文稿标题生成 B 站章节文本。

## 许可证

GPL-3.0，见仓库根目录 [LICENSE](../LICENSE)。
