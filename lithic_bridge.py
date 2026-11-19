"""Public-facing Lithic ASA responder. Expose ONLY this app via tunnel.

The member/demo application stays bound to 127.0.0.1:8000 and is never exposed.
Verified ASA requests are forwarded to a secret-protected localhost endpoint.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

import httpx
import logging
from fastapi import FastAPI, Request, HTTPException

app = FastAPI(title="Occlet Lithic ASA Responder", docs_url=None, redoc_url=None, openapi_url=None)
_log = logging.getLogger("uvicorn.error")


def _auth_header_names(headers) -> str:
    """Which signing headers arrived (names only, never values) for diagnosing 401s."""
    names = {k.lower() for k in headers.keys()}
    wanted = ("webhook-id", "webhook-timestamp", "webhook-signature", "x-lithic-hmac")
    return ",".join(n for n in wanted if n in names) or "none"


def verify_lithic_signature(raw: bytes, headers: dict, secret: str, now: float | None = None):
    """Verify HMAC-SHA256 of <webhook-id>.<timestamp>.<raw body>.

    HMAC secret supplied by GET /v1/auth_stream/secret; *not* Lithic API key.
    """
    if not secret:
        raise ValueError("ASA HMAC secret is missing")
    headers = {k.lower(): v for k, v in headers.items()}
    wid = headers.get("webhook-id")
    stamp = headers.get("webhook-timestamp")
    signature = headers.get("webhook-signature")
    if not all((wid, stamp, signature)):
        raise ValueError("Missing Lithic authentication headers")
    try:
        timestamp = int(stamp)
    except (TypeError, ValueError):
        raise ValueError("Invalid Lithic timestamp") from None
    if abs((now if now is not None else time.time()) - timestamp) > 300:
        raise ValueError("Lithic request outside five-minute replay window")
    key_material = secret.removeprefix("whsec_")
    try:
        key = base64.b64decode(key_material, validate=True)
    except Exception as err:
        raise ValueError("Bad ASA HMAC secret format") from err
    message = f"{wid}.{stamp}.".encode() + raw
    expected = hmac.new(key, message, hashlib.sha256).digest()
    for candidate in signature.split():
        version, separator, encoded = candidate.partition(",")
        if separator and version == "v1":
            try:
                found = base64.b64decode(encoded, validate=True)
                if hmac.compare_digest(expected, found):
                    return wid
            except (ValueError, base64.binascii.Error):
                continue
    raise ValueError("Lithic signature verification failed")


@app.get("/healthz")
def health():
    return {"status": "up", "configured": bool(os.environ.get("LITHIC_ASA_SECRET"))}


@app.post("/lithic/asa")
async def lithic_asa(request: Request):
    secret = os.environ.get("LITHIC_ASA_SECRET", "")
    internal_secret = os.environ.get("OCCLET_BRIDGE_SECRET", "")
    if not secret or not internal_secret:
        # Deliberately fail closed when webhook verification is not configured.
        raise HTTPException(503, "Webhook verifier / internal bridge not configured")
    raw = await request.body()
    if len(raw) > 128_000:
        raise HTTPException(413, "Payload too large")
    try:
        wid = verify_lithic_signature(raw, dict(request.headers), secret)
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as err:
        # Reason strings are our own fixed messages; no secret or payload is logged.
        _log.warning("ASA rejected: %s (signing headers: %s)", type(err).__name__ + ": " + str(err)[:80],
                     _auth_header_names(request.headers))
        raise HTTPException(401, "Invalid signed ASA request") from None
    if not isinstance(payload, dict):
        raise HTTPException(400, "Invalid ASA payload")

    try:
        async with httpx.AsyncClient(timeout=2.0, trust_env=False) as client:
            decision = await client.post(
                "http://127.0.0.1:8000/api/internal/lithic/authorize",
                headers={"X-Occlet-Bridge-Secret": internal_secret},
                json={"webhook_id": wid, "authorization": payload},
            )
            decision.raise_for_status()
            result = decision.json()
    except (httpx.HTTPError, ValueError):
        # Never approve if local policy engine is unavailable.
        return {"result": "UNAUTHORIZED_MERCHANT"}
    if result.get("result") not in {"APPROVED", "UNAUTHORIZED_MERCHANT", "VELOCITY_EXCEEDED", "INSUFFICIENT_FUNDS", "CARD_PAUSED", "SUSPECTED_FRAUD"}:
        return {"result": "UNAUTHORIZED_MERCHANT"}
    return {"result": result["result"]}

@app.post('/lithic/events')
async def lithic_transaction_event(request: Request):
    """Signed Lithic Events API webhook, distinct from synchronous ASA.

    Requires an Events API subscription and its OWN LITHIC_EVENT_SECRET.
    No credentials are relayed to the local member dashboard.
    """
    event_secret=os.environ.get('LITHIC_EVENT_SECRET','')
    internal_secret=os.environ.get('OCCLET_BRIDGE_SECRET','')
    if not event_secret or not internal_secret:
        raise HTTPException(503,'Events verifier not configured')
    raw=await request.body()
    if len(raw)>200_000:
        raise HTTPException(413,'Event payload too large')
    try:
        wid=verify_lithic_signature(raw,dict(request.headers),event_secret)
        event=json.loads(raw)
        if not isinstance(event,dict) or event.get('event_type')!='card_transaction.updated':
            raise ValueError('Unsupported event payload')
    except (ValueError,UnicodeDecodeError,json.JSONDecodeError) as err:
        _log.warning("Event rejected: %s (signing headers: %s)", type(err).__name__ + ": " + str(err)[:80],
                     _auth_header_names(request.headers))
        raise HTTPException(401,'Invalid signed transaction event') from None
    try:
        async with httpx.AsyncClient(timeout=5.0,trust_env=False) as client:
            reply=await client.post('http://127.0.0.1:8000/api/internal/lithic/event',
                headers={'X-Occlet-Bridge-Secret':internal_secret},
                json={'webhook_id':wid,'event':event})
            reply.raise_for_status()
            return {'received':True,'action':reply.json().get('action')}
    except (httpx.HTTPError,ValueError):
        # Non-2xx means Lithic should retry; don't ACK events the ledger did not process.
        raise HTTPException(503,'Transaction event not committed; retry required') from None
