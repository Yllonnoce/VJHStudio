@echo off
rem uninstall.bat — undoes what install.bat set up on Windows.
rem Your pictures, projects and settings are KEPT unless you add /purge.
rem Double-click this file, or run it from a terminal.
setlocal EnableExtensions

rem The folder this file sits in, without the trailing backslash.
set "DIR=%~dp0"
if "%DIR:~-1%"=="\" set "DIR=%DIR:~0,-1%"
set "PURGE="
if /i "%~1"=="/purge" set "PURGE=1"
set "PORT=%VJHSTUDIO_PORT%"
if "%PORT%"=="" set "PORT=8080"

rem --- Step 1: ask the app to stop, then wait up to 10 seconds ----------------
echo Stopping VJHStudio if it is running...
curl.exe -s -X POST "http://127.0.0.1:%PORT%/api/shutdown" >nul 2>&1
set /a TRIES=0
:wait
curl.exe -s -o nul "http://127.0.0.1:%PORT%/" >nul 2>&1
if errorlevel 1 goto :stopped
set /a TRIES+=1
if %TRIES% geq 10 goto :stopped
timeout /t 1 /nobreak >nul
goto :wait
:stopped

rem --- Step 2: read what the installer wrote down ------------------------------
set "SERVICE="
set "DESKTOP="
set "INFO=%DIR%\data\install.json"
if not exist "%INFO%" goto :noinfo
findstr /r /c:"service.: true" "%INFO%" >nul && set "SERVICE=1"
findstr /r /c:"desktop.: true" "%INFO%" >nul && set "DESKTOP=1"
:noinfo

rem --- Step 3: remove the "start when I log in" task ---------------------------
if defined SERVICE echo Removing the start-at-login task...
if defined SERVICE schtasks /Delete /TN VJHStudio /F >nul 2>&1

rem --- Step 4: remove the shortcut from both possible Desktop folders ---------
if defined DESKTOP echo Removing the shortcut...
if defined DESKTOP del /q "%USERPROFILE%\Desktop\VJHStudio.lnk" >nul 2>&1
if defined DESKTOP del /q "%USERPROFILE%\Desktop\VJHStudio.url" >nul 2>&1
if defined DESKTOP del /q "%USERPROFILE%\OneDrive\Desktop\VJHStudio.lnk" >nul 2>&1
if defined DESKTOP del /q "%USERPROFILE%\OneDrive\Desktop\VJHStudio.url" >nul 2>&1

rem --- Step 5: remove the installed Python packages and the installer's note ---
echo Removing the installed Python packages...
if exist "%DIR%\.venv" rmdir /s /q "%DIR%\.venv"
if exist "%INFO%" del /q "%INFO%"

rem --- Step 6: only with /purge: delete your pictures, projects and settings ---
if not defined PURGE goto :done
echo.
echo This deletes every picture, project and setting in "%DIR%\data"
set "CONFIRM="
set /p "CONFIRM=Type DELETE and press Enter to confirm: "
if not "%CONFIRM%"=="DELETE" goto :kept
rmdir /s /q "%DIR%\data"
echo Your data folder was deleted.
goto :done
:kept
echo Nothing was deleted.
:done

echo.
echo Done. The VJHStudio folder, uv and Git were left in place.
echo Delete "%DIR%" yourself if you no longer want it.
pause
exit /b 0
