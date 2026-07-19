@echo off
chcp 65001 >nul
title PaperBase
cd /d "%~dp0"

rem PaperBase web UI launcher (GPU venv with CUDA torch).
rem This console window IS the running server. Close it or press Ctrl+C to stop.

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m paperbase serve --open --port 8765
) else (
    echo venv not found, launching via system python...
    python -m paperbase serve --open --port 8765
)

echo.
echo PaperBase server stopped. You can close this window.
pause >nul
