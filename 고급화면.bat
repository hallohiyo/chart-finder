@echo off
cd /d "%~dp0"
title 주식 찾기 - 고급 화면

if not exist ".venv\Scripts\pythonw.exe" (
    echo   먼저 "주식찾기.bat" 을 한 번 실행해 준비 작업을 끝내주세요.
    pause
    exit /b 1
)

start "" ".venv\Scripts\pythonw.exe" -m chartfinder.ui
exit /b 0
