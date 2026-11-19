"""Sandbox-only Lithic helper. Never prints or stores PAN, CVC or API key."""
from __future__ import annotations

import os
import uuid
import httpx

SANDBOX = "https://sandbox.lithic.com/v1"


class LithicError(Exception):
    """Sanitized Lithic API failure; no secrets or response bodies returned to the UI."""
    def __init__(self, message: str, *, status_code: int | None = None,
                 category: str = "API_ERROR"):
        super().__init__(message)
        self.status_code = status_code
        self.category = category


def lithic_request(method: str, path: str, *, payload: dict | None = None) -> dict:
    key = os.environ.get("LITHIC_API_KEY", "").strip()
    if not key:
        raise LithicError("Set LITHIC_API_KEY in local environment")
    if not path.startswith("/") or "//" in path:
        raise LithicError("Invalid Lithic endpoint")
    try:
        with httpx.Client(timeout=12, trust_env=True) as client:
            api_url = ("https://sandbox.lithic.com" + path) if path.startswith("/v2/") else (SANDBOX + path)
            headers = {"Authorization": key, "Accept": "application/json"}
            if method == "POST":
                headers["Idempotency-Key"] = str(uuid.uuid4())
            response = client.request(method, api_url, json=payload, headers=headers)
        if response.status_code >= 400:
            # Never relay provider response bodies; they may contain account identifiers.
            categories = {
                400: ("BAD_REQUEST", "invalid request parameter"),
                401: ("INVALID_API_KEY", "API key invalid, expired, or not active"),
                403: ("ACCESS_DENIED", "API key or program lacks access to this endpoint"),
                404: ("NOT_FOUND", "endpoint or account resource not found"),
                422: ("UNPROCESSABLE", "request could not be processed"),
                429: ("RATE_LIMITED", "too many requests"),
            }
            category, explanation = categories.get(response.status_code, ("HTTP_ERROR", "API call failed"))
            raise LithicError(
                f"Lithic HTTP {response.status_code}: {explanation} ({category})",
                status_code=response.status_code, category=category)
        try:
            return response.json() if response.content else {}
        except ValueError as exc:
            raise LithicError("Lithic returned a non-JSON success response", category="INVALID_RESPONSE") from exc
    except httpx.HTTPError as ex:
        raise LithicError(f"Lithic sandbox connection failed: {type(ex).__name__}", category="CONNECTION_ERROR") from ex


def create_test_card(*, account_token: str | None = None,
                     memo: str = "OCCLET-AGENTIC-DEMO") -> dict:
    # Sandbox-only. A virtual card is a PAYMENT INSTRUMENT, not a new funded account.
    payload = {"type": "VIRTUAL", "memo": memo}
    if account_token:
        payload["account_token"] = account_token
    data = lithic_request("POST", "/cards", payload=payload)
    if not data.get("token"):
        raise LithicError("Card creation succeeded without card token")
    return {"token": data["token"], "last_four": data.get("last_four", "****"),
            "account_token": data.get("account_token")}


def simulate_auth(card_token: str, amount_cents: int, merchant_id: str) -> dict:
    card = lithic_request("GET", f"/cards/{card_token}")
    pan = card.get("pan")
    if not pan:
        raise LithicError("Sandbox card PAN not available (ensure this is a sandbox card)")
    merchant_ids = {"GROCERY_101": ("GROCERY101", "5411", "OCCLET GROCERY"),
                    "ELECTRONICS_202": ("ELECTRONICS202", "5732", "OCCLET ELECTRONICS"),
                    "TICKETS_303": ("TICKETS303", "7922", "OCCLET TICKET AGENCY")}
    if merchant_id not in merchant_ids:
        raise LithicError("Unsupported test merchant")
    acceptor, mcc, descriptor = merchant_ids[merchant_id]
    return lithic_request("POST", "/simulate/authorize", payload={
        "amount": amount_cents, "billing_currency": "USD", "descriptor": descriptor,
        "mcc": mcc, "merchant_acceptor_id": acceptor, "pan": pan})


def get_transaction(transaction_token: str) -> dict:
    # UUID validation prevents arbitrary endpoint construction. Never return PAN/CVV.
    try:
        parsed = uuid.UUID(transaction_token)
    except (ValueError, TypeError) as exc:
        raise LithicError("Invalid Lithic sandbox transaction UUID") from exc
    data = lithic_request("GET", f"/transactions/{parsed}")
    return {"token": data.get("token"), "account_token": data.get("account_token"),
            "card_token": data.get("card_token"), "status": data.get("status"),
            "result": data.get("result"), "detailed_results": data.get("detailed_results"),
            "rule_results": [{"name": r.get("name"), "result": r.get("result"),
                              "explanation": r.get("explanation")}
                             for r in data.get("rule_results", []) if isinstance(r, dict)]}
