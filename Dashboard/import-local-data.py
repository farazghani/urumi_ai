"""Snapshot local data, then import it into an EMPTY MariaDB database without exposing secrets."""
import argparse
from contextlib import closing
import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def snapshot_sqlite(source, destination):
    """Include committed WAL pages, then make the copy readable on a read-only mount."""
    with closing(sqlite3.connect(f'{source.resolve().as_uri()}?mode=ro', uri=True)) as original:
        with closing(sqlite3.connect(destination)) as snapshot:
            original.backup(snapshot)
            snapshot.commit()
            # Backup retains the source's WAL mode. A read-only Docker mount cannot
            # create its -wal/-shm files, so normalize only the staged copy.
            mode = snapshot.execute('PRAGMA journal_mode=DELETE').fetchone()[0]
            if mode != 'delete':
                raise RuntimeError('Could not make the SQLite snapshot self-contained.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'backend' / '.data')
    parser.add_argument('--project-name', help='Optional Compose project name, matching your deployment')
    args = parser.parse_args()
    source = args.source.resolve()
    if not (source / 'dashboard.db').is_file() or not (source / 'encryption.key').is_file():
        raise SystemExit('Source must contain dashboard.db and encryption.key. Build the images before importing.')
    with tempfile.TemporaryDirectory(prefix='urumi-import-') as staging:
        stage = Path(staging)
        snapshot_sqlite(source / 'dashboard.db', stage / 'dashboard.db')
        for name in ('encryption.key', 'bootstrap-admin.txt'):
            if (source / name).is_file():
                shutil.copyfile(source / name, stage / name)
        for file in stage.iterdir():
            os.chmod(file, 0o600)
        command = ['docker', 'compose', '-f', str(ROOT / 'docker-compose.yml')]
        if args.project_name:
            command += ['--project-name', args.project_name]
        subprocess.run([*command, 'up', '-d', '--wait', '--wait-timeout', '120', 'db'], check=True)
        command += ['run', '--rm', '--no-deps', '-T', '--user', '0:0',
                    '--volume', f'{stage}:/import:ro', 'backend',
                    'python', '-m', 'backend.import_data', '/import']
        subprocess.run(command, check=True)


if __name__ == '__main__':
    main()
