' EvoAlpha roadmap dashboard (8790) - on-demand launcher.
'
' 2026-09-14 (user ruling, scheduled-task audit): replaces the every-5-min keepalive
' task EvoAlphaRoadmapServer with a manual entry point. Double-click this file to
' (1) make sure the dashboard server is listening (idempotent) and (2) open the browser.
' Once started the server stays resident, so later clicks only open the browser.
'
' ASCII-ONLY BY DESIGN, and self-locating via WScript.ScriptFullName - a .vbs holding a
' hardcoded non-ASCII path would be mis-decoded by the script host on this machine.
' Hidden window (0): this project must never show a console popup.
'
' NOTE on the relative script path below: the repo path contains non-ASCII characters.
' Handing that absolute path to `powershell.exe -File` on the command line was measured
' to arrive mangled (a GBK/UTF-8 mix-up), so instead we chdir into the repo and pass a
' pure-ASCII RELATIVE path. The guard resolves its own location from $PSScriptRoot.

Option Explicit

Dim fso, sh, here, repo, guardName, cmd, rc

Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")

here      = fso.GetParentFolderName(WScript.ScriptFullName)   ' ...\yaoban-system\scripts
repo      = fso.GetParentFolderName(here)                     ' ...\yaoban-system
guardName = "ensure_roadmap_server.ps1"

If Not fso.FileExists(here & "\" & guardName) Then
  sh.Popup "EvoAlpha dashboard: missing " & here & "\" & guardName, 6, "EvoAlpha", 16
  WScript.Quit 1
End If

sh.CurrentDirectory = repo

cmd = "powershell.exe -NoProfile -WindowStyle Hidden -NonInteractive" _
    & " -ExecutionPolicy Bypass -File ""scripts\" & guardName & """"

' 0 = hidden window, True = wait (the guard spends ~3s verifying the port)
rc = sh.Run(cmd, 0, True)

If rc = 0 Then
  sh.Run "http://127.0.0.1:8790/", 1, False
Else
  sh.Popup "EvoAlpha dashboard did not start (rc=" & rc & ")." & vbCrLf & _
           "See outputs\dashboard\ensure_roadmap_server.log", 8, "EvoAlpha", 48
End If
