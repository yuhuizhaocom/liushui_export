' Portable launcher: uses ONLY the interpreter shipped next to this file.
' Package layout (created by tools/build_portable.py, usually on GitHub Actions):
'   <pkg>\run-portable.vbs        <- you double-click this
'   <pkg>\python\pythonw.exe      <- bundled interpreter (tkinter included)
'   <pkg>\app\core\main_gui.py    <- the program (code + platform scripts)
'   <pkg>\app\site-packages       <- playwright and its deps
'   <pkg>\app\runtime\ms-playwright\chromium-XXXX  <- bundled browser
' No install, no registry, no system Python lookup.
Option Explicit

Dim fso, sh, here, py, appDir, site

Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")

here = fso.GetParentFolderName(fso.GetAbsolutePathName(WScript.ScriptFullName))
py = here & "\python\pythonw.exe"
appDir = here & "\app"
site = appDir & "\site-packages"

If Not fso.FileExists(py) Then
    MsgBox "Bundled Python not found." & vbCrLf & "Expected: " & py, 48, "Liushui Export Tool"
    WScript.Quit 1
End If
If Not fso.FolderExists(appDir) Then
    MsgBox "Program folder not found." & vbCrLf & "Expected: " & appDir, 48, "Liushui Export Tool"
    WScript.Quit 1
End If

' cwd = app folder so the app can find core/ and platforms/; PYTHONPATH adds deps.
sh.Environment("PROCESS")("PYTHONPATH") = site & ";" & appDir
sh.CurrentDirectory = appDir
' Second arg 0 = no console window; third arg False = do not wait.
sh.Run """" & py & """ -m core.main_gui", 0, False
