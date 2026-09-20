@echo off
rem start.bat — Windows launcher. Copies itself to %TEMP% and runs the copy so that
rem a git pull can never rewrite the batch file that cmd is currently executing.
setlocal EnableExtensions
if /i "%~1"=="--child" goto :child
set "SELF=%~f0"
set "COPY=%TEMP%\vjh_start_%RANDOM%%RANDOM%.bat"
copy /y "%SELF%" "%COPY%" >nul
call "%COPY%" --child "%~dp0" %*
set "RC=%errorlevel%"
del /q "%COPY%" >nul 2>&1
exit /b %RC%

:child
shift
set "HOME_DIR=%~1"
shift
cd /d "%HOME_DIR%"
set "UV=%VJHSTUDIO_UV%"
if "%UV%"=="" ( where uv >nul 2>&1 && set "UV=uv" )
if "%UV%"=="" if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
if "%UV%"=="" (
  rem uv (the tool that runs the app's Python) is not installed yet. Install it now.
  echo uv not found; installing it...
  curl.exe -LsSf https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip -o "%TEMP%\uv.zip"
  if not exist "%USERPROFILE%\.local\bin" mkdir "%USERPROFILE%\.local\bin"
  tar.exe -xf "%TEMP%\uv.zip" -C "%USERPROFILE%\.local\bin"
  if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
)
if "%UV%"=="" ( echo Could not install uv automatically. Install it yourself from https://astral.sh/uv and try again. & pause & exit /b 1 )
set "GIT=%VJHSTUDIO_GIT%"
if "%GIT%"=="" ( where git >nul 2>&1 && set "GIT=git" )
if "%GIT%"=="" if exist "%LOCALAPPDATA%\Programs\MinGit\cmd\git.exe" set "GIT=%LOCALAPPDATA%\Programs\MinGit\cmd\git.exe"
if "%GIT%"=="" if exist "%ProgramFiles%\Git\cmd\git.exe" set "GIT=%ProgramFiles%\Git\cmd\git.exe"
set "VJHSTUDIO_UV=%UV%"
set "VJHSTUDIO_GIT=%GIT%"
set "VJHSTUDIO_HOME=%HOME_DIR%"
set "VJHSTUDIO_LAUNCHER=1"
set "PORT=%VJHSTUDIO_PORT%"
if "%PORT%"=="" set "PORT=8080"
set "OPENFLAG=--open"
if /i "%~1"=="--no-browser" set "OPENFLAG=--no-browser"
if not "%~1"=="" if /i not "%~1"=="--no-browser" if /i not "%~1"=="--open" set "PORT=%~1"
if /i "%~2"=="--no-browser" set "OPENFLAG=--no-browser"
title VJHStudio
:loop
"%UV%" run --frozen python run.py serve --port %PORT% %OPENFLAG%
if errorlevel 76 goto :done
if errorlevel 75 (
  echo VJHStudio: restarting...
  set "OPENFLAG=--no-browser"
  timeout /t 1 /nobreak >nul
  goto :loop
)
:done
if not "%errorlevel%"=="0" ( echo VJHStudio exited with code %errorlevel%. & pause )
exit /b %errorlevel%
