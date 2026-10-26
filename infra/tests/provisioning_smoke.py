#!/usr/bin/env python3
"""Two real stores: restart recovery, chat tools, isolation and complete teardown.

Requires the local Helm deployment and kubectl context. Creates only disposable
stores; preserves the original store. Model replies are a deterministic fixture
inside an isolated Python test process; every gateway/WooCommerce call is real.
No OpenRouter key, external provider calls or billing are required.
"""
import argparse
import base64
import concurrent.futures
import http.client
import json
import subprocess
import time
import uuid


class Dashboard:
    def __init__(self, host, address, password, email):
        self.host, self.address, self.cookie = host, address, ''
        _, headers = self.request('POST', '/api/auth/login', {'email': email, 'password': password})
        self.cookie = headers['set-cookie'].split(';', 1)[0]

    def request(self, method, path, data=None, expected=200):
        connection = http.client.HTTPConnection(self.address, 80, timeout=60)
        connection.request(method, path, json.dumps(data) if data is not None else None,
            {'Host': self.host, 'Content-Type': 'application/json', 'Origin': 'http://' + self.host, 'Cookie': self.cookie})
        response = connection.getresponse()
        body, headers = response.read(), dict((k.lower(), v) for k, v in response.getheaders())
        connection.close()
        assert response.status == expected, f'{method} {path}: HTTP {response.status}'
        return json.loads(body), headers

    def api(self, method, path, data=None, expected=200):
        return self.request(method, path, data, expected)[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--namespace', default='urumi')
    parser.add_argument('--release', default='urumi')
    parser.add_argument('--host', default='dashboard.urumi.test')
    parser.add_argument('--address', default='127.0.0.1')
    parser.add_argument('--email', default='admin@urumi.test')
    parser.add_argument('--timeout', type=int, default=1000)
    args = parser.parse_args()
    kubectl = ['kubectl']

    def kube(*command, namespace=None, source=None):
        result = subprocess.run(kubectl + ['-n', namespace or args.namespace] + list(command),
            input=source, text=True, capture_output=True, check=True)
        return result.stdout

    password = base64.b64decode(json.loads(kube('get', 'secret', args.release + '-dashboard', '-o', 'json'))['data']['admin-password']).decode()
    api = Dashboard(args.host, args.address, password, args.email)
    rows = []
    marker = uuid.uuid4().hex[:6]

    def wait(sid, status):
        end, last = time.monotonic() + args.timeout, ''
        while time.monotonic() < end:
            row = api.api('GET', '/api/stores/' + sid)
            state = row['status'] + '/' + row['phase']
            if state != last:
                print(row['name'], state, row['reason'], flush=True)
                last = state
            if row['status'] == status:
                return row
            if row['status'] == 'Failed' and status == 'Ready':
                raise AssertionError(row['reason'])
            time.sleep(3)
        raise AssertionError('Timed out waiting for store ' + sid + ': ' + last)

    try:
        with concurrent.futures.ThreadPoolExecutor(2) as executor:
            futures = [executor.submit(api.api, 'POST', '/api/stores',
                {'name': name + '-' + marker, 'request_key': uuid.uuid4().hex}, 202) for name in ('Alpha', 'Beta')]
            # Track every successful admission even if another request fails.
            errors = []
            for future in futures:
                try:
                    rows.append(future.result())
                except Exception as error:
                    errors.append(error)
            if errors:
                raise errors[0]
        assert all(row['status'] == 'Provisioning' for row in rows)
        # Restart after at least one durable phase has been processed.
        for _ in range(40):
            if any(api.api('GET', '/api/stores/' + r['id'])['phase'] == 'Bootstrapping' for r in rows):
                break
            time.sleep(1)
        kube('delete', 'pod', '-l', f'app.kubernetes.io/instance={args.release},app.kubernetes.io/component=provisioner')
        rows = [wait(row['id'], 'Ready') for row in rows]
        print('PASS: concurrent stores become Ready after controller pod replacement', flush=True)

        for row, totals in zip(rows, ([15, 25], [12])):
            php = 'foreach (' + json.dumps(totals) + ' as $total) {$order=wc_create_order();$order->set_currency("USD");$order->set_total($total);$order->set_date_created(time());$order->save();} echo "fixture orders created";'
            kube('exec', 'deployment/wordpress', '--', 'wp', '--allow-root', 'eval', php, namespace=row['namespace'])
            connection = http.client.HTTPConnection(args.address, 80, timeout=30)
            connection.request('GET', '/', headers={'Host': row['host']})
            response = connection.getresponse(); response.read()
            assert response.status == 200, f"Store Ingress failed: {response.status}"
            connection.close()
        print('PASS: both store Ingress hosts serve WordPress; today has Alpha USD 40 / 2 orders, Beta USD 12 / 1 order', flush=True)

        pods = json.loads(kube('get', 'pods', '-o', 'json', namespace=rows[1]['namespace']))['items']
        targets = [(pod['status']['podIP'], 3306 if pod['metadata']['labels']['app.kubernetes.io/component'] == 'database' else 80)
            for pod in pods if pod['metadata']['labels'].get('app.kubernetes.io/component') in ('database', 'wordpress')]
        targets += [('wordpress.' + rows[1]['namespace'] + '.svc', 80), ('database.' + rows[1]['namespace'] + '.svc', 3306)]
        source = 'import socket\nsocket.create_connection(("wordpress",80),3).close()\n'
        source += 'targets=' + repr(targets) + '\nfor host,port in targets:\n try:\n  socket.create_connection((host,port),2).close()\n except OSError:\n  continue\n raise AssertionError("Cross-store connection allowed")\nprint("PASS: own WordPress reachable; other store WordPress and database blocked by Service DNS and Pod IP")\n'
        print(kube('exec', '-i', 'deployment/mcp', '--', 'python', '-', namespace=rows[0]['namespace'], source=source).strip(), flush=True)

        # Exercise the actual /api/chat handler inside the backend pod. Only the
        # provider reply is replaced; authentication, discovery, routing, audit,
        # date filtering and the two independent Woo REST/database paths are live.
        source = r'''
import importlib,json,os
from datetime import datetime,timezone,timedelta
from decimal import Decimal
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend import store
module=importlib.import_module('backend.app')
rows=ROWS
names=[r['tool_prefix']+'__list_orders' for r in rows]
day=datetime.now(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
async def complete(model,key,messages,tools):
    available={t['function']['name'] for t in tools}
    assert all(name in available for name in names)
    results=[m for m in messages if m['role']=='tool']
    if not results:
        return {'role':'assistant','content':None,'tool_calls':[{'id':str(i),'type':'function','function':{'name':name,'arguments':json.dumps({'after':day.strftime('%Y-%m-%dT%H:%M:%S'),'before':(day+timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%S'),'per_page':100})}} for i,name in enumerate(names)]}
    summaries=[]
    for row,message in zip(rows,results):
        result=json.loads(message['content']);assert not result.get('isError'),result
        data=result.get('structuredContent') or json.loads(result['content'][0]['text'])
        data=data.get('result',data)
        orders=data['items'];total=sum((Decimal(o['total']) for o in orders),Decimal('0'))
        summaries.append({'store':row['name'],'count':len(orders),'total':str(total),'currency':'USD'})
    assert [s['count'] for s in summaries]==[2,1],summaries
    assert [Decimal(s['total']) for s in summaries]==[Decimal(40),Decimal(12)],summaries
    return {'role':'assistant','content':json.dumps(summaries)}
with patch.object(module,'llm_config',return_value={'model':'fixture/tool-model','api_key':store.seal('fixture-key')}),patch.object(module.openrouter,'complete',complete),TestClient(module.app) as client:
    assert client.post('/api/auth/login',json={'email':os.environ['ADMIN_EMAIL'],'password':os.environ['ADMIN_PASSWORD']}).status_code==200
    result=client.post('/api/chat',json={'message':"Compare today's orders across "+' and '.join(r['name'] for r in rows)});assert result.status_code==200,result.text
    result=result.json();assert len(result['tools'])==2
    print('PASS: chat handler with fixture model and real store tools:',result['message'])
'''.replace('ROWS', repr(rows))
        print(kube('exec', '-i', 'deployment/' + args.release + '-backend', '--', 'python', '-', source=source).strip(), flush=True)

        removed, remaining = rows
        old_tool = removed['tool_prefix'] + '__list_orders'
        api.api('DELETE', '/api/stores/' + removed['id'], expected=202)
        result = api.api('POST', '/api/tools/call', {'name': old_tool, 'arguments': {}})
        assert result['isError'], 'Deleted store tools still callable'
        wait(removed['id'], 'Deleted')
        assert not kube('get', 'namespace', removed['namespace'], '--ignore-not-found').strip()
        volumes = json.loads(kube('get', 'pv', '-o', 'json'))['items']
        assert not any(v.get('spec', {}).get('claimRef', {}).get('namespace') == removed['namespace'] for v in volumes)
        servers = api.api('GET', '/api/servers')
        assert not any(s.get('store_id') == removed['id'] for s in servers)
        result = api.api('POST', '/api/tools/call', {'name': remaining['tool_prefix'] + '__list_orders', 'arguments': {'per_page': 1}})
        assert not result.get('isError')
        print('PASS: deleted namespace, PVC-backed volumes, Secrets and gateway tools are gone; second store still works', flush=True)
    finally:
        for row in rows:
            api.api('DELETE', '/api/stores/' + row['id'], expected=202)
        for row in rows:
            wait(row['id'], 'Deleted')
        api.api('POST', '/api/auth/logout', {})


if __name__ == '__main__':
    main()
