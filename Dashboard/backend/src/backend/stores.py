"""Durable provisioning intents. The web process has no Kubernetes credentials."""
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from fastapi import HTTPException
from . import store


def config():
    path = os.getenv('STORE_CONFIG_FILE')
    return json.loads(Path(path).read_text()) if path else {'enabled': False, 'maxStoresPerUser': 3}


def owned(user, sid, cursor=None):
    sql = 'SELECT * FROM stores WHERE id=%s'
    if cursor:
        cursor.execute(sql + ' FOR UPDATE', (sid,))
        row = cursor.fetchone()
    else:
        row = store.query(sql, (sid,), one=True)
    if not row or (row['user_id'] != user['id'] and user['role'] != 'admin'):
        raise HTTPException(404, 'Store not found.')
    return row


def listing(user):
    where, args = ('', ()) if user['role'] == 'admin' else (' AND user_id=?', (user['id'],))
    return store.query("SELECT * FROM stores WHERE status!='Deleted'" + where + ' ORDER BY created DESC,id', args)


def create(user, name, request_key):
    settings = config()
    if not settings['enabled']:
        raise HTTPException(503, 'Store provisioning is disabled in this deployment.')
    name = name.strip()
    if not name:
        raise HTTPException(422, 'Enter a store name.')
    with store.db() as con, con.cursor() as cur:
        # Serialize admission per owner, including concurrent HTTP requests.
        cur.execute('SELECT id FROM users WHERE id=%s FOR UPDATE', (user['id'],))
        cur.execute('SELECT * FROM stores WHERE user_id=%s AND request_key=%s', (user['id'], request_key))
        previous = cur.fetchone()
        if previous:
            if previous['name'] != name:
                raise HTTPException(409, 'This request key already belongs to another store name.')
            if previous['status'] == 'Deleted':
                raise HTTPException(410, 'This request was deleted. Use a new request key for a new store.')
            sid = previous['id']
        else:
            cur.execute("SELECT COUNT(*) AS count FROM stores WHERE user_id=%s AND status!='Deleted'", (user['id'],))
            if cur.fetchone()['count'] >= settings['maxStoresPerUser']:
                raise HTTPException(409, 'Maximum stores reached. Delete a store and wait for cleanup before creating another.')
            sid = uuid.uuid4().hex
            host = sid + '.' + settings['domain']
            slug = re.sub('[^a-z0-9]+', '_', name.lower()).strip('_')[:14] or 'store'
            prefix = f'store_{slug}_{sid[:12]}'
            deadline = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=settings['timeoutSeconds'])
            cur.execute('''INSERT INTO stores(id,user_id,name,request_key,namespace,host,url,tool_prefix,deadline)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                (sid, user['id'], name, request_key, 'urumi-store-' + sid, host,
                 ('https' if settings['tls'] else 'http') + '://' + host, prefix, deadline))
    return owned(user, sid)


def remove(user, sid):
    with store.db() as con, con.cursor() as cur:
        row = owned(user, sid, cur)
        if row['status'] != 'Deleted':
            # Registration disappears in the same transaction as the delete intent.
            cur.execute('DELETE FROM upstreams WHERE id=%s', (row['upstream_id'],))
            cur.execute("UPDATE stores SET desired='Deleted',status='Deleting',phase='Cleaning',reason='',updated=UTC_TIMESTAMP() WHERE id=%s", (sid,))
    return owned(user, sid)


def retry(user, sid):
    settings = config()
    if not settings['enabled']:
        raise HTTPException(503, 'Store provisioning is disabled.')
    with store.db() as con, con.cursor() as cur:
        row = owned(user, sid, cur)
        if row['status'] == 'Provisioning':
            return {'id': sid, 'status': 'Provisioning'}
        if row['status'] != 'Failed' or row['phase'] != 'Failed' or row['desired'] != 'Ready':
            raise HTTPException(409, 'Only a failed store whose cleanup has completed can be retried.')
        cur.execute("UPDATE stores SET status='Provisioning',phase='Requested',reason='',deadline=DATE_ADD(UTC_TIMESTAMP(), INTERVAL %s SECOND),updated=UTC_TIMESTAMP() WHERE id=%s",
                    (settings['timeoutSeconds'], sid))
    return owned(user, sid)
