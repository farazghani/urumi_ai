#!/usr/bin/env python3
"""Exercise local Ingress, login, authenticated MCP and optional pod persistence.

Uses Host headers so /etc/hosts is not required. --restart creates a temporary
draft product/file, replaces application pods, verifies state and cleans up.
Never calls OpenRouter or changes an existing product/order.
"""
import argparse
import base64
import http.client
import json
import socket
import ssl
import subprocess
import time
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--namespace', default='urumi')
    parser.add_argument('--release', default='urumi')
    parser.add_argument('--dashboard-secret', default='urumi-dashboard')
    parser.add_argument('--dashboard-host', default='dashboard.urumi.test')
    parser.add_argument('--store-host', default='store.urumi.test')
    parser.add_argument('--admin-email', default='admin@urumi.test')
    parser.add_argument('--connect-address', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=80)
    parser.add_argument('--https', action='store_true')
    parser.add_argument('--ca-file', help='Trusted CA certificate for local HTTPS verification')
    parser.add_argument('--restart', action='store_true')
    args = parser.parse_args()
    kubectl = ['kubectl', '-n', args.namespace]
    secret = json.loads(subprocess.check_output(kubectl + ['get', 'secret', args.dashboard_secret, '-o', 'json']))
    password = base64.b64decode(secret['data']['admin-password']).decode()
    cookie = ''

    def request(method, path, data=None, *, host=None, token=None, expected=200):
        host = host or args.dashboard_host
        if args.https:
            connection = http.client.HTTPSConnection(host, args.port, timeout=45,
                context=ssl.create_default_context(cafile=args.ca_file))
            # Keep the real hostname for certificate/SNI validation while avoiding
            # a hosts-file change for local tests.
            connection._create_connection = lambda address, timeout, source_address: socket.create_connection(
                (args.connect_address, address[1]), timeout, source_address)
        else:
            connection = http.client.HTTPConnection(args.connect_address, args.port, timeout=45)
        scheme = 'https' if args.https else 'http'
        headers = {'Host': host, 'Origin': f'{scheme}://{host}', 'Content-Type': 'application/json'}
        if cookie:
            headers['Cookie'] = cookie
        if token:
            headers['Authorization'] = 'Bearer ' + token
        connection.request(method, path, body=json.dumps(data) if data is not None else None, headers=headers)
        response = connection.getresponse()
        body = response.read()
        result_headers = {key.lower(): value for key, value in response.getheaders()}
        status = response.status
        connection.close()
        assert status == expected, f'{method} {path}: expected {expected}, got {status}'
        if 'application/json' in result_headers.get('content-type', ''):
            body = json.loads(body)
        return body, result_headers

    request('GET', '/', host=args.store_host)
    request('GET', '/')
    _, headers = request('POST', '/api/auth/login', {'email': args.admin_email, 'password': password})
    cookie = headers['set-cookie'].split(';', 1)[0]
    assert ('secure' in headers['set-cookie'].lower()) == args.https
    servers, _ = request('GET', '/api/servers')
    for name in ('WooCommerce', 'Clock', 'Calculator'):
        assert any(s['name'] == name and s['status'] == 'online' for s in servers), f'{name} is offline'
    key, _ = request('POST', '/api/keys', {'name': 'infra-smoke-' + uuid.uuid4().hex[:8]})
    token = key['token']
    rpc_id = 0

    def rpc(method, params=None):
        nonlocal rpc_id
        rpc_id += 1
        result, _ = request('POST', '/mcp', {'jsonrpc': '2.0', 'id': rpc_id,
                                           'method': method, 'params': params or {}}, token=token)
        assert 'error' not in result, 'Gateway returned an RPC error'
        return result['result']

    tools = rpc('tools/list')['tools']
    assert len(tools) >= 13
    names = {t['name'].split('__', 1)[1]: t['name'] for t in tools}

    def call(tool, arguments):
        result = rpc('tools/call', {'name': names[tool], 'arguments': arguments})
        assert not result.get('isError'), f'Tool {tool} failed: {result.get("content")}'
        structured = result.get('structuredContent')
        if structured is None:
            structured = json.loads(result['content'][0]['text'])
        return structured.get('result', structured)

    product_id = None
    marker = 'infra-smoke-' + uuid.uuid4().hex
    marker_path = f'/var/www/html/wp-content/uploads/{marker}.txt'
    try:
        call('list_products', {'per_page': 1})
        assert call('calculate', {'operation': 'multiply', 'a': 6, 'b': 7}) == 42
        request('POST', '/mcp', {'jsonrpc': '2.0', 'id': 99, 'method': 'tools/list'}, expected=401)
        print('PASS: both Ingress hosts, login, three upstreams, MCP auth, real WooCommerce read and calculator', flush=True)
        if args.restart:
            product = call('create_product', {'name': marker, 'regular_price': 1, 'status': 'draft'})
            product_id = product['id']
            subprocess.run(kubectl + ['exec', '-i', f'deployment/{args.release}-wordpress', '--',
                           'sh', '-ec', f'mkdir -p /var/www/html/wp-content/uploads; cat > {marker_path}'],
                           input=marker.encode(), check=True)
            subprocess.run(kubectl + ['delete', 'pods', '-l', f'app.kubernetes.io/instance={args.release}'], check=True)
            for kind, component in [('statefulset', 'store-db'), ('statefulset', 'dashboard-db')] + [
                    ('deployment', c) for c in ('wordpress', 'backend', 'frontend', 'woo-mcp', 'clock', 'calculator')]:
                subprocess.run(kubectl + ['rollout', 'status', f'{kind}/{args.release}-{component}', '--timeout=180s'], check=True)
            # Allow endpoint propagation through Traefik after readiness.
            for attempt in range(15):
                try:
                    request('GET', '/api/me')
                    break
                except (AssertionError, OSError):
                    if attempt == 14:
                        raise
                    time.sleep(1)
            assert call('get_product', {'product_id': product_id})['name'] == marker
            content = subprocess.check_output(kubectl + ['exec', f'deployment/{args.release}-wordpress', '--',
                                                       'cat', marker_path], text=True)
            assert content == marker
            print('PASS: database records, session, API key, encrypted upstream token and WordPress content survive pod replacement', flush=True)
    finally:
        if product_id:
            subprocess.run(kubectl + ['exec', f'deployment/{args.release}-wordpress', '--', 'wp', '--allow-root',
                                      'post', 'delete', str(product_id), '--force'], check=True)
            subprocess.run(kubectl + ['exec', f'deployment/{args.release}-wordpress', '--', 'rm', '-f', marker_path], check=True)
        request('PATCH', '/api/keys/' + str(key['id']), {'status': 'revoked'})
        request('POST', '/api/auth/logout')


if __name__ == '__main__':
    main()
