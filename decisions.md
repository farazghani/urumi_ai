# Assignment overview



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
