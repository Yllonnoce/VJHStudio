"""CLI entry point: serve | migrate | version | doctor."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import socket
import sys
import threading
import webbrowser

import httpx

from . import __version__, config, secrets
from .services import gitinfo, migrate

log = logging.getLogger("vjhstudio")

LOG_LEVELS = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG")


def log_level(env=None) -> str:
    """VJHSTUDIO_LOG_LEVEL normalised to an upper-case level name.

    Accepts any casing (``debug`` as well as ``DEBUG``) and falls back to INFO
    for an unknown name rather than letting logging raise before the CLI runs.
    """
    env = os.environ if env is None else env
    name = (env.get("VJHSTUDIO_LOG_LEVEL") or "INFO").strip().upper()
    if name == "WARN":
        name = "WARNING"
    if name not in LOG_LEVELS:
        return "INFO"
    return name


def is_ours(host: str, port: int) -> bool:
    try:
        r = httpx.get(f"http://{host}:{port}/api/health", timeout=1.5)
        return r.status_code == 200 and r.json().get("app") == "VJHStudio"
    except Exception:  # noqa: BLE001
        return False


def _port_free(host: str, port: int) -> bool:
    # Deliberately no SO_REUSEADDR: on Windows that flag lets the bind succeed
    # on a port another socket is actively listening on, which would report a
    # busy port as free.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def pick_port(host: str, port: int, tries: int = 10) -> tuple[int, bool]:
    """Return (port, already_ours). If the requested port runs VJHStudio, report it.
    Otherwise walk forward until a free port is found.

    The "is it us?" question comes first so the answer never depends on whether
    the platform's bind test can see a listening socket.
    """
    if is_ours(host, port):
        return port, True
    if _port_free(host, port):
        return port, False
    for p in range(port + 1, port + 1 + tries):
        if _port_free(host, p):
            return p, False
    raise SystemExit(f"No free port in {port}..{port + tries}")


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from . import boot
    from .web.app import create_app

    paths = config.resolve_paths()
    host = args.host or config.env_str(os.environ, "VJHSTUDIO_HOST", config.DEFAULT_HOST)
    want = args.port or config.env_int(os.environ, "VJHSTUDIO_PORT", config.DEFAULT_PORT)
    port, ours = pick_port(host, want)
    url = f"http://{host}:{port}/"
    if ours:
        print(f"VJHStudio is already running at {url}")
        if args.open:
            webbrowser.open(url)
        return 0
    if port != want:
        print(f"Port {want} is busy; using {port}")
    try:
        info = boot.boot(paths)
    except migrate.MigrationFailed as e:
        print(str(e), file=sys.stderr)
        return config.MIGRATION_FAIL_EXIT_CODE
    app = create_app(paths, port=port, boot_info=info)
    if args.open:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"VJHStudio {__version__} on {url}  (data: {paths.data})")
    uvicorn.run(app, host=host, port=port, log_level=log_level().lower())
    return 0


def cmd_migrate(_args: argparse.Namespace) -> int:
    paths = config.resolve_paths()
    config.ensure_dirs(paths)
    try:
        from . import boot

        info = boot.boot(paths)
    except migrate.MigrationFailed as e:
        print(str(e), file=sys.stderr)
        return config.MIGRATION_FAIL_EXIT_CODE
    print(f"schema {info.schema_revision} at {paths.db}")
    return 0


def cmd_version(_args: argparse.Namespace) -> int:
    c = gitinfo.current_commit()
    print(f"VJHStudio {__version__}" + (f" ({c.short} {c.subject})" if c else ""))
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    paths = config.resolve_paths()
    port = config.env_int(os.environ, "VJHSTUDIO_PORT", config.DEFAULT_PORT)
    print(f"version    : {__version__}")
    print(f"python     : {sys.version.split()[0]} ({sys.executable})")
    print(f"data dir   : {paths.data} ({'exists' if paths.data.exists() else 'missing'})")
    print(f"database   : {paths.db} schema={migrate.current(paths.db)} head={migrate.head()}")
    print(f"api key    : {secrets.key_source(paths)}")
    print(
        f"git        : {'checkout' if gitinfo.is_git_install() else 'not a git checkout'} "
        f"{(gitinfo.current_commit() or gitinfo.CommitInfo('', '-', '')).short}"
    )
    print(f"uv         : {os.environ.get('VJHSTUDIO_UV') or shutil.which('uv') or 'not found'}")
    print(f"git binary : {os.environ.get('VJHSTUDIO_GIT') or shutil.which('git') or 'not found'}")
    print(f"launcher   : {'yes' if os.environ.get('VJHSTUDIO_LAUNCHER') == '1' else 'no'}")
    if sys.platform == "darwin":
        print("macOS notes:")
        print(
            f'  - Open the app at http://127.0.0.1:{port}/ (not "localhost": Safari may try IPv6 first).'
        )
        print(
            '  - If macOS asks to allow "Local Network" access, allow it: '
            "System Settings > Privacy & Security > Local Network."
        )
        print(
            "  - If a downloaded launcher will not open, right-click it and choose Open once, "
            "or run: xattr -d com.apple.quarantine <file>"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vjhstudio")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the web app")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    g = s.add_mutually_exclusive_group()
    g.add_argument("--open", dest="open", action="store_true", help="open the browser")
    g.add_argument("--no-browser", dest="open", action="store_false")
    s.set_defaults(open=False, func=cmd_serve)
    sub.add_parser("migrate", help="create/upgrade the database").set_defaults(func=cmd_migrate)
    sub.add_parser("version", help="print version").set_defaults(func=cmd_version)
    sub.add_parser("doctor", help="print environment diagnostics").set_defaults(func=cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=log_level())
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
