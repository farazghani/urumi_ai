"""One-time, transactional SQLite-to-MariaDB import. Stop the backend first."""
import os
import sqlite3
import sys
from pathlib import Path
from cryptography.fernet import Fernet
from . import store

TABLES = ('users', 'sessions', 'api_keys', 'upstreams', 'audit', 'settings')


def main():
    source = Path(sys.argv[1])
    target = store.DATA
    if os.geteuid() != 0:
        raise SystemExit('Run the import container with --user 0:0.')
    if not (source / 'dashboard.db').is_file() or not (source / 'encryption.key').is_file():
        raise SystemExit('Import requires both dashboard.db and encryption.key.')
    source_key = (source / 'encryption.key').read_bytes()
    cipher = Fernet(source_key)
    target.mkdir(parents=True, exist_ok=True)
    destination_key = target / 'encryption.key'
    if destination_key.exists() and destination_key.read_bytes() != source_key:
        raise SystemExit('Destination encryption key differs. Import refused; existing data was not changed.')
    store.create_schema()
    copied = []
    try:
        with store.db() as db, db.cursor() as cursor:
            cursor.execute("SELECT GET_LOCK('dashboard_sqlite_import', 0) AS acquired")
            if not cursor.fetchone()['acquired']:
                raise SystemExit('Another import is running.')
            for table in TABLES:
                cursor.execute(f'SELECT COUNT(*) AS n FROM `{table}`')
                if cursor.fetchone()['n']:
                    raise SystemExit('Destination is not empty. Import refused; existing MariaDB data was not changed.')
            with sqlite3.connect(f'{(source / "dashboard.db").resolve().as_uri()}?mode=ro', uri=True) as original:
                original.row_factory = sqlite3.Row
                for table in TABLES:
                    rows = original.execute(f'SELECT * FROM "{table}"').fetchall()
                    for row in rows:
                        values = dict(row)
                        if table == 'upstreams':
                            if values.get('credential'):
                                cipher.decrypt(values['credential'].encode())
                            for port, setting in [(8000, 'WOO_MCP_URL'), (8002, 'CLOCK_MCP_URL'), (8003, 'CALCULATOR_MCP_URL')]:
                                if values['url'] in (f'http://127.0.0.1:{port}/mcp', f'http://localhost:{port}/mcp'):
                                    values['url'] = os.getenv(setting, values['url'])
                        columns = ','.join(f'`{name}`' for name in values)
                        placeholders = ','.join(['%s'] * len(values))
                        cursor.execute(f'INSERT INTO `{table}` ({columns}) VALUES ({placeholders})', tuple(values.values()))
                # Preserve IDs reserved by deleted upstreams without implicit-commit DDL.
                if original.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone():
                    row = original.execute("SELECT seq FROM sqlite_sequence WHERE name='upstreams'").fetchone()
                    if row:
                        cursor.execute('SELECT COALESCE(MAX(id),0) AS highest FROM upstreams')
                        if int(row[0]) > cursor.fetchone()['highest']:
                            cursor.execute("INSERT INTO upstreams(id,name,url) VALUES(%s,'migration sequence reservation','')", (int(row[0]),))
                            cursor.execute('DELETE FROM upstreams WHERE id=%s', (int(row[0]),))
            for name in ('encryption.key', 'bootstrap-admin.txt'):
                path = target / name
                if (source / name).is_file() and not path.exists():
                    copied.append(path)
                    with path.open('xb') as out:
                        os.chmod(path, 0o600)
                        out.write((source / name).read_bytes())
                    os.chown(path, 10001, 10001)
            os.chown(target, 10001, 10001)
            os.chmod(target, 0o700)
            db.commit()
            copied.clear()
    except BaseException:
        for path in copied:
            path.unlink(missing_ok=True)
        raise
    print('Imported users, API keys, settings and audit history into MariaDB; retained the encryption key.')
    print('Default loopback upstream URLs adapted for Docker; custom URLs preserved. Source SQLite unchanged.')


if __name__ == '__main__':
    main()
