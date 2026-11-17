@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
title 백수톡 로그봇
color 0B

echo.
echo  ==================================================
echo        백수톡 로그봇 - 자동 설치 및 실행
echo  ==================================================
echo.

if not exist "bot.py" (
    echo  [X] bot.py 파일이 없어요.
    echo      실행하기.bat 와 bot.py 가 같은 폴더에 있어야 합니다.
    pause
    exit /b 1
)

REM ================= 1단계 : Python 확인 =================
echo [1/3] Python 확인 중...

set "PY="
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 (
    for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)"') do set "PY=%%i"
)
if not defined PY (
    python -c "import sys" >nul 2>nul
    if not errorlevel 1 (
        for /f "delims=" %%i in ('python -c "import sys;print(sys.executable)"') do set "PY=%%i"
    )
)

if not defined PY (
    echo        Python 이 없어서 자동 설치를 시도합니다...
    where winget >nul 2>nul
    if errorlevel 1 (
        echo.
        echo  [X] Python 이 설치되어 있지 않고 winget 도 없습니다.
        echo      https://www.python.org/downloads/ 에서 Python 을 설치할 때
        echo      [Add python.exe to PATH] 를 꼭 체크해 주세요.
        pause
        exit /b 1
    )
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    py -3 -c "import sys" >nul 2>nul
    if not errorlevel 1 (
        for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)"') do set "PY=%%i"
    )
    if not defined PY (
        if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    )
    if not defined PY (
        echo.
        echo  Python 설치를 마쳤습니다. 이 창을 닫고 실행하기.bat 를 다시 더블클릭해 주세요.
        pause
        exit /b 1
    )
)

echo        Python 확인:
"%PY%" --version

REM ================= 2단계 : 가상환경 + 패키지 =================
echo [2/3] 필요한 패키지 설치 중... 처음 한 번만 1~2분 걸려요.

if not exist ".venv\Scripts\python.exe" (
    echo        가상환경(.venv)을 만듭니다...
    "%PY%" -m venv .venv
    if errorlevel 1 goto :venvfail
)

if not exist ".venv\.installed" (
    ".venv\Scripts\python.exe" -m pip install --upgrade pip --disable-pip-version-check -q
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt --disable-pip-version-check
    if errorlevel 1 goto :pipfail
    echo ok> ".venv\.installed"
)
echo        패키지 OK

REM ================= 3단계 : 봇 토큰 =================
echo [3/3] 설정 확인 중...

if not exist ".env" (
    echo.
    echo   ── 봇 토큰이 아직 없어요 ──
    echo   1^) 브라우저에서 https://discord.com/developers/applications 접속
    echo   2^) [백수톡 로그봇] 선택 ^> 왼쪽 [Bot] 메뉴
    echo   3^) [토큰 초기화] ^> [비밀번호 사용] ^> 디스코드 비밀번호 입력
    echo   4^) 나온 토큰을 복사
    echo.
    set /p "TOKEN=   토큰을 붙여넣고 Enter: "
    if "!TOKEN!"=="" (
        echo.
        echo  [X] 토큰이 비어 있어 실행할 수 없어요. 다시 실행해 주세요.
        pause
        exit /b 1
    )
    > ".env" echo DISCORD_TOKEN=!TOKEN!
    >> ".env" echo SOURCE_GUILD_ID=386882960403726337
    >> ".env" echo LOG_CHANNEL_ID=1572394445142360064
    >> ".env" echo LOG_USER_ID=
    echo        .env 파일을 만들었습니다. 이 파일은 절대 남에게 보내지 마세요!
    echo        백수톡의 기록이 개인 서버 paranoia 의 #백수톡-로그 로 갑니다.
)
echo        설정 OK

REM ================= 실행 =================
echo.
echo  ==================================================
echo    봇을 켭니다. 이 창을 닫으면 봇이 꺼져요.
echo    디스코드 개인 서버 paranoia 의 #백수톡-로그 채널을 확인하세요.
echo  ==================================================
echo.

".venv\Scripts\python.exe" bot.py

echo.
echo  봇이 종료되었습니다.
echo  토큰을 바꾸려면 이 폴더의 .env 파일을 지우고 다시 실행하세요.
pause
exit /b 0

:venvfail
echo.
echo  [X] 가상환경(.venv)을 만들지 못했어요.
echo      디스크 공간을 확인하고 다시 실행해 주세요.
pause
exit /b 1

:pipfail
echo.
echo  [X] 패키지 설치에 실패했어요.
echo      인터넷 연결을 확인한 뒤 실행하기.bat 를 다시 실행해 주세요.
pause
exit /b 1
