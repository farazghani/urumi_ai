# Dashboard containers

The frontend and backend are separate images:

| Service | Image / process | Network access |
| --- | --- | --- |
| `frontend` | Nginx serving HTML, CSS, JavaScript | Published at `127.0.0.1:8001` by default |
| `backend` | FastAPI, authentication, MCP gateway, OpenRouter | Private `backend:8001`; no host port |
| `db` | `mariadb:11.4` | Private `db:3306`; separate from WooCommerce |
| `clock` | Clock MCP, using the backend image | Private `clock:8002` |
| `calculator` | Calculator MCP, using the backend image | Private `calculator:8003` |

Nginx forwards `/api/`, `/mcp`, and `/health` to the backend. The browser and MCP clients use one public URL, so session cookies and same-origin checks still work. MCP responses are not buffered, and proxy timeouts allow the existing 120-second chat budget. The frontend image contains no Python code, database, or secrets. The backend image contains no frontend assets. Application processes run as non-root users.

## Build and run a fresh Docker workspace

From the repository root, copy `Dashboard/.env.example` to `Dashboard/.env` and replace both database password placeholders with different strong passwords. An existing configured `.env` can be reused.

```sh
docker compose -f Dashboard/docker-compose.yml up -d --build
```

Open **http://127.0.0.1:8001**. External MCP clients use **http://127.0.0.1:8001/mcp**.

Stop the local Python launcher with Ctrl-C first if it already occupies port 8001. To run Docker alongside it on another port:

```sh
DASHBOARD_PORT=8081 docker compose -f Dashboard/docker-compose.yml up -d --build
```

For configuration, copy `Dashboard/.env.example` to `Dashboard/.env` and edit it locally. Compose reads that file automatically. Credentials are supplied at runtime and excluded from both build contexts.

Docker stores the MariaDB database in `mariadb_data`, and its encryption key and bootstrap login in `dashboard_data`. Back up both together. MariaDB uses a dedicated non-root application account; its database port is private. WooCommerce keeps its existing MySQL image, credentials, and volumes. A fresh volume creates a fresh admin account and new application state. Existing local users/API keys are retained only if you import the local workspace before the first Docker startup.

Read the generated admin login without putting it in image layers or logs:

```sh
docker compose -f Dashboard/docker-compose.yml exec backend cat /data/bootstrap-admin.txt
```

If `ADMIN_PASSWORD` was supplied before the first startup, use that password instead; a bootstrap file is not generated. Changing `ADMIN_PASSWORD` does not reset an existing account.

## Retain existing local users, API keys and settings

Import **before** starting the Docker backend for the first time:

```sh
docker compose -f Dashboard/docker-compose.yml build
python3 Dashboard/import-local-data.py
docker compose -f Dashboard/docker-compose.yml up -d
```

The importer starts MariaDB, takes a consistent SQLite snapshot including committed WAL transactions, converts only that staged copy to a self-contained file for its read-only Docker mount, and transfers rows in a transaction into an **empty** MariaDB database. It retains IDs, password/token hashes, timestamps, encrypted secrets, and the matching encryption key and bootstrap login. It refuses an existing database or a different destination encryption key. Stop the backend before importing. Local data remains unchanged; temporary files are owner-readable and removed after import. The application subsequently runs as UID/GID 10001.

Default upstream loopback URLs are rewritten in the imported copy to their Docker equivalents. Custom URLs remain unchanged. Existing encrypted upstream credentials and OpenRouter keys remain usable. The source SQLite and MariaDB databases are separate copies; future changes are not synchronized.

For another source directory or Compose project:

```sh
python3 Dashboard/import-local-data.py --source /absolute/path/to/data --project-name my-dashboard
```

Use that same project name for subsequent Compose commands. Do not use `down -v` if you want to retain Docker users, API keys, configuration, or the encryption key.

## WooCommerce connection

WooCommerce stays in its existing separate Compose stack:

```sh
docker compose -f woocommerce_app/docker-compose.yml up -d
```

On Docker Desktop, the dashboard defaults to `http://host.docker.internal:8000/mcp`, reaching the existing host-published WooCommerce MCP service. Clock and Calculator use Docker service names instead of localhost. Upstream availability is independent: the dashboard can start while a store or helper is unavailable.

For another host or Linux setup where the store's loopback-only port is not reachable from a container, set `WOO_MCP_URL` to an address reachable from the backend container, or connect the backend and WooCommerce to a shared Docker network and use its service DNS name. Keep direct upstream endpoints private. `WOO_MCP_URL` seeds new databases; for an existing database, edit the server URL in **MCP servers**.

## Operations

```sh
# Status and logs
docker compose -f Dashboard/docker-compose.yml ps
docker compose -f Dashboard/docker-compose.yml logs -f frontend backend

# Rebuild only the frontend
docker compose -f Dashboard/docker-compose.yml up -d --no-deps --build frontend

# Rebuild the backend and helpers
docker compose -f Dashboard/docker-compose.yml up -d --no-deps --build backend clock calculator

# Stop containers and retain both data volumes
docker compose -f Dashboard/docker-compose.yml down
```

`/frontend-health` checks Nginx; `/health` checks the proxied backend and its database connection. Backend, database, and helper ports are not published in the default Compose setup. The backend trusts proxy headers because only the private Docker network can reach it; Nginx overwrites forwarded host/protocol/client-IP headers. The supplied setup serves local HTTP. Add TLS termination and set `COOKIE_SECURE=1` when deploying the frontend over HTTPS; configure forwarded protocol handling for that TLS proxy.

For a previous Docker deployment whose SQLite database is already in `dashboard_data`, stop the application, start MariaDB, and import directly from that volume before restarting:

```sh
docker compose -f Dashboard/docker-compose.yml stop frontend backend
docker compose -f Dashboard/docker-compose.yml up -d --wait db
docker compose -f Dashboard/docker-compose.yml run --rm --no-deps --user 0:0 backend python -m backend.import_data /data
docker compose -f Dashboard/docker-compose.yml up -d
```

Build the updated images first. The SQLite file is retained as a legacy backup; the backend reads and writes only MariaDB.

## Local Python development

Expose only the development database on loopback and export the same credentials used by Compose. Run these commands from the repository root:

```sh
set -a
. Dashboard/.env
set +a
docker compose -f Dashboard/docker-compose.yml -f Dashboard/docker-compose.dev.yml up -d --wait db
export MYSQL_HOST=127.0.0.1 MYSQL_PORT=3307
cd Dashboard/backend
uv sync --locked
uv run python ../run.py
```

A pre-existing local SQLite workspace must be imported first using the instructions above. After importing it into Docker, keep the original local `.data/encryption.key` when using the same MariaDB from local Python. A database imported from another workspace needs that workspace’s original encryption key in `DASHBOARD_DATA`. Never use a new encryption key with existing database records. Local and Docker processes share the same database when configured with the same connection credentials.

Containers set `SERVE_FRONTEND=0` to run the backend as an API-only service; local Python serves the frontend from FastAPI.

## Verify the containers

After building the images, run the smoke test from the repository root:

```sh
Dashboard/backend/.venv/bin/python Dashboard/tests/docker_smoke.py
```

It uses a unique Compose project, a random loopback port, and disposable fixture data. It verifies full rollback of a failed import, refusal on a nonempty destination, preserved upstream namespace IDs, encrypted credential reuse, frontend assets, proxied login/origin handling, authenticated MCP calls, non-root API-only operation, and persistence after MariaDB and backend recreation. Backend integration tests also launch a disposable `mariadb:11.4` instance and require Docker. It removes only its own test containers and volume afterward and never calls a real model or changes store records.
