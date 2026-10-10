@echo off
REM Daily WSJ Chinese digest: discover -> fetch full text -> RSS -> EPUB -> push to Kindle
REM Registered as a Windows scheduled task with "wake the computer to run this task".
REM Pass extra flags through, e.g.:  run_daily.cmd --skip-discover --no-send
chcp 65001 >nul
set PY="F:\Program Files\Python\python.exe"
set ROOT=%~dp0
set ROOT=%ROOT:~0,-1%
cd /d "%ROOT%"
if not exist "%ROOT%\logs" mkdir "%ROOT%\logs"

REM Private SMTP settings live outside git; without them the send step is skipped.
if exist "%ROOT%\secrets.local.cmd" (
  call "%ROOT%\secrets.local.cmd"
) else (
  echo [warn] secrets.local.cmd not found - credentials will be missing, send will be skipped.>> "%ROOT%\logs\pipeline.log"
)

echo. >> "%ROOT%\logs\pipeline.log"
echo ===== run started %DATE% %TIME% ===== >> "%ROOT%\logs\pipeline.log"
%PY% "%ROOT%\scripts\run_pipeline.py" --project "%ROOT%" %* >> "%ROOT%\logs\pipeline.log" 2>&1
set RC=%ERRORLEVEL%
echo ===== run finished %DATE% %TIME% (exit %RC%) ===== >> "%ROOT%\logs\pipeline.log"
exit /b %RC%
