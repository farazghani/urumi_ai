"""Deployment contracts: mounted keys, authenticated seeds and independent probes."""
import importlib

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from backend import store
from backend.upstreams import make_server


def test_mounted_key_and_authenticated_seed_survive_restart(tmp_path, monkeypatch, mariadb):
    monkeypatch.setattr(store, 'DATA', tmp_path / 'data')
    mounted = tmp_path / 'mounted-key'
    mounted.write_bytes(Fernet.generate_key())
    mounted.chmod(0o400)
    monkeypatch.setenv('ENCRYPTION_KEY_FILE', str(mounted))
    monkeypatch.setenv('ADMIN_PASSWORD', 'deployment-admin-password')
    monkeypatch.setenv('WOO_MCP_AUTH_TOKEN', 'private-mcp-token')
    monkeypatch.setenv('WOO_MCP_URL', 'http://release-woo-mcp:8000/mcp')
    with store.db() as connection, connection.cursor() as cursor:
        cursor.execute('SET FOREIGN_KEY_CHECKS=0')
        for table in ('sessions', 'api_keys', 'audit', 'upstreams', 'settings', 'users'):
            cursor.execute(f'DROP TABLE IF EXISTS `{table}`')
    store.init()
    saved = store.query("SELECT * FROM upstreams WHERE name='WooCommerce'", one=True)
    assert saved['auth_header'] == 'Authorization'
    assert saved['url'] == 'http://release-woo-mcp:8000/mcp'
    assert 'private-mcp-token' not in saved['credential']
    assert store.unseal(saved['credential']) == 'Bearer private-mcp-token'
    assert not (store.DATA / 'encryption.key').exists()
    store.init()
    assert len(store.query('SELECT id FROM upstreams')) == 3
    assert store.unseal(saved['credential']) == 'Bearer private-mcp-token'


def test_liveness_does_not_require_database(monkeypatch):
    module = importlib.import_module('backend.app')
    def unavailable(*args, **kwargs):
        raise store.pymysql.OperationalError('private database error')
    monkeypatch.setattr(store, 'query', unavailable)
    # No lifespan: simulate a database outage after application startup.
    client = TestClient(module.app)
    assert client.get('/live').status_code == 200
    response = client.get('/health')
    assert response.status_code == 503
    assert 'private database error' not in response.text


def test_helper_health_endpoints():
    for kind in ('clock', 'calculator'):
        with TestClient(make_server(kind).http_app()) as client:
            assert client.get('/health').status_code == 200
