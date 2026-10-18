"""Frozen legacy SQLite fixture, used only to verify one-time migration."""
import json
import sqlite3
from cryptography.fernet import Fernet
from backend import store


def create(directory, email, password):
    directory.mkdir(parents=True)
    key = Fernet.generate_key()
    (directory / 'encryption.key').write_bytes(key)
    cipher = Fernet(key)
    encrypt = lambda value: cipher.encrypt(value.encode()).decode()
    with sqlite3.connect(directory / 'dashboard.db') as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE users(id INTEGER PRIMARY KEY,name TEXT,email TEXT,password TEXT,role TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE sessions(token TEXT PRIMARY KEY,user_id INTEGER,expires INTEGER);
        CREATE TABLE api_keys(id INTEGER PRIMARY KEY,user_id INTEGER,name TEXT,prefix TEXT,hash TEXT,status TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP,last_used TEXT);
        CREATE TABLE upstreams(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT,url TEXT,enabled INTEGER DEFAULT 1,auth_header TEXT DEFAULT '',credential TEXT DEFAULT '',timeout REAL DEFAULT 8);
        CREATE TABLE audit(id INTEGER PRIMARY KEY,user_id INTEGER,action TEXT,target TEXT,status TEXT,duration INTEGER DEFAULT 0,created TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT);
        ''')
        db.execute("INSERT INTO users(name,email,password,role) VALUES(?,?,?,'admin')", ('Fixture', email, store.password_hash(password)))
        for name, port in [('WooCommerce',8000),('Clock',8002),('Calculator',8003)]:
            db.execute('INSERT INTO upstreams(name,url) VALUES(?,?)', (name,f'http://127.0.0.1:{port}/mcp'))
        db.execute("UPDATE upstreams SET auth_header='X-Fixture',credential=? WHERE name='Clock'", (encrypt('fixture-upstream-secret'),))
        db.execute("INSERT INTO upstreams(name,url,enabled) VALUES('Custom','https://example.invalid/mcp',0)")
        db.execute("INSERT INTO upstreams(id,name,url) VALUES(40,'Deleted','http://example.invalid')")
        db.execute('DELETE FROM upstreams WHERE id=40')
        db.execute("INSERT INTO settings VALUES('llm',?)", (json.dumps({'provider':'openrouter','model':'fixture/model','api_key':encrypt('fixture-model-secret')}),))
        db.execute("INSERT INTO settings VALUES('seeded','1')")
        db.execute("INSERT INTO audit(user_id,action,target,status) VALUES(1,'tool.call','fixture','success')")
        db.execute("INSERT INTO api_keys(user_id,name,prefix,hash,status) VALUES(1,'Legacy','fixture',?,'approved')", (store.digest('fixture-legacy-api-key'),))
