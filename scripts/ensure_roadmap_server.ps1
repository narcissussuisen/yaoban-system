# EvoAlpha roadmap dashboard server - ensure running (start if not listening).
#
# ASCII-ONLY BY DESIGN: a no-BOM UTF-8 .ps1 with Chinese comments gets mis-decoded
# as GBK by PowerShell 5.1 and can silently swallow the following line.
#
# 2026-09-14 (user ruling, scheduled-task audit): the every-5-min keepalive task
# EvoAlphaRoadmapServer was RETIRED - the dashboard now starts on demand only.
# Current entry point: scripts\open_dashboard.vbs (self-locating, hidden window),
# which calls this guard and then opens the browser. Running this file by hand is
# also fine; it stays idempotent (already listening => exit 0, no new process).

$ErrorActionPreference = "Continue"

$Port    = 8790
$Repo    = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Server  = Join-Path $Repo "tools\roadmap_server.py"
$LogDir  = Join-Path $Repo "outputs\dashboard"
$GuardLog = Join-Path $LogDir "ensure_roadmap_server.log"

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

function Write-Guard($msg) {
    $line = "[" + (Get-Date).ToString("yyyy-MM-dd HH:mm:ss") + "] " + $msg
    Add-Content -LiteralPath $GuardLog -Value $line -Encoding UTF8
}

if (-not (Test-Path -LiteralPath $Server)) {
    Write-Guard ("FAIL server script missing: " + $Server)
    exit 2
}

# --- is something already listening? ---
$listening = $false
try {
    $c = New-Object System.Net.Sockets.TcpClient
    $c.Connect("127.0.0.1", $Port)
    $listening = $c.Connected
    $c.Close()
} catch {
    $listening = $false
}

if ($listening) {
    Write-Guard ("OK already listening on " + $Port)
    exit 0
}

# --- locate pythonw (multi-candidate, never hardcode a managed version dir) ---
$candidates = @()
$candidates += (Get-ChildItem "C:\Users\YZP\.workbuddy\binaries\python\versions\*\pythonw.exe" -ErrorAction SilentlyContinue |
                Sort-Object FullName -Descending | ForEach-Object { $_.FullName })
$candidates += "C:\Program Files\Python314\pythonw.exe"
$candidates += (Get-Command pythonw.exe -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })

$pyw = $null
foreach ($c in $candidates) {
    if ($c -and (Test-Path -LiteralPath $c)) { $pyw = $c; break }
}

if (-not $pyw) {
    Write-Guard "FAIL no pythonw.exe found"
    exit 3
}

# --- start detached, hidden ---
try {
    Start-Process -FilePath $pyw -ArgumentList ('"' + $Server + '" --port ' + $Port) -WindowStyle Hidden
    Write-Guard ("STARTED via " + $pyw)
} catch {
    Write-Guard ("FAIL start: " + $_.Exception.Message)
    exit 4
}

Start-Sleep -Seconds 3

$ok = $false
try {
    $c2 = New-Object System.Net.Sockets.TcpClient
    $c2.Connect("127.0.0.1", $Port)
    $ok = $c2.Connected
    $c2.Close()
} catch {
    $ok = $false
}

if ($ok) { Write-Guard "VERIFIED listening after start"; exit 0 }
Write-Guard "WARN started but port not accepting yet"
exit 5
