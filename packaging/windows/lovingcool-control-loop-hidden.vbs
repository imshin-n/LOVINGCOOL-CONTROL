' lovingcool-control :: packaging\windows\lovingcool-control-loop-hidden.vbs
' =============================================================
' Launches lovingcool-control-loop.bat with its console window hidden (windowStyle
' 0), so the restart-loop wrapper doesn't leave a stray cmd.exe box
' sitting around. This is what install_service.bat actually points the
' scheduled task at — not the .bat directly.

Set objShell = CreateObject("WScript.Shell")
scriptDir = Left(WScript.ScriptFullName, Len(WScript.ScriptFullName) - Len(WScript.ScriptName))
batPath = scriptDir & "lovingcool-control-loop.bat"
objShell.Run """" & batPath & """", 0, False
