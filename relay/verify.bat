@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0.."
title Jokate - 릴레이 결과 검증

rem  릴레이가 끝난 뒤 실행: 테스트, 브릿지 문법, MNYS 전체 파싱, 엉뚱한 폴더, 변경 파일 목록
echo.
echo  === 1. pytest
uv run --no-project --with pytest pytest -q
if errorlevel 1 echo  [X] 테스트 실패

echo.
echo  === 2. 에디터 브릿지 문법
python -m py_compile jokate\ue\jokate_bridge.py
if errorlevel 1 ( echo  [X] jokate_bridge.py 문법 오류 ) else ( echo  OK )

echo.
echo  === 3. MNYS 전체 파싱 - 파싱실패 0 이어야 함
if exist "%USERPROFILE%\Projects\MNYS\Content" python -m jokate scan "%USERPROFILE%\Projects\MNYS"

echo.
echo  === 4. 릴레이가 만든 엉뚱한 폴더 - 백슬래시 경로 사고
if exist "Userskkkk4017ProjectsMNYS" echo  [!] Userskkkk4017ProjectsMNYS 폴더가 생겼습니다. 지우세요.

echo.
echo  === 5. 바뀐 파일
git status --short

echo.
pause
