@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title Jokate - 에디터 브릿지 설치

rem  사용법: bridge-install.bat [프로젝트 폴더 또는 .uproject]
rem  에디터 브릿지(켜진 채 롤백 + 콘텐츠 브라우저 우클릭 Jokate 메뉴)를 프로젝트의 Content\Python 에 설치한다

call "%~dp0_project.bat" %*
if errorlevel 1 goto :end

echo.
echo  프로젝트 : %PROJ%
echo.
python -m jokate bridge-install "%PROJ%"
echo.
python -m jokate bridge-status "%PROJ%"

:end
echo.
pause
