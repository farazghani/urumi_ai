"""Run with `uv run python ../run.py`; Ctrl-C/SIGTERM stop every child."""
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
SERVICES = [
    ('Clock MCP', 8002, ['-m', 'backend.upstreams', 'clock']),
    ('Calculator MCP', 8003, ['-m', 'backend.upstreams', 'calculator']),
    ('Dashboard', 8001, ['-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', '8001']),
]


def check_ports():
    """Fail before starting anything; never terminate an existing listener."""
    occupied = []
    for name, port, _ in SERVICES:
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(('127.0.0.1', port))
            except OSError:
                occupied.append(f'{name}: 127.0.0.1:{port}')
    if occupied:
        print('Cannot start; these ports are already in use:\n  ' + '\n  '.join(occupied), file=sys.stderr)
        print('Stop the existing services, then rerun this command.', file=sys.stderr)
        return False
    return True


def stop_children(children):
    for _, child in children:
        if child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    for _, child in children:
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()


def interrupted(_signum, _frame):
    # A terminated launcher must execute finally, rather than orphan its children.
    raise KeyboardInterrupt


def main():
    children = []
    previous_handlers = {}
    try:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            previous_handlers[sig] = signal.signal(sig, interrupted)
        if not check_ports():
            return 1
        for name, _, command in SERVICES:
            child = subprocess.Popen([sys.executable, *command], cwd=ROOT / 'backend', start_new_session=True)
            children.append((name, child))
        ready = False
        deadline = time.monotonic() + 30
        while True:
            for name, child in children:
                status = child.poll()
                if status is not None:
                    print(f'{name} stopped (exit {status}); stopping the other services.', file=sys.stderr)
                    return 1
            if not ready:
                try:
                    with urlopen('http://127.0.0.1:8001/health', timeout=.3) as response:
                        ready = response.status == 200
                except (URLError, TimeoutError, OSError):
                    pass
                if ready:
                    print('Urumi dashboard: http://127.0.0.1:8001 (Ctrl-C stops all three services)', flush=True)
                elif time.monotonic() > deadline:
                    print('Dashboard startup timed out; stopping all services.', file=sys.stderr)
                    return 1
            time.sleep(.2)
    except KeyboardInterrupt:
        print('\nStopping the dashboard and MCP helpers…', flush=True)
        return 0
    finally:
        # Repeated signals must not interrupt cleanup halfway through.
        for sig in previous_handlers:
            signal.signal(sig, signal.SIG_IGN)
        try:
            stop_children(children)
        finally:
            for sig, handler in previous_handlers.items():
                signal.signal(sig, handler)


if __name__ == '__main__':
    sys.exit(main())
