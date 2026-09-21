#!/usr/bin/env bash
# install.sh — installs VJHStudio on Linux or macOS.
# It downloads the app, sets it up, asks two yes/no questions, and starts it.
# Run it with:  bash install.sh
main() {
  set -u

  # --- What you can change with flags (you normally need none of them) ---------
  local DIR BRANCH START SERVICE DESKTOP OS UV ANSWER
  DIR="${VJHSTUDIO_HOME:-${HOME:-}/VJHStudio}"  # where the app folder goes
  BRANCH="main"                                 # which version to download
  START="yes"                                   # start the app when we are done
  SERVICE="ask"                                 # start it at login? ask/yes/no
  DESKTOP="ask"                                 # make a shortcut? ask/yes/no

  while [ $# -gt 0 ]; do
    case "$1" in
      --dir) DIR="${2:-}"; shift 2 ;;
      --branch) BRANCH="${2:-}"; shift 2 ;;
      --no-start) START="no"; shift ;;
      --service) SERVICE="yes"; shift ;;
      --no-service) SERVICE="no"; shift ;;
      --desktop) DESKTOP="yes"; shift ;;
      --no-desktop) DESKTOP="no"; shift ;;
      -h|--help)
        echo "Usage: bash install.sh [options]"
        echo "  --dir <folder>    install into this folder (default: ${HOME:-}/VJHStudio)"
        echo "  --branch <name>   download this version (default: main)"
        echo "  --no-start        do not start the app at the end"
        echo "  --service         start it at login (--no-service to skip the question)"
        echo "  --desktop         put a shortcut on your Desktop (--no-desktop to skip)"
        exit 0 ;;
      *) echo "I do not understand '$1'. Run: bash install.sh --help"; exit 1 ;;
    esac
  done
  if [ -z "$DIR" ] || [ -z "$BRANCH" ]; then
    echo "--dir and --branch each need a value after them."
    exit 1
  fi

  case "$(uname -s)" in
    Darwin) OS="macos" ;;
    *) OS="linux" ;;
  esac
  echo "Installing VJHStudio into $DIR"

  # --- Step 1: make sure Git is installed (it downloads the app) ---------------
  if ! command -v git >/dev/null 2>&1; then
    echo "Git is missing. Installing it now..."
    if [ "$OS" = "macos" ]; then
      xcode-select --install
      echo "Finish the Apple install window that just opened, then run this script again."
      exit 1
    elif command -v apt-get >/dev/null 2>&1; then
      sudo apt-get update && sudo apt-get install -y git
    elif command -v dnf >/dev/null 2>&1; then
      sudo dnf install -y git
    elif command -v pacman >/dev/null 2>&1; then
      sudo pacman -S --noconfirm git
    elif command -v zypper >/dev/null 2>&1; then
      sudo zypper install -y git
    else
      echo "Please install Git yourself, then run this script again."
      exit 1
    fi
  fi

  # --- Step 2: make sure uv is installed (it runs the app's Python) ------------
  UV="$(command -v uv 2>/dev/null || true)"
  if [ -z "$UV" ] && [ -x "${HOME:-}/.local/bin/uv" ]; then UV="${HOME:-}/.local/bin/uv"; fi
  if [ -z "$UV" ]; then
    echo "uv is missing. Installing it now..."
    curl -LsSf https://astral.sh/uv/install.sh | sh -s -- --no-modify-path
    UV="${HOME:-}/.local/bin/uv"
  fi
  if [ ! -x "$UV" ]; then
    echo "Could not install uv. Install it from https://astral.sh/uv and run this script again."
    exit 1
  fi

  # --- Step 3: download the app, or update the copy that is already there ------
  if [ -d "$DIR/.git" ]; then
    echo "A copy is already in $DIR. Updating it..."
    git -C "$DIR" pull --ff-only || { echo "Could not update $DIR. Move it aside and try again."; exit 1; }
  else
    echo "Downloading VJHStudio..."
    git clone --branch "$BRANCH" https://github.com/yllonnoce/VJHStudio.git "$DIR" \
      || { echo "Download failed. Check your internet connection and try again."; exit 1; }
  fi
  cd "$DIR" || { echo "Could not open $DIR."; exit 1; }
  # Make sure the two scripts you will click on can be run.
  chmod +x start.sh uninstall.sh

  # --- Step 4: install the app's Python packages (this takes a few minutes) ----
  # "uv sync --frozen" installs exactly the package versions the app was tested with.
  echo "Setting up the app. This takes a few minutes the first time..."
  "$UV" sync --frozen || { echo "Setup failed. Run the script again."; exit 1; }

  # --- Step 5: create or update the database -----------------------------------
  "$UV" run --frozen python run.py migrate || { echo "Database setup failed."; exit 1; }

  # --- Step 6: the two questions (answering nothing means no) ------------------
  if [ "$SERVICE" = "ask" ]; then
    if [ -t 0 ]; then
      read -r -p "Start VJHStudio automatically when you log in? [y/N] " ANSWER
      case "$ANSWER" in [Yy]*) SERVICE="yes" ;; *) SERVICE="no" ;; esac
    else
      SERVICE="no"
    fi
  fi
  if [ "$DESKTOP" = "ask" ]; then
    if [ -t 0 ]; then
      read -r -p "Create a desktop link? [y/N] " ANSWER
      case "$ANSWER" in [Yy]*) DESKTOP="yes" ;; *) DESKTOP="no" ;; esac
    else
      DESKTOP="no"
    fi
  fi

  # --- Step 7: start at login, if that was answered yes ------------------------
  if [ "$SERVICE" = "yes" ] && [ "$OS" = "linux" ]; then
    mkdir -p "${HOME:-}/.config/systemd/user"
    cat > "${HOME:-}/.config/systemd/user/vjhstudio.service" <<EOF
[Unit]
Description=VJHStudio

[Service]
ExecStart=$DIR/start.sh --no-browser
Restart=always

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable --now vjhstudio
    echo "Tip: to keep VJHStudio running while you are logged out, run once:"
    echo "  sudo loginctl enable-linger $(id -un)"
  fi
  if [ "$SERVICE" = "yes" ] && [ "$OS" = "macos" ]; then
    mkdir -p "${HOME:-}/Library/LaunchAgents"
    cat > "${HOME:-}/Library/LaunchAgents/com.yllonnoce.vjhstudio.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.yllonnoce.vjhstudio</string>
  <key>ProgramArguments</key>
  <array><string>$DIR/start.sh</string><string>--no-browser</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict>
</plist>
EOF
    launchctl load -w "${HOME:-}/Library/LaunchAgents/com.yllonnoce.vjhstudio.plist"
  fi

  # --- Step 8: the shortcut, if that was answered yes --------------------------
  if [ "$DESKTOP" = "yes" ] && [ "$OS" = "linux" ]; then
    mkdir -p "${HOME:-}/.local/share/applications"
    cat > "${HOME:-}/.local/share/applications/vjhstudio.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=VJHStudio
Comment=Make pictures with VJHStudio
Exec=$DIR/start.sh
Path=$DIR
Icon=$DIR/vjhstudio/web/static/img/favicon.svg
Terminal=true
Categories=Graphics;
EOF
    chmod +x "${HOME:-}/.local/share/applications/vjhstudio.desktop"
    if [ -d "${HOME:-}/Desktop" ]; then
      cp "${HOME:-}/.local/share/applications/vjhstudio.desktop" "${HOME:-}/Desktop/vjhstudio.desktop"
      chmod +x "${HOME:-}/Desktop/vjhstudio.desktop"
    fi
  fi
  if [ "$DESKTOP" = "yes" ] && [ "$OS" = "macos" ]; then
    mkdir -p "${HOME:-}/Desktop"
    printf '#!/bin/sh\ncd "%s" && ./start.sh\n' "$DIR" > "${HOME:-}/Desktop/VJHStudio.command"
    chmod +x "${HOME:-}/Desktop/VJHStudio.command"
  fi

  # --- Step 9: write down what we did, so uninstall.sh can undo exactly that ---
  local SERVICE_JSON="false" DESKTOP_JSON="false"
  [ "$SERVICE" = "yes" ] && SERVICE_JSON="true"
  [ "$DESKTOP" = "yes" ] && DESKTOP_JSON="true"
  mkdir -p "$DIR/data"
  cat > "$DIR/data/install.json" <<EOF
{"version": 1,
 "home": "$DIR",
 "os": "$OS",
 "service": $SERVICE_JSON,
 "desktop": $DESKTOP_JSON,
 "installed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"}
EOF

  # --- Step 10: done. Start the app unless it is already running as a service --
  echo "VJHStudio is installed in $DIR"
  if [ "$SERVICE" = "yes" ] || [ "$START" = "no" ]; then
    echo "Open http://127.0.0.1:8080/ in your browser."
    exit 0
  fi
  exec ./start.sh
}
main "$@"
