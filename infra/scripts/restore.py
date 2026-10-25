#!/usr/bin/env python3
"""Restore into empty databases in a release installed with maintenance.enabled=true.

Apply the backed-up Secret JSON files BEFORE installing that release. This tool
refuses an active gateway, a public Ingress, or a destination containing tables.
"""
import argparse
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--namespace', default='urumi')
    parser.add_argument('--release', default='urumi')
    parser.add_argument('--secret-prefix', default='urumi')
    args = parser.parse_args()
    for filename in ('store.sql', 'dashboard.sql', 'wordpress.tar.gz'):
        if not (args.directory / filename).is_file():
            parser.error(f'Missing backup file: {filename}')
    kubectl = ['kubectl', '-n', args.namespace]
    replicas = subprocess.check_output(kubectl + ['get', 'deployment', f'{args.release}-backend',
                                                 '-o', 'jsonpath={.spec.replicas}'], text=True)
    ingress = subprocess.check_output(kubectl + ['get', 'ingress', args.release,
                                                '--ignore-not-found', '-o', 'name'], text=True)
    if replicas.strip() != '0' or ingress.strip():
        parser.error('Install the destination with maintenance.enabled=true first')
    for suffix in ('store-db', 'dashboard-db', 'wordpress', 'woo-mcp', 'dashboard'):
        name = f'{args.secret_prefix}-{suffix}'
        saved = json.loads((args.directory / f'{name}.json').read_text())
        destination = json.loads(subprocess.check_output(kubectl + ['get', 'secret', name, '-o', 'json']))
        if saved['data'] != destination['data']:
            parser.error(f'Destination Secret {name} does not match the backup; restore Secrets before installation')
    destinations = [
        ('store.sql', f'statefulset/{args.release}-store-db',
         'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -u root "$MYSQL_DATABASE"'),
        ('dashboard.sql', f'statefulset/{args.release}-dashboard-db',
         'MYSQL_PWD="$MARIADB_ROOT_PASSWORD" mariadb -u root "$MARIADB_DATABASE"'),
    ]
    for _, workload, command in destinations:
        count = subprocess.check_output(kubectl + ['exec', workload, '--', 'sh', '-ec',
            command + ' -Nse "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE()"'], text=True)
        if count.strip() != '0':
            parser.error(f'{workload} is not empty; refusing to overwrite data')
    for filename, workload, command in destinations:
        with (args.directory / filename).open('rb') as source:
            subprocess.run(kubectl + ['exec', '-i', workload, '--', 'sh', '-ec', command],
                           stdin=source, check=True)
    with (args.directory / 'wordpress.tar.gz').open('rb') as source:
        subprocess.run(kubectl + ['exec', '-i', f'deployment/{args.release}-wordpress', '--',
                                  'tar', '-xzf', '-', '-C', '/var/www/html'], stdin=source, check=True)
    print('Restore complete. Disable maintenance mode using the same Helm values to start the gateway.')


if __name__ == '__main__':
    main()
