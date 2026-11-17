#!/usr/bin/env bash
# console.sh — Linux only. Opens the VJHStudio menu: start, stop, restart, logs.
# The desktop link runs this. The app itself keeps running when you close the window.
main() {
  set -u
  local here UV code
  here="$(cd "$(dirname "$0")" && pwd)"
  cd "$here" || exit 1
  UV="${VJHSTUDIO_UV:-}"
  if [ -z "$UV" ]; then
    for c in "$(command -v uv 2>/dev/null)" "${HOME:-}/.local/bin/uv" "${HOME:-}/.cargo/bin/uv"; do
      [ -n "$c" ] && [ -x "$c" ] && UV="$c" && break
    done
  fi
  if [ -z "$UV" ]; then
    echo "uv was not found. Run ./start.sh once; it installs uv."
    read -r -p "Press Enter to close" _
    exit 1
  fi
  export VJHSTUDIO_UV="$UV" VJHSTUDIO_HOME="$here"
  export VJHSTUDIO_GIT="${VJHSTUDIO_GIT:-$(command -v git 2>/dev/null || echo git)}"
  "$UV" run --frozen python run.py console
  code=$?
  # Something went wrong: keep the words on screen until you have read them.
  if [ "$code" -ne 0 ]; then echo; read -r -p "Press Enter to close" _; fi
  exit "$code"
}
main "$@"
