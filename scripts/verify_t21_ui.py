"""Latest UI E2E with real API/Worker, synthetic model, fresh data and free ports."""
import os
import socket
import subprocess
import time
import tempfile
from pathlib import Path
import httpx
from agentbuddy.config import Settings
from agentbuddy.persistence.migrate import upgrade
from verify_t02 import ROOT, PYTHON, PNPM


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main():
    folder = ROOT / 'data/validation' / ('t21-ui-' + time.strftime('%Y%m%d-%H%M%S'))
    folder.mkdir(parents=True, exist_ok=False)
    data_folder = Path(tempfile.mkdtemp(prefix='agentbuddy-t21-ui-')).resolve()
    (folder / 'data-directory.txt').write_text(str(data_folder), encoding='utf-8')
    settings = Settings(data_dir=data_folder)
    settings.prepare_directories()
    upgrade(settings.database_path)
    api_port, ui_port, model_port = free_port(), free_port(), free_port()
    env = {**os.environ, 'DATA_DIR': str(data_folder), 'T02_E2E': '1', 'T04_E2E': '1',
           'T21_UI_URL': f'http://127.0.0.1:{ui_port}',
           'API_PROXY_TARGET': f'http://127.0.0.1:{api_port}',
           'T21_MODEL_URL': f'http://127.0.0.1:{model_port}/qwen_dashscope',
           'T21_RESULTS': str(folder / 'results'), 'APP_BASE_PATH': '/app', 'API_PREFIX': '/api/v1',
           'T21_PYTHON': str(PYTHON), 'T21_MCP_SCRIPT': str(ROOT / 'scripts/t21_mcp_fixture.py')}
    children, logs = [], []
    def start(name, args, cwd=ROOT):
        log = (folder / f'{name}.log').open('w', encoding='utf-8')
        logs.append(log)
        child = subprocess.Popen(args, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        children.append(child)
        return child
    def ready(url, child):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and child.poll() is None:
            try:
                if httpx.get(url).status_code == 200: return
            except httpx.HTTPError: pass
            time.sleep(.2)
        raise RuntimeError(f'Service not ready: {url}; see {folder}')
    print(f'Isolated evidence: {folder}', flush=True)
    try:
        fixture = start('fixture', [str(PYTHON), '-m', 'uvicorn', 't21_provider_fixture:app', '--app-dir', str(ROOT/'scripts'), '--host', '127.0.0.1', '--port', str(model_port)])
        ready(f'http://127.0.0.1:{model_port}/openapi.json', fixture)
        api = start('api', [str(PYTHON), '-m', 'uvicorn', 'agentbuddy.main:create_app', '--factory', '--host', '127.0.0.1', '--port', str(api_port)])
        ready(f'http://127.0.0.1:{api_port}/api/v1/health', api)
        start('worker', [str(PYTHON), '-m', 'agentbuddy.worker'])
        ui = start('vite', ['node', str(ROOT/'frontend/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', str(ui_port)], ROOT/'frontend')
        ready(env['T21_UI_URL']+'/app/', ui)
        subprocess.run([PNPM, '--dir', 'frontend', 'test:e2e', '--config', 'tests/t21.config.ts'], cwd=ROOT, env=env, check=True, timeout=300)
    finally:
        for child in reversed(children):
            child.terminate()
            try: child.wait(timeout=12)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        for log in logs: log.close()
        ledger = data_folder / 'external-calls.jsonl'
        if ledger.exists(): (folder / ledger.name).write_bytes(ledger.read_bytes())


if __name__ == '__main__': main()
