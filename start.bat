@echo off
cd /d "%~dp0"
title Liushui Export Tool - Fallback Launcher

echo ============================================
echo    Liushui Export Tool v1.0
echo    Fallback launcher (plain python)
echo ============================================
echo.

REM Find python
set "PYTHON="
set "PYTHON_CAND=E:\Python\Python314\python.exe"
if exist "%PYTHON_CAND%" set "PYTHON=%PYTHON_CAND%"
if not defined PYTHON (
    where python >nul 2>&1
    if %errorlevel% equ 0 set "PYTHON=python"
)

if not defined PYTHON (
    echo [ERROR] Python not found. Please install Python 3.10+.
    echo Download: https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

echo Python: %PYTHON%
echo.

REM Run the GUI directly (console stays open so errors are visible)
"%PYTHON%" -m core.main_gui

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] The application exited with code %errorlevel%.
    echo Please check the "logs" folder for details.
    pause
)

exit /b 0