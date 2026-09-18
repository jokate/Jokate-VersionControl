@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title Jokate - 타임라인

rem  사용법: start.bat [프로젝트 폴더 또는 .uproject]    .uproject 를 이 파일에 끌어다 놓아도 됨
rem  하는 일: 창 없는 데몬 하나로 자동 스냅샷과 타임라인 웹 UI 를 띄우고 브라우저를 연다
rem  끄기: 웹 화면 오른쪽 위 종료 버튼, 또는 stop.bat
rem  JOKATE_NO_BROWSER=1 이면 브라우저를 열지 않음

call "%~dp0_project.bat" %*
if errorlevel 1 goto :fail

set "PORT=8765"
for /f "usebackq" %%p in (`python -c "import os; from jokate import config; print(config.load(os.environ['PROJ']).port)"`) do set "PORT=%%p"
set "URL=http://127.0.0.1:%PORT%"

rem 이미 켜져 있으면 브라우저만 연다
netstat -ano | findstr /r /c:":%PORT% .*LISTENING" >nul 2>&1
if not errorlevel 1 goto :already

where pythonw >nul 2>&1
if errorlevel 1 goto :fallback
start "" pythonw -m jokate daemon "%PROJ%" --port %PORT%
goto :wait

:fallback
start "Jokate" /min python -m jokate daemon "%PROJ%" --port %PORT%

:wait
set /a TRY=0
:waitloop
netstat -ano | findstr /r /c:":%PORT% .*LISTENING" >nul 2>&1
if not errorlevel 1 goto :open
set /a TRY+=1
if %TRY% GEQ 10 goto :timeout
timeout /t 1 /nobreak >nul
goto :waitloop

:open
echo.
echo  프로젝트 : %PROJ%
echo  타임라인 : %URL%
echo  끄려면 웹 화면의 종료 버튼 또는 stop.bat
if not "%JOKATE_NO_BROWSER%"=="1" start "" "%URL%"
exit /b 0

:timeout
echo.
echo  [X] 데몬이 10초 안에 뜨지 않았습니다. 로그를 확인하세요:
echo      %PROJ%\.jokate\daemon.log
pause
exit /b 1

:already
echo  이미 %URL% 에서 실행 중입니다. 브라우저만 엽니다.
if not "%JOKATE_NO_BROWSER%"=="1" start "" "%URL%"
exit /b 0

:fail
pause
exit /b 1
