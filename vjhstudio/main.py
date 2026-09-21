"""CLI entry point: serve | migrate | update | backup | restore | version | doctor."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import socket
import sys
import threading
import webbrowser
from pathlib import Path

import httpx

from . import __version__, config, secrets
from .services import archive, gitinfo, migrate, update

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
    """True when we could listen on host:port right now.

    POSIX: SO_REUSEADDR lets the bind succeed while the previous server's
    connections are still in TIME_WAIT (the normal state right after a
    restart) but still fails against an active listener. Windows: that same
    flag would let the bind succeed on a port another process is listening on,
    so use SO_EXCLUSIVEADDRUSE there instead.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if sys.platform == "win32":
            excl = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
            if excl is not None:
                s.setsockopt(socket.SOL_SOCKET, excl, 1)
        else:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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


def print_step(step: update.Step) -> None:
    print(f"[{'ok' if step.ok else '!!'}] {step.title}")
    if step.detail:
        for line in step.detail.splitlines():
            print(f"     {line}")


def cmd_update(args: argparse.Namespace) -> int:
    if args.check:
        info = update.check_updates()
        if info["current"]:
            print(f"current    : {info['current']}")
        if info["error"]:
            print(info["error"])
            return 0
        if not info["behind"]:
            print("Up to date.")
            return 0
        print(f"{info['behind']} commits behind:")
        for subject in info["commits"]:
            print(f"  - {subject}")
        print('Run "vjhstudio update" to install them.')
        return 0
    paths = config.resolve_paths()
    state = update.UpdateState(on_step=print_step)
    # A terminal run must never re-exec itself: this process holds no database
    # handle, so a restart here would only start the failing update over again.
    if not update.run_update(state, paths, restart_on_restore=False):
        if any(s.title == update.RESTORE_STEP for s in state.steps):
            print("Database restored from the pre-update backup; start the app again.")
        print(state.message or "Update failed.", file=sys.stderr)
        return 1
    print(state.message or "Update complete.")
    print("Restart VJHStudio to run the new version.")
    return 0


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def cmd_backup(args: argparse.Namespace) -> int:
    from . import boot

    paths = config.resolve_paths()
    try:
        info = boot.boot(paths)
    except migrate.MigrationFailed as e:
        print(str(e), file=sys.stderr)
        return config.MIGRATION_FAIL_EXIT_CODE
    try:
        dest = archive.create_archive(
            info.session_factory, paths, uploads=args.uploads, outputs=args.outputs
        )
    except (archive.ArchiveError, OSError) as e:
        print(str(e), file=sys.stderr)
        return 1
    finally:
        info.engine.dispose()
    print(f"{dest}  ({human_size(dest.stat().st_size)})")
    return 0


def confirm(prompt: str = "Type yes to continue: ") -> bool:
    try:
        return input(prompt).strip().lower() == "yes"
    except EOFError:
        return False


MERGE_ROW = "{:<15}{:>6}{:>10}{:>15}"


def print_merge_table(report: archive.MergeReport) -> None:
    print(MERGE_ROW.format("table", "new", "existing", "missing files"))
    for table in archive.MERGE_TABLES:
        c = report.counts[table]
        print(MERGE_ROW.format(table, c.new, c.existing, c.missing_files))


def cmd_merge(paths: config.Paths, zip_path: Path, *, yes: bool) -> int:
    """`restore --merge`: dry-run preview first, always; then the additive union."""
    from . import boot

    try:
        info = boot.boot(paths)
    except migrate.MigrationFailed as e:
        print(str(e), file=sys.stderr)
        return config.MIGRATION_FAIL_EXIT_CODE
    failures = (archive.ArchiveError, migrate.MigrationFailed, OSError)
    try:
        try:
            preview = archive.preview_merge(info.session_factory, paths, zip_path)
        except failures as e:
            print(f"{zip_path}: {e}", file=sys.stderr)
            return 1
        print(f"Merge {zip_path}")
        print(
            f"  created {preview.created_at or '?'} | app {preview.app_version or '?'} | "
            f"schema {preview.schema_revision or '?'}"
        )
        print_merge_table(preview)
        for problem in preview.errors:
            print(f"  ! {problem}")
        if preview.total_new == 0:
            print("Nothing new to merge.")
            return 0
        if not yes:
            print(f"This ADDS the rows above to {paths.db}. Rows you already have are kept.")
            print("A safety backup of the current database is written first.")
            if not confirm():
                print("Cancelled.")
                return 1
        try:
            report = archive.merge(info.session_factory, paths, zip_path)
        except failures as e:
            print(str(e), file=sys.stderr)
            # A merge that got far enough to take one leaves the database recoverable.
            safety = getattr(e, "safety_backup", None)
            if safety is not None:
                print(f"safety backup: {safety}", file=sys.stderr)
            return 1
        if report.safety_backup is not None:
            print(f"safety backup: {report.safety_backup}")
        print(archive.merge_summary(report))
        for problem in report.errors:
            print(f"  ! {problem}")
        return 0
    finally:
        info.engine.dispose()


def cmd_restore(args: argparse.Namespace) -> int:
    paths = config.resolve_paths()
    config.ensure_dirs(paths)
    zip_path = Path(args.zip)
    if args.merge:
        return cmd_merge(paths, zip_path, yes=args.yes)
    try:
        manifest = archive.manifest_of(zip_path)
    except archive.ArchiveError as e:
        print(f"{zip_path}: {e}", file=sys.stderr)
        return 1
    if not args.yes:
        includes = ", ".join(str(x) for x in (manifest.get("includes") or ["db"]))
        print(f"Restore {zip_path}")
        print(
            f"  created {manifest.get('created_at', '?')} | "
            f"app {manifest.get('app_version', '?')} | "
            f"schema {manifest.get('schema_revision', '?')} | includes {includes}"
        )
        print(f"This REPLACES the database at {paths.db} and overwrites the files in the archive.")
        print("A safety backup of the current database is written first.")
        if not confirm():
            print("Cancelled.")
            return 1
    try:
        result = archive.restore_replace(paths, zip_path)
    except (archive.ArchiveError, migrate.MigrationFailed, OSError) as e:
        print(str(e), file=sys.stderr)
        return 1
    if result.safety_backup is not None:
        print(f"safety backup: {result.safety_backup}")
    print("restored: " + ", ".join(f"{k}={v}" for k, v in sorted(result.counts.items())))
    print(f"outputs root: {result.outputs_root}")
    if result.missing_outputs or result.missing_assets:
        print(
            f"files not in the archive: {result.missing_outputs} outputs "
            f"(marked missing), {result.missing_assets} assets"
        )
    if result.skipped:
        print(f"skipped {len(result.skipped)} member(s) a restore does not own:")
        for name in result.skipped[:10]:
            print(f"  - {name}")
    print("Restart VJHStudio to use the restored data.")
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
    u = sub.add_parser("update", help="update VJHStudio from git")
    u.add_argument("--check", action="store_true", help="only report what is available")
    u.set_defaults(func=cmd_update)
    b = sub.add_parser("backup", help="write a backup archive to data/backups")
    b.add_argument("--uploads", action="store_true", help="include the asset library files")
    b.add_argument("--outputs", action="store_true", help="include the generated output files")
    b.set_defaults(func=cmd_backup)
    r = sub.add_parser("restore", help="restore a backup archive (replaces the database)")
    r.add_argument("zip", help="path to a vjhstudio-backup-*.zip")
    r.add_argument("--merge", action="store_true", help="merge instead of replace")
    r.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    r.set_defaults(func=cmd_restore)
    sub.add_parser("version", help="print version").set_defaults(func=cmd_version)
    sub.add_parser("doctor", help="print environment diagnostics").set_defaults(func=cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=log_level())
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
