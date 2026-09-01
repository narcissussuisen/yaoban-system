# Yaoban runner: daily_pipeline_v5.py（R7' 上线基板：信号输出，人工确认执行）
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$libs = Join-Path (Split-Path $root) 'py_libs'
$env:PYTHONPATH = $libs + ';' + (Join-Path $root 'src')
$py = 'C:\Users\YZP\.workbuddy\binaries\python\envs\default\Scripts\python.exe'
& $py -X utf8 (Join-Path $root 'scripts\daily_pipeline_v5.py') *>> (Join-Path $root 'outputs\daily_v5.log')