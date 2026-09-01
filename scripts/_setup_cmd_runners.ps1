$ErrorActionPreference = 'Stop'
$root = 'C:UsersYZPWorkBuddyClaw方法论与研究文档yaoban-system'
$libs = 'C:UsersYZPWorkBuddyClaw方法论与研究文档py_libs'
$py = 'C:UsersYZP.workbuddyinariespythonenvsdefaultScriptspython.exe'
$specs = @(
  ,@('run_tick.cmd', 'collect_tick_daily.py', '', 'tick_collect.log')
  ,@('run_loop_engine.cmd', 'loop_engine.py', '', 'loop_engine.log')
  ,@('run_daily.cmd', 'daily_pipeline.py', '--update', 'daily.log')
  ,@('run_candidates.cmd', 'daily_candidates.py', '', 'candidates.log')
)
foreach ($s in $specs) {
  $line2 = "set PYTHONPATH=" + $libs
  $line3 = '"' + $py + '" -X utf8 "' + $root + 'scripts' + $s[1] + '" ' + $s[2] + ' >> "' + $root + 'outputs' + $s[3] + '" 2>&1'
  $content = '@echo off' + [char]13 + [char]10 + $line2 + [char]13 + [char]10 + $line3
  Set-Content -Path (Join-Path $root ('scripts' + $s[0])) -Value $content -Encoding Default -NoNewline
  Write-Host ('wrote ' + $s[0])
}
