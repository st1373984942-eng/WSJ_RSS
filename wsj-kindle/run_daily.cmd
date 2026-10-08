@echo off
REM Daily WSJ Chinese digest: RSS discover -> bpc-fetch -> RSS -> EPUB -> (optional) Kindle
REM Call this file from Windows Task Scheduler.
chcp 65001 >nul
set PY="F:\Program Files\Python\python.exe"
set ROOT=%~dp0
set ROOT=%ROOT:~0,-1%
cd /d "%ROOT%"
if not exist "%ROOT%\logs" mkdir "%ROOT%\logs"
%PY% "%ROOT%\scripts\run_pipeline.py" --project "%ROOT%" %* >> "%ROOT%\logs\pipeline.log" 2>&1
exit /b %ERRORLEVEL%