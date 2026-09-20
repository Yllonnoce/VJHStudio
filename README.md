# VJHStudio

A local web app that makes images and videos with RunWare.AI and keeps them on your computer.

You do not need to know anything about programming to use it. This page walks you through every step.

## The terminal

A few of the steps below are typed into a **terminal**. A terminal is just a window where you type
one line at a time and press Enter. It looks plain, but nothing here is dangerous.

Here is how to open one:

- **Windows:** click the Start menu, type `cmd`, and press Enter. A black window called Command
  Prompt opens.
- **macOS:** press Cmd+Space, type `Terminal`, and press Enter. A small white window opens.
- **Linux:** press Ctrl+Alt+T.

For every command on this page: **copy the line, paste it into the terminal, and press Enter.**
(On Windows, paste with a right-click. On macOS, use Cmd+V. On Linux, use Ctrl+Shift+V.)

Leave the terminal window open while you work through the steps.

## Before you start

You need an internet connection and a free tool called **Git**, which downloads the app for you.

**Windows** — paste this and press Enter:

```
winget install --id Git.Git -e --source winget
```

You should see a progress bar, then a line saying it was installed successfully.

If the terminal says `winget` is not recognised, go to https://git-scm.com/download/win instead,
download the installer, and click Next through it without changing anything.

**macOS** — paste this and press Enter:

```
xcode-select --install
```

A small dialog box appears. Click **Install** and wait for it to finish. If it says the tools are
already installed, you are done with this step.

**Linux** — paste the line for your system:

```
sudo apt install -y git
```

```
sudo dnf install -y git
```

```
sudo pacman -S git
```

Use `apt` on Ubuntu or Debian, `dnf` on Fedora, `pacman` on Arch. It will ask for your password;
type it and press Enter. Nothing appears on screen as you type your password, which is normal.

**When that is done, close the terminal window and open a new one.** New programs are only
available in a fresh terminal.

## Where to put it

The app lives in a folder on your computer. Pick somewhere you can find again.

We suggest your home folder, so the app ends up in `C:\Users\<your name>\VJHStudio` on Windows, or
`~/VJHStudio` on macOS and Linux. The steps below do exactly that.

Please avoid folders that sync to the cloud (OneDrive, iCloud Drive, Dropbox), and avoid system
folders like Program Files or /usr. The app writes files constantly and cloud sync gets in the way.

## Install

Four lines, one at a time. After each one, wait until the terminal shows a fresh prompt before
pasting the next.

**Windows**

Step 1 — go to your home folder:

```
cd %USERPROFILE%
```

The start of the line in the terminal changes to your home folder, something like
`C:\Users\yourname>`.

Step 2 — download the app with Git:

```
git clone https://github.com/yllonnoce/VJHStudio.git
```

You should see a few lines counting objects and a message ending in `done.`

Step 3 — go into the folder you just downloaded:

```
cd VJHStudio
```

The prompt now ends in `\VJHStudio>`.

Step 4 — start the app:

```
start.bat
```

**macOS and Linux**

Step 1 — go to your home folder:

```
cd ~
```

Nothing visible happens. That is fine.

Step 2 — download the app with Git:

```
git clone https://github.com/yllonnoce/VJHStudio.git
```

You should see a few lines counting objects and a message ending in `done.`

Step 3 — go into the folder you just downloaded:

```
cd VJHStudio
```

Step 4 — start the app:

```
./start.sh
```

**What happens on the first start:** the app sets itself up, which takes a few minutes. You will see
a lot of text scroll past — that is normal, and you do not need to read it. When it is ready your
web browser opens by itself at **http://127.0.0.1:8080**, and the terminal window stays open in the
background.

Keep that terminal window open. Closing it stops the app.

## First run

In the browser, click **Settings**.

Paste your RunWare API key into the box and click **Save**, then click **Test**. The page explains
how to get a key if you do not have one yet.

If the test worked, your balance appears at the top of the page.

## Starting it again

You do not have to repeat the install. To use the app again:

- **Windows:** open the VJHStudio folder and double-click `start.bat`.
- **macOS and Linux:** open a terminal, paste `cd ~/VJHStudio`, press Enter, then paste `./start.sh`
  and press Enter.

Your browser opens at http://127.0.0.1:8080 again.

To stop the app, close the terminal window, or click in it and press Ctrl+C.

## If something goes wrong

**"git is not recognized"** (or "command not found: git")

The terminal was opened before Git finished installing. Close the terminal window, open a new one,
and try again. If it still says that, install Git again from the "Before you start" section.

**The browser did not open**

The app is probably running anyway. Open your browser yourself and go to http://127.0.0.1:8080.

**It says the port is in use**

VJHStudio is most likely already running. Look for another VJHStudio window on your screen or in
your taskbar and use that one, or just open http://127.0.0.1:8080 in your browser.

**macOS says the file cannot be opened**

macOS blocks files it did not download itself. Right-click (or Control-click) the file, choose
**Open** from the menu, and then click **Open** in the dialog. You only have to do this once.

## macOS notes

- Use http://127.0.0.1:8080 rather than "localhost". Safari sometimes gets confused by the latter.
- If macOS asks to allow Local Network access, click Allow. You can change it later under System
  Settings > Privacy & Security > Local Network.
- If a downloaded file will not open, right-click it and choose Open once.

## Where your files are

Everything you make is in the `data` folder inside VJHStudio: `data/outputs` for your images and
videos, `data/backups` for backups. Updating the app never touches it.

## For developers

See `docs/superpowers/specs/2026-09-20-runwarestudio-design.md`; `uv run pytest -q`.
