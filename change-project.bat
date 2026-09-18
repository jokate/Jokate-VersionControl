@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title Jokate - 프로젝트 바꾸기

rem  기억해 둔 프로젝트를 지우고 다시 고른다 (.uproject 를 이 파일에 끌어다 놓아도 됨)

del "%~dp0project.local.txt" >nul 2>&1
call "%~dp0_project.bat" %*
if errorlevel 1 goto :end
echo.
echo  이제 이 프로젝트를 씁니다: %PROJ%

:end
echo.
pause
