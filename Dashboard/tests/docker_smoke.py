"""Real Docker smoke test. Run with Dashboard/backend/.venv/bin/python.
Uses an isolated project, temporary fixture credentials/data, and a random loopback port.
Never sends model requests or changes the existing WooCommerce store.
"""
import asyncio
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from legacy_fixture import create
import secrets

ROOT = Path(__file__).resolve().parents[1]
PROJECT = f'urumi-smoke-{os.getpid()}'


def main():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, DASHBOARD_PORT=str(port), ADMIN_EMAIL='qa@example.test',
               ADMIN_PASSWORD='docker-fixture-password', WOO_MCP_URL='http://127.0.0.1:1/mcp',
               MYSQL_DATABASE='urumi_dashboard', MYSQL_USER='urumi', MYSQL_PASSWORD=secrets.token_urlsafe(24), MYSQL_ROOT_PASSWORD=secrets.token_urlsafe(24))
    command = ['docker', 'compose', '-f', str(ROOT / 'docker-compose.yml'), '--project-name', PROJECT]
    base = f'http://127.0.0.1:{port}'
    with tempfile.TemporaryDirectory(prefix='urumi-docker-test-') as directory, tempfile.TemporaryFile(mode='w+') as logs:
        def compose(*args, check=True):
            return subprocess.run([*command, *args], env=env, stdout=logs, stderr=logs, check=check)
        try:
            # Build a disposable local workspace to exercise safe import and secret decryption.
            source = Path(directory) / 'source'
            create(source, env['ADMIN_EMAIL'], env['ADMIN_PASSWORD'])
            migration = [sys.executable, str(ROOT / 'import-local-data.py'), '--source', str(source), '--project-name', PROJECT]
            # Force a late FK failure; the next successful import proves all rows rolled back.
            with sqlite3.connect(source / 'dashboard.db') as legacy:
                legacy.execute("UPDATE audit SET user_id=99999")
            assert subprocess.run(migration, env=env, stdout=logs, stderr=logs).returncode != 0
            with sqlite3.connect(source / 'dashboard.db') as legacy:
                legacy.execute("UPDATE audit SET user_id=1")
            subprocess.run(migration, env=env, stdout=logs, stderr=logs, check=True)
            print('PASS: failed import rolls back all database rows', flush=True)
            result = subprocess.run(migration, env=env, stdout=logs, stderr=logs)
            assert result.returncode != 0, 'Import must refuse to overwrite an existing volume'
            print('PASS: imported SQLite and encryption key; refused a second import', flush=True)
            compose('up', '-d', '--no-build', '--wait', '--wait-timeout', '90')
            with httpx.Client(base_url=base, timeout=20) as client:
                page = client.get('/')
                assert page.status_code == 200 and 'Urumi' in page.text
                assert client.get('/static/app.js').status_code == 200
                assert client.get('/static/style.css').status_code == 200
                assert client.get('/backend/.data/dashboard.db').status_code == 404
                assert client.get('/frontend-health').status_code == 200
                assert client.get('/health').json() == {'status':'ok'}
                assert 'Content-Security-Policy' in page.headers
                print('PASS: separate Nginx frontend serves assets and proxies backend health', flush=True)
                response = client.post('/api/auth/login', headers={'Origin':base}, json={'email':env['ADMIN_EMAIL'],'password':env['ADMIN_PASSWORD']})
                assert response.status_code == 200, response.text
                assert 'HttpOnly' in response.headers['set-cookie']
                assert client.get('/api/me').json()['email'] == env['ADMIN_EMAIL']
                assert client.post('/api/keys', headers={'Origin':'https://invalid.example'}, json={'name':'blocked'}).status_code == 403
                print('PASS: proxied origin/port, login cookies, and cross-origin rejection', flush=True)
                servers = client.get('/api/servers').json()
                assert next(s for s in servers if s['name']=='Clock')['status'] == 'online'
                assert next(s for s in servers if s['name']=='Calculator')['status'] == 'online'
                assert next(s for s in servers if s['name']=='Custom')['url'] == 'https://example.invalid/mcp'
                assert 'fixture-upstream-secret' not in json.dumps(servers)
                assert client.get('/api/settings/llm').json()['has_api_key']
                assert any(row['target']=='fixture' for row in client.get('/api/audit').json())
                imported = client.post('/mcp', headers={'Authorization':'Bearer fixture-legacy-api-key'}, json={'jsonrpc':'2.0','id':1,'method':'tools/list'})
                assert imported.status_code == 200
                new_server = client.post('/api/servers', json={'name':'Sequence check','url':'https://example.invalid/mcp','enabled':False}).json()
                assert new_server['id'] > 40, new_server
                key = client.post('/api/keys', headers={'Origin':base}, json={'name':'container-check'}).json()['token']
                async def mcp_check():
                    async with Client(StreamableHttpTransport(base+'/mcp', headers={'Authorization':'Bearer '+key})) as mcp:
                        tools = await mcp.list_tools()
                        assert len(tools) == 4  # WooCommerce is deliberately unavailable in this fixture.
                        result = await mcp.call_tool('s3__calculate', {'operation':'multiply','a':84,'b':12})
                        assert result.structured_content['result'] == 1008
                asyncio.run(mcp_check())
                print('PASS: authenticated MCP through Nginx; Docker helpers and imported secrets work', flush=True)
                compose('up', '-d', '--no-deps', '--force-recreate', '--wait', '--wait-timeout', '120', 'db')
                assert client.get('/api/me').status_code == 200
                # Recreate backend to verify both persistent data and refreshed proxy DNS.
                compose('up', '-d', '--no-deps', '--no-build', '--force-recreate', '--wait', '--wait-timeout', '60', 'backend')
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    try:
                        if client.get('/api/me').status_code == 200:
                            break
                    except httpx.RequestError:
                        pass
                    time.sleep(.5)
                else:
                    raise AssertionError('Frontend did not reconnect to recreated backend')
                asyncio.run(mcp_check())
                print('PASS: sessions, API keys and encrypted settings persist across backend recreation', flush=True)
                compose('exec','-T','backend','python','-c',"import os,urllib.request,urllib.error; assert os.getuid()==10001; assert not os.path.exists('/app/frontend'); r=urllib.request.urlopen('http://127.0.0.1:8001/health'); assert r.status==200")
                print('PASS: non-root API-only backend has no frontend assets', flush=True)
        except Exception:
            compose('logs','--no-color','--tail','35',check=False)
            logs.seek(0)
            print(logs.read()[-14000:], file=sys.stderr)
            raise
        finally:
            # Only this test's generated project/volume is removed.
            compose('down','--volumes','--remove-orphans',check=False)
    print('Docker smoke test passed; temporary containers and volume removed.', flush=True)


if __name__ == '__main__':
    main()
