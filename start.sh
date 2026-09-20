#!/usr/bin/env bash
# start.sh — Linux/macOS launcher. Loops while the app exits with 75 (restart requested).
main() {
  set -u
  local here
  here="$(cd "$(dirname "$0")" && pwd)"
  cd "$here" || exit 1
  local UV GIT
  UV="${VJHSTUDIO_UV:-}"
  if [ -z "$UV" ]; then
    for c in "$(command -v uv 2>/dev/null)" "${HOME:-}/.local/bin/uv" "${HOME:-}/.cargo/bin/uv"; do
      [ -n "$c" ] && [ -x "$c" ] && UV="$c" && break
    done
  fi
  if [ -z "$UV" ]; then
    # uv (the tool that runs the app's Python) is not installed yet. Install it now.
    echo "uv not found; installing it..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    UV="${HOME:-}/.local/bin/uv"
    if [ ! -x "$UV" ]; then
      echo "Could not install uv automatically. Install it yourself from https://astral.sh/uv and try again."
      exit 1
    fi
  fi
  GIT="${VJHSTUDIO_GIT:-$(command -v git 2>/dev/null || echo git)}"
  export VJHSTUDIO_UV="$UV" VJHSTUDIO_GIT="$GIT" VJHSTUDIO_HOME="$here" VJHSTUDIO_LAUNCHER=1
  local PORT="${VJHSTUDIO_PORT:-8080}" OPEN="--open" code
  case "${1:-}" in
    --no-browser) OPEN="--no-browser" ;;
    ''|--open) ;;
    *) PORT="$1"; [ "${2:-}" = "--no-browser" ] && OPEN="--no-browser" ;;
  esac
  while true; do
    "$UV" run --frozen python run.py serve --port "$PORT" $OPEN
    code=$?
    if [ "$code" -eq 75 ]; then echo "VJHStudio: restarting..."; OPEN="--no-browser"; sleep 1; continue; fi
    exit "$code"
  done
}
main "$@"
