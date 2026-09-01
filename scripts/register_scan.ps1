# Legacy scheduler entry point intentionally disabled.
# Superseded paths, times, frequencies, or actions here could overwrite the P0 production schedule.
$canonical = Join-Path $PSScriptRoot 'register_p0_schedule.ps1'
Write-Error ("Legacy scheduler '$($MyInvocation.MyCommand.Name)' is disabled. Use the canonical registrar: $canonical")
exit 64
