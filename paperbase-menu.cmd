@echo off
chcp 65001 >nul
title PaperBase
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m paperbase menu
) else (
    python -m paperbase menu
)
