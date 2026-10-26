"""Restart-safe Kubernetes reconciler. Run with python -m backend.provisioner."""
import asyncio
import base64
import json
import logging
import os
import ssl
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from . import store, stores
from .store_resources import OWNER, STORE, manifests

log = logging.getLogger('provisioner')
COLLECTIONS = {'Namespace': ('v1', 'namespaces'), 'Secret': ('v1', 'secrets'),
    'ConfigMap': ('v1', 'configmaps'), 'Service': ('v1', 'services'),
    'PersistentVolumeClaim': ('v1', 'persistentvolumeclaims'), 'ResourceQuota': ('v1', 'resourcequotas'),
    'LimitRange': ('v1', 'limitranges'), 'Deployment': ('apps/v1', 'deployments'),
    'StatefulSet': ('apps/v1', 'statefulsets'), 'Job': ('batch/v1', 'jobs'),
    'Ingress': ('networking.k8s.io/v1', 'ingresses'), 'NetworkPolicy': ('networking.k8s.io/v1', 'networkpolicies')}


class ProvisionError(Exception):
    """Only controlled messages, never upstream bodies or Secret content."""


class Kube:
    def __init__(self, cfg):
        self.cfg = cfg
        self.root = Path('/var/run/secrets/kubernetes.io/serviceaccount')
        context = ssl.create_default_context(cafile=str(self.root / 'ca.crt'))
        self.client = httpx.AsyncClient(base_url='https://kubernetes.default.svc', verify=context, timeout=15)

    async def request(self, method, path, body=None, missing=False):
        # Projected tokens rotate: do not cache the bearer token in the process.
        result = await self.client.request(method, path, json=body,
            headers={'Authorization': 'Bearer ' + (self.root / 'token').read_text().strip()})
        if result.status_code == 404 and missing:
            return None
        if result.status_code >= 400:
            raise ProvisionError(f'Kubernetes {method} {path.split("/")[-2:]} returned HTTP {result.status_code}.')
        return result.json()

    def path(self, kind, namespace=None, name=None):
        version, plural = COLLECTIONS[kind]
        root = '/api/' if version == 'v1' else '/apis/'
        return root + version + (('/namespaces/' + namespace) if namespace and kind != 'Namespace' else '') + '/' + plural + (('/' + name) if name else '')

    def ownership(self, resource, row):
        labels = resource.get('metadata', {}).get('labels', {})
        if labels.get(OWNER) != self.cfg['platform'] or labels.get(STORE) != row['id']:
            raise ProvisionError('Resource ownership mismatch. Operator intervention required; foreign resources were not changed.')

    async def ensure(self, resource, row):
        kind, meta = resource['kind'], resource['metadata']
        collection = self.path(kind, meta.get('namespace'))
        current = await self.request('GET', collection + '/' + meta['name'], missing=True)
        if current:
            self.ownership(current, row)
            if current['metadata'].get('deletionTimestamp'):
                raise ProvisionError('A store resource is still terminating. Wait for cleanup before retrying.')
            # Manifests are immutable for a store attempt. This also preserves Secret
            # bytes, PVC binding and Job templates after a process restart.
            return current
        try:
            return await self.request('POST', collection, resource)
        except ProvisionError:
            # Ambiguous response / competing controller: read back and validate.
            existing = await self.request('GET', collection + '/' + meta['name'], missing=True)
            if existing:
                self.ownership(existing, row)
                return existing
            raise

    async def storage_class(self):
        classes = await self.request('GET', '/apis/storage.k8s.io/v1/storageclasses')
        selected = [s for s in classes['items'] if s['metadata']['name'] == self.cfg['storageClass']] if self.cfg['storageClass'] else [
            s for s in classes['items'] if s['metadata'].get('annotations', {}).get('storageclass.kubernetes.io/is-default-class') == 'true']
        if len(selected) != 1 or selected[0].get('reclaimPolicy', 'Delete') != 'Delete':
            raise ProvisionError('Choose one available StorageClass with reclaimPolicy Delete; automatic volume teardown requires it.')

    async def apply(self, row):
        await self.storage_class()
        copied_registry_secrets = False
        for resource in manifests(row, self.cfg, os.getenv('STORE_BOOTSTRAP_DIR', '/bootstrap')):
            if resource['kind'] in ('StatefulSet', 'Deployment', 'Job') and not copied_registry_secrets:
                for reference in self.cfg['imagePullSecrets']:
                    name = reference['name']
                    if name == 'credentials':
                        raise ProvisionError('Registry pull Secret name conflicts with a reserved store Secret name.')
                    source = await self.request('GET', self.path('Secret', self.cfg['namespace'], name))
                    if source.get('type') not in ('kubernetes.io/dockerconfigjson', 'kubernetes.io/dockercfg'):
                        raise ProvisionError('Configured image pull Secret must contain registry credentials.')
                    await self.ensure({'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {
                        'name': name, 'namespace': row['namespace'],
                        'labels': {OWNER: self.cfg['platform'], STORE: row['id']}},
                        'type': source['type'], 'data': source['data']}, row)
                copied_registry_secrets = True
            await self.ensure(resource, row)

    async def ready(self, row):
        ns = row['namespace']
        job = await self.request('GET', self.path('Job', ns, 'bootstrap'), missing=True)
        if not job:
            raise ProvisionError('Bootstrap Job disappeared before completion.')
        conditions = {c['type']: c for c in job.get('status', {}).get('conditions', []) if c['status'] == 'True'}
        if 'Failed' in conditions:
            raise ProvisionError('WordPress bootstrap failed (' + conditions['Failed'].get('reason', 'JobFailed') + '). Inspect the bootstrap Job logs before retrying.')
        if 'Complete' not in conditions:
            return False
        if self.cfg['tls']:
            certificate = await self.request('GET', self.path('Secret', ns, 'store-tls'), missing=True)
            if not certificate or certificate.get('type') != 'kubernetes.io/tls' or not all(
                    certificate.get('data', {}).get(key) for key in ('tls.crt', 'tls.key')):
                return False
        for kind, name in (('StatefulSet', 'database'), ('Deployment', 'wordpress'), ('Deployment', 'mcp')):
            resource = await self.request('GET', self.path(kind, ns, name), missing=True)
            if not resource or resource.get('status', {}).get('readyReplicas', 0) != 1:
                return False
        token = await self.token(row)
        # Readiness is not merely Pod readiness: prove that Woo credentials work.
        try:
            async with asyncio.timeout(12):
                async with Client(StreamableHttpTransport(self.url(row), headers={'Authorization': 'Bearer ' + token})) as client:
                    result = await client.call_tool('list_products', {'per_page': 1}, raise_on_error=False)
                    return not result.is_error
        except Exception:
            return False

    @staticmethod
    def url(row):
        return f"http://mcp.{row['namespace']}.svc:8000/mcp"

    async def token(self, row):
        secret = await self.request('GET', self.path('Secret', row['namespace'], 'credentials'))
        self.ownership(secret, row)
        return base64.b64decode(secret['data']['auth-token']).decode()

    async def cleanup(self, row):
        ns = await self.request('GET', self.path('Namespace', name=row['namespace']), missing=True)
        if ns:
            self.ownership(ns, row)
            if not ns['metadata'].get('deletionTimestamp'):
                # UID precondition prevents deleting a replacement with the same name.
                await self.request('DELETE', self.path('Namespace', name=row['namespace']), {
                    'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': ns['metadata']['uid']}})
            return False
        # Namespace disappearance can precede CSI/local-path volume cleanup.
        volumes = await self.request('GET', '/api/v1/persistentvolumes')
        return not any(v.get('spec', {}).get('claimRef', {}).get('namespace') == row['namespace'] for v in volumes['items'])


class Controller:
    def __init__(self, cfg, kube):
        self.cfg, self.kube = cfg, kube
        self.heartbeat = time.monotonic()

    @staticmethod
    def row(sid):
        return store.query('SELECT * FROM stores WHERE id=?', (sid,), one=True)

    @staticmethod
    def unregister(sid):
        with store.db() as con, con.cursor() as cur:
            cur.execute('SELECT upstream_id FROM stores WHERE id=%s FOR UPDATE', (sid,))
            row = cur.fetchone()
            if row and row['upstream_id']:
                cur.execute('DELETE FROM upstreams WHERE id=%s', (row['upstream_id'],))

    async def register(self, row):
        token = await self.kube.token(row)
        with store.db() as con, con.cursor() as cur:
            cur.execute('SELECT * FROM stores WHERE id=%s FOR UPDATE', (row['id'],))
            latest = cur.fetchone()
            # A delete arriving during readiness/probe must never resurrect tools.
            if latest['desired'] != 'Ready' or latest['status'] != 'Provisioning':
                return
            if not latest['upstream_id']:
                cur.execute('INSERT INTO upstreams(name,url,auth_header,credential,timeout) VALUES(%s,%s,%s,%s,%s)',
                    (latest['name'], self.kube.url(row), 'Authorization', store.seal('Bearer ' + token), 15))
                upstream = cur.lastrowid
            else:
                upstream = latest['upstream_id']
            cur.execute("UPDATE stores SET upstream_id=%s,status='Ready',phase='Ready',reason='',updated=UTC_TIMESTAMP() WHERE id=%s", (upstream, row['id']))

    async def reconcile(self, sid):
        # MariaDB named locks are released on process/connection death, providing
        # cross-replica exclusion without volatile task ownership or stale leases.
        with store.db() as lock, lock.cursor() as cursor:
            cursor.execute('SELECT GET_LOCK(%s,0) AS acquired', ('urumi-store:' + sid,))
            if not cursor.fetchone()['acquired']:
                return
            try:
                row = self.row(sid)
                if not row or row['status'] in ('Deleted', 'Ready') or row['phase'] == 'Failed':
                    return
                if row['desired'] == 'Deleted' or row['phase'] == 'CleaningFailed':
                    self.unregister(sid)
                    if await self.kube.cleanup(row):
                        deleted = row['desired'] == 'Deleted'
                        store.execute('UPDATE stores SET status=?,phase=?,updated=UTC_TIMESTAMP() WHERE id=?',
                            ('Deleted' if deleted else 'Failed', 'Deleted' if deleted else 'Failed', sid))
                    return
                deadline = datetime.fromisoformat(row['deadline'])
                if datetime.now(timezone.utc).replace(tzinfo=None) >= deadline:
                    raise ProvisionError(f"Provisioning timed out during {row['phase']}. Bootstrap, workload readiness, authenticated WooCommerce read or TLS issuance did not complete before the deadline.")
                if row['phase'] == 'Requested':
                    await self.kube.apply(row)
                    # Preserve a concurrent delete intent/phase.
                    store.execute("UPDATE stores SET phase='Bootstrapping',updated=UTC_TIMESTAMP() WHERE id=? AND desired='Ready' AND status='Provisioning'", (sid,))
                if await self.kube.ready(row):
                    await self.register(row)
            except ProvisionError as exc:
                self.unregister(sid)
                store.execute("UPDATE stores SET status=IF(desired='Deleted','Deleting','Failed'),phase=IF(desired='Deleted','Cleaning','CleaningFailed'),reason=?,updated=UTC_TIMESTAMP() WHERE id=?", (str(exc)[:500], sid))
            except Exception:
                # Infrastructure outages remain retryable against the original
                # deadline; no exception can serialize a Secret into the UI/log.
                log.warning('Reconciliation interrupted for store %s; retrying', sid)
            finally:
                cursor.execute('SELECT RELEASE_LOCK(%s)', ('urumi-store:' + sid,))

    async def run(self):
        semaphore = asyncio.Semaphore(self.cfg['concurrency'])

        async def one(sid):
            async with semaphore:
                await self.reconcile(sid)

        while True:
            try:
                rows = store.query("SELECT id FROM stores WHERE status NOT IN ('Ready','Deleted') AND phase!='Failed' ORDER BY created")
                # Poll small bounded steps. No store occupies a slot while waiting
                # for a Job, PVC or Deployment to become ready.
                await asyncio.gather(*(one(row['id']) for row in rows))
                self.heartbeat = time.monotonic()
            except Exception:
                log.warning('Controller database unavailable; retrying')
            await asyncio.sleep(3)


controller = None


@asynccontextmanager
async def lifespan(app):
    global controller
    store.create_schema()
    cfg = stores.config()
    kube = Kube(cfg)
    controller = Controller(cfg, kube)
    task = asyncio.create_task(controller.run())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await kube.client.aclose()


app = FastAPI(lifespan=lifespan)


@app.get('/health')
def health():
    if controller is None or time.monotonic() - controller.heartbeat > 120:
        return JSONResponse({'status': 'stalled'}, status_code=503)
    return {'status': 'ok'}


if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=8004)
