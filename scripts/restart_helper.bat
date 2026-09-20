@echo off
rem scripts/restart_helper.bat <pid> — used only when the server was NOT started by start.bat.
rem Waits for the old process to exit, then launches start.bat minimised without a browser.
set "PID=%~1"
set /a TRIES=0
:wait
tasklist /FI "PID eq %PID%" 2>nul | find "%PID%" >nul
if errorlevel 1 goto :go
set /a TRIES+=1
if %TRIES% geq 60 goto :go
timeout /t 1 /nobreak >nul
goto :wait
:go
start "VJHStudio" /min cmd /c ""%~dp0..\start.bat" --no-browser"
exit
