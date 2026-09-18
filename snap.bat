@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title Jokate - 스냅샷

rem  사용법: snap.bat [프로젝트 폴더 또는 .uproject]
rem  올리지 않은 변경을 보여주고, 메시지를 받아 라벨 스냅샷을 만든다 (메시지를 비우면 취소)

call "%~dp0_project.bat" %*
if errorlevel 1 goto :end

echo.
echo  프로젝트 : %PROJ%
echo.
python -m jokate status "%PROJ%"
echo.
set "MSG="
set /p "MSG= 스냅샷 메시지 (비우면 취소): "
if "%MSG%"=="" goto :end
echo.
python -m jokate snap "%PROJ%" -m "%MSG%"

:end
echo.
pause
