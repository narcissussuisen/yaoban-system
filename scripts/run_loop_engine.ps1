# EvoAlpha runner: loop_engine.py
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$libs = Join-Path (Split-Path $root) 'py_libs'
$env:PYTHONPATH = $libs
$py = 'C:\Users\YZP\.workbuddy\binaries\python\envs\default\Scripts\python.exe'
& $py -X utf8 (Join-Path $root 'scripts\loop_engine.py')  *>> (Join-Path $root 'outputs\loop_engine.log')