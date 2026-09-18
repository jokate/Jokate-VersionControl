@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title Jokate - 종료

rem  사용법: stop.bat [프로젝트 폴더 또는 .uproject]
rem  하는 일: 창 없이 돌고 있는 Jokate 데몬을 종료한다

call "%~dp0_project.bat" %*
if errorlevel 1 goto :fail

python -m jokate daemon-stop "%PROJ%"
echo.
timeout /t 2 /nobreak >nul
exit /b 0

:fail
pause
exit /b 1
