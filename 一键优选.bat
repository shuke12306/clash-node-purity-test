@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM One-click launcher for node_purity_tool.py menu (UI prints in Chinese).
REM Extra args pass through, e.g.:  --regions  --report
REM Keep this file ASCII-only with CRLF line endings: cmd.exe
REM mis-parses .bat files that use bare LF or non-ASCII bytes.

where /q python
if %errorlevel%==0 (
    set "PY=python"
) else (
    set "PY=py"
)

%PY% "node_purity_tool.py" menu %*

echo.
pause
