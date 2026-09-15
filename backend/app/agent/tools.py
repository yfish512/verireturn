from __future__ import annotations

from typing import Any, Protocol

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential


class ToolError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 502):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class BusinessTools(Protocol):
    def get_order(self, actor_id: str, request_id: str) -> dict[str, Any]: ...


class M1ToolClient:
    """The only M2 path to M1 business state: typed HTTP calls with actor injection."""

    def __init__(self, base_url: str, timeout_seconds: float = 8.0, max_retries: int = 2):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout_seconds
        self.max_retries = max_retries

    def _headers(self, actor_id: str, request_id: str, idempotency_key: str | None = None) -> dict[str, str]:
        headers = {"X-Demo-User-Id": actor_id, "X-Request-Id": request_id}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    @retry(retry=retry_if_exception_type(httpx.RequestError), stop=stop_after_attempt(3), wait=wait_exponential(min=0.1, max=1), reraise=True)
    def _transport(
        self, method: str, path: str, actor_id: str, request_id: str, *, body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> httpx.Response:
        with httpx.Client(timeout=self.timeout) as client:
            return client.request(method, f"{self.base_url}{path}", headers=self._headers(actor_id, request_id, idempotency_key), json=body)

    def _request(
        self, method: str, path: str, actor_id: str, request_id: str, *, body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        try:
            response = self._transport(method, path, actor_id, request_id, body=body, idempotency_key=idempotency_key)
        except httpx.RequestError as error:
            raise ToolError("M1_NETWORK_ERROR", "业务系统暂时不可用，请稍后重试。", 503) from error
        if response.is_success:
            return response.json()
        try:
            detail = response.json().get("detail", {})
        except ValueError:
            detail = {}
        raise ToolError(detail.get("code", "M1_HTTP_ERROR"), detail.get("message", response.text), response.status_code)

    def get_order(self, actor_id: str, order_id: str, request_id: str) -> dict[str, Any]:
        return self._request("GET", f"/tools/orders/{order_id}", actor_id, request_id)

    def get_order_items(self, actor_id: str, order_id: str, request_id: str) -> list[dict[str, Any]]:
        return self._request("GET", f"/tools/orders/{order_id}/items", actor_id, request_id)

    def get_logistics(self, actor_id: str, order_id: str, request_id: str) -> dict[str, Any]:
        return self._request("GET", f"/tools/orders/{order_id}/logistics", actor_id, request_id)

    def get_fulfillment(self, actor_id: str, case_id: int, request_id: str) -> dict[str, Any]:
        return self._request("GET", f"/tools/after-sales/cases/{case_id}/fulfillment", actor_id, request_id)

    def check_eligibility(self, actor_id: str, order_id: str, request_type: str, reason: str, request_id: str) -> dict[str, Any]:
        return self._request("POST", "/tools/after-sales/eligibility", actor_id, request_id, body={"order_id": order_id, "request_type": request_type, "reason": reason})

    def create_case(self, actor_id: str, order_id: str, request_type: str, reason: str, items: list[dict[str, Any]] | None, request_id: str, idempotency_key: str) -> dict[str, Any]:
        body = {"order_id": order_id, "request_type": request_type, "reason": reason}
        if items: body["items"] = items
        return self._request("POST", "/tools/after-sales/cases", actor_id, request_id, body=body, idempotency_key=idempotency_key)

    def confirm_case(self, actor_id: str, case_id: int, request_id: str, idempotency_key: str) -> dict[str, Any]:
        return self._request("POST", f"/tools/after-sales/cases/{case_id}/confirm", actor_id, request_id, idempotency_key=idempotency_key)

    def cancel_case(self, actor_id: str, case_id: int, request_id: str, idempotency_key: str) -> dict[str, Any]:
        return self._request("POST", f"/tools/after-sales/cases/{case_id}/cancel", actor_id, request_id, idempotency_key=idempotency_key)

    def schedule_pickup(self, actor_id: str, case_id: int, time_slot: str, request_id: str, idempotency_key: str) -> dict[str, Any]:
        return self._request("POST", f"/tools/after-sales/cases/{case_id}/pickup", actor_id, request_id, body={"time_slot": time_slot}, idempotency_key=idempotency_key)

    def create_review_ticket(self, actor_id: str, order_id: str, request_type: str, reason: str, request_id: str, idempotency_key: str) -> dict[str, Any]:
        return self._request(
            "POST", "/review-tickets", actor_id, request_id,
            body={"order_id": order_id, "request_type": request_type, "reason": reason}, idempotency_key=idempotency_key,
        )
