# EvoAlpha runner: collect_tick_daily.py
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$libs = Join-Path (Split-Path $root) 'py_libs'
$env:PYTHONPATH = $libs
$py = 'C:\Users\YZP\.workbuddy\binaries\python\envs\default\Scripts\python.exe'
& $py -X utf8 (Join-Path $root 'scripts\collect_tick_daily.py')  *>> (Join-Path $root 'outputs\tick_collect.log')