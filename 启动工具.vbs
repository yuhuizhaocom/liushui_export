' ============================================
'  Liushui Export Tool - one-click launcher
'  Double-click this file to start.
'  - Uses pythonw, no console window
'  - Auto-installs missing dependencies on first run
'  - Debug: cscript //nologo <this file>.vbs --print-python
'    (prints the resolved interpreters and exits without launching)
' ============================================
Option Explicit

Dim sh, fso, dir, py, pyw, run, cmd, ret, i
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir

' ---- Locate an interpreter ----
' Tier 1 is the historic hard-coded list, kept first on purpose: machines that work
' today must not silently switch to another interpreter. The later tiers exist so a
' normal Python install is no longer reported as "Python was not found".
' launcherName is the py-launcher spelling of the same thing (py.exe / pyw.exe):
' the launcher is never called python.exe, so it cannot be matched by exeName.
Function FindPython(exeName, launcherName)
    Dim p, f, windir, localApp
    windir = sh.ExpandEnvironmentStrings("%WINDIR%")
    localApp = sh.ExpandEnvironmentStrings("%LocalAppData%")

    ' (1) machine-wide installs this launcher used to know about
    For Each p In Array("E:\Python\Python314\", "C:\Python314\", "C:\Python313\", _
                        "C:\Python312\", "C:\Python311\", "C:\Python310\")
        If fso.FileExists(p & exeName) Then
            FindPython = p & exeName
            Exit Function
        End If
    Next

    ' (2) the Windows py launcher, which resolves every registered Python itself
    If fso.FileExists(windir & "\" & launcherName) Then
        FindPython = windir & "\" & launcherName
        Exit Function
    End If

    ' (3) python.org per-user default: %LocalAppData%\Programs\Python\Python3xx
    For Each f In PythonDirs(localApp & "\Programs\Python")
        If fso.FileExists(f & "\" & exeName) Then
            FindPython = f & "\" & exeName
            Exit Function
        End If
    Next

    ' (4) the project's own virtualenv, as a last resort
    p = dir & "\.venv\Scripts\" & exeName
    If fso.FileExists(p) Then
        FindPython = p
        Exit Function
    End If

    ' (5) any C:\Python3xx style folder
    For Each f In PythonDirs("C:\")
        If fso.FileExists(f & "\" & exeName) Then
            FindPython = f & "\" & exeName
            Exit Function
        End If
    Next

    FindPython = ""
End Function

' Sub-folders that look like Python installs (Python3xx). A folder we are not
' allowed to list must never abort the launcher, so enumeration is fault-tolerant.
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

pyw = FindPython("pythonw.exe", "pyw.exe")
py = FindPython("python.exe", "py.exe")

If WScript.Arguments.Count > 0 Then
    If LCase(WScript.Arguments(0)) = "--print-python" Then
        ' Report what was found and stop: no install prompt, no GUI window.
        WScript.Echo "pythonw=" & pyw
        WScript.Echo "python=" & py
        WScript.Quit 0
    End If
End If

If pyw = "" And py = "" Then
    MsgBox "Python was not found. Please install Python 3.10+ first." & vbCrLf & _
           "Download: https://www.python.org/downloads/", _
           vbCritical, "Liushui Export Tool"
    WScript.Quit 1
End If

If pyw <> "" Then
    run = pyw
Else
    run = py
End If

' ---- Check playwright (first run may need install) ----
cmd = Chr(34) & run & Chr(34) & " -c ""import playwright"""
ret = sh.Run(cmd, 0, True)
If ret <> 0 Then
    ret = MsgBox( _
        "Dependencies are missing." & vbCrLf & vbCrLf & _
        "Click Yes to install them automatically (network required, ~1-3 minutes)." & vbCrLf & _
        "The tool will start after installation finishes.", _
        vbYesNo + vbInformation + vbDefaultButton1, "Liushui Export Tool - First Run")
    If ret <> vbYes Then
        WScript.Quit 0
    End If

    If py <> "" Then
        run = py
    End If

    ' Install playwright package (visible console so user can see progress)
    cmd = Chr(34) & run & Chr(34) & " -m pip install playwright"
    ret = sh.Run(cmd, 1, True)
    If ret <> 0 Then
        MsgBox "Dependency installation failed." & vbCrLf & _
               "Please check your network, then try again.", _
               vbCritical, "Liushui Export Tool"
        WScript.Quit 1
    End If

    ' Install chromium browser
    cmd = Chr(34) & run & Chr(34) & " -m playwright install chromium"
    sh.Run cmd, 1, True

    MsgBox "Installation finished. Starting the tool...", _
           vbInformation, "Liushui Export Tool"
End If

' ---- Launch the GUI (no console) ----
If pyw <> "" Then
    run = pyw
Else
    run = py
End If
sh.Run Chr(34) & run & Chr(34) & " -m core.main_gui", 0, False
