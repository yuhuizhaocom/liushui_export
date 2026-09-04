' ============================================
'  Liushui Export Tool - one-click launcher
'  Double-click this file to start.
'  - Uses pythonw, no console window
'  - Auto-installs missing dependencies on first run
' ============================================
Option Explicit

Dim sh, fso, dir, py, pyw, run, cmd, ret, i
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir

' Candidate paths (pythonw first, python second)
Dim pw(5), pe(5)
pw(0) = "E:\Python\Python314\pythonw.exe"
pe(0) = "E:\Python\Python314\python.exe"
pw(1) = "C:\Python314\pythonw.exe"
pe(1) = "C:\Python314\python.exe"
pw(2) = "C:\Python313\pythonw.exe"
pe(2) = "C:\Python313\python.exe"
pw(3) = "C:\Python312\pythonw.exe"
pe(3) = "C:\Python312\python.exe"
pw(4) = "C:\Python311\pythonw.exe"
pe(4) = "C:\Python311\python.exe"
pw(5) = "C:\Python310\pythonw.exe"
pe(5) = "C:\Python310\python.exe"

pyw = ""
py = ""
For i = 0 To 5
    If fso.FileExists(pw(i)) Then
        pyw = pw(i)
        Exit For
    End If
Next
If pyw = "" Then
    For i = 0 To 5
        If fso.FileExists(pe(i)) Then
            py = pe(i)
            Exit For
        End If
    Next
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
sh.Run Chr(34) & run & Chr(34) & " main_gui.py", 0, False