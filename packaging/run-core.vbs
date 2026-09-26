' Core launcher: for the build that ships NO dependencies (run-core.bat is the same
' thing with a console, for troubleshooting).
'
' It differs from run-portable.vbs in one thing only: it does not expect an interpreter
' inside the package. It looks for a Python that already lives on this computer:
'   (1) %WINDIR%\pyw.exe                            the py launcher, resolves registered Pythons
'   (2) pythonw.exe on PATH                         venv / conda / Store install
'   (3) %LocalAppData%\Programs\Python\Python3xx    python.org per-user default
' A zero-byte pythonw.exe under WindowsApps is Microsoft's "not installed yet" stub that
' opens the Store, so it is skipped - running it would just look like a silent crash.
'
' Deliberately NOT in this file: any check or install of playwright / Chromium. That
' lives in core/main_gui.check_dependencies(), so the same question can never get two
' different answers in two places.
Option Explicit

Dim fso, sh, here, appDir, pyw, py, diag

Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")

here = fso.GetParentFolderName(fso.GetAbsolutePathName(WScript.ScriptFullName))
appDir = here & "\app"

pyw = FindPython("pythonw.exe", "pyw.exe")
py = FindPython("python.exe", "py.exe")

diag = False
If WScript.Arguments.Count > 0 Then
    If LCase(WScript.Arguments(0)) = "--print-python" Then diag = True
End If
If diag Then
    ' Report what was found and stop: no prompt, no GUI window. Used by the tests and by
    ' support. It runs BEFORE the "is the package intact" check on purpose - a modal
    ' MsgBox in a diagnostic branch hangs whoever called it (that is exactly how this
    ' file once froze a test run: the copy under packaging\ has no app\ next to it).
    WScript.Echo "pythonw=" & pyw
    WScript.Echo "python=" & py
    WScript.Quit 0
End If

If Not fso.FolderExists(appDir) Then
    MsgBox "Program folder not found." & vbCrLf & "Expected: " & appDir, 48, "Liushui Export Tool"
    WScript.Quit 1
End If

If pyw = "" And py = "" Then
    MsgBox "This build needs Python 3.10 or newer on this computer, and none was found." & _
           vbCrLf & vbCrLf & _
           "Either install Python from python.org (tick ""Add python.exe to PATH"")," & vbCrLf & _
           "or use the FULL build instead: it carries its own Python, playwright and" & vbCrLf & _
           "Chromium and needs nothing installed." & vbCrLf & vbCrLf & _
           "Searched:" & vbCrLf & _
           "  " & sh.ExpandEnvironmentStrings("%WINDIR%") & "\pyw.exe" & vbCrLf & _
           "  pythonw.exe on PATH" & vbCrLf & _
           "  " & sh.ExpandEnvironmentStrings("%LocalAppData%") & "\Programs\Python", _
           48, "Liushui Export Tool"
    WScript.Quit 1
End If

If pyw = "" Then pyw = py        ' console interpreter as last resort: a window will show

' cwd = app folder so the program finds core/ and platforms/. No site-packages here:
' playwright is expected to be installed into the interpreter we just picked.
sh.Environment("PROCESS")("PYTHONPATH") = appDir
sh.CurrentDirectory = appDir
' Second arg 0 = no console window; third arg False = do not wait.
sh.Run """" & pyw & """ -m core.main_gui", 0, False


' ------------------------------------------------------------------ interpreter search

Function FindPython(exeName, launcherName)
    Dim windir, localApp, p, f
    windir = sh.ExpandEnvironmentStrings("%WINDIR%")
    localApp = sh.ExpandEnvironmentStrings("%LocalAppData%")

    If fso.FileExists(windir & "\" & launcherName) Then
        FindPython = windir & "\" & launcherName
        Exit Function
    End If

    p = FindOnPath(exeName)
    If p <> "" Then
        FindPython = p
        Exit Function
    End If

    For Each f In PythonDirs(localApp & "\Programs\Python")
        If fso.FileExists(f & "\" & exeName) Then
            FindPython = f & "\" & exeName
            Exit Function
        End If
    Next

    FindPython = ""
End Function

Function FindOnPath(exeName)
    Dim parts, i, cand
    parts = Split(sh.ExpandEnvironmentStrings("%PATH%"), ";")
    FindOnPath = ""
    For i = 0 To UBound(parts)
        If parts(i) <> "" Then
            cand = parts(i) & "\" & exeName
            If fso.FileExists(cand) Then
                If Not IsStoreStub(cand) Then
                    FindOnPath = cand
                    Exit Function
                End If
            End If
        End If
    Next
End Function

' The Store puts 0-byte alias files in WindowsApps; they open the Store instead of
' running Python. Only treated as a stub when the file really is empty.
Function IsStoreStub(path)
    IsStoreStub = False
    On Error Resume Next
    If InStr(LCase(path), "windowsapps") > 0 Then
        If fso.GetFile(path).Size = 0 Then IsStoreStub = True
    End If
    If Err.Number <> 0 Then Err.Clear
    On Error GoTo 0
End Function

' Sub-folders that look like Python installs. A folder we may not be allowed to list
' must never abort the launcher, so enumeration is fault tolerant.
Function PythonDirs(parentPath)
    Dim out(), n, subFolder
    n = 0
    ReDim out(0)
    out(0) = ""
    On Error Resume Next
    If fso.FolderExists(parentPath) Then
        For Each subFolder In fso.GetFolder(parentPath).SubFolders
            If LCase(Left(subFolder.Name, 7)) = "python3" Then
                ReDim Preserve out(n)
                out(n) = subFolder.Path
                n = n + 1
            End If
        Next
    End If
    If Err.Number <> 0 Then Err.Clear
    On Error GoTo 0
    If n > 0 Then ReDim Preserve out(n - 1)
    PythonDirs = out
End Function
