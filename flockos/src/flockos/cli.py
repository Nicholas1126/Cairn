"""FlockOS one-shot launcher: start/stop/status a single uvicorn serving the unified app."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import click
import uvicorn

from flockos.app import build_app

RUN_DIR = Path(os.environ.get("FLOCKOS_HOME", Path.home() / ".cairn")) / "run"
PID_FILE = RUN_DIR / "flockos.pid"
LOG_FILE = RUN_DIR / "flockos.log"

FLOCK_STATIC = Path(__file__).resolve().parents[2] / "static" / "flock"
FLOCK_FRONTEND = Path(__file__).resolve().parents[3] / "flock" / "src" / "flock" / "frontend"


def _ensure_frontend() -> None:
    if (FLOCK_STATIC / "index.html").exists():
        return
    click.echo("Building flock frontend (first run)...")
    subprocess.run(["npm", "install"], cwd=FLOCK_FRONTEND, check=True)
    subprocess.run(["npm", "run", "build"], cwd=FLOCK_FRONTEND, check=True)
    FLOCK_STATIC.mkdir(parents=True, exist_ok=True)
    dist = FLOCK_FRONTEND / "dist"
    for item in dist.iterdir():
        dest = FLOCK_STATIC / item.name
        if item.is_dir():
            import shutil
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            dest.write_bytes(item.read_bytes())


def _read_pid() -> int | None:
    try:
        return int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


@click.group()
def main():
    """FlockOS launcher."""


@main.command()
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8000, show_default=True)
@click.option("--foreground", is_flag=True, help="Run in the foreground (do not daemonize).")
def start(host: str, port: int, foreground: bool):
    """Start FlockOS (unified flock + cairn web)."""
    _ensure_frontend()
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_pid()
    if existing and _alive(existing):
        click.echo(f"FlockOS already running (pid {existing})")
        return

    if foreground:
        uvicorn.run(build_app(), host=host, port=port)
        return

    log = open(LOG_FILE, "ab")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "flockos.app:build_app", "--factory",
         "--host", host, "--port", str(port)],
        stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True,
    )
    PID_FILE.write_text(str(proc.pid))
    click.echo(f"FlockOS started (pid {proc.pid}) on http://{host}:{port}  logs: {LOG_FILE}")


@main.command()
def stop():
    """Stop FlockOS."""
    pid = _read_pid()
    if pid is None:
        click.echo("FlockOS not running (no pid file)")
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        click.echo("Removing stale pid file")
        PID_FILE.unlink(missing_ok=True)
        return
    for _ in range(10):
        if not _alive(pid):
            break
        time.sleep(0.3)
    if _alive(pid):
        os.kill(pid, signal.SIGKILL)
    PID_FILE.unlink(missing_ok=True)
    click.echo(f"FlockOS stopped (pid {pid})")


@main.command()
def status():
    """Show FlockOS status."""
    pid = _read_pid()
    if pid and _alive(pid):
        click.echo(f"FlockOS running (pid {pid})")
    else:
        click.echo("FlockOS not running")
