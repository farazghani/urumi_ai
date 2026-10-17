# WooCommerce MCP server

From this directory, install dependencies and run the HTTP server:

```sh
uv sync --locked
uv run python service.py
```

Set these environment variables (or put them in a local `.env` file):

- `WOO_BASE_URL`: store root URL, e.g. `http://localhost:8080` for the local Docker store.
- `WOO_CONSUMER_KEY` and `WOO_CONSUMER_SECRET`: WooCommerce REST API credentials.
- `MCP_AUTH_TOKEN`: bearer token required by the MCP endpoint.
- `MCP_HOST` and `MCP_PORT`: optional bind address and port (defaults: `0.0.0.0:8000`).

The endpoint is `/mcp`; `/health` is open for health checks. Without
`MCP_AUTH_TOKEN`, the server starts without bearer authentication.
Use HTTPS for remote stores. For the local WordPress Docker store only, loopback
HTTP URLs send `X-Forwarded-Proto: https` so WordPress accepts Basic authentication;
the local connection itself still uses HTTP.

Run the end-to-end check against a fake store (no real store or credentials needed):

```sh
uv run python test_service.py
```

The check starts temporary servers on loopback ports and tests authentication,
tool schemas, all five tools, validation, missing orders, and a store outage.
This is a standalone async test script, not a pytest test module.

## Docker Compose

Keep WooCommerce credentials and `MCP_AUTH_TOKEN` in `mcp/.env` (see
`.env.example`). Existing `WC_CONSUMER_KEY` and `WC_CONSUMER_SECRET` names are
also supported. Keep the MySQL settings in `woocommerce_app/.env`.

From `woocommerce_app`, start the whole stack:

```sh
docker compose up --build -d
docker compose ps
docker compose logs -f mcp
```

MCP starts automatically alongside WordPress and MySQL. Connect to
`http://localhost:8000/mcp` using the bearer token from `mcp/.env`.
Check startup with `curl http://localhost:8000/health`.
Set `MCP_PORT` in `woocommerce_app/.env` to change the host port.

Inside Compose, the store URL is `http://wordpress`. Compose explicitly enables
`WOO_FORWARDED_PROTO=https` for Basic authentication on the private Docker network.
The MCP health check reports server availability; WooCommerce must still be
installed and its API credentials valid before store tools can succeed.
Credentials are supplied at runtime and excluded from the Docker build context.
