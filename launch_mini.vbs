' Launches the mini widget without any console window.
Set sh = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
here = fs.GetParentFolderName(WScript.ScriptFullName)
If fs.FileExists(here & "\.protected-launch") Then
  sh.Run """" & fs.GetSpecialFolder(1) & "\schtasks.exe"" /Run /TN ""MonitorWidget-Launch""", 0, False
  WScript.Quit
End If
sh.Run """C:\Program Files\PowerShell\7\pwsh.exe"" -NoProfile -STA -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & here & "\widget.ps1""", 0, False
