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

**Windows**

Step 1 — open a terminal (see "The terminal" above if you have not already).

Step 2 — copy the line below. It installs Git and uv if you do not have them yet, downloads
VJHStudio into `%USERPROFILE%\VJHStudio`, and starts it. It asks you two quick questions along
the way (see below).

Step 3 — paste the line into the terminal and press Enter:

```
curl.exe -fsSLo %TEMP%\install.bat https://raw.githubusercontent.com/yllonnoce/VJHStudio/main/install.bat && %TEMP%\install.bat
```

**macOS and Linux**

Step 1 — open a terminal (see "The terminal" above if you have not already).

Step 2 — copy the line below. It installs Git and uv if you do not have them yet, downloads
VJHStudio into `~/VJHStudio`, and starts it. It asks you two quick questions along the way (see
below).

Step 3 — paste the line into the terminal and press Enter:

```
curl -fsSL https://raw.githubusercontent.com/yllonnoce/VJHStudio/main/install.sh | bash
```

The installer asks two questions: whether to start VJHStudio automatically when you log in, and
whether to put a link on your desktop. Answering no to both is fine — you can always start it with
start.sh / start.bat.

You will see a lot of text scroll past while it sets itself up — that is normal, and you do not
need to read it. This takes a few minutes the first time. When it is ready your web browser opens
by itself at **http://127.0.0.1:8080**, and the terminal window stays open in the background.

Keep that terminal window open. Closing it stops the app.

## First run

In the browser, click **Settings**.

Paste your RunWare API key into the box and click **Save**, then click **Test**. The page explains
how to get a key if you do not have one yet.

If the test worked, your balance appears at the top of the page.

## Make your first image

1. The home page has two big cards, **Create an image** and **Create a video**. Click **Create an
   image** (or click **Generate** in the top menu).
2. Type a few words into **Subject** — for example "a red fox in a snowy forest".
3. Under Style, Mood, Lighting and the other boxes, click a few of the ideas to add them, or just
   type your own. Clicking an idea again removes it.
4. Pick a model from the dropdown if you want something other than the default, then click
   **Generate**.
5. Watch the progress bar; when it finishes, your image appears in **Results** and in the
   **Gallery**.

## Models

Click **Models** in the top menu to see every model VJHStudio knows about, with its price and a
few badges.

Some models only accept certain image sizes or video lengths, and a few video models can only edit
an existing video rather than make a new one. VJHStudio starts out knowing the common cases, but you
can ask it to check for sure: click **Harvest constraints**. This asks RunWare which sizes and
lengths each model accepts, and reads the model's page on RunWare's own docs site. It is free —
nothing is generated and nothing is charged, and the check stops itself immediately if your balance
ever moves.

Once a model has been checked, the Models page shows a small **sizes known** badge next to it, and
the **Generate** page only offers sizes that actually work for that model — no more guessing a size
and having the job rejected. Video models that can only edit an existing video (and cannot yet be
given one) are hidden from the Generate dropdown; they stay on the Models page with a badge
explaining why.

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

## Updating

Open the app, click **Settings**, then **Updates**. You will see the version you have and the
commit it is built from. Click **Check for updates** to see what is new.

If there is something new, click **Update now**. VJHStudio backs up your database, downloads the
new version, installs it, updates the database, and restarts itself — you do not need to do
anything else. If your app is behind, the header shows an "Update available" badge so you notice
without checking Settings first.

If the update fails partway through, VJHStudio restores the backup it took first, so the app is
left exactly as it was before you clicked Update now.

**Manually, from a terminal:** go to the VJHStudio folder and run `git pull`, then start the app
again the normal way (`./start.sh` or `start.bat`).

**From a terminal, without opening the app:**

```
uv run vjhstudio update --check
uv run vjhstudio update
```

## Backups

Open the app, click **Settings**, then **Backups**.

Click **Create backup** to make a copy you can keep or move elsewhere. Two checkboxes let you
choose what goes in it, on top of your database (which is always included):

- **uploads** — the sample images and videos you added yourself.
- **outputs** — the images and videos VJHStudio generated for you. This can be large.

From the Backups list you can **Download** a backup to save it somewhere safe (a USB drive, cloud
storage, another computer).

- **Restore** replaces everything currently in VJHStudio with what is in the backup. A safety copy
  of your database is taken first, in case you picked the wrong file; files with the same name are
  replaced. Then the app restarts.
- **Merge** only adds what you do not already have — it never removes or overwrites anything, and
  it never copies your settings or your RunWare API key. It shows you a preview of what will be
  added before it changes anything.

**From a terminal:**

```
uv run vjhstudio backup --uploads --outputs
uv run vjhstudio restore <zip>
uv run vjhstudio restore <zip> --merge
```

## Uninstall

If you installed VJHStudio with the one-line installer above, an uninstaller was placed in the
VJHStudio folder alongside it.

**macOS and Linux** — open a terminal, go to the VJHStudio folder, and run:

```
./uninstall.sh
```

**Windows** — open the VJHStudio folder and double-click `uninstall.bat`, or run it from a
terminal:

```
uninstall.bat
```

This stops the app, removes the start-at-login entry and the desktop link if you had them, and
removes the `.venv` folder it installed. Your `data` folder — your projects, prompts, images and
videos — is kept.

To also delete your `data` folder, add `--purge` (`/purge` on Windows):

```
./uninstall.sh --purge
```

```
uninstall.bat /purge
```

You will be asked to type `DELETE` and press Enter to confirm. Nothing is deleted unless you type
that exact word.

## macOS notes

- Use http://127.0.0.1:8080 rather than "localhost". Safari sometimes gets confused by the latter.
- If macOS asks to allow Local Network access, click Allow. You can change it later under System
  Settings > Privacy & Security > Local Network.
- If a downloaded file will not open, right-click it and choose Open once.

## Where your files are

Everything you make is in the `data` folder inside VJHStudio: `data/outputs` for your images and
videos, `data/backups` for backups. An update only adds to it: a safety backup of your database is
written to `data/backups` before anything else happens.

## For developers

See `docs/superpowers/specs/2026-09-20-runwarestudio-design.md`; `uv run pytest -q`.
