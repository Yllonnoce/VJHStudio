# VJHStudio

A local web app that makes images and videos with RunWare.AI and keeps them on your computer.

## Before you start

You need Git and an internet connection.

**Windows:** open the Start menu, type `cmd`, open Command Prompt, and run:

```
winget install --id Git.Git -e --source winget
```

If winget is missing, download Git from https://git-scm.com/download/win and run the installer with default options.

**macOS:** open Terminal (Applications > Utilities) and run:

```
xcode-select --install
```

Then click Install in the dialog that appears.

**Linux:** open a terminal and run the command for your distribution:

- Ubuntu/Debian: `sudo apt install -y git`
- Fedora: `sudo dnf install -y git`
- Arch: `sudo pacman -S git`

Close and reopen the terminal afterwards.

## Where to put it

Pick a folder you can find again. We recommend your home folder, so the app ends up in `~/VJHStudio` (macOS/Linux) or `C:\Users\<you>\VJHStudio` (Windows).

Avoid folders that sync to the cloud (OneDrive, iCloud Drive, Dropbox) and avoid system folders (Program Files, /usr).

## Install

**Windows (Command Prompt):**

```
cd %USERPROFILE%
```

```
git clone https://github.com/yllonnoce/VJHStudio.git
```

```
cd VJHStudio
```

```
start.bat
```

**macOS / Linux (Terminal):**

```
cd ~
```

```
git clone https://github.com/yllonnoce/VJHStudio.git
```

```
cd VJHStudio
```

```
./start.sh
```

The first start downloads Python and the app's packages (a few minutes). Your browser opens at http://127.0.0.1:8080.

## First run

Go to Settings, paste your RunWare API key (the page explains how to get one), then click Test.

## Starting it again

Double-click `start.bat` (Windows) or run `./start.sh` (macOS/Linux).

To stop: close the window or press Ctrl+C.

## macOS notes

- Use http://127.0.0.1:8080 (not "localhost").
- If macOS asks to allow Local Network access, allow it under System Settings > Privacy & Security > Local Network.
- If a downloaded file will not open, right-click it and choose Open once.

## Where your files are

Everything you make is in the `data` folder inside VJHStudio (`data/outputs` for images and videos, `data/backups` for backups). Updating the app never touches it.

## For developers

See `docs/superpowers/specs/2026-09-20-runwarestudio-design.md`; `uv run pytest -q`.
