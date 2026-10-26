#!/usr/bin/env python3
"""Back up application state. Run during Helm maintenance mode for consistency."""
import argparse
import json
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path, help='New private backup directory (must not exist)')
    parser.add_argument('--namespace', default='urumi')
    parser.add_argument('--release', default='urumi')
    parser.add_argument('--secret-prefix', default='urumi')
    args = parser.parse_args()
    kubectl = ['kubectl', '-n', args.namespace]
    replicas = subprocess.check_output(kubectl + ['get', 'deployment', f'{args.release}-backend',
                                                 '-o', 'jsonpath={.spec.replicas}'], text=True)
    ingress = subprocess.check_output(kubectl + ['get', 'ingress', args.release,
                                                '--ignore-not-found', '-o', 'name'], text=True)
    pods = json.loads(subprocess.check_output(kubectl + ['get', 'pods', '-l',
        f'app.kubernetes.io/instance={args.release},app.kubernetes.io/component=backend', '-o', 'json']))
    if replicas.strip() != '0' or ingress.strip() or pods['items']:
        parser.error('Enable maintenance mode and wait for backend pods to terminate before backup')
    controllers = json.loads(subprocess.check_output(kubectl + ['get', 'pods', '-l',
        f'app.kubernetes.io/instance={args.release},app.kubernetes.io/component=provisioner', '-o', 'json']))
    if controllers['items']:
        parser.error('Wait for the provisioning controller to stop in maintenance mode before backup')
    stores = json.loads(subprocess.check_output(['kubectl', 'get', 'namespaces', '-l',
        f'urumi.io/platform={args.namespace}-{args.release}', '-o', 'json']))
    if stores['items']:
        parser.error('This backup covers the original store only. Back up provisioned store namespaces/volumes/Secrets separately; see docs/PROVISIONING.md.')
    os.umask(0o077)
    args.directory.mkdir(parents=True, mode=0o700)

    def capture(filename, command):
        with (args.directory / filename).open('xb') as output:
            subprocess.run(kubectl + command, stdout=output, check=True)

    capture('store.sql', ['exec', f'statefulset/{args.release}-store-db', '--', 'sh', '-ec',
            'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump -u root --single-transaction '
            '--routines --triggers --no-tablespaces "$MYSQL_DATABASE"'])
    capture('dashboard.sql', ['exec', f'statefulset/{args.release}-dashboard-db', '--', 'sh', '-ec',
            'MYSQL_PWD="$MARIADB_ROOT_PASSWORD" exec mariadb-dump -u root --single-transaction '
            '--routines --triggers "$MARIADB_DATABASE"'])
    capture('wordpress.tar.gz', ['exec', f'deployment/{args.release}-wordpress', '--',
            'tar', '-czf', '-', '--exclude=./urumi-health.php', '-C', '/var/www/html', '.'])
    for suffix in ('store-db', 'dashboard-db', 'wordpress', 'woo-mcp', 'dashboard'):
        name = f'{args.secret_prefix}-{suffix}'
        secret = json.loads(subprocess.check_output(kubectl + ['get', 'secret', name, '-o', 'json']))
        portable = {'apiVersion': 'v1', 'kind': 'Secret', 'type': secret['type'],
                    'metadata': {'name': name, 'namespace': args.namespace}, 'data': secret['data']}
        (args.directory / f'{name}.json').write_text(json.dumps(portable))
    print(f'Backup complete: {args.directory}. It includes credentials; store it securely off the VPS.')


if __name__ == '__main__':
    main()
