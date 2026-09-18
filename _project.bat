@echo off
rem  공용 도우미: UE 프로젝트 폴더를 PROJ 에 넣는다. 다른 bat 이 call 로 부른다.
rem  우선순위: 인자(.uproject 파일 또는 폴더 — 드래그앤드롭 가능) > project.local.txt > 파일 선택 창
cd /d "%~dp0"
set "PROJ="
set "FROMSAVED="

if "%~1"=="" goto :saved
if /i "%~x1"==".uproject" goto :from_uproject
set "PROJ=%~1"
goto :check
:from_uproject
set "PROJ=%~dp1"
goto :check

:saved
if exist "%~dp0project.local.txt" set /p PROJ=<"%~dp0project.local.txt"
if not "%PROJ%"=="" set "FROMSAVED=1"
if not "%PROJ%"=="" goto :check

echo  UE 프로젝트의 .uproject 파일을 선택하세요...
for /f "usebackq delims=" %%P in (`powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\pick_project.ps1"`) do set "PROJ=%%P"

:check
if "%PROJ%"=="" goto :noproj
if "%PROJ:~-1%"=="\" set "PROJ=%PROJ:~0,-1%"
if not exist "%PROJ%\Content\" goto :nocontent
where python >nul 2>&1 || goto :nopython

>"%~dp0project.local.txt" echo %PROJ%
if exist "%PROJ%\.jokate\config.toml" exit /b 0

python -m jokate init "%PROJ%"
echo.
echo  [i] 처음 쓰는 프로젝트입니다. 마켓 팩처럼 가져온 폴더는
echo      %PROJ%\.jokate\config.toml 의 vendor 목록에 적으면 히스토리에서 빠집니다.
echo.
exit /b 0

:noproj
echo  [X] 프로젝트를 고르지 않았습니다.
exit /b 1
:nocontent
echo  [X] Content 폴더가 없습니다: %PROJ%
if "%FROMSAVED%"=="1" del "%~dp0project.local.txt" >nul 2>&1
exit /b 1
:nopython
echo  [X] python 이 없습니다. Python 3.11 이상을 설치하세요.
exit /b 1
