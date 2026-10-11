@echo off
chcp 65001 >nul
rem 살짝 빗나간 추천 - 업데이트: 최신 코드 받기 + 라이브러리 빌드
cd /d "%~dp0"

where git >nul 2>nul
if errorlevel 1 (
  echo Git이 설치되어 있지 않아요. https://git-scm.com 에서 설치한 뒤 다시 실행해 주세요.
  goto :fail
)
where npm >nul 2>nul
if errorlevel 1 (
  echo Node.js가 설치되어 있지 않아요. https://nodejs.org 에서 LTS 버전을 설치한 뒤 다시 실행해 주세요.
  goto :fail
)
if not exist ".git" (
  echo 이 폴더는 ZIP으로 받은 폴더라서 자동 업데이트를 할 수 없어요.
  echo README의 "업데이트를 쉽게 하기" 순서대로 git clone 으로 한 번 다시 받아 주세요.
  goto :fail
)

echo [1/3] 최신 코드 받는 중...
git pull --ff-only
if errorlevel 1 goto :fail

echo.
echo [2/3] 라이브러리 설치 중...
call npm install --ignore-scripts
if errorlevel 1 goto :fail

echo.
echo [3/3] 빌드 중...
call npm run build
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo  완료! 크롬 주소창에 chrome://extensions 를 열고
echo  "살짝 빗나간 추천"의 새로고침(↻) 버튼을 눌러 주세요.
echo ============================================================
pause
exit /b 0

:fail
echo.
echo 문제가 생겼어요. 이 창의 메시지를 복사해서 알려 주세요.
pause
exit /b 1
