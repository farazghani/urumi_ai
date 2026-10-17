"""Async WooCommerce REST client used by the MCP tools."""
from __future__ import annotations

import os
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

import httpx


class WooError(Exception):
    """Configuration, validation, or store request failure safe to show to callers."""


class WooClient:
    def __init__(self, base_url: str, consumer_key: str, consumer_secret: str,
                 *, transport: httpx.AsyncBaseTransport | None = None,
                 forwarded_proto: str | None = None) -> None:
        url = urlsplit(base_url)
        if url.scheme not in {"http", "https"} or not url.hostname or url.query or url.fragment or url.username:
            raise WooError("WOO_BASE_URL must be an HTTP(S) store URL without credentials, query, or fragment")
        if not consumer_key or not consumer_secret:
            raise WooError("WOO_CONSUMER_KEY and WOO_CONSUMER_SECRET are required")
        if forwarded_proto not in {None, "http", "https"}:
            raise WooError("WOO_FORWARDED_PROTO must be http or https")
        if forwarded_proto is None and url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost", "::1"}:
            forwarded_proto = "https"
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/wp-json/wc/v3/",
            auth=httpx.BasicAuth(consumer_key, consumer_secret),
            # Compose opts in for its private WordPress network; local development
            # also uses this workaround for loopback stores.
            headers={"X-Forwarded-Proto": forwarded_proto} if forwarded_proto else {},
            timeout=15,
            transport=transport,
        )

    @classmethod
    def from_env(cls) -> WooClient:
        base_url = os.getenv("WOO_BASE_URL") or os.getenv("WC_BASE_URL", "")
        if not base_url:
            raise WooError("WOO_BASE_URL is required")
        return cls(base_url,
                   os.getenv("WOO_CONSUMER_KEY") or os.getenv("WC_CONSUMER_KEY", ""),
                   os.getenv("WOO_CONSUMER_SECRET") or os.getenv("WC_CONSUMER_SECRET", ""),
                   forwarded_proto=os.getenv("WOO_FORWARDED_PROTO") or None)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.RequestError:
            raise WooError("Cannot reach WooCommerce store; check its URL and availability") from None
        if response.is_error:
            try:
                error = response.json()
                message = error.get("message", "Store request failed") if isinstance(error, dict) else "Store request failed"
            except ValueError:
                message = "Store returned a non-JSON error"
            raise WooError(f"WooCommerce HTTP {response.status_code}: {message}")
        return response

    @staticmethod
    def _json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            raise WooError("WooCommerce returned invalid JSON") from None

    @staticmethod
    def _product(item: dict[str, Any]) -> dict[str, Any]:
        return {key: item.get(key) for key in (
            "id", "name", "status", "price", "regular_price", "sku", "stock_status", "permalink"
        )}

    @staticmethod
    def _order(item: dict[str, Any], *, detailed: bool = False) -> dict[str, Any]:
        order = {key: item.get(key) for key in (
            "id", "status", "total", "currency", "date_created", "line_items"
        )}
        billing = item.get("billing") or {}
        order["customer"] = " ".join(filter(None, (billing.get("first_name"), billing.get("last_name"))))
        if detailed:
            order.update(payment_method=item.get("payment_method_title"),
                         shipping_total=item.get("shipping_total"),
                         customer_note=item.get("customer_note"),
                         date_modified=item.get("date_modified"))
        return order

    async def _list(self, resource: str, params: dict[str, Any]) -> dict[str, Any]:
        response = await self._request("GET", resource, params={k: v for k, v in params.items() if v is not None})
        items = self._json(response)
        if not isinstance(items, list):
            raise WooError("WooCommerce returned an invalid list response")
        summarize = self._product if resource == "products" else self._order
        try:
            return {"items": [summarize(item) for item in items], "page": params["page"],
                    "per_page": params["per_page"], "total": int(response.headers.get("X-WP-Total", len(items))),
                    "total_pages": int(response.headers.get("X-WP-TotalPages", 1))}
        except (TypeError, ValueError, AttributeError):
            raise WooError("WooCommerce returned an invalid list response") from None

    async def list_products(self, *, search: str | None = None, status: str | None = None,
                            page: int = 1, per_page: int = 20) -> dict[str, Any]:
        return await self._list("products", dict(search=search, status=status, page=page, per_page=per_page))

    async def create_product(self, name: str, regular_price: float, *, description: str = "",
                             sku: str | None = None, status: str = "publish") -> dict[str, Any]:
        try:
            price = Decimal(str(regular_price))
        except InvalidOperation:
            raise WooError("regular_price must be a finite non-negative number") from None
        if not price.is_finite() or price < 0:
            raise WooError("regular_price must be a finite non-negative number")
        body = dict(name=name, type="simple", regular_price=format(price, "f"), description=description, status=status)
        if sku is not None:
            body["sku"] = sku
        return self._product(self._json(await self._request("POST", "products", json=body)))

    async def list_orders(self, *, status: str | None = None, after: str | None = None,
                          before: str | None = None, search: str | None = None,
                          page: int = 1, per_page: int = 20) -> dict[str, Any]:
        for field, value in (("after", after), ("before", before)):
            if value is not None:
                try:
                    datetime.fromisoformat(value)
                    if "T" not in value:
                        raise ValueError
                except ValueError:
                    raise WooError(f"{field} must be an ISO 8601 datetime") from None
        return await self._list("orders", dict(status=status, after=after, before=before, search=search,
                                                page=page, per_page=per_page, orderby="date", order="desc"))

    async def get_order(self, order_id: int) -> dict[str, Any]:
        return self._order(self._json(await self._request("GET", f"orders/{order_id}")), detailed=True)

    async def update_order_status(self, order_id: int, status: str) -> dict[str, Any]:
        return self._order(self._json(await self._request("PUT", f"orders/{order_id}", json={"status": status})), detailed=True)
