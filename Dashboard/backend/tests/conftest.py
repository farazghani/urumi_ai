"""Integration tests use the production MariaDB image, never SQLite emulation."""
import os
import secrets
import subprocess
import time
import pytest
import pymysql


@pytest.fixture(scope='session')
def mariadb():
    name = 'urumi-db-test-' + secrets.token_hex(6)
    password = secrets.token_urlsafe(24)
    env = dict(os.environ, MARIADB_DATABASE='urumi_test', MARIADB_USER='urumi',
               MARIADB_PASSWORD=password, MARIADB_ROOT_PASSWORD=secrets.token_urlsafe(24))
    command = ['docker', 'run', '-d', '--rm', '--name', name,
               '-e', 'MARIADB_DATABASE', '-e', 'MARIADB_USER', '-e', 'MARIADB_PASSWORD',
               '-e', 'MARIADB_ROOT_PASSWORD', '-p', '127.0.0.1::3306', 'mariadb:11.4']
    saved = {k: os.environ.get(k) for k in ('MYSQL_HOST','MYSQL_PORT','MYSQL_DATABASE','MYSQL_USER','MYSQL_PASSWORD')}
    try:
        subprocess.run(command, env=env, check=True, stdout=subprocess.DEVNULL)
        port = subprocess.check_output(['docker','port',name,'3306'], text=True).strip().rsplit(':',1)[1]
        os.environ.update(MYSQL_HOST='127.0.0.1', MYSQL_PORT=port, MYSQL_DATABASE='urumi_test', MYSQL_USER='urumi', MYSQL_PASSWORD=password)
        for _ in range(120):
            try:
                con = pymysql.connect(host='127.0.0.1', port=int(port), user='urumi', password=password, database='urumi_test', connect_timeout=1)
                con.close()
                break
            except pymysql.OperationalError:
                time.sleep(.5)
        else:
            raise RuntimeError('Test MariaDB did not become ready')
        yield
    finally:
        subprocess.run(['docker','rm','-f',name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
