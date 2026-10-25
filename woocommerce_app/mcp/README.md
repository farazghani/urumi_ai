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
tool schemas, all nine tools, partial updates, validation, missing records, malformed store responses, and a store outage. All writes use the disposable fake store. The suite has 56 checks.
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

## Nine WooCommerce tools

| Tool | Purpose | Required arguments |
| --- | --- | --- |
| `list_products` | Search/filter products with pagination | None |
| `get_product` | Product details, prices, descriptions and stock | `product_id` |
| `create_product` | Create a simple product | `name`, `regular_price` |
| `update_product` | Change supplied fields on an existing product | `product_id`, at least one change |
| `list_orders` | Search/filter orders, newest first | None |
| `get_order` | Detailed order information | `order_id` |
| `update_order_status` | Change an order's status | `order_id`, `status` |
| `list_customers` | Search/filter registered customers with pagination | None |
| `get_customer` | Registered customer identity/account summary | `customer_id` |

Use IDs returned by the list tools; IDs are not guaranteed to start at 1. Customer tools operate on registered accounts, rather than guest checkout identities. They exclude passwords, arbitrary metadata and billing/shipping addresses.

`update_product` supports `name`, `regular_price`, `description`, `sku`, and `status`. Omitted/null fields are preserved. A zero price is valid; an empty description or SKU clears that field. A request with no changes returns a clear tool error. Names must contain a non-whitespace character and prices must be finite and non-negative.

Optional filters may be omitted or set to null. For example:

```json
{"search": null, "status": null, "page": 1, "per_page": 20}
```

Product status accepts `publish`, `draft`, `private`, or `pending`; an empty string is invalid. Order status accepts `pending`, `processing`, `on-hold`, `completed`, `cancelled`, `refunded`, or `failed`. Date filters use an ISO 8601 datetime, such as `2026-10-17T00:00:00`.

The MCP server converts store/network/validation failures into readable tool errors. Missing IDs return the WooCommerce 404 error. A healthy `/health` endpoint confirms the MCP process is running, not that every store operation or credential is valid.

## Deploy code changes and refresh clients

From the repository root, rebuild only this MCP service:

```sh
docker compose -f woocommerce_app/docker-compose.yml up -d --no-deps --build mcp
```

Refresh the dashboard server/tool listing and reconnect clients that cached the old tool schemas.

- Direct WooCommerce endpoint: `http://127.0.0.1:8000/mcp`, **9 tools**, authenticated with `MCP_AUTH_TOKEN` when configured.
- Dashboard gateway: `http://127.0.0.1:8001/mcp`, authenticated with an approved dashboard-generated API key. It exposes these 9 WooCommerce tools under `s<server-id>__<tool-name>`, plus tools from other enabled servers. With Clock and Calculator enabled, the gateway has **13 tools total**.

The WooCommerce REST consumer key/secret are different from either MCP token. The REST key needs write access for `create_product`, `update_product`, and `update_order_status`, and its owning WordPress user needs permission to access the corresponding resource.

REST endpoint references: [Products](https://developer.woocommerce.com/docs/apis/rest-api/v3/products/) and [Customers](https://developer.woocommerce.com/docs/apis/rest-api/v3/customers/).

### Order lookups

`get_order` requires the internal `id` returned by `list_orders`. An ID of 1 is only schema-valid; it does not mean order 1 exists. A missing order returns a 404 tool error that explains how to find a valid ID. The dashboard's order runner loads recent orders and uses a real ID for read-only lookups. Order status updates require an explicit selection. Empty stores never produce an invented ID, and another known ID can be entered manually in the JSON arguments.
