@echo off
cd /d "%~dp0"
title 주식 찾기

rem 처음 실행하면 가상환경을 만들고 필요한 것을 설치한다 (몇 분 걸린다).
if not exist ".venv\Scripts\pythonw.exe" (
    echo.
    echo   처음 실행이라 준비 작업을 합니다. 몇 분 걸립니다...
    echo.
    where python >nul 2>nul
    if errorlevel 1 (
        echo   [!] 파이썬이 설치돼 있지 않습니다.
        echo       https://www.python.org/downloads/ 에서 설치하세요.
        echo       설치 화면에서 "Add Python to PATH" 를 꼭 체크하세요.
        echo.
        pause
        exit /b 1
    )
    python -m venv .venv
    if errorlevel 1 goto failed

    echo   필요한 프로그램을 내려받는 중...
    ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
    ".venv\Scripts\python.exe" -m pip install --quiet -e ".[ui]"
    if errorlevel 1 goto failed
    echo   준비 완료.
)

rem pythonw 로 띄우면 검은 명령창이 남지 않는다
start "" ".venv\Scripts\pythonw.exe" -m chartfinder.simple_ui
exit /b 0

:failed
echo.
echo   [!] 준비 작업이 실패했습니다.
echo       명령 프롬프트에서 아래를 직접 실행해 메시지를 확인하세요.
echo.
echo       cd /d "%~dp0"
echo       python -m venv .venv
echo       .venv\Scripts\activate
echo       pip install -e ".[ui]"
echo.
pause
exit /b 1
