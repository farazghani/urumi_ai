"""MariaDB persistence. Passwords and API/session tokens are never stored in plaintext."""
import hashlib
import os
import secrets
from contextlib import contextmanager
from datetime import date, datetime
import pymysql
from pymysql import IntegrityError
from pathlib import Path
from cryptography.fernet import Fernet

DATA = Path(os.getenv('DASHBOARD_DATA', Path(__file__).resolve().parents[2] / '.data'))

@contextmanager
def db():
    password = os.getenv('MYSQL_PASSWORD')
    if not password:
        raise RuntimeError('MYSQL_PASSWORD is required. Configure the dashboard MariaDB connection.')
    con = pymysql.connect(
        host=os.getenv('MYSQL_HOST', '127.0.0.1'),
        port=int(os.getenv('MYSQL_PORT', '3307')),
        user=os.getenv('MYSQL_USER', 'urumi'), password=password,
        database=os.getenv('MYSQL_DATABASE', 'urumi_dashboard'),
        charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=5, read_timeout=15, write_timeout=15,
        init_command="SET time_zone = '+00:00'",
    )
    try:
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()

def query(sql, args=(), one=False):
    with db() as con, con.cursor() as cursor:
        cursor.execute(sql.replace('?', '%s'), args)
        rows = cursor.fetchall()
    # Keep the public API's UTC SQL date strings compatible with existing clients.
    rows = [{k: v.isoformat(sep=' ') if isinstance(v, datetime) else
             v.isoformat() if isinstance(v, date) else v for k, v in row.items()} for row in rows]
    return (rows[0] if rows else None) if one else rows

def execute(sql, args=()):
    with db() as con, con.cursor() as cursor:
        cursor.execute(sql.replace('?', '%s'), args)
        return cursor.lastrowid

def set_setting(key, value):
    execute('INSERT INTO settings (`key`,value) VALUES(?,?) ON DUPLICATE KEY UPDATE value=VALUES(value)', (key, value))

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS users(
        id BIGINT PRIMARY KEY AUTO_INCREMENT, name VARCHAR(255) NOT NULL,
        email VARCHAR(254) UNIQUE NOT NULL, password VARCHAR(255) NOT NULL,
        role VARCHAR(16) NOT NULL DEFAULT 'customer', created DATETIME DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS sessions(
        token VARCHAR(64) PRIMARY KEY, user_id BIGINT, expires BIGINT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id), INDEX(expires))""",
    """CREATE TABLE IF NOT EXISTS api_keys(
        id BIGINT PRIMARY KEY AUTO_INCREMENT, user_id BIGINT, name VARCHAR(255) NOT NULL,
        prefix VARCHAR(64) NOT NULL, hash VARCHAR(64) UNIQUE NOT NULL, status VARCHAR(32) NOT NULL,
        created DATETIME DEFAULT CURRENT_TIMESTAMP, last_used DATETIME,
        FOREIGN KEY(user_id) REFERENCES users(id), INDEX(user_id,status))""",
    """CREATE TABLE IF NOT EXISTS upstreams(
        id BIGINT PRIMARY KEY AUTO_INCREMENT, name VARCHAR(255) NOT NULL, url TEXT NOT NULL,
        enabled BOOLEAN NOT NULL DEFAULT 1, auth_header VARCHAR(255) NOT NULL DEFAULT '',
        credential TEXT NOT NULL DEFAULT '', timeout DOUBLE NOT NULL DEFAULT 8)""",
    """CREATE TABLE IF NOT EXISTS audit(
        id BIGINT PRIMARY KEY AUTO_INCREMENT, user_id BIGINT, action VARCHAR(100) NOT NULL,
        target TEXT NOT NULL, status VARCHAR(32) NOT NULL, duration INTEGER DEFAULT 0,
        created DATETIME DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(user_id) REFERENCES users(id),
        INDEX(user_id,created), INDEX(created))""",
    "CREATE TABLE IF NOT EXISTS settings (`key` VARCHAR(128) PRIMARY KEY, value LONGTEXT NOT NULL)",
    """CREATE TABLE IF NOT EXISTS stores(
        id VARCHAR(32) PRIMARY KEY, user_id BIGINT NOT NULL, name VARCHAR(60) NOT NULL,
        request_key VARCHAR(64) NOT NULL, namespace VARCHAR(63) UNIQUE NOT NULL,
        host VARCHAR(253) NOT NULL, url VARCHAR(300) NOT NULL, tool_prefix VARCHAR(40) UNIQUE NOT NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'Provisioning', desired VARCHAR(16) NOT NULL DEFAULT 'Ready',
        phase VARCHAR(32) NOT NULL DEFAULT 'Requested', reason VARCHAR(500) NOT NULL DEFAULT '',
        upstream_id BIGINT UNIQUE, created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, deadline DATETIME NOT NULL,
        UNIQUE(user_id,request_key), INDEX(status), FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(upstream_id) REFERENCES upstreams(id) ON DELETE SET NULL)""",
]

def create_schema():
    with db() as con, con.cursor() as cursor:
        for statement in SCHEMA:
            cursor.execute(statement + ' ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci')

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    return salt + ':' + hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()

def cipher():
    return Fernet(encryption_key_path().read_bytes().strip())

def encryption_key_path():
    """A mounted Kubernetes Secret can replace the legacy writable key file."""
    return Path(os.environ['ENCRYPTION_KEY_FILE']) if os.getenv('ENCRYPTION_KEY_FILE') else DATA / 'encryption.key'

def seal(value):
    return cipher().encrypt(value.encode()).decode() if value else ''

def unseal(value):
    return cipher().decrypt(value.encode()).decode() if value else ''

def init():
    create_schema()
    existing = query('SELECT id FROM users LIMIT 1', one=True)
    if (DATA / 'dashboard.db').exists() and not existing:
        raise RuntimeError('Legacy SQLite data found. Run import-local-data.py before starting the backend.')
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(DATA, 0o700)
    key = encryption_key_path()
    if not key.exists():
        if os.getenv('ENCRYPTION_KEY_FILE'):
            raise RuntimeError('ENCRYPTION_KEY_FILE must point to an existing encryption key.')
        if existing:
            raise RuntimeError('Existing MariaDB data requires its original encryption.key. Restore the data volume.')
        with key.open('xb') as f:
            os.chmod(key, 0o600)
            f.write(Fernet.generate_key())
    # Validate before creating users or encrypting bootstrap credentials.
    cipher()
    if not query("SELECT id FROM users WHERE role='admin'", one=True):
        password = os.getenv('ADMIN_PASSWORD') or secrets.token_urlsafe(18)
        email = os.getenv('ADMIN_EMAIL', 'admin@urumi.local').lower()
        execute('INSERT INTO users(name,email,password,role) VALUES(?,?,?,?)', ('Workspace admin', email, password_hash(password), 'admin'))
        if not os.getenv('ADMIN_PASSWORD'):
            path = DATA / 'bootstrap-admin.txt'
            with path.open('w') as f:
                os.chmod(path, 0o600)
                f.write(f'Email: {email}\nPassword: {password}\n')
    if not query("SELECT `key` FROM settings WHERE `key`='seeded'", one=True):
        for name, url in [('WooCommerce', os.getenv('WOO_MCP_URL', 'http://127.0.0.1:8000/mcp')), ('Clock', os.getenv('CLOCK_MCP_URL', 'http://127.0.0.1:8002/mcp')), ('Calculator', os.getenv('CALCULATOR_MCP_URL', 'http://127.0.0.1:8003/mcp'))]:
            token = os.getenv('WOO_MCP_AUTH_TOKEN', '') if name == 'WooCommerce' else ''
            execute('INSERT INTO upstreams(name,url,auth_header,credential) VALUES(?,?,?,?)',
                    (name, url, 'Authorization' if token else '', seal('Bearer ' + token) if token else ''))
        execute("INSERT INTO settings VALUES('seeded','1')")

def audit(user, action, target='', status='success', duration=0):
    execute('INSERT INTO audit(user_id,action,target,status,duration) VALUES(?,?,?,?,?)', (user['id'], action,target,status,duration))
