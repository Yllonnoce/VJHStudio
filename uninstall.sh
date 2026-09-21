#!/usr/bin/env bash
# uninstall.sh — undoes what install.sh set up on Linux or macOS.
# Your pictures, projects and settings are KEPT unless you add --purge.
# Run it with:  bash uninstall.sh
main() {
  set -u

  local DIR DATA PURGE PORT TRIES SERVICE DESKTOP INFO REPLY
  DIR="$(cd "$(dirname "$0")" && pwd)"   # the folder this script sits in
  [ -n "$DIR" ] || { echo "Cannot find my own folder."; exit 1; }
  DATA="${VJHSTUDIO_DATA_DIR:-$DIR/data}"   # where your pictures and settings live
  PURGE="no"
  case "${1:-}" in
    --purge) PURGE="yes" ;;
    "") ;;
    *) echo "Usage: bash uninstall.sh [--purge]   (--purge also deletes your data)"; exit 1 ;;
  esac
  PORT="${VJHSTUDIO_PORT:-8080}"

  # --- Step 1: read what the installer wrote down ------------------------------
  INFO="$DIR/data/install.json"   # install.sh always writes it here
  SERVICE="no"
  DESKTOP="no"
  if [ -f "$INFO" ]; then
    grep -q '"service": *true' "$INFO" && SERVICE="yes"
    grep -q '"desktop": *true' "$INFO" && DESKTOP="yes"
  fi

  # --- Step 2: remove the "start when I log in" entry --------------------------
  # This comes first on purpose: the system is told to restart VJHStudio whenever
  # it stops, so stopping it before removing this entry would just start it again.
  if [ "$SERVICE" = "yes" ]; then
    echo "Removing the start-at-login entry..."
    if [ "$(uname -s)" = "Darwin" ]; then
      launchctl unload -w "${HOME:-}/Library/LaunchAgents/com.yllonnoce.vjhstudio.plist" 2>/dev/null
      rm -f "${HOME:-}/Library/LaunchAgents/com.yllonnoce.vjhstudio.plist"
    else
      systemctl --user disable --now vjhstudio 2>/dev/null
      rm -f "${HOME:-}/.config/systemd/user/vjhstudio.service"
      systemctl --user daemon-reload 2>/dev/null
    fi
  fi

  # --- Step 3: ask the app to stop, then wait up to 10 seconds ----------------
  echo "Stopping VJHStudio if it is running..."
  curl -s -X POST "http://127.0.0.1:$PORT/api/shutdown" >/dev/null 2>&1
  TRIES=0
  while [ "$TRIES" -lt 10 ]; do
    curl -s -o /dev/null "http://127.0.0.1:$PORT/" 2>/dev/null || break
    TRIES=$((TRIES + 1))
    sleep 1
  done

  # --- Step 4: remove the shortcut ---------------------------------------------
  if [ "$DESKTOP" = "yes" ]; then
    echo "Removing the shortcut..."
    rm -f "${HOME:-}/.local/share/applications/vjhstudio.desktop"
    rm -f "${HOME:-}/Desktop/vjhstudio.desktop"
    rm -f "${HOME:-}/Desktop/VJHStudio.command"
  fi

  # --- Step 5: remove the installed Python packages and the installer's note ---
  echo "Removing the installed Python packages..."
  rm -rf "$DIR/.venv"
  rm -f "$INFO"

  # --- Step 6: only with --purge: delete your pictures, projects and settings --
  if [ "$PURGE" = "yes" ]; then
    echo ""
    echo "This deletes every picture, project and setting in $DATA"
    REPLY=""
    read -r -p "Type DELETE and press Enter to confirm: " REPLY || REPLY=""
    if [ "$REPLY" = "DELETE" ]; then
      rm -rf "$DATA"
      echo "Deleted: $DATA"
    else
      echo "Nothing was deleted."
    fi
  fi

  echo ""
  echo "Done. The VJHStudio folder, uv and Git were left in place."
  echo "Delete $DIR yourself if you no longer want it."
}
main "$@"
