"""Render real Helm configuration, then check generated per-store resources."""
import json
from pathlib import Path
import tempfile
import unittest
from test_chart import render
from backend.store_resources import manifests, credentials


class StoreResources(unittest.TestCase):
    def test_isolation_and_guardrails(self):
        for environment in ('local', 'prod'):
            docs = render(environment)
            cfg = json.loads(next(d['data']['settings.json'] for d in docs if d['kind'] == 'ConfigMap' and d['metadata']['name'].endswith('-provisioning')))
            wp = next(d['data'] for d in docs if d['kind'] == 'ConfigMap' and d['metadata']['name'].endswith('-wordpress'))
            with tempfile.TemporaryDirectory() as folder:
                for name, value in wp.items():
                    Path(folder, name).write_text(value)
                row = {'id': 'a' * 32, 'namespace': 'urumi-store-' + 'a' * 32, 'url': 'http://store.example.com', 'host': 'store.example.com', 'name': 'Alpha'}
                resources = list(manifests(row, cfg, folder))
            self.assertEqual([r['kind'] for r in resources[:3]], ['Namespace', 'ResourceQuota', 'LimitRange'])
            policies = [r for r in resources if r['kind'] == 'NetworkPolicy']
            self.assertEqual(len(policies), 7 if environment == 'prod' else 6)
            if environment == 'prod':
                acme = next(r for r in policies if r['metadata']['name'] == 'acme-http01')['spec']
                self.assertEqual(acme['podSelector']['matchLabels'], {'acme.cert-manager.io/http01-solver': 'true'})
                self.assertEqual(acme['ingress'][0]['ports'], [{'port': 8089, 'protocol': 'TCP'}])
                self.assertEqual(acme['egress'], [])
                self.assertEqual(cfg['quota']['count/ingresses.networking.k8s.io'], '2')
            deny = next(r for r in policies if r['metadata']['name'] == 'default-deny')
            self.assertEqual(deny['spec']['policyTypes'], ['Ingress', 'Egress'])
            self.assertEqual(deny['spec']['ingress'], [])
            self.assertEqual(deny['spec']['egress'], [])
            mcp = next(r for r in policies if r['metadata']['name'] == 'mcp')['spec']
            # Same-namespace peer: no namespace selector permitting another store.
            self.assertEqual(mcp['egress'][0]['to'], [{'podSelector': {'matchLabels': {'app.kubernetes.io/component': 'wordpress'}}}])
            self.assertEqual(mcp['egress'][0]['ports'], [{'port': 80, 'protocol': 'TCP'}])
            for resource in resources:
                if resource['kind'] not in ('Deployment', 'StatefulSet', 'Job'):
                    continue
                pod = resource['spec']['template']['spec']
                self.assertFalse(pod['automountServiceAccountToken'])
                for container in pod['containers']:
                    self.assertIn('requests', container['resources'])
                    self.assertIn('limits', container['resources'])
                    if resource['kind'] != 'Job':
                        self.assertIn('readinessProbe', container)
                        self.assertIn('livenessProbe', container)
            pvcs = [r for r in resources if r['kind'] == 'PersistentVolumeClaim']
            self.assertEqual(len(pvcs), 2)
            self.assertFalse(any('annotations' in r['metadata'] for r in pvcs))
            ingress = next(r for r in resources if r['kind'] == 'Ingress')
            self.assertEqual('tls' in ingress['spec'], environment == 'prod')
        self.assertNotEqual(credentials(), credentials())


if __name__ == '__main__':
    unittest.main()
