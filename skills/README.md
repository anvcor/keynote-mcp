# lens-video-script

Claude skill：陪作者写镜头评测 / 光学设计 / 行业评论视频文稿。

- `lens-video-script/` — skill 源文件（SKILL.md + references/ + evals/）
- `lens-video-script.skill` — 打包好的上传件（在 Claude 的 Skills 设置里 Upload skill）
- `lens-video-script-workspace/` — skill-creator 评测迭代记录

2026-09-25：默认口吻改为批判性评论（references/critical-voice.md），新增评论稿骨架（structure-essay.md）与飞书同步流程。

2026-11-03：新增「配合 Keynote」一节：能用 keynote 工具时，逐帧把画面和备注做进 Keynote，收尾统一同步备注；视频由作者在 Keynote 里审核后手动导出。

重新打包：以 `lens-video-script/` 为顶层目录、正斜杠路径压成 zip，排除 `evals/`。

## 许可证

GPL-3.0，见 [LICENSE](LICENSE)。
