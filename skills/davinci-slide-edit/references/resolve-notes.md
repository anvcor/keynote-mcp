# Resolve 脚本 API 实测笔记（DaVinci Resolve Studio 19，macOS）

## 连接
- 模块路径：`/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules`
- 库：`/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so`
- `scripts/resolve_common.py` 已处理环境变量；`connect()` 返回 `(resolve, project, mediapool)`
- 需要 偏好设置 > 系统 > 常规 > 外部脚本使用 = 本地

## AppendToTimeline(clipInfos)
```python
{"mediaPoolItem": item, "startFrame": a, "endFrame": b, "mediaType": 1(视频)/2(音频),
 "trackIndex": 1, "recordFrame": timeline.GetStartFrame() + 相对帧}
```
- `endFrame` **含**：想要 `[a, b)` 就传 `endFrame=b-1`。传错会和下一段重叠 1 帧，Resolve 会把下一段往后挤并吃掉它的首帧
- 视频：`startFrame/endFrame` 是源视频帧；源和时间线同帧率时 1:1
- 音频：单位是该片段 `GetClipProperty('FPS')` 的帧（mp3 显示 24），放到 60fps 时间线上自动换算
- 一次调用传几百个 clipInfo 没问题，返回 TimelineItem 列表

## 其它
- `timeline.DeleteClips(items, False)`：False = 不波纹删除
- `timeline.DuplicateTimeline(name)`：做备份；`mediapool.DeleteTimelines([tl])` 删临时时间线
- `timeline.CreateSubtitlesFromAudio({})`：Studio 才有；结果在 subtitle 轨 1，`item.GetName()` 是文字，起止帧按该时间线帧率
- `mediapool.CreateEmptyTimeline(name)` 建出的时间线用工程默认帧率（可能是 24），和用户主时间线不一定相同
- `project.GetSetting('timelineFrameRate')` 可能和当前时间线不一致，读 `timeline.GetSetting(...)`
- 音频 TimelineItem：`GetProperty()` 返回空字典，没有 Volume；`GetSourceAudioChannelMapping` 在 19.0 beta 不可用；MediaPoolItem 只有只读的 `GetAudioMapping()` → 音量、声道只能走界面
- `ProjectManager.SaveProject()` 保存

## 音频界面操作（computer-use，需要全屏控制）
1. 点时间线上第一段音频 → `Shift+Z` 缩放到整条时间线可见
2. 按住 cmd 逐个点其余音频段（A1 全部选中，检查器标题变成 "Multiple Clips"）
3. 检查器 > Audio > Volume 输入框：双击 → cmd+A → 输入 `-5` → 回车（显示 -5.00）
4. 在选中的音频段上**右键** > `Clip Attributes...`（不在顶部 Clip 菜单里）
5. Audio 页：Format 下拉选 `Stereo` → 第二行（Right）Source Channel 从 `Mute` 改成 `Embedded Channel 1` → OK
6. 单独选一段再打开 Clip Attributes 复查；cmd+S 保存
7. 做完 `release_full_control`

背景音乐（A2，`add_bgm.py` 铺好之后）：同样 cmd 逐个点选 A2 的几段 → Volume 填 `-15`。生成的 FLAC 已是立体声，不用改 Clip Attributes。复查时先点时间线结尾之后的空白处取消多选，再单独点一段看检查器。

## 幻灯片视频的结构（Keynote 导出，自动播放）
- 每页静止约 3 秒（180 帧@60fps），转场约 25–55 帧
- 空白页：整帧单色，std≈0；夹在两页之间就是"淡出-淡入"
- 章节卡：一页 10 秒 = 空白 3 秒 → 字淡入 ~0.4 秒 → 字 3 秒 → 淡出 → 空白 3 秒。字很小，整帧均值变化不到 0.5 级
- 有的页中间有一两帧闪动（Keynote 动画残留），`analyze_slides.py` 会把间隔 ≤8 帧的相同画面合并
