# Kubernetes and Helm

One application chart deploys the entire project to k3d locally or k3s on a VPS.
Environment differences live in `values-local.yaml` and `values-prod.yaml`.
There are no external databases or host-running application services.

## Architecture

| Workload | Type / port | State |
| --- | --- | --- |
| WordPress + WooCommerce | Deployment / 80 | WordPress directory PVC |
| Store MySQL 8.4 | StatefulSet / 3306 | Database PVC |
| Dashboard MariaDB 11.4 | StatefulSet / 3306 | Database PVC |
| Gateway backend | Deployment / 8001 | MariaDB and mounted encryption-key Secret |
| Dashboard Nginx | Deployment / 8080 | None |
| WooCommerce MCP | Deployment / 8000 | None |
| Clock MCP | Deployment / 8002 | None |
| Calculator MCP | Deployment / 8003 | None |

Ingress routes the store host to WordPress and the dashboard host to Nginx.
Nginx proxies `/api/`, `/mcp`, and `/health` to the backend, keeping browser requests
on one origin. It uses Kubernetes Service DNS, preserves the host, configures the
public scheme from TLS values, disables MCP buffering and permits 150-second calls.
The backend reaches upstreams through Service DNS; MCP reaches WordPress privately.

All application Services are ClusterIP. Ingress NetworkPolicies allow only the expected
Ingress-controller and application paths. These require a CNI with NetworkPolicy
enforcement; k3s provides it. For another controller, adjust
`networkPolicy.ingressNamespace`, `networkPolicy.ingressPodSelector`, and
`frontend.trustedProxyCidrs` to the actual deployment.

## Local quick start

Prerequisites: Docker running, k3d, kubectl, Helm 3, Python 3, and free loopback
ports 80/443. On macOS, Docker Desktop must have enough memory for Kubernetes and
the application (start with 6 GB available). Install CLI prerequisites with your
package manager, for example `brew install helm k3d kubectl`.

From the repository root:

```sh
./infra/scripts/local-up.sh
```

This creates/reuses the `urumi` k3d cluster, builds four application images, imports
them into its nodes, creates only missing Secrets, and installs the chart with
`--wait`. A post-install/post-upgrade Job installs WordPress if needed, activates
WooCommerce, creates its database tables, enables REST permalinks, and creates or
updates one API key named `urumi-infra` from its Secret. No sample orders, products,
or model requests are created. The Job retries safely and has a deadline; completed
Jobs are removed, failed Jobs remain available for diagnosis.

Add this line to `/etc/hosts` using an editor with administrator privileges:

```text
127.0.0.1 store.urumi.test dashboard.urumi.test
```

Then open:

- Store: <http://store.urumi.test>
- WordPress admin: <http://store.urumi.test/wp-admin/> (user `urumi-admin`)
- Dashboard: <http://dashboard.urumi.test> (email `admin@urumi.test`)
- Gateway for external MCP clients: <http://dashboard.urumi.test/mcp>

The `.test` names resolve through the hosts file and remain stable across pod
restarts. k3d maps loopback 80/443 to Traefik. Cluster pods use Service DNS instead
of these loopback hostnames. You can test routing without editing the hosts file:

```sh
curl --resolve dashboard.urumi.test:80:127.0.0.1 http://dashboard.urumi.test/health
curl --resolve store.urumi.test:80:127.0.0.1 http://store.urumi.test/urumi-health.php
```

The local script does not change your current kubectl context. For subsequent commands:

```sh
export KUBECONFIG=$(k3d kubeconfig write urumi)
kubectl -n urumi get pods,pvc,ingress
```

Read the generated passwords privately when signing in (these commands print a
credential to your terminal; do not paste their output into logs or commit it):

```sh
kubectl -n urumi get secret urumi-dashboard -o jsonpath='{.data.admin-password}' | base64 --decode
kubectl -n urumi get secret urumi-wordpress -o jsonpath='{.data.admin-password}' | base64 --decode
```

Generate a dashboard API key in the UI to connect an MCP client. The WooCommerce
MCP token and WooCommerce REST keys are private upstream credentials, not the
dashboard gateway API key.

To rebuild after source changes, rerun `local-up.sh`, then restart the affected
Deployment because the `local` tag does not change its pod template:

```sh
kubectl -n urumi rollout restart deployment/urumi-backend deployment/urumi-frontend
kubectl -n urumi rollout status deployment/urumi-backend
```

Restart `urumi-clock` and `urumi-calculator` too after changing the shared backend
image; restart `urumi-woo-mcp` after changing its code. Kubernetes does not use
Dockerfile HEALTHCHECK directives: the chart defines all probes explicitly.

## Secrets contract

The chart does not render Secret objects, generate passwords during upgrades, or
accept plaintext credentials in values. It references these existing Secrets:

| Secret (default name) | Required keys |
| --- | --- |
| `urumi-store-db` | `password`, `root-password` |
| `urumi-dashboard-db` | `password`, `root-password` |
| `urumi-wordpress` | `admin-password` |
| `urumi-woo-mcp` | `consumer-key`, `consumer-secret`, `auth-token` |
| `urumi-dashboard` | `admin-password`, `encryption-key`; optional `openrouter-api-key` |

Names are configurable with `secrets.*`. For a fresh namespace:

```sh
python3 infra/scripts/create-secrets.py --namespace urumi
```

The script uses random values, passes secret file paths rather than secret values
to kubectl, uses temporary mode-0600 files, removes them on exit, and never replaces
an existing Secret. Use a matching `--prefix` when changing Secret names uniformly.
You may instead create the same Secrets through your deployment secret manager.

For OpenRouter, add `openrouter-api-key` to `urumi-dashboard` from a private file
using your secret-management workflow and set `backend.openrouterModel`. The app
also supports saving encrypted workspace credentials through its admin UI; these
runtime settings remain encrypted in MariaDB using the Secret-backed Fernet key.
Bootstrap configuration is in Secrets and never in source or image layers.

The backend reads `ENCRYPTION_KEY_FILE` as a read-only Secret mount. Its writable
`/data/workspace` is temporary because admin credentials are explicitly supplied
and all durable dashboard state is in MariaDB. **Preserve the encryption-key
Secret together with the dashboard database.** Replacing it makes previously
encrypted credentials unreadable.

Admin passwords and database initialization variables apply on first creation;
changing a Secret alone does not reset an existing account/database password.
Rotate database passwords in SQL and the matching Secret together. WooCommerce
REST key rotation is applied by the bootstrap Job on Helm upgrade. The gateway
seeds its upstream URL/token only for a fresh dashboard database: on an existing
workspace, update the upstream Authorization credential through the admin UI when
rotating `auth-token`. Restart consumers after changing environment-backed Secrets.
Do not rotate Fernet keys without re-encrypting stored credentials.

Kubernetes Secrets are base64 encoded API objects. Restrict RBAC and configure k3s
Secret encryption at rest for the VPS; do not grant application pods API access.

## VPS deployment

This targets one production-like VPS, not a highly available database cluster.
Start with roughly 4 vCPU / 8 GB RAM and sufficient disk for the three production
PVCs, images, database growth, and backups; adjust from actual measurements.

1. Install a supported k3s release with Traefik, local-path storage, network-policy
   enforcement, and Secret encryption enabled. Keep its API server restricted to
   trusted operator addresses; expose only 80/443 for application traffic. Protect
   SSH and the server independently. Pin the chosen k3s version for repeatability.
   See the [k3s encryption setup](https://docs.k3s.io/security/secrets-encryption).
2. Point real store/dashboard DNS records to the VPS and verify routing to Traefik.
3. Install cert-manager through its [official Helm chart](https://cert-manager.io/docs/installation/helm/), with CRDs enabled, and
   create a `letsencrypt-prod` ClusterIssuer using your email and the Traefik HTTP-01
   solver. Test certificate issuance before relying on production access. The
   application chart does not install cluster-wide certificate components.
   For example, set `CERT_MANAGER_VERSION` to the supported version you have
   selected, then run:

   ```sh
   helm repo add jetstack https://charts.jetstack.io
   helm repo update
   helm upgrade --install cert-manager jetstack/cert-manager \
     -n cert-manager --create-namespace --version "$CERT_MANAGER_VERSION" \
     --set crds.enabled=true --wait --timeout 5m
   # Edit the email in a private/local copy of this example, then apply it.
   kubectl apply -f /absolute/path/to/cluster-issuer.yaml
   ```

   Start from `infra/cluster/cluster-issuer.example.yaml`. The account private key
   is generated by cert-manager into a Secret, rather than supplied in source.
4. Build and push the four images to a registry accessible from the VPS. From an
   Apple Silicon machine targeting an x86 VPS, use Buildx, for example:

   ```sh
   IMAGE_REGISTRY=ghcr.io/your-org IMAGE_TAG=YOUR_COMMIT \
     PLATFORMS=linux/amd64,linux/arm64 ./infra/scripts/build-images.sh
   ```

   Without `PLATFORMS`, the script builds native local images and does not push.
   Authenticate to the registry first. Private pulls use
   `global.imagePullSecrets: [{name: registry-credentials}]`.
5. Edit a copy of `values-prod.yaml` with your domains, email, registry paths,
   immutable image tags (or digests), storage class and resource budgets. Replace
   every `your-org`, `example.com` and `replace-with-commit` placeholder.
6. On the intended kubeconfig/context:

   ```sh
   kubectl create namespace urumi
   python3 infra/scripts/create-secrets.py --namespace urumi
   helm upgrade --install urumi infra/helm/urumi -n urumi \
     -f /absolute/path/to/values-prod.yaml --wait --timeout 15m
   ```

The production values enable TLS, secure dashboard cookies and the `websecure`
Traefik entrypoint. Public app routes accept HTTPS; HTTP-01 solver routes can still
use port 80. Configure an entrypoint HTTP-to-HTTPS redirect at cluster level if
desired. The chart renders standard Ingress resources rather than controller CRDs.
MCP traffic to WordPress uses private HTTP with `X-Forwarded-Proto: https` so
WooCommerce accepts Basic authentication. These credentials must not be routed
across an untrusted network. Database clients use the private cluster network;
WP-CLI disables TLS for its initialization check because MySQL's generated
self-signed certificate is not a trusted server identity. Configure verified
database TLS separately if the cluster network requires transport encryption.

Readiness/liveness and resource requests/limits cover all eight long-running
workloads. Backend readiness checks MariaDB; `/live` is independent of it. WordPress
readiness checks its database, and its liveness only executes a small PHP endpoint.
The WordPress check works before site installation, so Helm's `--wait` does not
deadlock the post-install Job. MCP checks verify process availability; store API
credentials are verified by the smoke test. The finite bootstrap Job uses retry
and deadline controls instead of HTTP probes. Database startup probes accommodate
initialization; transient dependency failures do not trigger backend liveness
restarts.

## Validation

```sh
helm lint infra/helm/urumi -f infra/helm/urumi/values-local.yaml
helm lint infra/helm/urumi -f infra/helm/urumi/values-prod.yaml
Dashboard/backend/.venv/bin/python infra/tests/test_chart.py
Dashboard/backend/.venv/bin/python -m pytest Dashboard/backend/tests -q
python3 infra/tests/smoke.py
python3 infra/tests/smoke.py --restart
```

The render tests cover both environments, Secret references, all workload probes
and resources, private Services, retained PVCs, TLS settings, custom resources, and
pre-existing PVCs. The local smoke test checks both Ingress hosts, admin login,
three real upstreams, authenticated MCP, a WooCommerce read and calculator call.
`--restart` also creates a temporary draft product and WordPress file, replaces
application pods, and verifies retained products, files, sessions, API keys and
encrypted credentials. It removes its fixtures and revokes its temporary key.
Run restart tests only against a disposable/local workspace: they cause downtime.
No real LLM request is made.

Validated locally on 2026-10-23 with Helm 3.19.0, k3d 5.8.3 and k3s 1.31.5:
both chart variants passed lint and API validation; 24 backend tests, 56 MCP
checks, and three chart contract tests passed. Runtime checks covered Ingress,
authenticated WooCommerce calls, full pod replacement, verified HTTPS and secure
cookies, database-outage readiness without liveness restarts, and backup/restore
into an isolated namespace. VPS certificate issuance and registry access must
still be checked against your actual domain and server.

For a local TLS check, supply a trusted certificate Secret, enable
`ingress.tls.enabled`, and set `ingress.tls.secretName` to that Secret. Run
`python3 infra/tests/smoke.py --https --port 443 --ca-file /private/path/ca.crt`.
The test verifies certificates and secure cookies; it does not disable TLS
verification. Configure local trust separately for browser use.

## Backup and restore

Three PVCs retain MySQL, MariaDB, and the WordPress directory, including uploads,
plugins, themes and generated WordPress configuration. `helm uninstall` retains
these PVCs by default; database StatefulSets use explicitly named PVCs. Set
`*.storage.existingClaim` to reuse imported claims. Do not change a bound claim's
storage class or shrink its capacity. The local-path provisioner does not resize
existing volumes automatically; plan capacity or migrate to larger volumes.

Local-path volumes survive pod recreation on the same node. Deleting the k3d
cluster or losing the VPS disk can destroy them. Keep off-node backups; a PVC is
not a backup. Retained claims are still bound to node-local disks and cannot
automatically move to another node.

For a consistent backup, enter a maintenance window using the **same environment
values** as the installed release. Maintenance removes public Ingress routes,
stops the gateway, and skips bootstrap hooks; databases and WordPress stay running
for backup commands. Wait for in-flight calls to finish before enabling it.

```sh
helm upgrade urumi infra/helm/urumi -n urumi \
  -f infra/helm/urumi/values-local.yaml --set maintenance.enabled=true --wait --timeout 15m
kubectl -n urumi wait --for=delete pod \
  -l app.kubernetes.io/instance=urumi,app.kubernetes.io/component=backend --timeout=120s
python3 infra/scripts/backup.py .backups/local-snapshot
helm upgrade urumi infra/helm/urumi -n urumi \
  -f infra/helm/urumi/values-local.yaml --set maintenance.enabled=false --wait --timeout 15m
```

Backup captures both SQL dumps, WordPress files and all five application Secrets
in a new private directory. Transfer it to protected off-node storage. Never
commit the directory or print its Secret JSON files.

Restore to an **empty destination**, using the same release, namespace, database
names, usernames and Secret names initially. On a new cluster/context:

```sh
kubectl create namespace urumi
for secret_file in .backups/local-snapshot/urumi-*.json; do
  kubectl apply -f "$secret_file"
done
helm upgrade --install urumi infra/helm/urumi -n urumi \
  -f infra/helm/urumi/values-local.yaml --set maintenance.enabled=true --wait --timeout 15m
python3 infra/scripts/restore.py .backups/local-snapshot
helm upgrade urumi infra/helm/urumi -n urumi \
  -f infra/helm/urumi/values-local.yaml --set maintenance.enabled=false --wait --timeout 15m
python3 infra/tests/smoke.py
```

Restore refuses nonempty databases and an active gateway/Ingress. Keep the
destination private until restore succeeds; a partial failed restore requires
fresh empty destination volumes before retrying. Use production values on the
VPS. If domains change, use WP-CLI search-replace for serialized WordPress content,
update canonical URL values, and test media/links. Verify file ownership (UID 33)
and ensure the dashboard uses its original encryption-key Secret.

## Migrating existing Docker data

The local quick start creates a fresh Kubernetes workspace. It does not import or
modify existing Compose volumes. To migrate:

1. Stop writes to both Docker stacks; back up both databases and WordPress files.
2. Export the existing dashboard `/data/encryption.key` privately. Run
   `create-secrets.py --encryption-key-file /private/path/encryption.key` **before**
   starting the Kubernetes backend, or restore the original key into its Secret.
   Preserve the other original credentials in the corresponding Secrets.
3. Install with maintenance enabled, import SQL into the empty databases, and
   restore WordPress files. Existing nonempty databases must never be initialized
   with a new Fernet key. Use the Docker migration guide first for legacy SQLite.
4. Existing dashboard upstream records are preserved. Change their old localhost
   or Docker URLs through the admin UI to `http://urumi-woo-mcp:8000/mcp`,
   `http://urumi-clock:8002/mcp`, and `http://urumi-calculator:8003/mcp`; configure
   WooCommerce's bearer credential from the Secret. Enable maintenance=false and
   verify the complete flow before retiring Docker.

WordPress's entire directory is persisted. Rebuilding its image does **not**
overwrite existing core/plugins. Back up and update persisted WordPress and
WooCommerce using their normal upgrade process (WP-CLI or admin UI); test database
migrations and restore procedures before production upgrades. Base WordPress and
WP-CLI images are pinned by multi-architecture digest and the plugin download by
version in the WordPress Dockerfile. Update these pins deliberately for fresh installs.

## Operations and limits

```sh
kubectl -n urumi get pods,pvc,ingress
kubectl -n urumi logs deployment/urumi-backend
kubectl -n urumi logs job/urumi-wordpress-bootstrap
helm history urumi -n urumi
k3d cluster stop urumi
k3d cluster start urumi
```

Failed bootstrap Jobs remain inspectable; successful ones are removed. A Helm
upgrade retries the bootstrap Job. Helm rollback does not undo SQL migrations,
restore data, or rotate credentials; restore a compatible backup when necessary.
Never delete the retained PVCs or Secrets as a routine redeployment step.

The backend and WordPress intentionally run one replica. WordPress uses a
`Recreate` rollout for its single writable volume; the backend uses `Recreate` to
avoid overlapping schema/bootstrap owners and retains its process-local login
throttle. These rollouts can interrupt service. Scaling requires a shared rate
limiter, reviewed migrations, and shared WordPress storage. This setup adds no
database replication, automatic node failover or external secret manager.
