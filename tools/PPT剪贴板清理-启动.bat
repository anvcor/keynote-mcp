@echo off
chcp 65001 >nul
rem Watch the clipboard in the background: PowerPoint copies lose their web formats (SVG, ppt/slides),
rem so Feishu and other web editors paste the picture, and pasting back into PowerPoint stays editable.
start "" pythonw "%~dp0ppt_clip_cleaner.py"
echo 已在后台启动（已经在运行时会自动退出，不会重复）。日志：%~dp0ppt_clip_cleaner.log
timeout /t 3 >nul
