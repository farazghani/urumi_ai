"""Kubernetes resources for one store. No user-supplied manifests or credentials."""
import json
import secrets
from pathlib import Path

OWNER = 'urumi.io/platform'
STORE = 'urumi.io/store-id'
COMPONENT = 'app.kubernetes.io/component'


def credentials():
    return {'root-password': secrets.token_urlsafe(32), 'password': secrets.token_urlsafe(32),
            'admin-password': secrets.token_urlsafe(32), 'consumer-key': 'ck_' + secrets.token_hex(20),
            'consumer-secret': 'cs_' + secrets.token_hex(20), 'auth-token': secrets.token_urlsafe(32)}


def manifests(row, cfg, bootstrap_dir):
    ns = row['namespace']
    labels = {OWNER: cfg['platform'], STORE: row['id']}

    def obj(kind, name, spec=None, api='v1', **extra):
        item = {'apiVersion': api, 'kind': kind, 'metadata': {'name': name, 'namespace': ns, 'labels': labels.copy()}, **extra}
        if spec is not None:
            item['spec'] = spec
        return item

    namespace = obj('Namespace', ns)
    namespace['metadata'].pop('namespace')
    yield namespace
    yield obj('ResourceQuota', 'store', {'hard': cfg['quota']})
    yield obj('LimitRange', 'store', {'limits': [{'type': 'Container',
        'defaultRequest': {'cpu': '50m', 'memory': '64Mi'},
        'default': {'cpu': '500m', 'memory': '256Mi'},
        'max': {'cpu': '2', 'memory': '2Gi'}, 'min': {'cpu': '10m', 'memory': '16Mi'}}]})

    def peer(component):
        return {'podSelector': {'matchLabels': {COMPONENT: component}}}

    def external(namespace, selector):
        return {'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': namespace}},
                'podSelector': {'matchLabels': selector}}

    def port(number, protocol='TCP'):
        return {'port': number, 'protocol': protocol}

    def policy(name, selector, ingress=None, egress=None):
        return obj('NetworkPolicy', name, {'podSelector': {'matchLabels': selector},
            'policyTypes': ['Ingress', 'Egress'], 'ingress': ingress or [], 'egress': egress or []}, 'networking.k8s.io/v1')

    yield policy('default-deny', {})
    dns = external('kube-system', {'k8s-app': 'kube-dns'})
    yield policy('dns', {}, egress=[{'to': [dns], 'ports': [port(53), port(53, 'UDP')]}])
    yield policy('database', {COMPONENT: 'database'}, ingress=[
        {'from': [peer('wordpress'), peer('bootstrap')], 'ports': [port(3306)]}])
    yield policy('wordpress', {COMPONENT: 'wordpress'}, ingress=[
        {'from': [peer('mcp'), external(cfg['ingressNamespace'], cfg['ingressPodSelector'])], 'ports': [port(80)]}],
        egress=[{'to': [peer('database')], 'ports': [port(3306)]}])
    yield policy('bootstrap', {COMPONENT: 'bootstrap'}, egress=[{'to': [peer('database')], 'ports': [port(3306)]}])
    yield policy('mcp', {COMPONENT: 'mcp'}, ingress=[{'from': [
        external(cfg['namespace'], {'app.kubernetes.io/instance': cfg['release'], COMPONENT: component})
        for component in ('backend', 'provisioner')], 'ports': [port(8000)]}],
        egress=[{'to': [peer('wordpress')], 'ports': [port(80)]}])
    if cfg['tls']:
        # cert-manager creates a temporary solver in this isolated namespace.
        yield policy('acme-http01', {'acme.cert-manager.io/http01-solver': 'true'},
            ingress=[{'from': [external(cfg['ingressNamespace'], cfg['ingressPodSelector'])], 'ports': [port(8089)]}])

    # Created once by the client: subsequent reconciliation never rotates credentials.
    yield obj('Secret', 'credentials', type='Opaque', stringData=credentials())
    files = {name: (Path(bootstrap_dir) / name).read_text() for name in
             ('bootstrap.sh', 'bootstrap.php', 'health.php', 'health-apache.conf')}
    files['config-extra'] = "define('WP_HOME', " + json.dumps(row['url']) + ");\ndefine('WP_SITEURL', " + json.dumps(row['url']) + ");\ndefine('DISALLOW_FILE_EDIT', true);"
    files['bootstrap.php'] += "\nupdate_option('timezone_string', 'UTC');\n"
    yield obj('ConfigMap', 'wordpress', data=files)
    for name in ('database', 'wordpress'):
        spec = {'accessModes': ['ReadWriteOnce'], 'resources': {'requests': {'storage': cfg['volumeSize']}}}
        if cfg['storageClass']:
            spec['storageClassName'] = cfg['storageClass']
        yield obj('PersistentVolumeClaim', name, spec)
    for name, number in (('database', 3306), ('wordpress', 80), ('mcp', 8000)):
        yield obj('Service', name, {'type': 'ClusterIP', 'selector': {COMPONENT: name},
            'ports': [{'name': 'service', 'port': number, 'targetPort': number}]})

    def env(name, value):
        return {'name': name, 'value': value}

    def secret(name, key):
        return {'name': name, 'valueFrom': {'secretKeyRef': {'name': 'credentials', 'key': key}}}

    def resources(cpu, memory, limit_cpu, limit_memory):
        return {'requests': {'cpu': cpu, 'memory': memory}, 'limits': {'cpu': limit_cpu, 'memory': limit_memory}}

    def http_probe(path, number):
        return {'httpGet': {'path': path, 'port': number}, 'periodSeconds': 5, 'timeoutSeconds': 3, 'failureThreshold': 6}

    def pod(component, container, volumes=None, uid=None):
        spec = {'automountServiceAccountToken': False, 'restartPolicy': 'Always',
                'securityContext': {'seccompProfile': {'type': 'RuntimeDefault'}},
                'containers': [container], 'volumes': volumes or []}
        if uid is not None:
            spec['securityContext'].update(runAsNonRoot=True, runAsUser=uid, runAsGroup=uid, fsGroup=uid)
        if cfg['imagePullSecrets']:
            spec['imagePullSecrets'] = cfg['imagePullSecrets']
        container['imagePullPolicy'] = cfg['imagePullPolicy']
        return {'metadata': {'labels': labels | {COMPONENT: component}}, 'spec': spec}

    def deployment(component, container, volumes=None, uid=None):
        return obj('Deployment', component, {'replicas': 1, 'strategy': {'type': 'Recreate'},
            'selector': {'matchLabels': {COMPONENT: component}},
            'template': pod(component, container, volumes, uid)}, 'apps/v1')

    mount_data = lambda name, path: {'name': name, 'mountPath': path}
    pvc_volume = lambda name: {'name': name, 'persistentVolumeClaim': {'claimName': name}}
    db_probe = {'exec': {'command': ['sh', '-ec', 'MYSQL_PWD="$MYSQL_PASSWORD" mysql --protocol=TCP -h127.0.0.1 -uwordpress woocommerce -e "SELECT 1" >/dev/null']},
                'periodSeconds': 5, 'timeoutSeconds': 3, 'failureThreshold': 6}
    database = {'name': 'database', 'image': cfg['images']['database'],
        'env': [env('MYSQL_DATABASE', 'woocommerce'), env('MYSQL_USER', 'wordpress'),
                secret('MYSQL_PASSWORD', 'password'), secret('MYSQL_ROOT_PASSWORD', 'root-password')],
        'resources': resources('100m', '384Mi', '1', '1Gi'),
        'startupProbe': db_probe | {'failureThreshold': 120}, 'readinessProbe': db_probe, 'livenessProbe': db_probe,
        'volumeMounts': [mount_data('database', '/var/lib/mysql')]}
    yield obj('StatefulSet', 'database', {'replicas': 1, 'serviceName': 'database',
        'selector': {'matchLabels': {COMPONENT: 'database'}},
        'template': pod('database', database, [pvc_volume('database')])}, 'apps/v1')
    wp_env = [env('WORDPRESS_DB_HOST', 'database:3306'), env('WORDPRESS_DB_NAME', 'woocommerce'),
        env('WORDPRESS_DB_USER', 'wordpress'), secret('WORDPRESS_DB_PASSWORD', 'password'),
        {'name': 'WORDPRESS_CONFIG_EXTRA', 'valueFrom': {'configMapKeyRef': {'name': 'wordpress', 'key': 'config-extra'}}}]
    config_volume = {'name': 'config', 'configMap': {'name': 'wordpress'}}
    wp = {'name': 'wordpress', 'image': cfg['images']['wordpress'], 'env': wp_env,
        'resources': resources('100m', '192Mi', '1', '768Mi'),
        'startupProbe': http_probe('/urumi-health.php', 80) | {'failureThreshold': 120},
        'livenessProbe': http_probe('/urumi-health.php', 80),
        'readinessProbe': http_probe('/urumi-health.php?check=ready', 80),
        'volumeMounts': [mount_data('wordpress', '/var/www/html'), {'name': 'config', 'mountPath': '/opt/urumi', 'readOnly': True},
            {'name': 'config', 'mountPath': '/etc/apache2/conf-enabled/urumi-health.conf', 'subPath': 'health-apache.conf', 'readOnly': True}]}
    yield deployment('wordpress', wp, [pvc_volume('wordpress'), config_volume])
    mcp = {'name': 'mcp', 'image': cfg['images']['mcp'],
        'env': [env('WOO_BASE_URL', 'http://wordpress'), env('WOO_FORWARDED_PROTO', 'https'),
                env('MCP_HOST', '0.0.0.0'), env('MCP_PORT', '8000'), secret('WOO_CONSUMER_KEY', 'consumer-key'),
                secret('WOO_CONSUMER_SECRET', 'consumer-secret'), secret('MCP_AUTH_TOKEN', 'auth-token')],
        'resources': resources('50m', '128Mi', '500m', '384Mi'),
        'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}},
        'startupProbe': http_probe('/health', 8000) | {'failureThreshold': 120},
        'livenessProbe': http_probe('/health', 8000), 'readinessProbe': http_probe('/health', 8000),
        'volumeMounts': [mount_data('tmp', '/tmp')]}
    yield deployment('mcp', mcp, [{'name': 'tmp', 'emptyDir': {}}], 10001)
    bootstrap = {'name': 'bootstrap', 'image': cfg['images']['wordpress'],
        'command': ['bash', '/bootstrap/bootstrap.sh'],
        'env': wp_env + [env('HOME', '/tmp'), env('WORDPRESS_PUBLIC_URL', row['url']), env('WORDPRESS_TITLE', row['name']),
            env('WORDPRESS_ADMIN_USER', 'store-admin'), env('WORDPRESS_ADMIN_EMAIL', 'admin@' + row['host']),
            secret('WORDPRESS_ADMIN_PASSWORD', 'admin-password'), secret('WOO_CONSUMER_KEY', 'consumer-key'), secret('WOO_CONSUMER_SECRET', 'consumer-secret')],
        'resources': resources('100m', '192Mi', '1', '768Mi'),
        'securityContext': {'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL']}},
        'volumeMounts': [mount_data('wordpress', '/var/www/html'), {'name': 'config', 'mountPath': '/bootstrap', 'readOnly': True}, mount_data('tmp', '/tmp')]}
    template = pod('bootstrap', bootstrap, [pvc_volume('wordpress'), config_volume, {'name': 'tmp', 'emptyDir': {}}], 33)
    template['spec']['restartPolicy'] = 'Never'
    yield obj('Job', 'bootstrap', {'backoffLimit': 2, 'activeDeadlineSeconds': cfg['timeoutSeconds'], 'template': template}, 'batch/v1')
    ingress = obj('Ingress', 'wordpress', {'ingressClassName': cfg['ingressClass'], 'rules': [
        {'host': row['host'], 'http': {'paths': [{'path': '/', 'pathType': 'Prefix',
            'backend': {'service': {'name': 'wordpress', 'port': {'number': 80}}}}]}}]}, 'networking.k8s.io/v1')
    ingress['metadata']['annotations'] = cfg['ingressAnnotations']
    if cfg['tls']:
        ingress['spec']['tls'] = [{'hosts': [row['host']], 'secretName': 'store-tls'}]
    yield ingress
