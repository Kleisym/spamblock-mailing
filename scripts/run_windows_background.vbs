Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
ScriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
RootDir = fso.GetParentFolderName(ScriptDir)

WshShell.CurrentDirectory = RootDir
' Run cmd silently (0 = hide window, false = don't wait for completion)
WshShell.Run "cmd.exe /c python bot.py", 0, False
