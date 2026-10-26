# Urumi · MCP gateway workspace

A shared workspace for a WooCommerce store, three MCP upstreams, an authenticated
gateway, and a tool-calling chat agent. FastAPI and MariaDB power the backend;
Nginx serves the JavaScript dashboard and proxies its APIs on the same origin.
No Node build step is required. Kubernetes runs the eight core workloads plus a
store provisioning controller. Dashboard members can create isolated stores with
automatically registered MCP tools.

## Project contents

- [Dashboard frontend](Dashboard/frontend/) and [backend/API](Dashboard/backend/src/backend/app.py).
- [MCP aggregation and routing](Dashboard/backend/src/backend/gateway.py).
- [WooCommerce MCP tools](woocommerce_app/mcp/service.py) and [REST client](woocommerce_app/mcp/woo_client.py).
- [Chat agent loop](Dashboard/backend/src/backend/app.py) (`chat`) and [OpenRouter integration](Dashboard/backend/src/backend/openrouter.py).
- [Helm chart](infra/helm/urumi/), [local values](infra/helm/urumi/values-local.yaml), and [production values](infra/helm/urumi/values-prod.yaml).
- [Store provisioning guide](docs/PROVISIONING.md), [controller](Dashboard/backend/src/backend/provisioner.py), and [per-store resources](Dashboard/backend/src/backend/store_resources.py).
- [System design and tradeoffs](docs/SYSTEM_DESIGN.md) and [threat model](docs/THREAT_MODEL.md).
- [Demo video script and recording guide](docs/DEMO_VIDEO.md).

## Local setup: Kubernetes with k3d

Install Docker, k3d, kubectl, Helm 3, and Python 3, and make their commands available
on your `PATH`. Start Docker and leave loopback ports 80/443 available. Allow Docker
enough memory for the cluster and both databases; the infrastructure guide suggests
starting with 6 GB. From the repository root:

```sh
docker info
./infra/scripts/local-up.sh

# Run this in your terminal too: the script cannot export variables to its parent.
export KUBECONFIG="$(k3d kubeconfig write urumi)"
kubectl config current-context
kubectl -n urumi get pods,pvc,ingress
helm status urumi -n urumi
```

The context should be `k3d-urumi`, nine long-running application pods should become
`1/1 Running`, and three PVCs should be `Bound`. Setup builds/imports four images,
creates missing Secrets, installs Helm, and bootstraps WordPress/WooCommerce plus
its REST API key. Successful bootstrap Jobs are removed. Reruns preserve Secrets
and data; this starts a fresh workspace rather than importing existing Compose data.

Add the following line to `/etc/hosts` with an administrator-capable editor:

```text
127.0.0.1 store.urumi.test dashboard.urumi.test
```

| Service | Local URL | Initial login |
| --- | --- | --- |
| Store | <http://store.urumi.test> | Shopper checkout is separate from dashboard login |
| WordPress admin | <http://store.urumi.test/wp-admin/> | `urumi-admin` |
| Dashboard | <http://dashboard.urumi.test> | `admin@urumi.test` |
| External MCP gateway | <http://dashboard.urumi.test/mcp> | Approved dashboard API key |

Read initial passwords privately from Kubernetes Secrets:

```sh
kubectl -n urumi get secret urumi-dashboard -o jsonpath='{.data.admin-password}' | base64 --decode
kubectl -n urumi get secret urumi-wordpress -o jsonpath='{.data.admin-password}' | base64 --decode
```

Check the application without needing hosts-file resolution for the smoke test:

```sh
curl --resolve dashboard.urumi.test:80:127.0.0.1 http://dashboard.urumi.test/health
python3 infra/tests/smoke.py
# Optional: briefly replace all application pods and verify persisted state.
python3 infra/tests/smoke.py --restart
```

If kubectl tries `localhost:8080`, set `KUBECONFIG` in that same terminal using the
command above. For a stopped cluster, start Docker and run `k3d cluster start urumi`.
Stop it with `k3d cluster stop urumi`; deleting the cluster can destroy local data.
See the [infrastructure guide](infra/README.md) for rebuilds, logs, backups and migration.

## Create multiple stores

After the core smoke test passes, open **Stores → Create store**. Each store gets
its own namespace, database, content volumes, Secrets, Ingress host and WooCommerce
MCP server. Watch Provisioning/Ready/Failed status and follow the store URL once
Ready. Several stores can provision concurrently. Owners and admins can access a
store's tools; other members cannot. Tool names include its name and stable ID.

Local generated hosts use `<id>.127.0.0.1.sslip.io` for wildcard loopback DNS.
For a VPS, configure `provisioning.domain` and matching wildcard DNS, a Delete
reclaim-policy StorageClass, registry credentials and Ingress TLS/cert-manager.

Create orders in two stores, then ask Agent playground to compare today's orders
for both stores using UTC and all pages. Delete one using **Stores → Delete**;
its tools disappear immediately and its row disappears after namespace/PV cleanup.
Deletion permanently removes that store's data.

```sh
# Disposable two-store acceptance test, including controller restart and isolation.
# Uses real MCP/store data and a deterministic model fixture; no provider billing.
python3 infra/tests/provisioning_smoke.py
```

See [provisioning operations, guardrails and recovery](docs/PROVISIONING.md) for
idempotency, timeouts, NetworkPolicies, limits, cleanup and production tradeoffs.

## VPS / production-like setup: k3s

Use the **same chart**, with production Helm values. This is a single-VPS deployment,
not a highly available database setup. Read the [production procedure](infra/README.md#vps-deployment)
and [threat model](docs/THREAT_MODEL.md) before exposing the dashboard to other users.

1. Install a supported, pinned k3s release with Traefik, local-path storage,
   NetworkPolicy enforcement, and Secret encryption at rest. Restrict the Kubernetes
   API/SSH to operators; public application traffic uses ports 80/443.
2. Point real store/dashboard DNS records at the VPS. Install cert-manager through
   Helm and configure a `letsencrypt-prod` ClusterIssuer using the
   [issuer example](infra/cluster/cluster-issuer.example.yaml) with your email.
3. Authenticate to your image registry, then build/push images for the VPS architecture:

   ```sh
   IMAGE_REGISTRY=ghcr.io/your-org IMAGE_TAG="$(git rev-parse --short HEAD)" \
     PLATFORMS=linux/amd64,linux/arm64 ./infra/scripts/build-images.sh
   ```

4. Make a deployment-specific copy of [values-prod.yaml](infra/helm/urumi/values-prod.yaml).
   Replace registry paths, tags/digests, domains and admin emails. Set the actual
   StorageClass, volume capacities, proxy CIDRs, controller selectors and resources.
   Production values enable TLS and secure cookies; provide the matching issuer
   and registry pull Secret if needed. Keep passwords/tokens out of values files.
5. With kubectl/Helm pointing to the VPS cluster, create its namespace and Secrets,
   then install. The helper below is for a fresh workspace; migration needs the
   original credentials and Fernet key instead.

   ```sh
   kubectl config current-context
   kubectl create namespace urumi
   python3 infra/scripts/create-secrets.py --namespace urumi
   helm upgrade --install urumi infra/helm/urumi -n urumi \
     -f /path/to/your/values-prod.yaml --wait --timeout 15m
   kubectl -n urumi get pods,pvc,ingress
   ```

6. Verify valid HTTPS, login, tool discovery and a read-only store call at your real
   hostnames. Set up off-node backups for both databases, WordPress files and Secrets.
   Node-local PVCs do not survive disk loss, and Helm rollback cannot undo SQL changes.

Signup currently grants browser access to shared tools. Add an access gate or
implement invitations/member approval before admitting untrusted users. Current
NetworkPolicies restrict ingress, not outbound destinations; private database/MCP
hops use the cluster network without transport encryption.

## Place a demo order

Use the local test store and fictitious customer details. Helm installs the store
and integration credentials; it does not seed products, shipping or payment settings.

1. Sign into WordPress admin using the credentials above. Complete any store setup
   prompts and check **WooCommerce → Settings → General** for selling/shipping
   countries and currency. If the store is in Coming soon mode, change its site
   visibility to Live for the demo. Verify Cart and Checkout pages are assigned
   under **Settings → Advanced → Page setup** if those pages are missing.
2. Under **Products → Add new**, create a simple physical product named `Demo mug`,
   set a regular price such as `10`, keep it in stock and **Publish** it.
3. Under **WooCommerce → Settings → Shipping → Shipping zones**, create a zone
   covering your test address, add **Flat rate**, and set its cost, for example `2`.
   A physical product needs a shipping method available for the checkout address.
4. Under **Settings → Payments → Take offline payments**, enable **Cash on delivery**.
   In its settings, allow the shipping method chosen above and save. This demo uses
   an offline payment method; no card provider or real payment is needed.
5. In a private browser window, open `http://store.urumi.test/?post_type=product`,
   open `Demo mug`, add it to the cart and proceed to Checkout. Enter test customer
   details matching the shipping zone, choose Cash on delivery, and place the order.
   If checkout requires a store account, create one there; a dashboard account is separate.
6. Confirm the order under **WooCommerce → Orders**. In the dashboard's **Tool explorer**,
   run `list_orders`, select the returned internal ID, then call `get_order`. You can
   also ask the chat agent to list recent orders. Do not guess the ID from a sample
   or assume a displayed order number is the internal ID.

If checkout reports no shipping/payment method, check the address's matched zone
and COD's allowed shipping methods. The MCP server does not expose order creation:
place the order through storefront checkout, then inspect/manage it through tools.
Official references: [adding products](https://woocommerce.com/document/managing-products/add-product/),
[flat-rate shipping](https://woocommerce.com/document/flat-rate-shipping/), and
[Cash on delivery](https://woocommerce.com/document/cash-on-delivery/).

## Optional Docker Compose deployment

The dashboard frontend (Nginx) and backend (FastAPI) have separate Dockerfiles and containers. Its database uses `mariadb:11.4`, separate from WooCommerce’s MySQL. Configure database passwords in `Dashboard/.env` using the example first:

```sh
docker compose -f Dashboard/docker-compose.yml up -d --build
```

Open `http://127.0.0.1:8001`. Docker uses separate persistent database and encryption-key volumes with a fresh workspace by default. To retain existing local users/API keys, import the local database and encryption key **before the first startup**. See [Dashboard Docker setup](Dashboard/README.md) for the import command, configuration, and separate rebuild commands.

## Optional host Python development

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/). From the repository root:

```sh
# First configure and start MariaDB as described in Dashboard/README.md.
cd Dashboard/backend
uv sync --locked
uv run python ../run.py
```

Open **http://127.0.0.1:8001**. Keep the launcher terminal open; Ctrl-C stops the dashboard and both helper servers. SIGTERM/SIGHUP also clean up all children. If a required port is already occupied, the launcher reports it before starting any service.

The launcher starts the dashboard plus two independent Streamable HTTP upstreams:

| Service | Endpoint | Tools |
| --- | --- | --- |
| Urumi gateway | `http://127.0.0.1:8001/mcp` | Aggregated tools; approved API key required |
| WooCommerce (existing Docker stack) | `http://127.0.0.1:8000/mcp` | Products and orders |
| Clock | `http://127.0.0.1:8002/mcp` | Current time in an IANA timezone; server info |
| Calculator | `http://127.0.0.1:8003/mcp` | Arithmetic; server info |

Start the existing store from the repository root:

```sh
docker compose -f woocommerce_app/docker-compose.yml up -d
```

Its configuration is in `woocommerce_app/.env` and `woocommerce_app/mcp/.env`; see [WooCommerce setup](woocommerce_app/mcp/README.md). The compose file defaults WordPress to port **8080** and MCP to **8000**. Change the WooCommerce endpoint in **MCP servers** if your mapping differs. If `MCP_AUTH_TOKEN` is set, edit the server and enter `Authorization` as the credential header and `Bearer <token>` as the credential.

## Sign in and access

For Kubernetes, use `admin@urumi.test` and the `urumi-dashboard` Secret shown above.
For host Python/Compose, the admin email defaults to `admin@urumi.local`; if no
initial password is supplied, it is generated in `.data/bootstrap-admin.txt` (or
Compose's `/data/bootstrap-admin.txt`). `ADMIN_EMAIL`/`ADMIN_PASSWORD` bootstrap a
new database rather than resetting an existing account.

Anyone signing up through the UI receives the **customer** role. Administrators see all users, all API keys, and all audit records. Customers see their own keys and audit records and may use the shared gateway tools through the dashboard.

- Generate a named API key under **API keys**. The token is shown only once.
- Customer keys begin **pending**. An admin must approve them.
- Admins can approve or suspend keys; suspension can be reversed.
- Owners and admins can revoke keys. Revocation is permanent and takes effect on the next gateway request.
- Customers cannot edit upstreams, AI settings, approve keys, or access other users' keys or audit records.

An approved API key grants access to enabled shared upstreams and its owner's
provisioned stores (admins can access all stores), including exposed writes.
Fine-grained per-tool scopes are not implemented. A logged-in member can
already use Tool Explorer/chat; pending API-key approval only blocks external MCP
requests. See the [threat model](docs/THREAT_MODEL.md) for shared-store implications.

## Connect an MCP client

Sign into the dashboard, generate a named key under **API keys**, and copy its full
`urm_...` token when shown. A customer must have an administrator approve the key.
Add this to an external client that accepts `mcpServers` configuration:

```json
{
  "mcpServers": {
    "urumi": {
      "url": "http://dashboard.urumi.test/mcp",
      "headers": { "Authorization": "Bearer YOUR_APPROVED_API_KEY" }
    }
  }
}
```

Replace `YOUR_APPROVED_API_KEY` with that token, reload the client's MCP connection,
list its tools, then try a read-only call such as `list_products`. Names include
the server ID, for example `s1__list_products`; use the actual discovered name.
For a VPS, use `https://YOUR_DASHBOARD_DOMAIN/mcp`; host Python/Compose instead uses
`http://127.0.0.1:8001/mcp`. A client running on another machine needs reachable DNS
and routing; your laptop's `.test`/loopback mapping is local to that laptop.

The dashboard includes an endpoint and connection guide. Other configuration
formats use the same Streamable HTTP transport and bearer header. This token is
different from both the private WooCommerce MCP token and the store's REST keys.

### Codex configuration

Codex uses TOML in `~/.codex/config.toml`, rather than the JSON example above:

```toml
[mcp_servers.woocommerce]
enabled = true
url = "http://dashboard.urumi.test/mcp"
http_headers = { Authorization = "Bearer YOUR_APPROVED_API_KEY" }
```

Use the complete generated key, not its displayed prefix. Alternatively, `bearer_token_env_var = "URUMI_API_KEY"` reads a token from an environment variable named `URUMI_API_KEY`; **do not put the actual key in that setting**. The variable must be available to the running Codex process. Static headers avoid dependence on terminal environment variables when using the desktop app. Keep the config file private because static headers contain the credential.

Keep the selected deployment running and reload the MCP connection after changing
configuration. Only host Python development needs `uv run python ../run.py` kept
open; Kubernetes runs its own backend pod. This gateway uses bearer API keys, so
`codex mcp login` (OAuth) is not its authentication flow. See
[official OpenAI MCP configuration documentation](https://developers.openai.com/codex/mcp).


The gateway implements stateless Streamable HTTP with JSON responses to `initialize`, `ping`, `tools/list`, and `tools/call`. Notifications return HTTP 202. GET streaming and DELETE sessions are not supported (HTTP 405); clients reconnect without a persistent gateway session. Resources, prompts, subscriptions, and OAuth are outside this implementation.

### Namespaces and upstream failures

Every tool is named `s<database-server-id>__<original-name>`. For example, `s2__info` and `s3__info` coexist even though both servers define `info`. Server renames preserve tool identifiers. Delete-and-recreate allocates a new namespace.

Discovery checks upstreams concurrently, with an independent 1–30 second budget per server. Disabled and unavailable upstreams contribute no tools. Calls open independent authenticated connections to the selected upstream, bounded by its timeout. Errors are returned as MCP `isError` content without upstream exception details. Already-running calls are allowed to finish when a key or server is disabled.

**Tool explorer** lets you inspect schemas and make real calls. Order lookup forms load recent orders instead of guessing ID 1. Select an order or use an existing internal ID from `list_orders`; storefront order numbers can differ. Store mutations, such as creating products, affect the configured store. Audit records capture actor, action, tool name, result status, duration, and UTC timestamp; they deliberately omit credentials, prompts, tool arguments, and results.

## Chat with OpenRouter tools

OpenRouter is the only chat provider. As an administrator, open **Agent playground
→ Connect OpenRouter** or **OpenRouter settings**, choose a model from the searchable
catalog and enter your OpenRouter API key. Customers use the shared workspace
connection and cannot edit its credentials.

1. Save the connection and optionally use **Test connection**.
2. Open **Agent playground** as the member whose actions should be audited. Browser
   chat uses your login session; it does not require generating a dashboard API key.
3. Try: “What time is it in Asia/Kolkata?”, “Calculate 84 × 12”, “List the store's
   products”, or “Show the five most recent orders”. Place a demo order first if
   you want a nonempty order result.
4. Inspect the displayed tool names, arguments and results alongside the answer.
   Look under **Audit log** for `chat.tool` entries attributed to that member.

The workspace's OpenRouter account handles model requests from all members. Chat
messages and tool results leave the cluster for that provider; use fictitious demo
data. Store-changing requests execute real writes, and confirmation is not enforced
by a separate approval mechanism.

The catalog includes models that advertise both tool and tool-choice support, with free models labeled. An API key is required even for free models. Saving validates model availability and checks the API key through OpenRouter's account endpoint without generating a completion. **Test connection** sends a small completion request with a harmless test tool to confirm the selected model can request tools; this uses the model's normal pricing and limits. It does not execute gateway tools or save changes.

The endpoint is fixed server-side to `https://openrouter.ai/api/v1/chat/completions`; there is no base URL input. Previously saved OpenRouter settings are recognized even if the full completion URL was entered. Keys from other providers are not reused. Saved model keys stay encrypted and are never returned to the browser. Empty key input preserves an existing OpenRouter key; the explicit removal checkbox clears it.

For environment configuration, export `OPENROUTER_MODEL` and `OPENROUTER_API_KEY` before starting. Saved OpenRouter settings take precedence over these defaults. See [`.env.example`](Dashboard/backend/.env.example). No local model or other provider is supported.

The assistant discovers enabled tools, sends their schemas to OpenRouter, executes calls through the same gateway routing function as external clients, and feeds results back to the model. Tool schemas accompany every model step, and OpenRouter routing requires support for the supplied parameters. Opaque reasoning metadata is preserved for model tool continuations. The UI displays each call, arguments, result, and failure. Calls use the logged-in user's identity and are audited as `chat.tool`.

The model/tool loop is bounded to six model steps, ten calls per step, and a
120-second budget, with up to 4096 completion tokens per model step. Initial
upstream discovery happens before that loop budget. Provider failures show
actionable messages for invalid keys, insufficient credits, unavailable models,
rate limits, and network errors. An OpenRouter authentication failure does not
sign the user out of the dashboard. Provider error bodies and credentials are
never exposed.

The assistant's confirmation behavior for mutations is prompt-based, not an enforced approval system. Conversations stay in browser memory and reset on reload/logout. See OpenRouter's [tool-calling guide](https://openrouter.ai/docs/guides/features/tool-calling) and [error reference](https://openrouter.ai/docs/api/reference/errors-and-debugging) for provider behavior.

## Security and persistence

- Passwords use salted scrypt. API and session tokens use SHA-256 hashes; tokens contain cryptographically random entropy.
- Sessions expire after 24 hours and use HttpOnly, SameSite=Strict cookies. Logout invalidates the session server-side.
- Sign-in/sign-up have an in-process IP throttle. Cross-origin writes are rejected; CSP restricts scripts to same-origin assets. User and upstream strings are HTML-escaped in the UI.
- Upstream credentials and saved model keys are encrypted with Fernet. Responses expose only whether a credential exists. Empty form inputs preserve credentials; explicit clear controls remove them.
- MariaDB stores dashboard records. Kubernetes mounts the Fernet key from the `urumi-dashboard` Secret using `ENCRYPTION_KEY_FILE`; host Python/Compose use a key in `.data/` or `dashboard_data`. Back up the database **and its matching encryption key together**. Losing the key makes saved credentials unreadable; anyone who can read both can decrypt them. Store MySQL and WordPress files have separate PVCs.
- Upstream configuration is administrator-only and allows loopback/private addresses because this gateway is designed to connect local services. Treat administrators as trusted network operators.

Host development/Compose binds public ports to loopback. The production Helm values
configure HTTPS-facing routing and secure cookies, while private Services and
ingress NetworkPolicies protect internal endpoints. Provisioned stores additionally
use ingress/egress isolation, namespace quotas and owner/admin tool authorization.
The original shared upstreams have no outbound restrictions or per-tool scopes;
shared chat usage quotas and invitations/member approval are not implemented. No dashboard password recovery or multi-factor login is included.
Read the [threat model](docs/THREAT_MODEL.md) before sharing a deployment.

## Verify

```sh
cd Dashboard/backend
uv run pytest -q
```

Backend cases cover authentication, API key permissions, MCP routing and failure isolation, encrypted credentials, a real MCP SDK connection, the OpenRouter tool loop, transaction rollback, foreign-key enforcement, and API date formatting. OpenRouter cases cover the fixed endpoint, supported-model filtering, save/key validation, credential preservation/removal, legacy settings, safe connection testing, and provider errors including errors inside HTTP 200 responses. Deployment cases check Secret-backed encryption keys, authenticated upstream seeding and independent health checks. Tests use a disposable MariaDB Docker container and local fixture servers; no real store mutations or model billing occur. For Helm checks and cluster smoke tests, see the [infrastructure validation guide](infra/README.md#validation).

JavaScript syntax check from the repository root:

```sh
node --check Dashboard/frontend/app.js
node --test Dashboard/frontend/tests/tool-inputs.test.cjs
```

## Layout

- `Dashboard/frontend/` — responsive overview, stores, servers, tool explorer, keys, chat, audit, users, and AI settings.
- `Dashboard/backend/src/backend/app.py` — authentication, dashboard API, MCP endpoint, and model loop.
- `Dashboard/backend/src/backend/openrouter.py` — fixed OpenRouter transport, model catalog, key checks, and safe provider errors.
- `Dashboard/backend/src/backend/gateway.py` — Streamable HTTP discovery, namespacing, routing, and timeouts.
- `Dashboard/backend/src/backend/store.py` — MariaDB, hashing, encryption, and first-run setup.
- `Dashboard/backend/src/backend/upstreams.py` — Clock and Calculator MCP servers.
- `Dashboard/run.py` — local process launcher and shutdown handling.
- `woocommerce_app/` — existing WordPress/WooCommerce stack, preserved.
