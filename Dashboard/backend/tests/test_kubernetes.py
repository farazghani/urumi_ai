"""Deployment contracts: mounted keys, authenticated seeds and independent probes."""
import importlib
import pytest

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
        for table in ('stores','sessions', 'api_keys', 'audit', 'upstreams', 'settings', 'users'):
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


def test_store_resources_preserve_secret_and_refuse_foreign_owner():
    import asyncio
    from backend.provisioner import Kube, ProvisionError
    from backend.store_resources import OWNER, STORE
    kube = object.__new__(Kube)
    kube.cfg = {'platform': 'test-platform'}
    row = {'id': 'a' * 32, 'namespace': 'urumi-store-' + 'a' * 32}
    existing = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': 'credentials',
        'labels': {OWNER: 'test-platform', STORE: row['id']}}, 'data': {'auth-token': 'original'}}
    calls = []
    async def request(method, path, body=None, missing=False):
        calls.append(method)
        return existing
    kube.request = request
    proposed = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': 'credentials', 'namespace': row['namespace']},
        'stringData': {'auth-token': 'must-not-rotate'}}
    result = asyncio.run(kube.ensure(proposed, row))
    assert result['data']['auth-token'] == 'original' and calls == ['GET']
    existing['metadata']['labels'][OWNER] = 'another-platform'
    with pytest.raises(ProvisionError, match='ownership mismatch'):
        asyncio.run(kube.ensure(proposed, row))
    assert calls == ['GET', 'GET']


def test_cleanup_waits_for_volume_deletion():
    import asyncio
    from backend.provisioner import Kube
    kube = object.__new__(Kube); kube.cfg = {'platform': 'test'}
    row = {'id': 'a' * 32, 'namespace': 'urumi-store-' + 'a' * 32}
    volumes = [{'spec': {'claimRef': {'namespace': row['namespace']}}}]
    async def request(method, path, body=None, missing=False):
        return {'items': volumes} if path.endswith('/persistentvolumes') else None
    kube.request = request
    assert not asyncio.run(kube.cleanup(row))
    volumes.clear()
    assert asyncio.run(kube.cleanup(row))


def test_private_registry_credentials_copied_before_pods(monkeypatch):
    import asyncio
    import backend.provisioner as module
    from backend.store_resources import OWNER, STORE
    kube = object.__new__(module.Kube)
    kube.cfg = {'platform': 'test', 'namespace': 'platform', 'imagePullSecrets': [{'name': 'registry'}]}
    row = {'id': 'a' * 32, 'namespace': 'urumi-store-' + 'a' * 32}
    monkeypatch.setattr(module, 'manifests', lambda *args: iter([{'kind': 'Namespace'}, {'kind': 'Deployment'}]))
    applied = []
    async def storage_class(): pass
    async def ensure(resource, row): applied.append(resource)
    async def request(*args, **kwargs):
        return {'type': 'kubernetes.io/dockerconfigjson', 'data': {'.dockerconfigjson': 'encoded-test-fixture'}}
    kube.storage_class, kube.ensure, kube.request = storage_class, ensure, request
    asyncio.run(kube.apply(row))
    assert [r['kind'] for r in applied] == ['Namespace', 'Secret', 'Deployment']
    copied = applied[1]
    assert copied['metadata']['namespace'] == row['namespace']
    assert copied['metadata']['labels'] == {OWNER: 'test', STORE: row['id']}
    assert copied['data']['.dockerconfigjson'] == 'encoded-test-fixture'


def test_tls_store_waits_for_certificate_secret():
    import asyncio
    from backend.provisioner import Kube
    kube = object.__new__(Kube); kube.cfg = {'tls': True}
    requested = []
    async def request(method, path, body=None, missing=False):
        requested.append(path)
        if '/jobs/' in path:
            return {'status': {'conditions': [{'type': 'Complete', 'status': 'True'}]}}
        return None
    kube.request = request
    assert not asyncio.run(kube.ready({'namespace': 'store-test'}))
    assert requested[-1].endswith('/secrets/store-tls')
