# Console menu for the Linux install: design

Date: 2026-11-17. Status: waiting for the owner's approval.

## Intent

The owner wants the VJHStudio desktop link on Linux to open a terminal menu that can start, stop
and restart the app, the same way the "RoadTales Server" desktop link does for RoadTales.

Today the link runs `start.sh` in the terminal: the window *is* the server, and closing it stops
the app. After this change the server runs in the background and the window is only a control
panel.

## Goals

- One desktop link that opens a menu showing whether VJHStudio is running, with start, stop and
  restart.
- The server keeps running when the menu window is closed.
- The installer stays simple: no new questions.

## Non-goals

- Windows and macOS. Their installers, `start.bat` and the macOS `.command` link are not touched
  (owner, 2026-11-17: "just linux install").
- Changing how `start.sh` works when run by hand.

## Anti-goals

- A second way of running in the background beside systemd (process id files, our own log files).
- Two servers fighting over the port: the menu must never start a second copy next to the service.

## Approaches

**A. systemd user service, like RoadTales (chosen by the owner).** `install.sh` always writes the
`vjhstudio` user service on Linux. The menu calls `systemctl --user` to start, stop and restart it
and `journalctl` to show the log. systemd brings it back after a crash and after the in-app
"restart" (exit code 75, which `start.sh` already loops on). Needs a Linux with systemd.

**B. Our own background runner.** The menu launches `start.sh` detached and tracks it with a
process id file and a log file. Works without systemd, but we would own stale id files, duplicate
servers and a mechanism that overlaps the login service. Rejected for that reason.

## Design

### The command

A new subcommand, `vjhstudio console`, in a new module `vjhstudio/console.py`. It loops: print the
status, print the choices, read a choice, act, repeat.

```
VJHStudio 0.5.0

  Server    running · http://127.0.0.1:8080 answering · since 09:14
  At login  on
  Update    up to date

  1  Start         5  Follow the log
  2  Stop          6  Update
  3  Restart       7  Backup now
  4  Open browser  8  Start at login: turn off
  q  Quit
>
```

| Choice | What it does |
|---|---|
| Start / Stop / Restart | `systemctl --user start\|stop\|restart vjhstudio`, then the new state is shown |
| Open browser | opens the app's address in the default browser |
| Follow the log | `journalctl --user -u vjhstudio -n 60 -f` in this window; Ctrl-C returns to the menu |
| Update | runs the existing `vjhstudio update` in this window, then restarts the service if it was running |
| Backup now | runs the existing `vjhstudio backup` in this window |
| Start at login | `systemctl --user enable` or `disable`; the label shows which one it will do |
| Enter | refreshes the status |

Status lines:

- **Server**: the service state from `systemctl --user is-active`, whether the address answers
  (a short HTTP request with a 2 second limit), and since when it has been up.
- **At login**: from `systemctl --user is-enabled`.
- **Update**: from the existing `update.check_updates()`; a failure to check prints "could not
  check" and never blocks the menu.

Ctrl-C at the prompt quits the menu. Ctrl-C during the log or a command ends only that command.

### The installer (`install.sh`, Linux only)

- The service file is always written, with the same content as today.
- The "start at login?" question stays. Yes means `enable --now`; no means the file is written but
  not enabled, and the installer starts it once with `systemctl --user start`.
- The desktop link's `Exec` changes from `start.sh` to a small new script, `console.sh`, which runs
  `uv run --frozen python run.py console` and waits for Enter if that fails, so an error stays
  readable.
- `data/install.json` gains nothing new; `uninstall.sh` now removes the service file on Linux
  whether or not "start at login" was chosen.
- No systemd on the machine (`systemctl --user` does not answer): the installer behaves exactly as
  it does today and the desktop link keeps pointing at `start.sh`.

### Existing installs

Running `install.sh` again over an existing install rewrites the service and the link. Nothing
migrates by itself.

## Calls made by the builder, not decided by the owner

- The menu is Python inside the app, not a shell script: the status and choice logic can be
  unit-tested, and it reuses the existing update and backup code.
- The port in the address comes from the same setting the server uses (`VJHSTUDIO_PORT`, default
  8080).
- Tests: the pure parts (status text, choice parsing, the `systemctl` argument lists) get unit
  tests with a fake command runner; `tests/test_installers.py` is extended for the changed
  `install.sh` and `uninstall.sh`. No test starts a real service.
- README: the Linux section gets two lines about the menu.

## Left to the builder

Exact wording of the menu and messages, and the internal function layout of `console.py`.
