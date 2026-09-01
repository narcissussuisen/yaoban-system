@echo off
chcp 936 >nul
set PYTHONPATH=C:\Users\YZP\WorkBuddy\Claw\方法论与研究文档\py_libs;C:\Users\YZP\WorkBuddy\Claw\方法论与研究文档\yaoban-system\src
"C:\Users\YZP\.workbuddy\binaries\python\envs\default\Scripts\python.exe" -X utf8 "C:\Users\YZP\WorkBuddy\Claw\方法论与研究文档\yaoban-system\scripts\daily_pipeline_v5.py" >> "C:\Users\YZP\WorkBuddy\Claw\方法论与研究文档\yaoban-system\outputs\daily_v5.log" 2>&1
