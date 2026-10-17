"""End-to-end test of service.py without a real store:

  FastMCP client --HTTP/Bearer--> service.py --WooClient--> fake WooCommerce (started here)

    uv run python test_service.py
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import socket
import subprocess
import sys
import threading
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from fastmcp import Client
from fastmcp.exceptions import ToolError

HERE = Path(__file__).resolve().parent
TOKEN = "gateway-secret"
results: list[bool] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f": {detail}"))


# ---------------------------------------------------------------- fake WooCommerce
PRODUCTS: dict[int, dict] = {}
ORDERS = {5: {"id": 5, "status": "processing", "total": "499", "currency": "INR",
              "date_created": "2026-10-16T10:00:00", "billing": {"first_name": "Asha", "last_name": "Rao"},
              "payment_method_title": "Cash on delivery", "shipping_total": "0", "date_modified": "x",
              "customer_note": "leave at door", "line_items": [{"name": "Sample T-Shirt", "quantity": 1, "total": "499"}]}}
seen_headers: list[dict] = []


class FakeWoo(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, code, body, extra=None):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers(); self.wfile.write(data)

    def _handle(self):
        # mimic WooCommerce: Basic key/secret is only accepted when the request looks like HTTPS
        auth = self.headers.get("Authorization", "")
        creds = base64.b64decode(auth[6:]).decode() if auth.startswith("Basic ") else ""
        if creds != "ck_test:cs_test" or self.headers.get("X-Forwarded-Proto") != "https":
            return self._send(401, {"code": "woocommerce_rest_cannot_view", "message": "Sorry, you cannot list resources."})
        seen_headers.append(dict(self.headers))
        url = urlparse(self.path); q = {k: v[0] for k, v in parse_qs(url.query).items()}
        p = url.path.replace("/wp-json/wc/v3", "")
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        pg = {"X-WP-Total": "0", "X-WP-TotalPages": "1"}
        if p == "/products" and self.command == "GET":
            items = [i for i in PRODUCTS.values() if q.get("search", "") in i["name"]]
            return self._send(200, items, {**pg, "X-WP-Total": str(len(items))})
        if p == "/products" and self.command == "POST":
            i = 100 + len(PRODUCTS); PRODUCTS[i] = {"id": i, "price": body["regular_price"], "permalink": f"http://shop/p/{i}",
                                                     "stock_status": "instock", **body}
            return self._send(201, PRODUCTS[i])
        if p == "/orders" and self.command == "GET":
            items = [o for o in ORDERS.values() if o["date_created"] > q.get("after", "")]
            return self._send(200, items, {**pg, "X-WP-Total": str(len(items))})
        if p.startswith("/orders/"):
            oid = int(p.rsplit("/", 1)[1])
            if oid not in ORDERS: return self._send(404, {"code": "woocommerce_rest_shop_order_invalid_id", "message": "Invalid ID."})
            if self.command == "PUT": ORDERS[oid]["status"] = body["status"]
            return self._send(200, ORDERS[oid])
        self._send(404, {"code": "no_route", "message": "No route"})

    do_GET = do_POST = do_PUT = _handle


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]


async def main() -> int:
    woo_port, mcp_port = free_port(), free_port()
    woo = ThreadingHTTPServer(("127.0.0.1", woo_port), FakeWoo)
    threading.Thread(target=woo.serve_forever, daemon=True).start()

    env = {**os.environ, "WOO_BASE_URL": f"http://127.0.0.1:{woo_port}", "WOO_CONSUMER_KEY": "ck_test",
           "WOO_CONSUMER_SECRET": "cs_test", "MCP_AUTH_TOKEN": TOKEN, "MCP_HOST": "127.0.0.1", "MCP_PORT": str(mcp_port)}
    # A file avoids blocking the child if its logs fill a pipe during the test.
    logs = tempfile.TemporaryFile()
    proc = subprocess.Popen([sys.executable, "service.py"], cwd=HERE, env=env, stdout=logs, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{mcp_port}"
    try:
        for _ in range(60):
            if proc.poll() is not None:
                logs.seek(0)
                print(logs.read().decode())
                return 1
            try:
                if httpx.get(f"{base}/health", timeout=1).status_code == 200: break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.5)
        else:
            logs.seek(0)
            print("Server did not become healthy within 30 seconds")
            print(logs.read().decode())
            return 1

        print("\nhttp + auth")
        check("/health open without token", httpx.get(f"{base}/health").text == "ok")
        r = httpx.post(f"{base}/mcp", json={})
        check("/mcp without token -> 401", r.status_code == 401, str(r.status_code))
        r = httpx.post(f"{base}/mcp", json={}, headers={"Authorization": "Bearer wrong"})
        check("/mcp with wrong token -> 401", r.status_code == 401, str(r.status_code))

        async with Client(f"{base}/mcp", auth=TOKEN) as c:
            print("\ntool listing")
            tools = {t.name: t for t in await c.list_tools()}
            check("exactly the 5 required tools", set(tools) == {"list_products", "create_product", "list_orders",
                                                                  "get_order", "update_order_status"}, str(set(tools)))
            enum = tools["update_order_status"].inputSchema["properties"]["status"]
            check("status exposed as enum in schema", "enum" in json.dumps(enum), str(enum))
            check("read tools marked read-only", tools["list_orders"].annotations.readOnlyHint is True)
            check("create_product not read-only", tools["create_product"].annotations.readOnlyHint is False)

            print("\ncalling tools")
            r = await c.call_tool("create_product", {"name": "Hat", "regular_price": 12.5})
            check("create_product", r.data["name"] == "Hat" and r.data["regular_price"] == "12.5", str(r.data))
            r = await c.call_tool("list_products", {"search": "Hat"})
            check("list_products finds it", r.data["items"][0]["name"] == "Hat", str(r.data))
            r = await c.call_tool("list_orders", {"after": "2026-10-16T00:00:00"})
            check("list_orders", r.data["items"][0]["customer"] == "Asha Rao", str(r.data))
            r = await c.call_tool("get_order", {"order_id": 5})
            check("get_order detailed", r.data["payment_method"] == "Cash on delivery", str(r.data))
            r = await c.call_tool("update_order_status", {"order_id": 5, "status": "on-hold"})
            check("update_order_status", r.data["status"] == "on-hold", str(r.data))
            check("store saw X-Forwarded-Proto + Basic auth", all(h.get("X-Forwarded-Proto") == "https" for h in seen_headers) and seen_headers)

            print("\nerrors")
            for name, args, expect in [
                ("get_order", {"order_id": 999}, "404"),
                ("get_order", {"order_id": 0}, "order_id"),                    # schema validation (ge=1)
                ("update_order_status", {"order_id": 5, "status": "bogus"}, "status"),
                ("list_orders", {"after": "yesterday"}, "iso 8601"),           # client validation via WooError
                ("create_product", {"name": "x", "regular_price": -1}, "regular_price"),
            ]:
                try:
                    await c.call_tool(name, args)
                    check(f"{name} {args}", False, "no error raised")
                except ToolError as e:
                    check(f"{name} {args} -> ToolError", expect.lower() in str(e).lower(), str(e))

        print("\nstore down")
        woo.shutdown(); woo.server_close()
        async with Client(f"{base}/mcp", auth=TOKEN) as c:
            try:
                await c.call_tool("list_products", {})
                check("dead store -> ToolError", False, "no error")
            except ToolError as e:
                check("dead store -> clear ToolError", "cannot reach" in str(e).lower(), str(e))
            check("server still healthy", httpx.get(f"{base}/health").status_code == 200)
    finally:
        proc.terminate()
        try: proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        logs.close()
        woo.shutdown()
        woo.server_close()

    print(f"\n{sum(results)} passed, {len(results) - sum(results)} failed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
