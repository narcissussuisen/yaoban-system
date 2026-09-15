@echo off
chcp 936 >nul
rem R0.10 修复(2026-09-12): 原第 3 行硬编码绝对路径且缺少 EvoAlpha 段
rem ("...\方法论与研究文档\yaoban-system\...")，EvoAlpha 目录收敛后必然失效。
rem 改为 %~dp0 定位同目录脚本 —— 目录结构再变也不受影响，且不再依赖 root.txt 的 BOM 状态。
"C:\Users\YZP\.workbuddy\binaries\python\envs\default\Scripts\python.exe" "%~dp0board_server.py" --port 8765
