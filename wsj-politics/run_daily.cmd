@echo off
REM Daily WSJ Politics digest: sitemap discover -> archive.today fetch -> RSS -> EPUB -> (optional) Kindle
REM Call this file from Windows Task Scheduler.
chcp 65001 >nul
set PY="F:\Program Files\Python\python.exe"
set ROOT=%~dp0
set ROOT=%ROOT:~0,-1%
set SCRIPTS=%ROOT%\..\wsj-kindle\scripts
cd /d "%ROOT%"
if not exist "%ROOT%\logs" mkdir "%ROOT%\logs"
%PY% "%SCRIPTS%\run_pipeline.py" --project "%ROOT%" %* >> "%ROOT%\logs\pipeline.log" 2>&1
exit /b %ERRORLEVEL%