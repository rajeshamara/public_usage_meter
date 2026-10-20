Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
folder = fs.GetParentFolderName(WScript.ScriptFullName)
python = folder & "\.venv\Scripts\pythonw.exe"
If Not fs.FileExists(python) Then
  python = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python313\pythonw.exe"
End If
If Not fs.FileExists(python) Then
  MsgBox "Create the project's .venv or install Python 3.13. See README.md for setup.", 16, "Usage Meter"
Else
  shell.Run Chr(34) & python & Chr(34) & " " & Chr(34) & folder & "\usage_meter.py" & Chr(34), 0, False
End If
