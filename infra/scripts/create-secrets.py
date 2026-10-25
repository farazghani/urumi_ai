#!/usr/bin/env python3
"""Create missing Kubernetes Secrets without exposing their contents in arguments.

Never overwrites an existing Secret. Generated files have mode 0600 in a 0700
temporary directory and are removed on exit. Nothing is written to the repo.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--namespace', default='urumi')
    parser.add_argument('--prefix', default='urumi', help='Prefix matching secrets.* in Helm values')
    parser.add_argument('--encryption-key-file', type=Path,
                        help='Original dashboard encryption.key when importing existing data')
    args = parser.parse_args()
    key = (args.encryption_key_file.read_bytes().strip() if args.encryption_key_file
           else base64.urlsafe_b64encode(os.urandom(32)))
    if len(base64.b64decode(key, altchars=b'-_', validate=True)) != 32:
        parser.error('Encryption key must be a Fernet key encoding 32 bytes')
    definitions = {
        'store-db': {'password': secrets.token_urlsafe(32), 'root-password': secrets.token_urlsafe(32)},
        'dashboard-db': {'password': secrets.token_urlsafe(32), 'root-password': secrets.token_urlsafe(32)},
        'wordpress': {'admin-password': secrets.token_urlsafe(24)},
        'woo-mcp': {'consumer-key': 'ck_' + secrets.token_hex(20),
                    'consumer-secret': 'cs_' + secrets.token_hex(20), 'auth-token': secrets.token_urlsafe(32)},
        'dashboard': {'admin-password': secrets.token_urlsafe(24), 'encryption-key': key.decode()},
    }
    for suffix, entries in definitions.items():
        name = f'{args.prefix}-{suffix}'
        # A failed API/auth request must fail, rather than be treated as absence.
        found = subprocess.check_output(['kubectl', '-n', args.namespace, 'get', 'secret', name,
                                         '--ignore-not-found', '-o', 'json'], text=True)
        if found.strip():
            existing = json.loads(found)['data']
            missing = entries.keys() - existing.keys()
            if missing:
                parser.error(f'Existing {name} is missing keys: {", ".join(sorted(missing))}')
            if suffix == 'dashboard' and args.encryption_key_file:
                if base64.b64decode(existing['encryption-key']).strip() != key:
                    parser.error('Existing dashboard Secret has a different encryption key; refusing to replace it')
            print(f'Keeping existing Secret {name}')
            continue
        with tempfile.TemporaryDirectory(prefix='urumi-secrets-') as directory:
            command = ['kubectl', '-n', args.namespace, 'create', 'secret', 'generic', name]
            for field, value in entries.items():
                path = Path(directory) / field
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, 'w') as stream:
                    stream.write(value)
                command.append(f'--from-file={field}={path}')
            subprocess.run(command, check=True)


if __name__ == '__main__':
    main()
