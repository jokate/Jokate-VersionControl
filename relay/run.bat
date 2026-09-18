@echo off
setlocal EnableExtensions
chcp 65001 >nul
rem  사용법: relay\run.bat <프롬프트 이름 또는 파일> [-Model opus^|fable^|sonnet] [-Relay quick-fable^|quick^|default]
rem  예:     relay\run.bat 17-layout
if "%~1"=="" goto :usage
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
exit /b %errorlevel%

:usage
echo  사용법: relay\run.bat 프롬프트이름  [-Model opus] [-Relay quick-fable]
echo.
echo  준비된 프롬프트:
dir /b "%~dp0prompts\*.txt"
exit /b 1
