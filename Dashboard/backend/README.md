# Urumi dashboard backend

See the [project README](../../README.md) for setup, configuration, architecture, and MCP client connections.

```sh
uv sync --locked
uv run python ../run.py
```

This starts the dashboard on `http://127.0.0.1:8001` and Clock/Calculator MCP upstreams on ports 8002/8003. Run the existing WooCommerce stack separately.

```sh
uv run pytest -q
```

Tests launch temporary HTTP MCP upstreams, connect with the real MCP SDK, and exercise an OpenAI-compatible model fixture. They use isolated temporary SQLite databases and never call a live model or store.
