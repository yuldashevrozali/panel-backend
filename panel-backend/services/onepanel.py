"""Server-only client for the 1xPanel API."""

import json
import os
import socket
import ssl
import threading
import time
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from dotenv import load_dotenv

load_dotenv()


class OnePanelError(Exception):
    """A safe, client-facing description of an upstream provider failure."""

    def __init__(self, message: str, unavailable: bool = False):
        super().__init__(message)
        self.unavailable = unavailable


class OnePanelClient:
    def __init__(self) -> None:
        self.url = os.getenv("ONEXPANEL_API_URL", "https://1xpanel.com/api/v2")
        self.api_key = os.getenv("ONEXPANEL_API_KEY")
        self.timeout = float(os.getenv("ONEXPANEL_TIMEOUT_SECONDS", "5"))
        self.cache_ttl = int(os.getenv("ONEXPANEL_SERVICES_CACHE_TTL", "300"))
        self._services_cache: list[dict[str, Any]] | None = None
        self._cache_expires_at = 0.0
        self._lock = threading.Lock()
        self._ssl_context = self._build_ssl_context()

    def _build_ssl_context(self) -> ssl.SSLContext | None:
        candidates = [
            os.getenv("SSL_CERT_FILE"),
            "/etc/ssl/cert.pem",
            "/etc/ssl/certs/ca-certificates.crt",
            "/etc/pki/tls/certs/ca-bundle.crt",
        ]
        for candidate in candidates:
            if candidate and os.path.exists(candidate):
                return ssl.create_default_context(cafile=candidate)
        return None

    def _post(self, action: str, **params: str | int) -> Any:
        if not self.api_key:
            raise OnePanelError("Service provider is not configured", unavailable=True)
        body = urlencode({"key": self.api_key, "action": action, **params}).encode()
        request = Request(self.url, data=body, method="POST")
        request_params: dict[str, Any] = {"timeout": self.timeout}
        if self._ssl_context is not None:
            request_params["context"] = self._ssl_context
        try:
            with urlopen(request, **request_params) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            raise OnePanelError("Service provider request failed") from exc
        except (socket.timeout, TimeoutError, URLError, ssl.SSLError) as exc:
            # Never include a URL or request body: either could contain credentials.
            raise OnePanelError(
                "Service provider is temporarily unavailable", unavailable=True
            ) from exc

        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise OnePanelError(
                "Service provider returned an invalid response"
            ) from exc
        if isinstance(payload, dict) and payload.get("error"):
            raise OnePanelError("Service provider rejected the request")
        return payload

    @staticmethod
    def _as_decimal(value: Any, field: str) -> Decimal:
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise OnePanelError(
                f"Service provider returned an invalid {field}"
            ) from exc
        if not number.is_finite() or number < 0:
            raise OnePanelError(f"Service provider returned an invalid {field}")
        return number

    @staticmethod
    def _as_flag(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().casefold() in {"1", "true", "yes"}
        return False

    def _normalize_service(self, item: Any) -> dict[str, Any]:
        if not isinstance(item, dict):
            raise OnePanelError(
                "Service provider returned an invalid services response"
            )
        required = ("service", "name", "category", "rate", "min", "max")
        if any(key not in item for key in required):
            raise OnePanelError("Service provider returned an incomplete service")
        try:
            minimum = int(item["min"])
            maximum = int(item["max"])
        except (TypeError, ValueError) as exc:
            raise OnePanelError(
                "Service provider returned invalid service limits"
            ) from exc
        if minimum < 0 or maximum < minimum:
            raise OnePanelError("Service provider returned invalid service limits")
        return {
            "service": str(item["service"]),
            "name": str(item["name"]),
            "category": str(item["category"]),
            "rate": self._as_decimal(item["rate"], "rate"),
            "min": minimum,
            "max": maximum,
            "type": str(item["type"]) if item.get("type") is not None else None,
            "refill": self._as_flag(item.get("refill", False)),
            "cancel": self._as_flag(item.get("cancel", False)),
            "dripfeed": self._as_flag(item.get("dripfeed", False)),
        }

    def get_services(self, force_refresh: bool = False) -> list[dict[str, Any]]:
        now = time.monotonic()
        with self._lock:
            if (
                not force_refresh
                and self._services_cache
                and now < self._cache_expires_at
            ):
                return self._services_cache.copy()
            payload = self._post("services")
            if not isinstance(payload, list):
                raise OnePanelError(
                    "Service provider returned an invalid services response"
                )
            services = [self._normalize_service(item) for item in payload]
            self._services_cache = services
            self._cache_expires_at = time.monotonic() + self.cache_ttl
            return services.copy()

    def find_service(
        self, service_id: str, force_refresh: bool = False
    ) -> dict[str, Any] | None:
        return next(
            (
                service
                for service in self.get_services(force_refresh)
                if service["service"] == str(service_id)
            ),
            None,
        )

    def create_order(self, service_id: str, link: str, quantity: int) -> dict[str, Any]:
        payload = self._post("add", service=service_id, link=link, quantity=quantity)
        if not isinstance(payload, dict) or payload.get("order") is None:
            raise OnePanelError("Service provider returned an invalid order response")
        return {
            "external_order_id": str(payload["order"]),
            "charge": payload.get("charge"),
        }

    def get_order_status(self, external_order_id: str) -> dict[str, Any]:
        payload = self._post("status", order=external_order_id)
        if not isinstance(payload, dict) or not isinstance(payload.get("status"), str):
            raise OnePanelError("Service provider returned an invalid order status")
        result: dict[str, Any] = {"status": payload["status"]}
        if payload.get("charge") is not None:
            result["charge"] = self._as_decimal(payload["charge"], "charge")
        return result


onepanel = OnePanelClient()
