@echo off
REM Troubleshooting launcher: same as run-portable.vbs but keeps the console open so
REM import errors are visible. Double-click this when the silent launcher "does nothing".
cd /d "%~dp0"
set "PYEXE=%~dp0python\python.exe"
set "PYTHONPATH=%~dp0app\site-packages;%~dp0app"
if not exist "%PYEXE%" (
  echo [ERROR] bundled python not found: "%PYEXE%"
  pause
  exit /b 1
)
cd /d "%~dp0app"
"%PYEXE%" -m core.main_gui
if errorlevel 1 (
  echo.
  echo [ERROR] exited with code %errorlevel%. Check the "logs" folder in your workspace.
  pause
)
