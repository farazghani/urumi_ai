"""SQLite persistence. Passwords and API/session tokens are never stored in plaintext."""
import hashlib
import os
import secrets
import sqlite3
from pathlib import Path
from cryptography.fernet import Fernet

DATA = Path(os.getenv('DASHBOARD_DATA', Path(__file__).resolve().parents[2] / '.data'))

def db():
    con = sqlite3.connect(DATA / 'dashboard.db', timeout=15)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    return con

def query(sql, args=(), one=False):
    with db() as con:
        rows = con.execute(sql, args).fetchall()
    return (dict(rows[0]) if rows else None) if one else [dict(r) for r in rows]

def execute(sql, args=()):
    with db() as con:
        return con.execute(sql, args).lastrowid

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    return salt + ':' + hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()

def cipher():
    return Fernet((DATA / 'encryption.key').read_bytes())

def seal(value):
    return cipher().encrypt(value.encode()).decode() if value else ''

def unseal(value):
    return cipher().decrypt(value.encode()).decode() if value else ''

def init():
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(DATA, 0o700)
    key = DATA / 'encryption.key'
    if not key.exists():
        with key.open('xb') as f:
            os.chmod(key, 0o600)
            f.write(Fernet.generate_key())
    with db() as con:
        con.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, password TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'customer', created TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id), expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS api_keys(id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), name TEXT NOT NULL, prefix TEXT NOT NULL, hash TEXT UNIQUE NOT NULL, status TEXT NOT NULL, created TEXT DEFAULT CURRENT_TIMESTAMP, last_used TEXT);
        CREATE TABLE IF NOT EXISTS upstreams(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, url TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, auth_header TEXT NOT NULL DEFAULT '', credential TEXT NOT NULL DEFAULT '', timeout REAL NOT NULL DEFAULT 8);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), action TEXT NOT NULL, target TEXT NOT NULL, status TEXT NOT NULL, duration INTEGER DEFAULT 0, created TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')
    # Preserve namespace identity when the highest-numbered upstream is deleted.
    with db() as con:
        schema = con.execute("SELECT sql FROM sqlite_master WHERE name='upstreams'").fetchone()[0]
        if 'AUTOINCREMENT' not in schema:
            con.executescript("""
            ALTER TABLE upstreams RENAME TO upstreams_legacy;
            CREATE TABLE upstreams(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, url TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, auth_header TEXT NOT NULL DEFAULT '', credential TEXT NOT NULL DEFAULT '', timeout REAL NOT NULL DEFAULT 8);
            INSERT INTO upstreams SELECT * FROM upstreams_legacy;
            DROP TABLE upstreams_legacy;
            """)
    if not query("SELECT id FROM users WHERE role='admin'", one=True):
        password = os.getenv('ADMIN_PASSWORD') or secrets.token_urlsafe(18)
        email = os.getenv('ADMIN_EMAIL', 'admin@urumi.local').lower()
        execute('INSERT INTO users(name,email,password,role) VALUES(?,?,?,?)', ('Workspace admin', email, password_hash(password), 'admin'))
        if not os.getenv('ADMIN_PASSWORD'):
            path = DATA / 'bootstrap-admin.txt'
            with path.open('w') as f:
                os.chmod(path, 0o600)
                f.write(f'Email: {email}\nPassword: {password}\n')
    if not query("SELECT key FROM settings WHERE key='seeded'", one=True):
        for name, url in [('WooCommerce', os.getenv('WOO_MCP_URL', 'http://127.0.0.1:8000/mcp')), ('Clock', 'http://127.0.0.1:8002/mcp'), ('Calculator', 'http://127.0.0.1:8003/mcp')]:
            execute('INSERT INTO upstreams(name,url) VALUES(?,?)', (name,url))
        execute("INSERT INTO settings VALUES('seeded','1')")

def audit(user, action, target='', status='success', duration=0):
    execute('INSERT INTO audit(user_id,action,target,status,duration) VALUES(?,?,?,?,?)', (user['id'], action,target,status,duration))
