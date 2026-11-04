@echo off
chcp 65001 >nul
powershell -NoProfile -Command "$p = Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*ppt_clip_cleaner.py*' }; if ($p) { $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; 'stopped ' + $_.ProcessId } } else { 'not running' }"
timeout /t 3 >nul
