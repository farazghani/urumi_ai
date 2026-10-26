# System design and tradeoffs

## Architecture choices

One Helm release runs eight workloads: WordPress/WooCommerce, store MySQL,
dashboard MariaDB, FastAPI backend/gateway/chat, dashboard Nginx, WooCommerce MCP,
Clock MCP and Calculator MCP. A ninth workload, the provisioning controller,
reconciles per-store namespaces from durable dashboard MariaDB intents.

```text
Browser / MCP client -> Ingress -> Nginx -> FastAPI gateway
                                          |-> dashboard MariaDB
                                          |-> WooCommerce MCP -> WordPress -> store MySQL
                                          |-> Clock / Calculator MCP
                                          |-> OpenRouter (chat only)
Shopper --------------> Ingress ----------> WordPress
```

| Choice | Benefit | Tradeoff |
| --- | --- | --- |
| Separate Nginx/JavaScript frontend and FastAPI backend, one public origin | Independent images; simple session cookies and origin checks; no Node build | Plain JavaScript becomes harder to maintain as the UI grows |
| Separate store MySQL and dashboard MariaDB | Store/customer data and gateway identities have independent schemas/lifecycles | Extra memory and two databases to back up |
| WooCommerce REST for tools; WP-CLI for bootstrap | Structured runtime requests; repeatable site/API-key setup | Depends on store configuration and REST availability |
| FastMCP upstreams with an explicit gateway | Reuses MCP tooling while centralizing member authentication, routing and audits | Gateway/database are shared dependencies; connection setup adds latency |
| Single backend/WordPress replicas | Simple initialization and writable storage on one VPS | Recreate rollouts interrupt service; no automatic failover |

Dashboard members and WooCommerce customers are different accounts. The external
gateway implements stateless initialize, ping, tool listing and calls; persistent
MCP sessions, resources, subscriptions and OAuth are outside its scope.

## Aggregation, routing and failure handling

1. Authenticate the caller using a browser session or approved external API key.
2. Discover enabled upstreams concurrently, each with an independent 1–30 second
   budget. Disabled/unavailable servers contribute no tools; listing can still
   wait for the slowest timeout.
3. Shared upstreams use `s<database-server-id>__<tool>`; provisioned stores use
   `store_<name>_<stable-id>__<tool>`. Discovery filters stores to owner/admin.
4. Resolve the prefix, enforce owner/admin access and enabled state, decrypt its credential,
   and open an independent authenticated MCP connection within that server's
   timeout. Incoming client tokens are never forwarded upstream.
5. Return tool content/structured results or MCP `isError` failures, avoiding raw
   connection exceptions. Audit the caller, tool, status, duration and timestamp.

Per-call connections simplify credential isolation but add handshake overhead;
connection reuse/discovery caching are future improvements. Timeouts do not undo
writes: retrying product creation can duplicate a committed request. Key revocation
or server disabling affects subsequent requests; in-flight calls can finish.

Chat sends discovered schemas/messages to OpenRouter and executes requested tools
through the same gateway under the logged-in member. Its model/tool loop permits
six steps, ten calls per step and 120 seconds; discovery precedes that budget.
Tool results return to the provider, and browser memory holds conversation history.
This adds an external availability/privacy dependency. Write confirmation is a
prompt instruction, not an enforced approval gate.

## What changes for production

The [same chart](../infra/helm/urumi/) uses local or production Helm values:

| Area | Local k3d | Production-like k3s VPS |
| --- | --- | --- |
| DNS | Hosts-file `.test` core names; `*.127.0.0.1.sslip.io` generated stores | Real core DNS plus wildcard store DNS point to the VPS |
| Ingress | Traefik, loopback ports, HTTP by default | Valid TLS/cert-manager issuer, `websecure`, secure cookies and correct proxy trust |
| Images | Build/import native images tagged `local` | Push matching architectures; use immutable references and registry pull Secrets |
| Storage class | Three core PVCs plus two per store | Set capacity/class; generated stores require Delete reclaim policy; no node/disk-loss failover with node-local storage |
| Secrets | Random pre-created Kubernetes Secrets | Controlled provisioning, restricted RBAC, encryption at rest and coordinated rotation |

Readiness controls routing; independent liveness/startup probes and resource
requests/limits cover long-running workloads. Core NetworkPolicies restrict
ingress; provisioned namespaces deny ingress/egress except their explicit paths. Back up both databases,
WordPress files and Secrets off-node, retaining the matching Fernet key. PVC
retention is not a backup, and Helm rollback cannot reverse data migrations.
Before broader use, add member admission, per-tool permissions, enforced write
approval, shared chat quotas, core egress restrictions and monitoring. New store
ownership is enforced; the original shared upstreams remain broadly accessible.

Provisioning uses Kubernetes API create-if-absent with ownership checks, persisted
phases/deadlines, per-store MariaDB locks and atomic MCP registration. A store gets
its own database, Secrets, PVCs, Ingress, MCP, ResourceQuota and LimitRange. Failure
and deletion unregister tools and wait for namespace/PV cleanup. Retries reuse the
store ID; process restarts preserve credentials and resume the intent. The tradeoff
is a trusted controller with explicit but cluster-wide creation/deletion authority,
and no continuous drift/upgrade/backup operator. See the
[provisioning lifecycle and limitations](PROVISIONING.md).
See [operations](../infra/README.md) and the [threat model](THREAT_MODEL.md).
