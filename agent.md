you are a senior backend engineer + frontend too:

you have been tasked to build woocommerce_mcp server + dashboard
for now things that have been done yet

docker image of wordpress + woocommerce with custom mcp server running on localhost:8080/mcp with no auth feature yet
(for more clear picture can visit woocommerce_app/mcp/server.py)

OUR NEXT TASK is build the dashboard:

the dashboard has following features:
AUTH
- 1 login ,signup,logout  as `customer` user and `admin`
- 2 one build in user as `admin`


API KEYS

- 1 as a user one can generate  api key and revoke too
    to directly connect with the external parties such as codex , claudecode etc via custom mcp using streammable http

-  2 as an admin i can see all the users and grant , revoke access to api keys created by users


Gateway
- Admins can add, edit, disable and remove upstream MCP servers that speak Streamable HTTP, each with an optional static credential (header or bearer token).
- The gateway exposes one MCP endpoint. Listing tools returns the tools of every enabled server, and calling a tool is routed to the right server.
- Two servers may expose a tool with the same name. Handle this with a scheme you document.
- A slow or dead upstream must not break tool listing or calls to other servers. Calls have timeouts and return a clear error.
- Upstream credentials stay on the server side. They are never sent to the browser or to an MCP client.
- You must connect at least three upstream servers: your WooCommerce server, and two others of your choice.



Chat agent

- A chat in the dashboard that uses an LLM to call tools through the gateway and shows which tools it called.
- It acts as the logged-in user, so its calls appear in the audit log under that user.
- The model, provider and design are your choice. A free tier or a local model is fine. We grade that the tool loop works, not the quality of the model's answers.
- LLM API keys follow the same rule as every other secret.
