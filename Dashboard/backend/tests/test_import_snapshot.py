"""Regression: live WAL backups must work when Docker mounts them read-only."""
from contextlib import closing
import importlib.util
from pathlib import Path
import sqlite3

spec = importlib.util.spec_from_file_location('dashboard_import', Path(__file__).resolve().parents[2] / 'import-local-data.py')
importer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(importer)


def test_snapshot_includes_live_wal_without_requiring_sidecars(tmp_path):
    source = tmp_path / 'original.db'
    snapshot = tmp_path / 'snapshot.db'
    with closing(sqlite3.connect(source)) as live:
        live.execute('PRAGMA journal_mode=WAL')
        live.execute('PRAGMA wal_autocheckpoint=0')
        live.execute('CREATE TABLE committed(value TEXT)')
        live.execute("INSERT INTO committed VALUES('latest committed WAL row')")
        live.commit()
        wal = Path(str(source) + '-wal')
        assert wal.stat().st_size > 0
        original_bytes, wal_bytes = source.read_bytes(), wal.read_bytes()
        importer.snapshot_sqlite(source, snapshot)
        assert source.read_bytes() == original_bytes
        assert wal.read_bytes() == wal_bytes
        assert live.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        assert not Path(str(snapshot) + '-wal').exists()
        assert not Path(str(snapshot) + '-shm').exists()
        with closing(sqlite3.connect(snapshot.as_uri() + '?mode=ro', uri=True)) as copied:
            assert copied.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'
            assert copied.execute('SELECT value FROM committed').fetchall() == [('latest committed WAL row',)]
