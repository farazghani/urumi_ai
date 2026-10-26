#!/usr/bin/env python3
"""Render both environments and assert the infrastructure requirements.

Run with the backend Python environment (PyYAML is already a dependency).
"""
import os
from pathlib import Path
import subprocess
import unittest
import yaml

CHART = Path(__file__).resolve().parents[1] / 'helm' / 'urumi'
HELM = os.getenv('HELM', 'helm')


def render(environment, *overrides):
    result = subprocess.check_output([HELM, 'template', 'test', str(CHART), '-n', 'urumi',
                                      '-f', str(CHART / f'values-{environment}.yaml'), *overrides], text=True)
    return [item for item in yaml.safe_load_all(result) if item]


class ChartRequirements(unittest.TestCase):
    def test_both_environments(self):
        for environment in ('local', 'prod'):
            with self.subTest(environment=environment):
                documents = render(environment)
                workloads = [d for d in documents if d['kind'] in ('Deployment', 'StatefulSet')]
                self.assertEqual(len(workloads), 9)
                self.assertEqual(sum(d['kind'] == 'StatefulSet' for d in workloads), 2)
                self.assertFalse(any(d['kind'] == 'Secret' for d in documents))
                for workload in workloads:
                    spec = workload['spec']['template']['spec']
                    self.assertEqual(spec['automountServiceAccountToken'], workload['metadata']['name'].endswith('-provisioner'))
                    for container in spec['containers']:
                        self.assertIn('readinessProbe', container)
                        self.assertIn('livenessProbe', container)
                        self.assertIn('startupProbe', container)
                    for container in spec['containers'] + spec.get('initContainers', []):
                        for bound in ('requests', 'limits'):
                            self.assertTrue(container['resources'][bound]['cpu'])
                            self.assertTrue(container['resources'][bound]['memory'])
                services = [d for d in documents if d['kind'] == 'Service']
                self.assertEqual(len(services), 8)
                self.assertTrue(all(d['spec']['type'] == 'ClusterIP' for d in services))
                pvcs = [d for d in documents if d['kind'] == 'PersistentVolumeClaim']
                self.assertEqual(len(pvcs), 3)
                self.assertTrue(all(d['metadata']['annotations']['helm.sh/resource-policy'] == 'keep' for d in pvcs))
                ingress = next(d for d in documents if d['kind'] == 'Ingress')
                self.assertEqual(len(ingress['spec']['rules']), 2)
                self.assertEqual('tls' in ingress['spec'], environment == 'prod')
                backend = next(d for d in workloads if d['metadata']['name'] == 'test-backend')
                env = {e['name']: e for e in backend['spec']['template']['spec']['containers'][0]['env']}
                self.assertEqual(env['COOKIE_SECURE']['value'], '1' if environment == 'prod' else '0')
                self.assertIn('secretKeyRef', env['WOO_MCP_AUTH_TOKEN']['valueFrom'])
                config = next(d for d in documents if d['metadata']['name'] == 'test-frontend' and d['kind'] == 'ConfigMap')
                self.assertNotIn('127.0.0.11', config['data']['nginx.conf'])

    def test_existing_claims_and_resources(self):
        documents = render('local', '--set', 'wordpress.storage.existingClaim=imported-wordpress',
                           '--set', 'databases.store.storage.existingClaim=imported-db',
                           '--set', 'clock.resources.requests.memory=160Mi')
        self.assertEqual(sum(d['kind'] == 'PersistentVolumeClaim' for d in documents), 1)
        wordpress = next(d for d in documents if d['kind'] == 'Deployment' and d['metadata']['name'] == 'test-wordpress')
        volumes = wordpress['spec']['template']['spec']['volumes']
        self.assertEqual(volumes[0]['persistentVolumeClaim']['claimName'], 'imported-wordpress')
        clock = next(d for d in documents if d['kind'] == 'Deployment' and d['metadata']['name'] == 'test-clock')
        self.assertEqual(clock['spec']['template']['spec']['containers'][0]['resources']['requests']['memory'], '160Mi')

    def test_maintenance_and_private_network_paths(self):
        documents = render('local', '--set', 'maintenance.enabled=true')
        self.assertFalse(any(d['kind'] in ('Ingress', 'Job') for d in documents))
        backend = next(d for d in documents if d['kind'] == 'Deployment' and d['metadata']['name'] == 'test-backend')
        self.assertEqual(backend['spec']['replicas'], 0)
        policies = [d for d in documents if d['kind'] == 'NetworkPolicy']
        self.assertEqual(len(policies), 8)
        policy = next(d for d in policies if d['metadata']['name'] == 'test-dashboard-db')
        peers = policy['spec']['ingress'][0]['from']
        self.assertEqual(len(peers), 2)
        self.assertEqual(peers[0]['podSelector']['matchLabels']['app.kubernetes.io/component'], 'backend')


if __name__ == '__main__':
    unittest.main()
