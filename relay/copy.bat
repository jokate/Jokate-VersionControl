@echo off
setlocal EnableExtensions
chcp 65001 >nul
rem  사용법: relay\copy.bat <프롬프트 이름>     예: relay\copy.bat 17-layout
rem  프롬프트 내용을 클립보드에 복사한다 → 릴레이 대시보드(http://127.0.0.1:8020)의 목표 입력란에 Ctrl+V
if "%~1"=="" goto :usage
set "F=%~dp0prompts\%~1"
if not exist "%F%" set "F=%~dp0prompts\%~1.txt"
if not exist "%F%" goto :nofile
powershell -NoProfile -Command "Set-Clipboard -Value ((Get-Content -LiteralPath $env:F -Raw -Encoding UTF8).Trim()); Write-Host (' 복사됨: ' + (Split-Path $env:F -Leaf) + '  ' + ((Get-Clipboard -Raw).Length) + '자')"
echo.
echo  대시보드에서: 저장소 jokate  ^|  릴레이 quick-fable  ^|  build 단계 모델 Opus  ^|  목표에 Ctrl+V
exit /b 0

:nofile
echo  [X] 프롬프트 없음: %~1
:usage
echo  사용법: relay\copy.bat 프롬프트이름
echo.
echo  준비된 프롬프트:
dir /b "%~dp0prompts\*.txt"
exit /b 1
