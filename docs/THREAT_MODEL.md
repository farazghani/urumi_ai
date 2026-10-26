# Threat model

The original upstreams form a shared workspace. Provisioned stores add owner/admin
authorization and separate namespaces, databases, Secrets, volumes and network policies. A dashboard member has
role `customer`, which is separate from a WooCommerce shopper. Protect store data,
write authority, member identities, provider credits, credentials and availability.
Cluster operators can read Secrets/volumes and are trusted. Dashboard admins are
trusted to configure destinations and shared access.

## Shared upstream credentials: authority without seeing the secret

The gateway uses hidden credentials on a member's behalf. With the current
read/write WooCommerce integration, members can read exposed order/customer data,
create products, change prices/descriptions/publication status and update existing
order statuses. Changes affect everyone's shared store. Hiding the secret does
not reduce delegated authority. Built-in tools do not create orders or delete products.

All enabled **shared** tools are available through Tool Explorer/chat to logged-in
members; approved API keys grant the same shared access plus the owner's provisioned stores. **API-key approval does
not restrict browser tool access.** Signup currently creates a working member
session, so new registrants can reach shared tools. Public exposure requires an
access gate or implemented invitations/member approval. Per-tool scopes are not implemented. Provisioned store ownership **is** enforced
in discovery and execution; guessing an upstream ID or tool name cannot bypass it.

## Why client tokens are not passed through

A dashboard `urm_...` token identifies its owner to the gateway, which checks its
hash and status. Outbound MCP headers use only the selected server's configured
credential, never the incoming Authorization header. WooCommerce MCP separately
uses the store's `ck_...` / `cs_...` REST credentials.

Passing dashboard tokens upstream would let a hostile server replay them as that
member. Separate credentials permit independent rotation and avoid requiring
upstreams to understand dashboard identities. Gateway audits retain the member's
identity; the store sees the shared integration user.

## Untrusted upstreams and model manipulation

A hostile tool description could claim that changing product prices is required
before answering a question. Schemas, tool results and store content/customer
notes can carry similar prompt injection. Descriptions and results reach the
model; upstream tools can change after registration.

Current controls include an instruction treating tool output as untrusted,
allowlisting discovered names for each chat request, input validation, timeouts
and loop limits. UI strings are HTML-escaped. **These do not enforce user intent:**
malicious descriptions can steer the model toward permitted writes. Read-only
annotations are hints; mutation confirmation is prompt-based. An upstream can
also leak its own credential in a result.

Admins should review upstream ownership/destinations and tool changes. Stronger
controls would pin/review definitions, separate read/write permissions, enforce
approval and restrict shared upstream egress; these additional controls are not implemented. A hostile admin could
redirect an upstream while preserving its credential and cause it to reach an
attacker. Dashboard admins are not isolated from shared integration authority.

## What one team member can do to another

| Action | Boundary or remaining risk |
| --- | --- |
| Read another's raw dashboard token/password or private audit view | Hashing, one-time key display and owner/admin checks prevent ordinary member access. Members cannot approve others' keys or edit upstream/provider settings. |
| Read shared orders/customer identities or overwrite another's product changes | Allowed through the original shared tools; provisioned stores require owner/admin access. No per-tool scopes. |
| List, call tools against or delete another member's provisioned store | Owner/admin checks reject ordinary members; service DNS/direct Pod-IP isolation blocks cross-store MCP access to WordPress/MySQL. |
| Manipulate another's chat through edited product content | Possible stored prompt injection when the other member's model reads it; HTML escaping does not prevent this. |
| Consume shared capacity/provider credits | Per-request limits exist, but no global tool/chat quota or concurrency limit prevents repeated requests. |
| Impersonate another in audits | Caller identity comes from authentication, not supplied tool arguments. A stolen/shared session or key grants its owner's identity. |

Revoking a key blocks later external requests, not browser sessions or in-flight
writes. Full member suspension/session revocation is not implemented. Audit
metadata supports attribution but is neither immutable nor an undo mechanism.

Use public HTTPS/secure cookies and private internal Services. The chart restricts
ingress on the original workloads; new stores additionally deny cross-store
ingress/egress. Admin-configured upstream URLs still permit private addresses/HTTP.
Private MCP/REST/database hops are not encrypted by this setup. Restrict Kubernetes
RBAC and enable Secret encryption at rest. MariaDB plus its Fernet-key Secret can
decrypt saved credentials; protect both and their backups. OpenRouter receives
chat messages/tool results, so customer data is not confined to the cluster.
See [production operations](../infra/README.md#vps-deployment).


The provisioning controller can create namespaces, Secrets and workloads across
namespaces and delete namespaces. Its explicit ClusterRole is narrower than
cluster-admin, but compromise still carries significant cluster-wide authority.
Resource ownership labels prevent accidental mutation, not a malicious controller.
Store pods/backend have no Kubernetes token; only the controller mounts one. Each
store's MCP token and Woo credentials stay in its Secret and encrypted gateway
registration. Registry pull credentials are copied to the store namespace when
needed. Cluster admins remain trusted. ResourceQuota/LimitRange, admission limits
and persisted provisioning deadlines bound store creation; new accounts can still
multiply per-user limits without an invitation/admission system. Local-path PVC
sizes do not impose physical disk quotas. Cleanup waits for storage deletion;
operators must repair stuck finalizers/drivers. See [provisioning](PROVISIONING.md).
