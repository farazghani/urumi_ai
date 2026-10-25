# Urumi · MCP gateway workspace

A local dashboard for managing MCP servers, user access, and an AI assistant. Built from `agent.md` with FastAPI, MariaDB, and a responsive JavaScript frontend served from the same origin. No Node build step is required.

## Kubernetes deployment

The whole stack runs on local k3d or a k3s VPS using the same Helm chart. See
[Kubernetes and Helm setup](infra/README.md) for local domains, production TLS,
Kubernetes Secrets, persistent storage, validation, backup and restore.

```sh
./infra/scripts/local-up.sh
```

## Docker deployment

The dashboard frontend (Nginx) and backend (FastAPI) have separate Dockerfiles and containers. Its database uses `mariadb:11.4`, separate from WooCommerce’s MySQL. Configure database passwords in `Dashboard/.env` using the example first:

```sh
docker compose -f Dashboard/docker-compose.yml up -d --build
```

Open `http://127.0.0.1:8001`. Docker uses separate persistent database and encryption-key volumes with a fresh workspace by default. To retain existing local users/API keys, import the local database and encryption key **before the first startup**. See [Dashboard Docker setup](Dashboard/README.md) for the import command, configuration, and separate rebuild commands.

## Start

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

One administrator is created on the first startup. Its email defaults to `admin@urumi.local`; its random password is written to `Dashboard/backend/.data/bootstrap-admin.txt` with owner-only permissions. Alternatively, export `ADMIN_EMAIL` and `ADMIN_PASSWORD` before the first startup. These values bootstrap a new database, rather than resetting an existing account.

Anyone signing up through the UI receives the **customer** role. Administrators see all users, all API keys, and all audit records. Customers see their own keys and audit records and may use the shared gateway tools through the dashboard.

- Generate a named API key under **API keys**. The token is shown only once.
- Customer keys begin **pending**. An admin must approve them.
- Admins can approve or suspend keys; suspension can be reversed.
- Owners and admins can revoke keys. Revocation is permanent and takes effect on the next gateway request.
- Customers cannot edit upstreams, AI settings, approve keys, or access other users' keys or audit records.

An API key grants access to **all tools on all enabled upstreams**. Per-server scopes are not implemented.

## Connect an MCP client

Add this to a client that accepts `mcpServers` configuration, replacing the token:

```json
{
  "mcpServers": {
    "urumi": {
      "url": "http://127.0.0.1:8001/mcp",
      "headers": { "Authorization": "Bearer YOUR_APPROVED_API_KEY" }
    }
  }
}
```

The dashboard includes the endpoint and a connection guide. For clients with another config format, use the same URL, Streamable HTTP transport, and bearer header.

### Codex configuration

Codex uses TOML in `~/.codex/config.toml`, rather than the JSON example above:

```toml
[mcp_servers.woocommerce]
enabled = true
url = "http://127.0.0.1:8001/mcp"
http_headers = { Authorization = "Bearer YOUR_APPROVED_API_KEY" }
```

Use the complete generated key, not its displayed prefix. Alternatively, `bearer_token_env_var = "URUMI_API_KEY"` reads a token from an environment variable named `URUMI_API_KEY`; **do not put the actual key in that setting**. The variable must be available to the running Codex process. Static headers avoid dependence on terminal environment variables when using the desktop app. Keep the config file private because static headers contain the credential.

Keep `uv run python ../run.py` running in a terminal while connecting, and reload the Codex MCP server or restart Codex after changing its configuration. This gateway uses bearer API keys, so `codex mcp login` (OAuth) is not its authentication flow. See [official OpenAI MCP configuration documentation](https://developers.openai.com/codex/mcp).


The gateway implements stateless Streamable HTTP with JSON responses to `initialize`, `ping`, `tools/list`, and `tools/call`. Notifications return HTTP 202. GET streaming and DELETE sessions are not supported (HTTP 405); clients reconnect without a persistent gateway session. Resources, prompts, subscriptions, and OAuth are outside this implementation.

### Namespaces and upstream failures

Every tool is named `s<database-server-id>__<original-name>`. For example, `s2__info` and `s3__info` coexist even though both servers define `info`. Server renames preserve tool identifiers. Delete-and-recreate allocates a new namespace.

Discovery checks upstreams concurrently, with an independent 1–30 second budget per server. Disabled and unavailable upstreams contribute no tools. Calls open independent authenticated connections to the selected upstream, bounded by its timeout. Errors are returned as MCP `isError` content without upstream exception details. Already-running calls are allowed to finish when a key or server is disabled.

**Tool explorer** lets you inspect schemas and make real calls. Order lookup forms load recent orders instead of guessing ID 1. Select an order or use an existing internal ID from `list_orders`; storefront order numbers can differ. Store mutations, such as creating products, affect the configured store. Audit records capture actor, action, tool name, result status, duration, and UTC timestamp; they deliberately omit credentials, prompts, tool arguments, and results.

## Chat with OpenRouter tools

OpenRouter is the only chat provider. In **Agent playground → Connect OpenRouter**, choose a model from the searchable catalog and enter your OpenRouter API key. The same form is available under **OpenRouter settings** for administrators. Customers use the workspace connection and cannot edit its credentials.

The catalog includes models that advertise both tool and tool-choice support, with free models labeled. An API key is required even for free models. Saving validates model availability and checks the API key through OpenRouter's account endpoint without generating a completion. **Test connection** sends a small completion request with a harmless test tool to confirm the selected model can request tools; this uses the model's normal pricing and limits. It does not execute gateway tools or save changes.

The endpoint is fixed server-side to `https://openrouter.ai/api/v1/chat/completions`; there is no base URL input. Previously saved OpenRouter settings are recognized even if the full completion URL was entered. Keys from other providers are not reused. Saved model keys stay encrypted and are never returned to the browser. Empty key input preserves an existing OpenRouter key; the explicit removal checkbox clears it.

For environment configuration, export `OPENROUTER_MODEL` and `OPENROUTER_API_KEY` before starting. Saved OpenRouter settings take precedence over these defaults. See [`.env.example`](Dashboard/backend/.env.example). No local model or other provider is supported.

The assistant discovers enabled tools, sends their schemas to OpenRouter, executes calls through the same gateway routing function as external clients, and feeds results back to the model. Tool schemas accompany every model step, and OpenRouter routing requires support for the supplied parameters. Opaque reasoning metadata is preserved for model tool continuations. The UI displays each call, arguments, result, and failure. Calls use the logged-in user's identity and are audited as `chat.tool`.

Each chat request is bounded to six model steps, ten calls per step, and 120 seconds total, with up to 4096 completion tokens per model step. Provider failures show actionable messages for invalid keys, insufficient credits, unavailable models, rate limits, and network errors. An OpenRouter authentication failure does not sign the user out of the dashboard. Provider error bodies and credentials are never exposed.

The assistant's confirmation behavior for mutations is prompt-based, not an enforced approval system. Conversations stay in browser memory and reset on reload/logout. See OpenRouter's [tool-calling guide](https://openrouter.ai/docs/guides/features/tool-calling) and [error reference](https://openrouter.ai/docs/api/reference/errors-and-debugging) for provider behavior.

## Security and persistence

- Passwords use salted scrypt. API and session tokens use SHA-256 hashes; tokens contain cryptographically random entropy.
- Sessions expire after 24 hours and use HttpOnly, SameSite=Strict cookies. Logout invalidates the session server-side.
- Sign-in/sign-up have an in-process IP throttle. Cross-origin writes are rejected; CSP restricts scripts to same-origin assets. User and upstream strings are HTML-escaped in the UI.
- Upstream credentials and saved model keys are encrypted with Fernet. Responses expose only whether a credential exists. Empty form inputs preserve credentials; explicit clear controls remove them.
- MariaDB stores dashboard records; `.data/` (or Docker’s `dashboard_data`) holds the encryption key and bootstrap login. The database uses the separate `mariadb_data` volume. It is excluded from Git. Back up the database **and encryption.key together**. Losing the key makes saved credentials unreadable. Anyone who can read both can decrypt them.
- Upstream configuration is administrator-only and allows loopback/private addresses because this gateway is designed to connect local services. Treat administrators as trusted network operators.

This is a local development application bound to loopback. For shared deployment, put it behind HTTPS, set `COOKIE_SECURE=1`, configure proxy/origin handling, restrict upstream egress, protect the existing upstream ports from direct access, and use managed secrets/storage and a shared rate limiter. No password recovery, multi-factor login, per-tool authorization, or user invitation system is included.

## Verify

```sh
cd Dashboard/backend
uv run pytest -q
```

Twenty integration cases cover authentication, API key permissions, MCP routing and failure isolation, encrypted credentials, a real MCP SDK connection, the OpenRouter tool loop, transaction rollback, foreign-key enforcement, and API date formatting. OpenRouter cases cover the fixed endpoint, supported-model filtering, save/key validation, credential preservation/removal, legacy settings, safe connection testing, and provider errors including errors inside HTTP 200 responses. Tests use a disposable MariaDB Docker container and local fixture servers; no real store mutations or model billing occur.

JavaScript syntax check from the repository root:

```sh
node --check Dashboard/frontend/app.js
node --test Dashboard/frontend/tests/tool-inputs.test.cjs
```

## Layout

- `Dashboard/frontend/` — responsive overview, servers, tool explorer, keys, chat, audit, users, and AI settings.
- `Dashboard/backend/src/backend/app.py` — authentication, dashboard API, MCP endpoint, and model loop.
- `Dashboard/backend/src/backend/openrouter.py` — fixed OpenRouter transport, model catalog, key checks, and safe provider errors.
- `Dashboard/backend/src/backend/gateway.py` — Streamable HTTP discovery, namespacing, routing, and timeouts.
- `Dashboard/backend/src/backend/store.py` — MariaDB, hashing, encryption, and first-run setup.
- `Dashboard/backend/src/backend/upstreams.py` — Clock and Calculator MCP servers.
- `Dashboard/run.py` — local process launcher and shutdown handling.
- `woocommerce_app/` — existing WordPress/WooCommerce stack, preserved.
