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
from woo_client import WooClient, WooError

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
CUSTOMERS = {
    7: {"id": 7, "email": "asha@example.test", "first_name": "Asha", "last_name": "Rao", "username": "asha", "date_created": "2026-10-16T10:00:00", "is_paying_customer": True, "password": "must-not-leak", "billing": {"address_1": "private"}, "meta_data": [{"key": "secret", "value": "must-not-leak"}]},
    8: {"id": 8, "email": "sam@example.test", "first_name": "Sam", "last_name": "Lee", "username": "sam", "date_created": "2026-10-17T10:00:00", "is_paying_customer": False},
}
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
        if p.startswith("/products/"):
            pid = int(p.rsplit("/", 1)[1])
            if pid not in PRODUCTS:
                return self._send(404, {"code": "woocommerce_rest_product_invalid_id", "message": "Invalid product ID."})
            if self.command == "PUT":
                PRODUCTS[pid].update(body)
                if "regular_price" in body:
                    PRODUCTS[pid]["price"] = body["regular_price"]
            return self._send(200, PRODUCTS[pid])
        if p == "/customers" and self.command == "GET":
            items = [c for c in CUSTOMERS.values() if (not q.get("email") or c["email"] == q["email"])
                     and q.get("search", "").lower() in (c["first_name"] + " " + c["last_name"]).lower()]
            page, per_page = int(q.get("page", 1)), int(q.get("per_page", 20))
            total = len(items)
            return self._send(200, items[(page - 1) * per_page:page * per_page],
                              {"X-WP-Total": str(total), "X-WP-TotalPages": str((total + per_page - 1) // per_page)})
        if p.startswith("/customers/"):
            cid = int(p.rsplit("/", 1)[1])
            if cid not in CUSTOMERS:
                return self._send(404, {"code": "woocommerce_rest_invalid_id", "message": "Invalid customer ID."})
            return self._send(200, CUSTOMERS[cid])
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
            check("exactly 9 WooCommerce tools", set(tools) == {"list_products", "create_product", "get_product", "update_product",
                                                                  "list_orders", "get_order", "update_order_status", "list_customers", "get_customer"}, str(set(tools)))
            enum = tools["update_order_status"].inputSchema["properties"]["status"]
            check("status exposed as enum in schema", "enum" in json.dumps(enum), str(enum))
            check("read tools marked read-only", tools["list_orders"].annotations.readOnlyHint is True)
            check("create_product not read-only", tools["create_product"].annotations.readOnlyHint is False)
            check("new read tools marked read-only", all(tools[n].annotations.readOnlyHint is True for n in ("get_product", "list_customers", "get_customer")))
            check("update_product marked idempotent write", tools["update_product"].annotations.readOnlyHint is False and tools["update_product"].annotations.idempotentHint is True)
            check("update_product only requires product_id", tools["update_product"].inputSchema.get("required") == ["product_id"])

            print("\ncalling tools")
            r = await c.call_tool("create_product", {"name": "Hat", "regular_price": 12.5})
            check("create_product", r.data["name"] == "Hat" and r.data["regular_price"] == "12.5", str(r.data))
            r = await c.call_tool("list_products", {"search": "Hat"})
            check("list_products finds it", r.data["items"][0]["name"] == "Hat", str(r.data))
            r = await c.call_tool("get_product", {"product_id": 100})
            check("get_product details", r.data["id"] == 100 and "description" in r.data)
            r = await c.call_tool("update_product", {"product_id": 100, "regular_price": 0, "description": ""})
            check("update_product accepts zero price and clearing description", r.data["regular_price"] == "0.0" and r.data["description"] == "")
            check("update_product preserves omitted name/SKU/status", r.data["name"] == "Hat" and r.data["status"] == "publish" and r.data["sku"] is None)
            r = await c.call_tool("update_product", {"product_id": 100, "name": "Updated Hat", "sku": "HAT-001", "status": "draft"})
            check("update_product changes requested fields", r.data["name"] == "Updated Hat" and r.data["sku"] == "HAT-001" and r.data["status"] == "draft")
            check("update_product preserves omitted price", r.data["regular_price"] == "0.0")
            r = await c.call_tool("update_product", {"product_id": 100, "name": None, "regular_price": None, "sku": ""})
            check("update_product null fields preserve values; empty SKU clears", r.data["name"] == "Updated Hat" and r.data["regular_price"] == "0.0" and r.data["sku"] == "")
            r = await c.call_tool("list_products", {"search": None, "status": None})
            check("list_products accepts optional null filters", len(r.data["items"]) == 1)
            r = await c.call_tool("list_customers", {"per_page": 1, "page": 2})
            check("list_customers pagination", r.data["items"][0]["id"] == 8 and r.data["total"] == 2 and r.data["total_pages"] == 2)
            r = await c.call_tool("list_customers", {"email": "asha@example.test"})
            check("list_customers email filter", len(r.data["items"]) == 1 and r.data["items"][0]["id"] == 7)
            check("list_customers excludes private fields", not any(k in r.data["items"][0] for k in ("password", "billing", "shipping", "meta_data")))
            r = await c.call_tool("list_customers", {"search": "Sam"})
            check("list_customers search filter", r.data["items"][0]["id"] == 8)
            r = await c.call_tool("get_customer", {"customer_id": 7})
            check("get_customer identity", r.data["email"] == "asha@example.test")
            check("get_customer excludes private fields", not any(k in r.data for k in ("password", "billing", "shipping", "meta_data")))
            r = await c.call_tool("list_orders", {"after": "2026-10-16T00:00:00"})
            check("list_orders", r.data["items"][0]["customer"] == "Asha Rao", str(r.data))
            r = await c.call_tool("list_orders", {"status": None, "after": None, "before": None, "search": None})
            check("list_orders accepts optional null filters", r.data["items"][0]["id"] == 5)
            r = await c.call_tool("get_order", {"order_id": 5})
            check("get_order detailed", r.data["payment_method"] == "Cash on delivery", str(r.data))
            r = await c.call_tool("update_order_status", {"order_id": 5, "status": "on-hold"})
            check("update_order_status", r.data["status"] == "on-hold", str(r.data))
            check("store saw X-Forwarded-Proto + Basic auth", all(h.get("X-Forwarded-Proto") == "https" for h in seen_headers) and seen_headers)

            print("\nerrors")
            for name, args, expect in [
                ("get_product", {"product_id": 999}, "404"),
                ("get_product", {"product_id": 0}, "product_id"),
                ("update_product", {"product_id": 999, "name": "missing"}, "404"),
                ("update_product", {"product_id": 100}, "at least one"),
                ("update_product", {"product_id": 100, "name": "   "}, "whitespace"),
                ("update_product", {"product_id": 100, "regular_price": -1}, "regular_price"),
                ("update_product", {"product_id": 100, "status": "bogus"}, "status"),
                ("get_customer", {"customer_id": 999}, "404"),
                ("get_customer", {"customer_id": 0}, "customer_id"),
                ("list_customers", {"per_page": 101}, "per_page"),
                ("create_product", {"name": "   ", "regular_price": 1}, "whitespace"),
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
                    if name == "get_order" and args["order_id"] == 999:
                        check("missing order explains how to find a valid ID", "list_orders" in str(e) and "999" in str(e))

        print("\nmalformed store responses")
        for tool, bad in [("get_product", []), ("get_customer", None), ("get_order", {"id": 1, "billing": "invalid"}), ("list_customers", [None])]:
            async def bad_response(request, payload=bad):
                return httpx.Response(200, json=payload)
            client = WooClient("https://example.test", "ck_test", "cs_test", transport=httpx.MockTransport(bad_response))
            try:
                method = getattr(client, tool)
                await (method(1) if tool.startswith("get_") else method())
                check(f"{tool} rejects malformed response", False)
            except WooError:
                check(f"{tool} rejects malformed response", True)
            finally:
                await client.aclose()
        for value in [float("nan"), float("inf"), -1]:
            try:
                WooClient._price(value)
                check(f"invalid price {value} rejected", False)
            except WooError:
                check(f"invalid price {value} rejected", True)

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
