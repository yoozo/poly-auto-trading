#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

WORKSPACE = Path('/Users/yoozo/Documents/poly-auto-trading')
FRONTEND = WORKSPACE / 'frontend'
LOG_DIR = Path('/private/tmp/poly-auto-trading')
BACKEND_PORT = 8000
FRONTEND_PORT = 5173


def fail(message: str, code: int = 1) -> None:
    print(f'[ERROR] {message}')
    raise SystemExit(code)


def require(command: str) -> str:
    path = shutil.which(command)
    if not path:
        fail(f'Missing required command: {command}')
    return path


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(('127.0.0.1', port)) == 0


def run(command: list[str], cwd: Path) -> None:
    print(f'[RUN] {" ".join(command)}')
    subprocess.run(command, cwd=cwd, check=True)


def start(name: str, command: list[str], cwd: Path, log_name: str) -> subprocess.Popen:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / log_name
    log_file = log_path.open('ab')
    print(f'[START] {name}: {" ".join(command)}')
    print(f'[LOG] {log_path}')
    return subprocess.Popen(
        command,
        cwd=cwd,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def wait_for_url(url: str, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=1.0) as response:
                if 200 <= response.status < 500:
                    return True
        except URLError:
            time.sleep(0.4)
    return False


def main() -> int:
    if not WORKSPACE.exists():
        fail(f'Workspace does not exist: {WORKSPACE}')
    if not FRONTEND.exists():
        fail(f'Frontend directory does not exist: {FRONTEND}')

    require('python3.13')
    require('uv')
    require('node')
    require('npm')

    run(['uv', 'sync', '--dev'], WORKSPACE)
    if not (FRONTEND / 'node_modules').exists():
        run(['npm', 'install'], FRONTEND)

    backend_started = None
    frontend_started = None

    if port_open(BACKEND_PORT):
        print(f'[OK] Backend port {BACKEND_PORT} already open')
    else:
        backend_started = start(
            'backend',
            ['uv', 'run', 'uvicorn', 'app.main:app', '--reload', '--port', str(BACKEND_PORT)],
            WORKSPACE,
            'backend.log',
        )

    if port_open(FRONTEND_PORT):
        print(f'[OK] Frontend port {FRONTEND_PORT} already open')
    else:
        frontend_started = start(
            'frontend',
            ['npm', 'run', 'dev', '--', '--port', str(FRONTEND_PORT)],
            FRONTEND,
            'frontend.log',
        )

    health_ok = wait_for_url(f'http://localhost:{BACKEND_PORT}/health')
    frontend_ok = wait_for_url(f'http://localhost:{FRONTEND_PORT}/')

    print('')
    print('Poly Auto Trading startup summary')
    print(f'- Backend:  http://localhost:{BACKEND_PORT}  health={"ok" if health_ok else "not ready"}')
    print(f'- Frontend: http://localhost:{FRONTEND_PORT}  ready={"ok" if frontend_ok else "not ready"}')
    print(f'- Logs:     {LOG_DIR}')

    if backend_started:
        print(f'- Backend PID:  {backend_started.pid}')
    if frontend_started:
        print(f'- Frontend PID: {frontend_started.pid}')

    if not health_ok or not frontend_ok:
        print('[WARN] One or more services did not become ready before timeout. Check logs above.')
        return 2
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('Interrupted')
        raise SystemExit(130)
