@echo off
REM Core build, console version: same idea as run-core.vbs but the window stays open,
REM so an import error can actually be read. Double-click this when the silent launcher
REM "does nothing".
REM
REM The interpreter search here is deliberately shorter than run-core.vbs (py launcher,
REM then python on PATH): this file exists to show errors, not to be clever. `where`
REM may hand back Microsoft Store's stub - the "using:" line below tells you which
REM interpreter was picked, so a wrong one is visible instead of silent.
setlocal
cd /d "%~dp0"
set "APPDIR=%~dp0app"
set "PYTHONPATH=%APPDIR%"

if not exist "%APPDIR%\core\main_gui.py" (
  echo [ERROR] program folder not found: "%APPDIR%"
  echo         Unzip the whole archive first, then run this file from inside it.
  pause
  exit /b 1
)

set "PYEXE="
if exist "%WINDIR%\py.exe" set "PYEXE=%WINDIR%\py.exe"
if not defined PYEXE (
  for /f "delims=" %%I in ('where python.exe 2^>nul') do if not defined PYEXE set "PYEXE=%%I"
)
if not defined PYEXE (
  echo [ERROR] No Python found on this computer.
  echo         Install Python 3.10+ from python.org and tick "Add python.exe to PATH",
  echo         or use the FULL build of this tool - it carries its own Python,
  echo         playwright and Chromium and needs nothing installed.
  pause
  exit /b 1
)

echo using: "%PYEXE%"
echo.
cd /d "%APPDIR%"
"%PYEXE%" -m core.main_gui
if errorlevel 1 (
  echo.
  echo [ERROR] exited with code %errorlevel%. Check the "logs" folder in your workspace.
  pause
)
