# Assignment overview

> Historical implementation notes: the SQLite and combined frontend/backend
> descriptions below predate the MariaDB and Kubernetes deployment. The current
> [System design and tradeoffs](docs/SYSTEM_DESIGN.md) and
> [threat model](docs/THREAT_MODEL.md) describe the delivered system.

## Store provisioning decisions — 2026-10-26

This section records the choices behind extending the working single-store
application into a provisioning platform. The earlier notes below remain as
implementation history. Operational instructions are in
[the provisioning guide](docs/PROVISIONING.md).

### Start from a verified core

The extension was started after the local core smoke test passed: both Ingress
hosts, dashboard login, authenticated gateway access, a real WooCommerce read and
calculator execution. The existing cluster first needed to be started. Keeping
the original shared store allowed the new lifecycle to be tested independently
and checked for regressions after deployment.

### Helm for the platform; Kubernetes API for individual stores

The same Helm chart deploys the dashboard, backend and provisioning controller
locally and on a production-like VPS. Environment differences remain in values.
Individual stores are created through the Kubernetes API, which the provisioning
requirement explicitly permits. This avoids running Helm subprocesses and managing
per-store Helm release state inside the application. The existing Python HTTP
client provides authenticated API access without another runtime dependency.

The tradeoff is that store resource definitions live in Python rather than a
separate per-store chart. They need their own resource-contract tests. Helm
uninstall removes the platform components but does not remove dynamically created
store namespaces; stores must be deleted through the platform first.

### A separate controller with durable database intents

The web API saves a request in MariaDB and returns HTTP 202. A separate controller
performs the Kubernetes work. This keeps a long bootstrap out of the HTTP request
and avoids relying on background tasks that disappear when a web process restarts.
The backend does not need a Kubernetes token.

MariaDB was already required for users, upstreams and audits, so the `stores`
table also records desired state, status, phase, deadline, failure reason and
upstream association. A polling controller resumes unfinished work from those
records. A queue or custom Kubernetes resource could support a larger operator,
but would introduce another persistence or API lifecycle for this project. The
tradeoff is dependence on the shared database and a short polling delay.

### Stable identities, safe retries and bounded concurrency

The server generates a store UUID, namespace and host. A browser-generated
`request_key`, unique per owner, makes repeated create submissions return the same
store. Reusing a key with a different name is rejected; a deleted request cannot
recreate its store. An explicit retry after failure cleanup keeps the ID/host and
starts a new attempt with a new deadline.

Kubernetes resources use stable names and ownership labels. Create-if-absent
preserves existing Secrets, PVC bindings and Job templates after an interrupted
operation. Foreign ownership causes failure instead of mutation. This supports
restart recovery, but deliberately does not update existing stores when templates
or image values change; continuous drift repair and upgrades remain future work.

A MariaDB named lock excludes competing reconciliation steps for the same store
and is released when its database connection dies. A semaphore allows different
stores to progress concurrently, with four slots by default. This avoids a global
provisioning lock while keeping controller concurrency bounded.

### One namespace and one stateful stack per store

Each store has its own WordPress, MySQL, WooCommerce MCP server, bootstrap Job,
Secrets, ConfigMap, Services, two PVCs and Ingress. Sharing WordPress or database
credentials would make deletion and isolation harder to reason about. A namespace
also gives Kubernetes a natural boundary for policies, quotas and teardown.

The cost is additional memory, CPU and storage for every store. Namespace isolation
is not equivalent to a separate VM or cluster. The controller, cluster operators,
dashboard database, encryption key and workspace administrators remain trusted
across stores.

### Register tools only after application readiness

Ready requires bootstrap completion, ready database/application workloads and an
authenticated `list_products` call through the store's MCP server. With TLS enabled,
the controller also waits for the store TLS Secret. Pod readiness alone would not
prove that WooCommerce bootstrap and integration credentials actually work.

The gateway registration and Ready state commit in one database transaction. The
controller rechecks desired state under a row lock before registering, preventing
a concurrent deletion from bringing the tools back. Bootstrap still uses WP-CLI;
runtime tools continue to use the WooCommerce REST API.

### Store-specific routing and ownership checks

Generated tools use `store_<name>_<stable-id>__<tool>`, making the target store
visible while preventing collisions. Existing shared upstreams retain their
`s<server-id>__<tool>` names for compatibility. Tool descriptions identify the
store and its UTC timezone.

Owners and admins can discover and invoke a provisioned store's tools. The gateway
checks this on every call, including calls with a guessed numeric upstream alias,
so hiding tools in the dashboard is not the authorization boundary. Generic
upstream edit/delete endpoints reject managed entries to preserve controller
ownership. The original shared tools remain available to workspace members;
fine-grained per-tool scopes are not implemented.

Each store receives random credentials in a Kubernetes Secret. The gateway stores
its MCP credential encrypted and supplies that credential on outbound connections;
client session/API tokens are never passed upstream. Registry pull Secrets are
copied into a store namespace when private images need them. Existing namespaces
require operator-managed credential rotation.

### Network isolation before workloads start

Default-deny ingress and egress policies are created before store pods. Narrow
allowances permit DNS, WordPress/bootstrap to the same namespace's database, MCP
to its own WordPress, Ingress-controller access to WordPress, and platform
backend/controller access to MCP. Both source egress and destination ingress
therefore restrict cross-store access.

This requires a NetworkPolicy-enforcing CNI. It also blocks plugin downloads,
outbound mail and arbitrary integrations until an operator adds suitable policies.
Only the controller mounts a Kubernetes token. Its explicit ClusterRole avoids
wildcard and exec permissions, but namespace/workload creation still gives it
substantial cluster-wide authority; ownership labels do not contain a compromised
controller.

### Guardrails at admission and inside Kubernetes

The default maximum is three stores per user. Admission locks the owner's database
row before counting and inserting, so parallel create requests cannot both take
the last slot. Failed and Deleting stores continue to occupy slots until deletion
completes. ResourceQuota limits aggregate namespace resources and object counts;
LimitRange supplies defaults and bounds container resources.

The default provisioning deadline is 900 seconds and survives controller restarts.
A timeout records a reason and enters cleanup. These controls bound provisioning
and namespace allocation; they do not provide a global chat quota, prevent users
creating multiple accounts, or turn local-path capacity requests into physical
filesystem quotas.

### Deletion completes only after storage cleanup

Deletion atomically removes MCP registration and records the delete intent, then
the controller deletes the owned namespace. It waits for the namespace and all
PV objects referencing it to disappear before marking the store Deleted. A
Deleted tombstone preserves request idempotency. Failure cleanup follows the same
resource-removal path; retry becomes available only after cleanup completes.

Provisioning requires a StorageClass with reclaim policy Delete. A Retain class
would conflict with the promised teardown. Stuck finalizers or storage failures
leave cleanup pending instead of reporting success; the controller does not
force-remove finalizers. An already running tool call can finish after deletion
is requested, but subsequent calls cannot use the removed registration.

### Stable URLs and production certificate support

Local generated hosts use `<id>.127.0.0.1.sslip.io`, avoiding a hosts-file edit for
each new store. This depends on external DNS and can be blocked by DNS rebinding
protection, so hosts-file or local wildcard DNS alternatives are documented.
Production uses wildcard DNS under the configured store domain and inherits the
platform's Ingress/TLS settings.

Each production store requests its own certificate rather than copying a wildcard
private key into every namespace. The quota reserves a second Ingress and a narrow
policy permits the temporary cert-manager HTTP-01 solver. The tradeoff is
certificate issuance time, ACME limits and dependence on correct public DNS and
issuer configuration.

### Evidence and boundaries of testing

The two-store acceptance test uses real Kubernetes resources, WordPress databases,
MCP connections and the chat handler. It creates stores concurrently, replaces the
controller during provisioning, seeds today's orders, compares Alpha's two orders
totaling USD 40 against Beta's one totaling USD 12, and checks isolation using both
Service DNS and direct Pod IPs. It then deletes one store, asserts namespace/PV/tool
removal and verifies the remaining store still works before removing the fixtures.
The dashboard create flow and automatic Ready update were also checked manually.

Only the model response is a deterministic fixture in the automated chat check.
This makes routing and lifecycle tests reproducible without provider credentials
or billing; it does not establish live-model reasoning quality. Live OpenRouter
inference and production certificate issuance remain separate checks. Backend
tests cover admission races, ownership, retries, interrupted provisioning, timeout
cleanup, delete/registration races, Secret preservation and waiting for PV removal.

Restart recovery assumes the dashboard database and cluster storage survive.
It is not disaster recovery. The original backup helper refuses active provisioned
namespaces because it does not capture their databases, files and Secrets. Full
coordinated store backups, upgrades and continuous health repair remain explicit
production follow-up work.

---


## problem statements
- `Store:` One WooCommerce store (WordPress and WooCommerce) running on Kubernetes.
- `WooCommerce MCP server:` An MCP server you write yourself that exposes the store's products and orders as tools.

## woocommerce_app


## versions
-  mySQL image :  `mysql:8.4`
- wordpress :  `wordpress:php8.3-apache`
- mcp server : `woo-mcp:local`

## architectural decisions

### choosing RESTAPI over wordpress CLI

 - I chose the WooCommerce REST API because my custom MCP server is a Python service that needs to interact with WooCommerce programmatically.

 - The REST API provides structured HTTP endpoints for operations like listing products, creating products, retrieving orders, and updating order statuses. This lets me integrate WooCommerce directly into my Python MCP tools without executing shell commands or managing a CLI process.

 - It also gives me a cleaner separation of concerns: the MCP server handles tool definitions and input validation, while WooCommerce handles the underlying store operations. Authentication is managed through WooCommerce API credentials, which I keep server-side rather than exposing them to clients.

 - WP-CLI is useful for WordPress administration, setup, and maintenance, but for a service that exposes product and order operations through MCP, the REST API is a more natural integration point. It also fits my gateway architecture, where clients communicate through a single MCP endpoint.

### MCP server using : FASTMCP

- choosing FASTMCP over MCPSDK

- I chose FastMCP because my primary goal was to build a working MCP gateway that could expose tools from multiple upstream servers through a single endpoint.

- FastMCP provides a higher-level abstraction for defining tools, managing MCP server behavior, and connecting to upstream servers, which reduces boilerplate and lets me focus on the core engineering challenges of the assignment: dynamic tool registration, routing, authentication, error handling, and audit logging.

- The official MCP Python SDK is also a valid choice and offers protocol-level building blocks. I don't think FastMCP is inherently better; it was a pragmatic choice for this project's scope and timeline.


##  Dashboard

### DATASTORES


- users(id , name , email , password, role , created)
- sessions(token , user_id , expires);
- api_keys(id , user_id , name , prefix , hash, status , created ,last_used);
- upstreams(id, name , url, enabled, auth_header, credential,timeout)
- audit(id , user_id , action, target , status, duration , created);
- settings(key , value);

### AUTH
- `POST`: `/api/auth/signup`
- `POST`: `/api/auth/login`
-  `GET` :  `/api/auth/logout`
- `GET`: `/api/me`

### API KEYS
- `GET`:`/api/keys`  : returns all existing api keys
- `POST`: `/api/keys` : creates an api key
- `PATCH`:`/api/keys/{kid}`: (For admin): for updating status     (grant , revoke) of all user (for customer): their own

### MCP servers
- `GET`:`/api/servers`	List registered MCP servers and their tools
- `POST`:`/api/servers`	Register a new MCP server
- `PUT`:`/api/servers/{sid}`	Update an MCP server
- `DELETE`:`/api/servers/{sid}`	Delete an MCP server

### WORKSPACE

`GET`:`/api/users`	List all workspace users (admin only)
`GET`:`/api/audit`	Retrieve audit logs
`GET`:`/api/overview`	Get usage metrics and daily tool-call count

### TOOLCALL and MCP CLIENT

`POST`:`/mcp`	            MCP JSON-RPC endpoint for clients
`POST`:`/api/tools/call`	Execute a tool through the gateway

### LLM configuration and chat

`GET`:`/api/settings/llm`	Get public LLM configuration
`GET`:`/api/settings/llm/models`List available OpenRouter models
`PUT`:`/api/settings/llm`	Configure OpenRouter model and API key

`POST`:	`/api/settings/llm/test`Test API key, model connection, and tool calling

`POST`:`/api/chat`Chat with the AI agent and execute tools


## architectural decisions


| Decision | Why I chose it | Tradeoff |
|---|---|---|
| **FastAPI backend with a frontend served from the same application** | One process serves the UI and APIs, making local setup simple and avoiding separate frontend deployment and CORS configuration. | The plain JavaScript frontend becomes harder to maintain as the UI grows. A component framework would help at a larger scale. |
| **SQLite for persistence** | Users, sessions, keys, server configuration, and audit records persist without running another database service. | Concurrent writes and deployment across multiple application instances are more limited than with PostgreSQL. |
| **Server-side sessions for dashboard login; bearer API keys for MCP clients** | Browser sessions can be invalidated immediately on logout. API keys work independently for external clients, with approval, suspension, and revocation. | Authentication requires database lookups. Keys currently grant access to all enabled tools, rather than supporting per-tool scopes or expiry. |
| **One gateway aggregating multiple MCP servers** | Clients configure one endpoint. The gateway centralizes authentication, upstream credentials, routing, and auditing. | The gateway becomes a dependency for every client connection and adds some latency. |
| **Tool names prefixed with a stable server ID** | Names such as `s1__get_order` prevent collisions when different servers expose identically named tools. Renaming a server preserves its tool names. | Names are less readable, and deleting/recreating a server changes its namespace. |
| **Concurrent discovery with independent upstream timeouts** | A slow or unavailable server cannot block every other server indefinitely. Calls have bounded execution time and clear errors. | Tool listing can still wait for the slowest timeout. Opening connections for individual operations adds overhead; connection reuse and caching would improve performance. |
| **Hashed passwords/tokens and encrypted upstream credentials** | Passwords and dashboard tokens do not need to be recoverable. Upstream credentials must be recoverable so the gateway can forward them, so they are encrypted. | The encryption key lives on the same host. This protects against a database-only leak, but not someone who can read both the database and encryption key. |
| **OpenRouter-only chat with a bounded tool loop** | A single provider integration simplifies configuration. Chat uses the same gateway execution function as external clients, preserving user identity and audit attribution. | It depends on an external provider’s availability, pricing, limits, and model behavior. Limits on steps, calls, and time control cost but can interrupt longer tasks. |
| **Audit metadata without storing prompts, arguments, or results** | Records identify who called which tool, whether it succeeded, and how long it took, while reducing sensitive data retention. | Debugging is harder because the exact failing arguments cannot be reconstructed from the audit log alone. |


Two additional points:
- Testing: “I tested writes against a disposable fake WooCommerce store and verified live reads through MCP. This avoids changing real store records during automated tests, but fixtures cannot reproduce every WordPress plugin or production configuration.”
- MCP scope: “I implemented stateless Streamable HTTP tool operations. That keeps the gateway simpler, but resources, prompts, server push, and OAuth are outside the current implementation.”


# Things i can improve:

- For shared production use, I would prioritize per-tool authorization, HTTPS deployment, managed secret storage, and a shared rate limiter. I would move to PostgreSQL and add connection pooling or discovery caching when concurrency measurements justify them.
