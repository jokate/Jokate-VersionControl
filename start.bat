@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title Jokate - 타임라인

rem  사용법: start.bat [프로젝트 폴더 또는 .uproject]    (.uproject 를 이 파일에 끌어다 놓아도 됨)
rem  하는 일: 자동 스냅샷(watch)을 최소화 창으로 띄우고, 타임라인 웹 UI(serve)를 열고, 브라우저를 연다
rem  JOKATE_NO_WATCH=1 이면 자동 스냅샷을 띄우지 않음 / JOKATE_NO_BROWSER=1 이면 브라우저를 열지 않음

call "%~dp0_project.bat" %*
if errorlevel 1 goto :fail

set "PORT=8765"
for /f "usebackq" %%p in (`python -c "import os; from jokate import config; print(config.load(os.environ['PROJ']).port)"`) do set "PORT=%%p"
set "URL=http://127.0.0.1:%PORT%"

rem 이미 켜져 있으면 브라우저만 연다
netstat -ano | findstr /r /c:":%PORT% .*LISTENING" >nul 2>&1
if not errorlevel 1 goto :already

echo.
echo  프로젝트 : %PROJ%
echo  타임라인 : %URL%
echo  끄려면 이 창에서 Ctrl+C  -  자동 스냅샷 창도 같이 닫힙니다
echo.

if "%JOKATE_NO_WATCH%"=="1" goto :serve
tasklist /v /fi "imagename eq cmd.exe" 2>nul | findstr /c:"Jokate watch" >nul 2>&1
if not errorlevel 1 goto :serve
start "Jokate watch" /min cmd /c "chcp 65001 >nul & python -m jokate watch "%PROJ%""

:serve
if not "%JOKATE_NO_BROWSER%"=="1" start "" /b cmd /c "timeout /t 2 /nobreak >nul & start "" "%URL%""
python -m jokate serve "%PROJ%" --port %PORT%

taskkill /fi "WINDOWTITLE eq Jokate watch*" >nul 2>&1
exit /b 0

:already
echo  이미 %URL% 에서 실행 중입니다. 브라우저만 엽니다.
if not "%JOKATE_NO_BROWSER%"=="1" start "" "%URL%"
exit /b 0

:fail
pause
exit /b 1
