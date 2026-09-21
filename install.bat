@echo off
rem install.bat — installs VJHStudio on Windows.
rem It downloads the app, sets it up, asks two yes/no questions, and starts it.
rem Double-click this file, or run it from a terminal.
setlocal EnableExtensions

rem This file first copies itself into the Windows temp folder and runs the copy,
rem so that downloading the app can never overwrite the file that is running.
if /i "%~1"=="--child" goto :child
set "COPY=%TEMP%\vjh_install_%RANDOM%%RANDOM%.bat"
copy /y "%~f0" "%COPY%" >nul
call "%COPY%" --child %*
set "RC=%errorlevel%"
del /q "%COPY%" >nul 2>&1
exit /b %RC%

:child
shift
set "NOSTART="
if /i "%~1"=="/nostart" set "NOSTART=1"

rem The folder the app is installed into.
set "DIR=%VJHSTUDIO_HOME%"
if "%DIR%"=="" set "DIR=%USERPROFILE%\VJHStudio"
echo Installing VJHStudio into %DIR%

rem --- Step 1: make sure Git is installed (it downloads the app) --------------
set "GIT="
where git >nul 2>&1 && set "GIT=git"
if "%GIT%"=="" if exist "%LOCALAPPDATA%\Programs\MinGit\cmd\git.exe" set "GIT=%LOCALAPPDATA%\Programs\MinGit\cmd\git.exe"
if "%GIT%"=="" (
  echo Git is missing. Installing it now...
  winget install --id Git.Git -e --accept-package-agreements --accept-source-agreements
  where git >nul 2>&1 && set "GIT=git"
)
if "%GIT%"=="" (
  echo Downloading a small copy of Git instead...
  curl.exe -L -o "%TEMP%\mingit.zip" https://github.com/git-for-windows/git/releases/download/v2.47.1.windows.1/MinGit-2.47.1-64-bit.zip
  if not exist "%LOCALAPPDATA%\Programs\MinGit" mkdir "%LOCALAPPDATA%\Programs\MinGit"
  tar.exe -xf "%TEMP%\mingit.zip" -C "%LOCALAPPDATA%\Programs\MinGit"
)
if "%GIT%"=="" if exist "%LOCALAPPDATA%\Programs\MinGit\cmd\git.exe" set "GIT=%LOCALAPPDATA%\Programs\MinGit\cmd\git.exe"
if "%GIT%"=="" ( echo Could not install Git. Install it from https://git-scm.com and run this again. & pause & exit /b 1 )

rem --- Step 2: make sure uv is installed (it runs the app's Python) -----------
set "UV="
where uv >nul 2>&1 && set "UV=uv"
if "%UV%"=="" if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
if "%UV%"=="" (
  echo uv is missing. Installing it now...
  winget install --id astral-sh.uv -e --accept-package-agreements --accept-source-agreements
  where uv >nul 2>&1 && set "UV=uv"
)
rem Which uv download fits this computer: the normal one, or the ARM64 one.
set "UVZIP=uv-x86_64-pc-windows-msvc.zip"
if /i "%PROCESSOR_ARCHITECTURE%"=="ARM64" set "UVZIP=uv-aarch64-pc-windows-msvc.zip"
if "%UV%"=="" (
  echo Downloading uv instead...
  curl.exe -L -o "%TEMP%\uv.zip" https://github.com/astral-sh/uv/releases/latest/download/%UVZIP%
  if not exist "%USERPROFILE%\.local\bin" mkdir "%USERPROFILE%\.local\bin"
  tar.exe -xf "%TEMP%\uv.zip" -C "%USERPROFILE%\.local\bin"
)
if "%UV%"=="" if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
if "%UV%"=="" ( echo Could not install uv. Install it from https://astral.sh/uv and run this again. & pause & exit /b 1 )

rem --- Step 3: download the app, or update the copy that is already there -----
if exist "%DIR%\.git" (
  echo A copy is already in %DIR%. Updating it...
  "%GIT%" -C "%DIR%" pull --ff-only
) else (
  echo Downloading VJHStudio...
  "%GIT%" clone https://github.com/yllonnoce/VJHStudio.git "%DIR%"
)
if not exist "%DIR%\run.py" ( echo Download failed. Check your internet connection and try again. & pause & exit /b 1 )
cd /d "%DIR%"

rem --- Step 4: install the app's Python packages (a few minutes the 1st time) -
rem "uv sync --frozen" installs exactly the package versions the app was tested with.
echo Setting up the app. This takes a few minutes the first time...
"%UV%" sync --frozen
if errorlevel 1 ( echo Setup failed. Run this again. & pause & exit /b 1 )

rem --- Step 5: create or update the database ----------------------------------
"%UV%" run --frozen python run.py migrate
if errorlevel 1 ( echo Database setup failed. & pause & exit /b 1 )

rem --- Step 6: the two questions (pressing Enter means no) --------------------
set "SERVICE=n"
set "DESKTOP=n"
set /p "SERVICE=Start VJHStudio automatically when you log in? [y/N] "
set /p "DESKTOP=Create a desktop link? [y/N] "
set "SERVICE_JSON=false"
set "DESKTOP_JSON=false"
if /i "%SERVICE:~0,1%"=="y" set "SERVICE_JSON=true"
if /i "%DESKTOP:~0,1%"=="y" set "DESKTOP_JSON=true"

rem --- Step 7: start at login, if that was answered yes -----------------------
if "%SERVICE_JSON%"=="true" schtasks /Create /SC ONLOGON /TN VJHStudio /TR "\"%DIR%\start.bat\" --no-browser" /F

rem --- Step 8: the shortcut, if that was answered yes -------------------------
rem Windows keeps the Desktop in OneDrive on some computers; use that one if it exists.
set "DESKTOPDIR=%USERPROFILE%\Desktop"
if exist "%USERPROFILE%\OneDrive\Desktop" set "DESKTOPDIR=%USERPROFILE%\OneDrive\Desktop"
set "VBS=%TEMP%\vjh_shortcut.vbs"
if not "%DESKTOP_JSON%"=="true" goto :nodesktop
where cscript >nul 2>&1
if errorlevel 1 goto :urlshortcut
rem Write a tiny script that makes the shortcut, run it, then delete it again.
> "%VBS%" echo Set shell = CreateObject("WScript.Shell")
>> "%VBS%" echo Set link = shell.CreateShortcut("%DESKTOPDIR%\VJHStudio.lnk")
>> "%VBS%" echo link.TargetPath = "%DIR%\start.bat"
>> "%VBS%" echo link.WorkingDirectory = "%DIR%"
>> "%VBS%" echo link.IconLocation = "%DIR%\vjhstudio\web\static\img\icon.ico"
>> "%VBS%" echo link.WindowStyle = 7
>> "%VBS%" echo link.Save
cscript //nologo "%VBS%"
del /q "%VBS%" >nul 2>&1
goto :nodesktop
:urlshortcut
rem cscript is missing on this computer, so make a simple clickable link instead.
> "%DESKTOPDIR%\VJHStudio.url" echo [InternetShortcut]
>> "%DESKTOPDIR%\VJHStudio.url" echo URL=file:///%DIR:\=/%/start.bat
:nodesktop

rem --- Step 9: write down what we did, so uninstall.bat can undo exactly that -
if not exist "%DIR%\data" mkdir "%DIR%\data"
> "%DIR%\data\install.json" echo {"version": 1,
>> "%DIR%\data\install.json" echo  "home": "%DIR:\=\\%",
>> "%DIR%\data\install.json" echo  "os": "windows",
>> "%DIR%\data\install.json" echo  "service": %SERVICE_JSON%,
>> "%DIR%\data\install.json" echo  "desktop": %DESKTOP_JSON%,
>> "%DIR%\data\install.json" echo  "installed_at": "%DATE% %TIME%"}

rem --- Step 10: done. Start the app unless it already starts by itself --------
echo VJHStudio is installed in %DIR%
if "%SERVICE_JSON%"=="true" goto :finish
if defined NOSTART goto :finish
start "" "%DIR%\start.bat"
:finish
echo Open http://127.0.0.1:8080/ in your browser.
pause
exit /b 0
