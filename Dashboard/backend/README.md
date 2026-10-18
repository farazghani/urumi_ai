# Urumi dashboard backend

See the [project README](../../README.md) for setup, configuration, architecture, and MCP client connections.

Configure and start MariaDB first using the [Docker/local development guide](../README.md). Export its `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_DATABASE`, `MYSQL_USER`, and `MYSQL_PASSWORD`.

```sh
uv sync --locked
uv run python ../run.py
```

This starts the dashboard on `http://127.0.0.1:8001` and Clock/Calculator MCP upstreams on ports 8002/8003. Run the existing WooCommerce stack separately.

```sh
uv run pytest -q
```

Tests launch temporary HTTP MCP upstreams, connect with the real MCP SDK, and exercise an OpenAI-compatible model fixture. They use an isolated temporary `mariadb:11.4` Docker container (Docker must be running) and never call a live model or store.
