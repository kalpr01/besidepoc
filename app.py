"""Occlet Agentic Virtual Card: local, no-money demonstration.

SECURITY BOUNDARY: This is an educational localhost-only demo, not an issuer or
merchant integration. The /api/member/* endpoints have no login and MUST NOT be
exposed to the internet. No payment card credentials are created or stored.
"""
from __future__ import annotations

import base64
import hmac
import os
import binascii
from datetime import datetime, timezone
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlsplit
from typing import Literal
import uuid

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

app = FastAPI(title="Occlet Agentic Card — Local Demo", version="0.1.0")
_lock = threading.RLock()
_agents: dict[str, dict] = {}
_cards: dict[str, dict] = {}
_intents: dict[str, dict] = {}
_nonces: set[tuple[str, str]] = set()
_network_refs: dict[str, dict] = {}
_lithic_refs: dict[str, dict] = {}
_events: list[dict] = []
_auth_traces: list[dict] = []

# A registered PURPOSE is the member's ongoing delegation scope. A PAYMENT
# INTENT is one proposed purchase within that scope. These are not the same.
PURPOSE_MERCHANTS = {
    "GROCERIES": frozenset(("GROCERY_101",)),
    "TICKETS": frozenset(("TICKETS_303",)),
}
TEST_MERCHANTS = frozenset(("GROCERY_101", "ELECTRONICS_202", "TICKETS_303"))
FLOW_SIGNED = "SIGNED_INTENT_REQUIRED"
FLOW_CARD_BOUND = "CARD_BOUND_NETWORK"


def default_parent() -> dict:
    # Demonstration of the MEMBER'S existing funding arrangement. NOT a new ledger.
    # Funds here are a local mirror for illustration, not Lithic account balances.
    return {
        "enabled": False, "id": "arr_parent_demo", "status": "ACTIVE",
        "credit_limit_cents": 500_000, "max_transaction_cents": 100_000,
        "monthly_policy_limit_cents": 500_000,
        "lithic_account_token": None, "lithic_parent_card_token": None,
        "parent_card_last_four": None, "holds": {},
        "ledger_mode": "DEMO_MIRROR", "lithic_financial_account_token": None,
        "last_lithic_available_cents": None, "policy_authorizations": {},
    }


_parent = default_parent()
_member: dict = dict({"id": "member_demo", "name": "Ann Gutierrez"})  # active lab test member


def parent_used() -> int:
    if _parent.get("ledger_mode") == "OCCLET_SQL_LEDGER":
        from occlet_demo_ledger import monthly_used
        return monthly_used(_parent["id"])
    return sum((_parent["policy_authorizations"] if _parent.get("ledger_mode") == "LITHIC_LEDGER" else _parent["holds"]).values())


def parent_available() -> int:
    if _parent.get("ledger_mode") == "LITHIC_LEDGER":
        # Display-only last observed balance; never use cached observations for approval.
        return _parent.get("last_lithic_available_cents")
    if _parent.get("ledger_mode") == "OCCLET_SQL_LEDGER":
        from occlet_demo_ledger import summary
        return summary(_parent["id"])["available_cents"]
    return max(0, _parent["credit_limit_cents"] - parent_used())


def parent_policy(amount: int, merchant_id: str) -> str | None:
    """Fail-closed parent eligibility; no independent funding for child cards."""
    if not _parent["enabled"]:
        return None
    if _parent["status"] != "ACTIVE":
        return "PARENT_ACCOUNT_PAUSED"
    if amount > _parent["max_transaction_cents"]:
        return "PARENT_PER_TRANSACTION_LIMIT"
    if parent_used() + amount > _parent["monthly_policy_limit_cents"]:
        return "PARENT_SHARED_SPENDING_LIMIT"
    if _parent.get("ledger_mode") == "LITHIC_LEDGER":
        # Lithic is the financial ledger when the sandbox program is configured
        # for authorization from balance. Never create a second local balance hold.
        if not _parent.get("lithic_financial_account_token") or os.environ.get("LITHIC_AUTH_FROM_BALANCE_CONFIRMED") != "1":
            return "LEDGER_BACKED_AUTH_NOT_CONFIRMED"
    elif amount > parent_available():
        return "PARENT_AVAILABLE_CREDIT"
    return None


# Q Delegated Card decision layers for a parent-card purchase on a W4 arrangement.
# Level 1 = errand card check: hard rules in a FIXED order against the arrangement and ledger.
# Level 2 = enhanced authorization: precomputed signals + competing-intent order; can ONLY tighten.
# The SQL row-locked authorize() remains the financial authority; this is the explained trace.
W4_PRICE_BANDS = {"GROCERY_101": 50000, "TICKETS_303": 60000, "ELECTRONICS_202": 150000}  # demo signals
W4_VELOCITY_WINDOW_SECONDS = 600
W4_VELOCITY_MAX_AUTHS = 5


def w4_rule_layers(amount: int, merchant: str) -> dict | None:
    if _parent.get("ledger_mode") != "OCCLET_SQL_LEDGER":
        return None
    from occlet_w4 import overview
    from occlet_demo_ledger import LedgerRejected
    try:
        ov = overview(_parent["id"])
    except LedgerRejected:
        return None  # W4 not initialized for this arrangement
    m = lambda c: f"${c / 100:,.2f}"
    bal = ov["balance"]
    available, protected, yld = bal["available_cents"], ov["protected_cents"], ov["yield_enabled"]
    room = available if yld else max(0, available - protected)
    used = parent_used()
    rules, decided_by, reason = [], None, None

    def add(level, rid, name, passed, detail, code):
        nonlocal decided_by, reason
        if decided_by:
            rules.append({"level": level, "id": rid, "name": name, "result": "NOT_REACHED", "detail": detail})
            return
        rules.append({"level": level, "id": rid, "name": name, "result": "PASS" if passed else "FAIL",
                      "detail": detail, **({} if passed else {"reason_code": code})})
        if not passed:
            decided_by, reason = f"LEVEL_{level}", code

    add(1, "L1.1", "Arrangement active", _parent["status"] == "ACTIVE",
        f"Funding arrangement status {_parent['status']}", "PARENT_ACCOUNT_PAUSED")
    add(1, "L1.2", "Per-purchase limit", amount <= _parent["max_transaction_cents"],
        f"{m(amount)} vs limit {m(_parent['max_transaction_cents'])}", "PARENT_PER_TRANSACTION_LIMIT")
    add(1, "L1.3", "Shared spending limit", used + amount <= _parent["monthly_policy_limit_cents"],
        f"{m(used)} used this month + {m(amount)} vs {m(_parent['monthly_policy_limit_cents'])}",
        "PARENT_SHARED_SPENDING_LIMIT")
    add(1, "L1.4", "Funds available (Occlet ledger)", amount <= available,
        f"{m(amount)} vs {m(available)} available", "INSUFFICIENT_FUNDS")
    add(1, "L1.5", "Protected intents" + (" (yield ON)" if yld else " (yield OFF)"), yld or amount <= room,
        f"Room {m(room)} = available {m(available)} − protected trip {m(protected)}"
        + ("; trip yields room" if yld else f"; {m(amount)} " + ("fits" if amount <= room else "would break the trip protection")),
        "PROTECTED_BY_MEMBER")
    level1_passed = decided_by is None
    if not level1_passed:
        for rid, name in (("L2.1", "Competing-intent order"), ("L2.2", "Velocity signal"), ("L2.3", "Price-band signal")):
            rules.append({"level": 2, "id": rid, "name": name, "result": "NOT_REACHED",
                          "detail": "Level 1 declined; Level 2 only runs after Level 1 passes"})
    else:
        after = available - amount
        add(2, "L2.1", "Competing-intent order", yld or after >= protected,
            "Must-happen intents first: trip " + (f"protection {m(protected)} still covered ({m(after)} left)"
                                                 if yld or after >= protected else f"would be {m(protected - after)} short"),
            "L2_COMPETING_INTENT")
        now = datetime.now(timezone.utc)
        recent = [a for a in bal["authorizations"] if a.get("status") in ("AUTHORIZED", "CLEARED")
                  and a.get("created_at") and hasattr(a["created_at"], "tzinfo")
                  and (now - a["created_at"]).total_seconds() <= W4_VELOCITY_WINDOW_SECONDS]
        add(2, "L2.2", "Velocity signal", len(recent) < W4_VELOCITY_MAX_AUTHS,
            f"{len(recent)} approved in the last {W4_VELOCITY_WINDOW_SECONDS // 60} min (max {W4_VELOCITY_MAX_AUTHS - 1})",
            "L2_VELOCITY")
        band = W4_PRICE_BANDS.get(merchant, 50000)
        add(2, "L2.3", "Price-band signal", amount <= band,
            f"{m(amount)} vs usual band up to {m(band)} for {merchant}", "L2_PRICE_BAND")
    return {"rules": rules, "decided_by": decided_by, "reason": reason, "level1_passed": level1_passed,
            "inputs": {"amount_cents": amount, "available_cents": available, "protected_cents": protected,
                       "yield_enabled": yld, "room_cents": room, "merchant_id": merchant}}


def finalize_layers(layers: dict | None, decision: str, reason: str | None) -> dict | None:
    """Record the authoritative outcome; flag if the SQL row-lock recheck disagreed with the trace."""
    if layers is None:
        return None
    layers["final"] = {"decision": decision, "reason": reason or "POLICY_AND_FUNDING_OK"}
    if decision != "APPROVED" and not layers["decided_by"]:
        layers["final"]["note"] = "Declined by the SQL row-locked recheck after the rules passed (state changed)"
    return layers


def reserve_parent_once(authorization_key: str, amount_cents: int, *,
                        card_reference: str = "PARENT_CARD",
                        merchant_id: str = "UNKNOWN") -> None:
    """Create exactly one hold at the selected DEMO financial authority.

    OCCLET_SQL_LEDGER: durable SQL transaction with a row lock, journal and reference.
    LITHIC_LEDGER: Lithic does the real balance/hold operation, only policy counters here.
    DEMO_MIRROR: legacy in-memory illustration preserved for older examples.
    """
    if not _parent["enabled"]:
        return
    if _parent.get("ledger_mode") == "OCCLET_SQL_LEDGER":
        from occlet_demo_ledger import authorize
        authorize(_parent["id"], authorization_key, amount_cents,
                  card_reference=card_reference, merchant_id=merchant_id,
                  max_transaction_cents=_parent["max_transaction_cents"],
                  monthly_limit_cents=_parent["monthly_policy_limit_cents"])
        return
    target = _parent["policy_authorizations"] if _parent.get("ledger_mode") == "LITHIC_LEDGER" else _parent["holds"]
    old = target.get(authorization_key)
    if old is not None and old != amount_cents:
        raise ValueError("Parent authorization reference reused for different amount")
    target[authorization_key] = amount_cents


def ledger_decline_code(exc) -> str:
    return ("INSUFFICIENT_FUNDS" if exc.code == "INSUFFICIENT_FUNDS" else
            "VELOCITY_EXCEEDED" if "LIMIT" in exc.code else "UNAUTHORIZED_MERCHANT")



def log(action: str, result: str, details: str = "") -> None:
    _events.append({"at": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
                    "action": action, "result": result, "details": details})
    del _events[:-100]


def add_auth_trace(*, source: str, mode: str, decision: str, card: dict | None,
                   merchant: str, amount: int, network_reference: str,
                   intent_id: str | None = None, reason: str | None = None,
                   layers: dict | None = None) -> None:
    _auth_traces.append({
        "at": datetime.now(timezone.utc).isoformat(),
        "source": source, "mode": mode, "decision": decision,
        "agent_id": card["agent_id"] if card else None,
        "agent_attribution": ("SIGNED_AGENT_INTENT" if mode == FLOW_SIGNED else
                              "INFERRED_FROM_CARD_BINDING" if card else "NONE"),
        "card_id": card["id"] if card else None,
        "purpose": card.get("purpose") if card else None,
        "merchant_id": merchant, "amount_cents": amount,
        "network_reference": network_reference, "payment_intent_id": intent_id,
        "reason": reason or ("POLICY_AND_FUNDING_OK" if decision == "APPROVED" else "DECLINED"),
        "warning": "Card binding attributes a transaction to the registered agent; it does NOT prove the agent initiated it"
                   if mode == FLOW_CARD_BOUND else None,
        "layers": layers,
    })
    del _auth_traces[:-40]


def card_scope_reason(card: dict, merchant_id: str, amount: int) -> str | None:
    """Evaluate hard, member-defined controls for either transaction flow."""
    if card["status"] != "ACTIVE":
        return "VIRTUAL_CARD_REVOKED"
    if card["agent_id"] not in _agents:
        return "AGENT_NOT_REGISTERED"
    if merchant_id not in card["allowed_merchants"]:
        return "MERCHANT_NOT_DELEGATED"
    if card.get("purpose") in PURPOSE_MERCHANTS and merchant_id not in PURPOSE_MERCHANTS[card["purpose"]]:
        return "PURPOSE_MISMATCH"
    if amount > card["per_transaction_cents"]:
        return "AGENT_TRANSACTION_LIMIT"
    if amount > card["budget_cents"] - card["spent_cents"] - reserved_for(card["id"]):
        return "AGENT_BUDGET_EXCEEDED"
    return parent_policy(amount, merchant_id)


def fail(status: int, action: str, reason: str):
    log(action, "DECLINED", reason)
    raise HTTPException(status_code=status, detail=reason)


INTENT_TTL_SECONDS = 600  # long enough to switch to the member page and approve


def request_card_bound_approval(card: dict, merchant_id: str, amount: int, origin: str = "LOCAL") -> str:
    """Flow B has no pre-purchase intent, so an over-threshold attempt is declined and
    leaves a member approval request. A retry of the same card/merchant/amount after
    the member approves consumes it once."""
    for i in _intents.values():
        if (i.get("flow") == FLOW_CARD_BOUND and i["card_id"] == card["id"] and i["merchant_id"] == merchant_id
                and i["amount_cents"] == amount and i["status"] in ("AWAITING_MEMBER_APPROVAL", "READY", "RETRYING")):
            return i["id"]
    approval_id = "pa_" + secrets.token_hex(7)
    _intents[approval_id] = {"id": approval_id, "card_id": card["id"], "agent_id": card["agent_id"],
                             "merchant_id": merchant_id, "amount_cents": amount, "flow": FLOW_CARD_BOUND, "origin": origin,
                             "status": "AWAITING_MEMBER_APPROVAL", "payment_token": None,
                             "created_at": datetime.now(timezone.utc).isoformat(),
                             "expires_at": time.time() + INTENT_TTL_SECONDS}
    log("Delegation policy", "STEP-UP REQUIRED",
        f"{approval_id}: card-bound ${amount / 100:.2f} at {merchant_id} declined; member approval requested")
    return approval_id


def card_bound_decision(card: dict, merchant_id: str, amount: int, origin: str = "LOCAL") -> tuple[str | None, dict | None, str | None]:
    """Returns (reason, consumed_approval, approval_request_id) for a Flow B attempt."""
    approval = None
    if amount > card["approval_threshold_cents"]:
        approval = next((i for i in _intents.values() if i.get("flow") == FLOW_CARD_BOUND
                         and i["card_id"] == card["id"] and i["merchant_id"] == merchant_id
                         and i["amount_cents"] == amount and i["status"] in ("READY", "RETRYING")), None)
        if approval:
            approval["status"] = "USED"  # so its reservation is not counted twice below
    reason = card_scope_reason(card, merchant_id, amount)
    if reason is None and amount > card["approval_threshold_cents"] and approval is None:
        return "MEMBER_APPROVAL_REQUIRED_BEFORE_PURCHASE", None, request_card_bound_approval(card, merchant_id, amount, origin)
    if reason and approval:
        approval["status"] = "READY"
        approval = None
    return reason, approval, None


def ensure_expirations() -> None:
    now = time.time()
    for intent in _intents.values():
        if intent["status"] in ("READY", "AWAITING_MEMBER_APPROVAL") and now > intent["expires_at"]:
            intent["status"] = "EXPIRED"
            log("Payment intent", "EXPIRED", intent["id"])


def reserved_for(card_id: str) -> int:
    return sum(i["amount_cents"] for i in _intents.values()
               if i["card_id"] == card_id and i["status"] in ("READY", "AWAITING_MEMBER_APPROVAL"))


def state_view() -> dict:
    ensure_expirations()
    parent = {k: v for k, v in _parent.items() if k != "holds"}
    ledger_backed = parent.get("ledger_mode") == "LITHIC_LEDGER"
    local_sql = parent.get("ledger_mode") == "OCCLET_SQL_LEDGER"
    ledger_balance = None
    if local_sql:
        from occlet_demo_ledger import summary
        ledger_balance = summary(_parent["id"])
    parent.update({"held_cents": (ledger_balance["held_cents"] if local_sql else
                    sum(_parent["holds"].values()) if not ledger_backed else 0),
                   "policy_utilization_cents": parent_used(),
                   "available_cents": ledger_balance["available_cents"] if local_sql else parent_available(),
                   "cash_cents": ledger_balance["cash_cents"] if local_sql else None,
                   "hold_count": (sum(a["status"] == "AUTHORIZED" for a in ledger_balance["authorizations"])
                                  if local_sql else len(_parent["holds"]) if not ledger_backed else 0),
                   "funding_note": ("OCCLET POSTGRES DEMO LEDGER: local authoritative balance, SQL double-entry journals and one shared funding account. NOT Lithic's balance."
                                    if local_sql else "LITHIC LEDGER: available is last observed (not used to authorize); holds and postings are at Lithic; local counters are policy utilization"
                                    if ledger_backed else "LOCAL DEMO MIRROR: not a Lithic balance or a second account")})
    return {"parent": parent, "agents": [{"agent_id": a["agent_id"], "name": a["name"], "key_fingerprint": a["key_fingerprint"],
                                           "purpose": a.get("purpose"), "purchase_goal": a.get("purchase_goal"),
                                           "registered_limits": a.get("registered_limits")}
                       for a in _agents.values()],
            "cards": [{**c, "reserved_cents": reserved_for(c["id"]),
                       "remaining_cents": c["budget_cents"] - c["spent_cents"] - reserved_for(c["id"])}
                      for c in _cards.values()],
            "intents": [{k: v for k, v in i.items() if k not in ("payment_token", "expires_at")}
                        for i in reversed(list(_intents.values()))],
            "events": list(reversed(_events)),
            "authorization_traces": list(reversed(_auth_traces))}


class ParentConfiguration(BaseModel):
    credit_limit_cents: int = Field(ge=1, le=100_000_000)
    max_transaction_cents: int = Field(ge=1, le=100_000_000)
    monthly_policy_limit_cents: int = Field(ge=1, le=100_000_000)


class ParentStatus(BaseModel):
    status: Literal["ACTIVE", "PAUSED"]


class AgentRegistration(BaseModel):
    agent_id: str = Field(min_length=4, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    public_key_b64: str
    # Optional solely to preserve legacy demo/tests. The new UI always provides
    # a member-defined purpose, purchase goal and constraints at registration.
    purpose: Literal["GROCERIES", "TICKETS"] | None = None
    purchase_goal: str | None = Field(default=None, max_length=240)
    registered_per_transaction_cents: int | None = Field(default=None, ge=1, le=10000000)
    registered_budget_cents: int | None = Field(default=None, ge=1, le=10000000)
    registered_approval_threshold_cents: int | None = Field(default=None, ge=1, le=10000000)


class CardIssue(BaseModel):
    agent_id: str
    per_transaction_cents: int = Field(ge=1, le=10000000)
    budget_cents: int = Field(ge=1, le=10000000)
    approval_threshold_cents: int = Field(ge=1, le=10000000)
    allowed_merchants: list[str] = Field(min_length=1)
    authorization_flow: Literal["SIGNED_INTENT_REQUIRED", "CARD_BOUND_NETWORK"] = FLOW_SIGNED


class AgentIntent(BaseModel):
    agent_id: str
    card_id: str
    merchant_id: str
    amount_cents: int = Field(gt=0)
    currency: Literal["USD"]
    nonce: str = Field(min_length=12, max_length=150)
    timestamp_ms: int
    signature_b64: str


class Approval(BaseModel):
    approved: bool


class AgentIntentStatus(BaseModel):
    agent_id: str
    timestamp_ms: int
    signature_b64: str


class NetworkAuth(BaseModel):
    payment_token: str
    merchant_id: str
    amount_cents: int = Field(gt=0)
    network_reference: str = Field(min_length=8, max_length=100)


class NetworkDirectAuth(BaseModel):
    # This mimics the card reference in a network authorization message.
    # Deliberately NO agent_id, signature, or payment_intent_id fields.
    card_reference: str
    merchant_id: str
    amount_cents: int = Field(gt=0)
    network_reference: str = Field(min_length=8, max_length=100)


# Optional SQL persistence. Single-process demo; production issuer processing must
# transact policy budget reservations atomically in SQL and consume Lithic events.
@app.on_event("startup")
def initialize_local_sql():
    from occlet_sql_store import enabled, initialize, load_runtime
    if not enabled():
        return
    initialize()  # fails startup rather than silently running with an empty database
    snapshot = load_runtime()
    with _lock:
        if snapshot:
            _restore_runtime(snapshot)
        else:
            from occlet_sql_store import get_active_member
            _member.clear(); _member.update(get_active_member() or {"id": "member_demo", "name": "Ann Gutierrez"})


def _restore_runtime(snapshot: dict | None, member: dict | None = None) -> None:
    """Load one member's saved state into the in-process cache. Caller holds _lock."""
    snapshot = snapshot or {}
    _parent.clear(); _parent.update(default_parent()); _parent.update(snapshot.get("parent", {}))
    for table, key in ((_agents, "agents"), (_cards, "cards"), (_intents, "intents"),
                       (_network_refs, "network_refs"), (_lithic_refs, "lithic_refs")):
        table.clear(); table.update(snapshot.get(key, {}))
    _nonces.clear(); _nonces.update(tuple(x) for x in snapshot.get("nonces", []))
    _events.clear(); _events.extend(snapshot.get("events", []))
    _auth_traces.clear(); _auth_traces.extend(snapshot.get("auth_traces", []))
    # Unsigned, expired-on-restart intents cannot be executed. The token is not persisted.
    for intent in _intents.values():
        if intent.get("status") in ("READY", "AWAITING_MEMBER_APPROVAL", "EXPIRED_ON_RESTART"):
            intent["status"] = "EXPIRED"
    _member.clear(); _member.update(member or snapshot.get("member") or {"id": "member_demo", "name": "Ann Gutierrez"})


def _persist_now() -> None:
    """Write the active member's state to SQL now. Caller holds _lock."""
    from occlet_sql_store import persist_runtime
    persist_runtime(parent=_parent, agents=_agents, cards=_cards, intents=_intents,
                    nonces=_nonces, network_refs=_network_refs, lithic_refs=_lithic_refs,
                    events=_events, auth_traces=_auth_traces, member=_member)


@app.middleware("http")
async def persist_local_demo_registry(request: Request, call_next):
    response = await call_next(request)
    if request.method != "POST" or response.status_code >= 400:
        return response
    from occlet_sql_store import enabled, persist_runtime
    if enabled():
        try:
            with _lock:
                persist_runtime(parent=_parent, agents=_agents, cards=_cards, intents=_intents,
                                nonces=_nonces, network_refs=_network_refs, lithic_refs=_lithic_refs,
                                events=_events, auth_traces=_auth_traces, member=_member)
        except Exception:
            # Never disclose SQL connection credentials, and never deliver an approval
            # if its audit state could not be persisted.
            return __import__('fastapi').responses.JSONResponse(
                status_code=503, content={"detail": "SQL registry persistence unavailable; request not confirmed"})
    return response


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/member")
def member_portal():
    """Local-only member-facing walkthrough, sharing the issuer demo's state."""
    return FileResponse(Path(__file__).parent / "static" / "beside.html", headers={"Cache-Control":"no-store"})


@app.get("/member/delegated")
@app.get("/member/delegated/setup")
def delegated_card_portal():
    """Direct route to the existing Beside design, with delegated-card view selected client-side."""
    return FileResponse(Path(__file__).parent / "static" / "beside.html", headers={"Cache-Control":"no-store"})


@app.get("/api/demo/version")
def demo_version():
    return {"edition":"beside-member-ledger-actions-v4", "member_portal":"/member", "delegated_card":"/member/delegated", "delegated_setup":"/member/delegated/setup"}


@app.get("/member/legacy")
def legacy_member_portal():
    """Previous engineering-oriented member portal remains available."""
    return FileResponse(Path(__file__).parent / "static" / "member.html")


@app.get("/beside")
def beside_portal():
    return FileResponse(Path(__file__).parent / "static" / "beside.html", headers={"Cache-Control":"no-store"})


@app.get("/member/assets/{filename}")
def member_assets(filename: str):
    if filename not in ("member.css", "member.js", "beside-delegated.css", "beside-delegated.js", "lab-members.js"):
        raise HTTPException(404, "Asset not found")
    return FileResponse(Path(__file__).parent / "static" / filename, headers={"Cache-Control":"no-store"})


@app.get("/api/state")
def get_state():
    with _lock:
        return state_view()


# Read-only consolidated member projection. Balances are NEVER derived from card budgets.
# This projection is local-demo only and inherits the app's localhost-only security boundary.
@app.get("/api/member/experience")
def member_experience():
    from occlet_demo_ledger import summary as sql_summary
    from occlet_w4 import overview as trip_overview
    from occlet_demo_ledger import LedgerRejected
    with _lock:
        view=state_view()
        parent=view["parent"]
        is_sql=parent.get("ledger_mode")=="OCCLET_SQL_LEDGER"
        book=sql_summary(parent["id"]) if is_sql else None
        try:
            protected=trip_overview(parent["id"]) if is_sql else None
        except LedgerRejected as exc:
            if exc.code!="W4_NOT_INITIALIZED":
                raise
            protected=None
        card_names={c["id"]: (next((a["name"] for a in view["agents"]
                        if a["agent_id"]==c["agent_id"]),c["alias"])) for c in view["cards"]}
        def card_label(card_ref):
            if card_ref in card_names:
                return card_names[card_ref]
            for c in view["cards"]:
                if card_ref==c.get("lithic_card_token"):
                    return card_names[c["id"]]
            if card_ref in ("PARENT_CARD","parent_demo_card",parent.get("lithic_parent_card_token")):
                return "Parent card"+(" •••• "+parent["parent_card_last_four"] if parent.get("parent_card_last_four") else "")
            return "Unmapped card (review)"
        transactions=[]
        ledger_refs=set()
        if book:
            for entry in book["authorizations"]:
                ref=entry["reference"]
                ledger_refs.add(ref)
                transactions.append({
                    "reference":ref,"card_id":entry["card_reference"],
                    "card_label":card_label(entry["card_reference"]),
                    "merchant_id":entry["merchant"],"amount_cents":entry["amount_cents"],
                    "settled_cents":entry["clearing_amount_cents"],"status":entry["status"],
                    "source":"OCCLET_SQL_LEDGER", "created_at":str(entry.get("created_at") or ""),
                    "can_request_release":entry["status"]=="AUTHORIZED"})
        # Only separate declines/pending attempts; SQL is the financial transaction authority.
        for trace in view["authorization_traces"]:
            match_ref=trace["network_reference"]
            if trace["source"]=="W4_LOCAL_PARENT_CARD":
                match_ref="w4-local:"+match_ref
            elif trace["source"]=="LOCAL_NETWORK_SIMULATOR":
                match_ref="local:"+match_ref
            if match_ref in ledger_refs and trace["decision"]=="APPROVED":
                continue
            transactions.append({
                "reference":trace["network_reference"],"card_id":trace["card_id"],
                "card_label": card_names.get(trace["card_id"],"Parent card" if trace["mode"]=="PARENT_CARD" else "Unmapped card (review)"),
                "merchant_id":trace["merchant_id"],"amount_cents":trace["amount_cents"],
                "settled_cents":None,"status":trace["decision"],"source":trace["source"],
                "reason":trace["reason"],"created_at":trace.get("at",""),
                "can_request_release":False})
        # Persist requests in existing audited demo runtime events. Do not treat the
        # member request as a reversal; that requires the processor/admin workflow.
        requests=[]; seen=set()
        reversed_refs={e["details"] for e in _events if e["action"]=="Occlet demo reversal" and e["result"]=="RELEASED"}
        for event in reversed(_events):
            if event["action"]!="Member hold release request":
                continue
            arrangement, _, ref=event["details"].partition("|")
            if arrangement!=parent["id"] or not ref or ref in seen:
                continue
            seen.add(ref)
            current=next((a for a in (book or {}).get("authorizations",[]) if a["reference"]==ref),None)
            req_status=("RELEASED" if ref in reversed_refs or current and current["status"]=="RELEASED"
                else "PENDING_REVIEW" if current and current["status"]=="AUTHORIZED" else "CLOSED")
            requests.append({"authorization_ref":ref,"status":req_status,
                             "card_label":card_label(current["card_reference"]) if current else "Unknown", "requested_at":event["at"]})
        intent_rows=[]
        for intent in view["intents"]:
            flow=intent.get("flow",FLOW_SIGNED)
            tx_ref=("lithic:"+intent["lithic_transaction_token"] if intent.get("lithic_transaction_token")
                    else "local:"+intent["network_reference"] if intent.get("network_reference") else None)
            intent_rows.append({"id":intent["id"],"card_id":intent["card_id"],
                "card_label":card_names.get(intent["card_id"],"Delegated card"),
                "agent_id":intent.get("agent_id"),"merchant_id":intent["merchant_id"],
                "amount_cents":intent["amount_cents"],"status":intent["status"],
                "kind":"MEMBER_APPROVAL_REQUEST" if flow==FLOW_CARD_BOUND else "SIGNED_AGENT_INTENT",
                "flow":flow,"member_approved":intent.get("member_approved"),
                "created_at":intent.get("created_at",""),"transaction_reference":tx_ref})
        intent_rows.sort(key=lambda r:r["created_at"] or "",reverse=True)
        pending=[]
        for intent in view["intents"]:
            if intent["status"]=="AWAITING_MEMBER_APPROVAL":
                pending.append({**intent, "card_label":card_names.get(intent["card_id"],"Delegated card"),
                                "expires_in_seconds":max(0, int(_intents[intent["id"]]["expires_at"]-time.time()))})
        return {
            "member":{"id":_member["id"],"name":_member["name"],"demo":True},
            "parent":{"arrangement_id":parent["id"],"status":parent["status"],
                "enabled":parent["enabled"],"ledger_mode":parent["ledger_mode"],
                "ledger_source":"OCCLET_POSTGRES_DEMO_LEDGER" if is_sql else "NOT_POSTGRES_LEDGER",
                "cash_cents":book["cash_cents"] if book else None,
                "available_cents":book["available_cents"] if book else None,
                "held_cents":book["held_cents"] if book else None,
                # Member "amount due" on the parent card: settled purchases + pending holds
                # across the shared arrangement (parent and delegated cards).
                "settled_cents":book.get("settled_cents",0) if book else None,
                "repaid_cents":book.get("repaid_cents",0) if book else None,
                "amount_due_cents":(max(0,book.get("settled_cents",0)-book.get("repaid_cents",0))+book["held_cents"]) if book else None,
                "spendable_after_protection_cents":protected["free_to_spend_cents"] if protected else (book["available_cents"] if book else None),
                "lithic_account_token":parent.get("lithic_account_token"),
                "parent_card_last_four":parent.get("parent_card_last_four"),
                "parent_card_linked":bool(parent.get("lithic_parent_card_token")),
                "note":"Local PostgreSQL demo balance only; not Lithic Ledger" if is_sql else "PostgreSQL financial ledger not initialized. No authoritative balance displayed."},
            "protected_trip":{"protected_cents":protected["protected_cents"],
                "yield_enabled":protected["yield_enabled"],"version":protected["version"]} if protected else None,
            "cards":view["cards"], "agents":view["agents"], "pending_approvals":pending,
            "intents":intent_rows[:60],
            "transactions":transactions[:80], "hold_release_requests":requests,
            "note":"Local demonstration. Member controls do not have real authentication; never expose port 8000."
        }


class MemberHoldReleaseRequest(BaseModel):
    authorization_ref: str = Field(min_length=4,max_length=180)

@app.post("/api/member/holds/request-release")
def request_member_hold_review(req: MemberHoldReleaseRequest):
    """Member asks for reversal review; never directly releases financial funds."""
    from occlet_demo_ledger import summary as sql_summary
    with _lock:
        if _parent.get("ledger_mode")!="OCCLET_SQL_LEDGER":
            raise HTTPException(409,"PostgreSQL demo ledger must be active")
        authorizations=sql_summary(_parent["id"])["authorizations"]
        a=next((r for r in authorizations if r["reference"]==req.authorization_ref),None)
        if not a or a["status"]!="AUTHORIZED":
            raise HTTPException(409,"Only active demo financial holds can be reviewed")
        detail=_parent["id"]+"|"+req.authorization_ref
        if not any(e["action"]=="Member hold release request" and e["details"]==detail for e in _events):
            log("Member hold release request","PENDING_REVIEW",detail)
        return {"status":"PENDING_REVIEW", "authorization_ref":req.authorization_ref,
                "message":"Requested review; no financial funds released. Admin/processor must confirm a reversal."}


@app.post("/api/demo/reset")
def reset():
    with _lock:
        _agents.clear(); _cards.clear(); _intents.clear(); _nonces.clear(); _network_refs.clear(); _lithic_refs.clear(); _events.clear(); _auth_traces.clear()
        _parent.clear(); _parent.update(default_parent())
        _member.clear(); _member.update({"id": "member_demo", "name": "Ann Gutierrez"})
        from occlet_sql_store import reset_sql_registry
        reset_sql_registry()
        log("Local demo", "RESET", "All test state removed; no real card credentials")
        return {"ok": True}




@app.post("/api/member/parent/configure")
def configure_parent(req: ParentConfiguration):
    with _lock:
        if _parent["holds"] or _parent.get("ledger_mode") == "OCCLET_SQL_LEDGER":
            fail(409, "Parent policy", "Ledger already initialized or auths exist. Reset demo before reconfiguring funding.")
        if req.max_transaction_cents > req.credit_limit_cents:
            fail(400, "Parent policy", "Transaction cap cannot exceed illustrated credit limit")
        _parent.update({"enabled": True, "status": "ACTIVE",
                        "credit_limit_cents": req.credit_limit_cents,
                        "max_transaction_cents": req.max_transaction_cents,
                        "monthly_policy_limit_cents": req.monthly_policy_limit_cents})
        log("Parent arrangement", "ENABLED", "Shared illustrative credit, inherited controls; no child funding")
        return state_view()["parent"]


@app.post("/api/member/parent/status")
def set_parent_status(body: ParentStatus):
    with _lock:
        if not _parent["enabled"]:
            fail(409, "Parent status", "Configure the parent arrangement first")
        _parent["status"] = body.status
        log("Parent account control", body.status, "Inherited by all agentic virtual cards")
        return {"status": body.status}


@app.post("/api/member/parent/lithic/create")
def issue_parent_test_card():
    from lithic_client import LithicError, create_test_card
    with _lock:
        if not _parent["enabled"]:
            fail(409, "Parent card", "Configure parent arrangement first")
        if _parent["lithic_parent_card_token"]:
            return {"card_token": _parent["lithic_parent_card_token"],
                    "account_token": _parent["lithic_account_token"], "created": False}
        try:
            card = create_test_card(account_token=_parent["lithic_account_token"],
                                    memo="OCCLET-PARENT-CARD-SANDBOX")
        except LithicError as exc:
            raise HTTPException(502, str(exc)) from None
        account = card.get("account_token")
        if not account or (_parent["lithic_account_token"] and account != _parent["lithic_account_token"]):
            fail(502, "Parent card", "Lithic parent card account token missing or mismatched")
        _parent["lithic_account_token"] = account
        _parent["lithic_parent_card_token"] = card["token"]
        _parent["parent_card_last_four"] = card["last_four"]
        log("Lithic funding relationship", "PARENT CARD CREATED", f"Shared Lithic account {account}; parent card {card['token']}")
        return {"created": True, "card_token": card["token"], "account_token": account,
                "last_four": card["last_four"]}


class ParentTestAuth(BaseModel):
    amount_cents: int = Field(gt=0, le=1000000)
    merchant_id: Literal["GROCERY_101", "ELECTRONICS_202", "TICKETS_303"]


@app.post("/api/member/parent/lithic/simulate")
def simulate_parent_transaction(body: ParentTestAuth):
    from lithic_client import LithicError, simulate_auth
    with _lock:
        token = _parent["lithic_parent_card_token"]
        if not token:
            fail(409, "Parent transaction", "Create parent Lithic sandbox card first")
    try:
        result = simulate_auth(token, body.amount_cents, body.merchant_id)
    except LithicError as exc:
        raise HTTPException(502, str(exc)) from None
    with _lock:
        log("Lithic parent card", "SUBMITTED", f"${body.amount_cents / 100:.2f} parent card authorization")
    return {"simulation": "submitted", "transaction_token": result.get("token")}

@app.post("/api/agents")
def register_agent(req: AgentRegistration):
    with _lock:
        if req.agent_id in _agents:
            fail(409, "Register agent", "Agent ID already registered")
        try:
            raw = base64.b64decode(req.public_key_b64, validate=True)
            Ed25519PublicKey.from_public_bytes(raw)
        except (ValueError, binascii.Error):
            fail(400, "Register agent", "Invalid Ed25519 public key")
        import hashlib
        fingerprint = hashlib.sha256(raw).hexdigest()[:16]
        policy_values = [req.registered_per_transaction_cents, req.registered_budget_cents,
                         req.registered_approval_threshold_cents]
        if req.purpose is not None:
            if not req.purchase_goal or not req.purchase_goal.strip() or any(x is None for x in policy_values):
                fail(400, "Agent registration", "Purpose, purchase goal, and all three member limits are required")
            if req.registered_approval_threshold_cents > req.registered_per_transaction_cents:
                fail(400, "Agent registration", "Auto-approval threshold cannot exceed transaction limit")
        elif any(x is not None for x in policy_values):
            fail(400, "Agent registration", "Purpose is required when registering spending limits")
        limits = ({"per_transaction_cents": req.registered_per_transaction_cents,
                   "budget_cents": req.registered_budget_cents,
                   "approval_threshold_cents": req.registered_approval_threshold_cents}
                  if req.purpose else None)
        _agents[req.agent_id] = {"agent_id": req.agent_id, "name": req.name,
                                 "public_key_b64": req.public_key_b64, "key_fingerprint": fingerprint,
                                 "purpose": req.purpose,
                                 "purchase_goal": req.purchase_goal.strip() if req.purchase_goal else None,
                                 "registered_limits": limits}
        log("Agent registration", "REGISTERED",
            f"{req.agent_id} • purpose={req.purpose or 'LEGACY_UNSCOPED'} • member-defined cap/budget/step-up; key {fingerprint}")
        return {"agent_id": req.agent_id, "key_fingerprint": fingerprint,
                "purpose": req.purpose, "purchase_goal": req.purchase_goal,
                "registered_limits": limits}


@app.post("/api/member/cards")
def issue_card(req: CardIssue):
    with _lock:
        agent = _agents.get(req.agent_id)
        if not agent:
            fail(404, "Issue virtual card", "Agent must be registered first")
        if req.approval_threshold_cents > req.per_transaction_cents:
            fail(400, "Issue virtual card", "Approval threshold cannot exceed per-transaction limit")
        allowed = sorted(set(req.allowed_merchants))
        if not allowed or any(m not in TEST_MERCHANTS for m in allowed):
            fail(400, "Issue virtual card", "Invalid test merchant")
        if agent.get("purpose"):
            if not _parent["enabled"]:
                fail(409, "Member delegation", "Configure the shared parent funding arrangement first")
            registered = agent["registered_limits"]
            if any(m not in PURPOSE_MERCHANTS[agent["purpose"]] for m in allowed):
                fail(403, "Member delegation", "Virtual card merchant must match purpose registered by member")
            if (req.per_transaction_cents > registered["per_transaction_cents"] or
                    req.budget_cents > registered["budget_cents"] or
                    req.approval_threshold_cents > registered["approval_threshold_cents"]):
                fail(403, "Member delegation", "Card cannot expand limits approved at agent registration")
        cid = "vc_" + secrets.token_hex(6)
        card = {"id": cid, "alias": "VIRTUAL-DEMO-" + cid[-6:].upper(),
                "agent_id": req.agent_id, "status": "ACTIVE",
                "authorization_flow": req.authorization_flow,
                "purpose": agent.get("purpose"), "purchase_goal": agent.get("purchase_goal"),
                "per_transaction_cents": req.per_transaction_cents,
                "budget_cents": req.budget_cents,
                "approval_threshold_cents": req.approval_threshold_cents,
                "allowed_merchants": allowed, "spent_cents": 0, "lithic_card_token": None,
                "parent_arrangement_id": _parent["id"] if _parent["enabled"] else None,
                "lithic_account_token": None}
        _cards[cid] = card
        log("Member delegation", "ACTIVE", f"{card['alias']} bound to {req.agent_id}; "
            f"{req.authorization_flow}; purpose={card['purpose']}; merchant + limits")
        return card


@app.post("/api/agent/intents")
def create_intent(req: AgentIntent):
    with _lock:
        ensure_expirations()
        agent = _agents.get(req.agent_id)
        if not agent:
            fail(401, "Agent identity", "Unknown agent")
        if abs(int(time.time() * 1000) - req.timestamp_ms) > 60_000:
            fail(401, "Agent authentication", "Stale signed request")
        canonical = (f"{req.agent_id}|{req.card_id}|{req.merchant_id}|{req.amount_cents}|"
                     f"{req.currency}|{req.nonce}|{req.timestamp_ms}").encode()
        try:
            signature = base64.b64decode(req.signature_b64, validate=True)
            public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(agent["public_key_b64"]))
            public_key.verify(signature, canonical)
        except (InvalidSignature, ValueError, binascii.Error):
            fail(401, "Agent authentication", "Invalid agent signature")
        if (req.agent_id, req.nonce) in _nonces:
            fail(409, "Replay prevention", "Nonce already used: signed request replay")
        _nonces.add((req.agent_id, req.nonce))
        log("Agent authentication", "VERIFIED", f"{req.agent_id}: valid Ed25519 signature; unique nonce")
        card = _cards.get(req.card_id)
        if not card or card["agent_id"] != req.agent_id or card["status"] != "ACTIVE":
            fail(403, "Delegation policy", "Card unavailable or not delegated to this agent")
        if card.get("authorization_flow", FLOW_SIGNED) != FLOW_SIGNED:
            fail(403, "Delegation policy", "This card is configured for network-direct authorization, not signed-intent flow")
        if req.merchant_id not in card["allowed_merchants"]:
            fail(403, "Delegation policy", f"Merchant {req.merchant_id} is not allowed")
        if req.amount_cents > card["per_transaction_cents"]:
            fail(403, "Delegation policy", "Amount exceeds per-transaction limit")
        parent_reason = parent_policy(req.amount_cents, req.merchant_id)
        if parent_reason:
            fail(403, "Inherited parent policy", parent_reason)
        if req.amount_cents > card["budget_cents"] - card["spent_cents"] - reserved_for(card["id"]):
            fail(403, "Delegation policy", "Amount exceeds remaining unreserved budget")
        # External Capital One demo has an independent, fail-closed threshold policy.
        # A loose member UI configuration must not accidentally expand the agent's scope.
        if card.get("capitalone_reference") and req.amount_cents > 2500:
            fail(403, "Capital One agent policy", "Amount exceeds $25.00 external checkout limit")
        need_approval = (req.amount_cents > card["approval_threshold_cents"]
                         or bool(card.get("capitalone_reference") and req.amount_cents >= 2400))
        intent_id = "pi_" + secrets.token_hex(7)
        intent = {"id": intent_id, "card_id": card["id"], "agent_id": req.agent_id,
                  "merchant_id": req.merchant_id, "amount_cents": req.amount_cents,
                  "status": "AWAITING_MEMBER_APPROVAL" if need_approval else "READY",
                  "payment_token": "pt_" + secrets.token_urlsafe(22),
                  "created_at": datetime.now(timezone.utc).isoformat(),
                  "expires_at": time.time() + INTENT_TTL_SECONDS}
        _intents[intent_id] = intent
        log("Delegation policy", "STEP-UP REQUIRED" if need_approval else "APPROVED",
            f"{intent_id}: ${req.amount_cents / 100:.2f} at {req.merchant_id}; budget reserved")
        result = {"intent_id": intent_id, "status": intent["status"], "expires_in_seconds": INTENT_TTL_SECONDS}
        if not need_approval:
            result["payment_token"] = intent["payment_token"]
        return result


@app.post("/api/member/intents/{intent_id}/approval")
def approve(intent_id: str, req: Approval):
    with _lock:
        ensure_expirations()
        intent = _intents.get(intent_id)
        if not intent or intent["status"] != "AWAITING_MEMBER_APPROVAL":
            fail(409, "Member approval", "No pending member approval for this intent")
        card = _cards[intent["card_id"]]
        if card["status"] != "ACTIVE":
            fail(403, "Member approval", "Card has been revoked")
        reason = parent_policy(intent["amount_cents"], intent["merchant_id"])
        if reason:
            fail(403, "Parent policy on member approval", reason)
        card_bound = intent.get("flow") == FLOW_CARD_BOUND
        intent["status"] = ("RETRYING" if card_bound else "READY") if req.approved else "REJECTED"
        intent["member_approved"] = bool(req.approved)
        log("Member approval", "APPROVED" if req.approved else "REJECTED", intent_id)
        result = {"intent_id": intent_id, "status": intent["status"]}
        if req.approved and not card_bound:
            result["payment_token"] = intent["payment_token"]
        retry = req.approved and card_bound
        origin = intent.get("origin", "LOCAL")
        card_id, merchant, amount = intent["card_id"], intent["merchant_id"], intent["amount_cents"]
    if retry:
        # Flow B has no agent waiting on a token: the member's approval re-sends the same
        # purchase once, on the route it came from. The retry consumes the approval.
        result["retry"] = retry_card_bound_purchase(intent_id, card_id, merchant, amount, origin)
        with _lock:
            result["status"] = _intents[intent_id]["status"]
    return result


def retry_card_bound_purchase(intent_id: str, card_id: str, merchant: str, amount: int, origin: str) -> dict:
    if origin == "LITHIC":
        try:
            sim = lithic_simulate(LithicTestAuth(card_id=card_id, merchant_id=merchant, amount_cents=amount))
            return {"route": "LITHIC", "transaction_token": sim.get("transaction_token")}
        except HTTPException as exc:
            with _lock:
                if _intents[intent_id]["status"] == "RETRYING":
                    _intents[intent_id]["status"] = "READY"
                log("Member approval", "RETRY FAILED", f"{intent_id}: Lithic simulation not sent: {exc.detail}")
            return {"route": "LITHIC", "error": str(exc.detail)}
    r = network_direct_authorize(NetworkDirectAuth(card_reference=card_id, merchant_id=merchant, amount_cents=amount,
                                                    network_reference="approved-" + intent_id))
    return {"route": "LOCAL", "decision": r["decision"], "reason": r.get("reason")}


@app.post("/api/agent/intents/{intent_id}/status")
def agent_intent_status(intent_id: str, req: AgentIntentStatus):
    """The agent polls its own intent with a signed request (canonical: status|intent_id|timestamp_ms).
    The one-time payment token is returned only to the agent, only once the member has approved."""
    with _lock:
        ensure_expirations()
        agent = _agents.get(req.agent_id)
        intent = _intents.get(intent_id)
        if not agent or not intent or intent["agent_id"] != req.agent_id:
            fail(404, "Agent intent status", "Unknown intent for this agent")
        if abs(int(time.time() * 1000) - req.timestamp_ms) > 60_000:
            fail(401, "Agent authentication", "Stale signed request")
        try:
            Ed25519PublicKey.from_public_bytes(base64.b64decode(agent["public_key_b64"])).verify(
                base64.b64decode(req.signature_b64, validate=True), f"status|{intent_id}|{req.timestamp_ms}".encode())
        except (InvalidSignature, ValueError, binascii.Error):
            fail(401, "Agent authentication", "Invalid agent signature")
        out = {"intent_id": intent_id, "status": intent["status"], "member_approved": intent.get("member_approved")}
        if intent["status"] == "READY":
            out["payment_token"] = intent["payment_token"]
        return out


@app.get("/api/member/approvals/{approval_id}")
def approval_status(approval_id: str):
    """Status only (never a payment token): used to retry a card-bound purchase after approval."""
    with _lock:
        ensure_expirations()
        intent = _intents.get(approval_id)
        if not intent:
            raise HTTPException(404, "Unknown approval request")
        return {"id": approval_id, "status": intent["status"], "member_approved": intent.get("member_approved"),
                "flow": intent.get("flow", FLOW_SIGNED), "origin": intent.get("origin"),
                "lithic_transaction_token": intent.get("lithic_transaction_token"),
                "network_reference": intent.get("network_reference")}


@app.post("/api/network/authorize")
def network_authorize(req: NetworkAuth):
    with _lock:
        if _parent.get("ledger_mode") == "LITHIC_LEDGER":
            raise HTTPException(409, "Use Lithic sandbox ASA for financial authorization in LITHIC_LEDGER mode")
        ensure_expirations()
        # Repeated network reference behaves idempotently; a new reference with
        # an already-used one-time handle is rejected.
        if req.network_reference in _network_refs:
            old = _network_refs[req.network_reference]
            if old.get("payment_token") == req.payment_token and old["merchant_id"] == req.merchant_id and old["amount_cents"] == req.amount_cents:
                return old["response"]
            fail(409, "Network correlation", "Network reference reused with different transaction attributes")
        intent = next((i for i in _intents.values() if i.get("payment_token") == req.payment_token), None)
        if not intent:
            fail(403, "Network authorization", "Unknown one-time credential handle")
        card = _cards[intent["card_id"]]
        if card.get("authorization_flow", FLOW_SIGNED) != FLOW_SIGNED:
            fail(403, "Network authorization", "Wrong flow for this payment handle")
        if card["status"] != "ACTIVE":
            fail(403, "Network authorization", "Virtual card revoked")
        if intent["status"] != "READY":
            fail(403, "Network authorization", f"Intent is {intent['status']}, not READY")
        if intent["merchant_id"] != req.merchant_id or intent["amount_cents"] != req.amount_cents:
            fail(403, "Network correlation", "Network message does not match approved merchant and amount")
        # This is a simulated issuer authorization, not a network call or settlement.
        reason = parent_policy(req.amount_cents, req.merchant_id)
        if reason:
            fail(403, "Inherited parent policy", reason)
        if card["spent_cents"] + req.amount_cents > card["budget_cents"]:
            fail(403, "Network authorization", "Insufficient remaining budget")
        from occlet_demo_ledger import LedgerRejected
        try:
            reserve_parent_once("local:" + req.network_reference, req.amount_cents,
                                card_reference=card["id"], merchant_id=req.merchant_id)
        except LedgerRejected as exc:
            fail(403, "Local financial authorization", exc.code)
        intent["status"] = "AUTHORIZED"
        intent["network_reference"] = req.network_reference
        card["spent_cents"] += req.amount_cents
        response = {"decision": "APPROVED", "authorization_id": "auth_" + secrets.token_hex(6),
                    "matched_intent_id": intent["id"], "card_alias": card["alias"],
                    "network_reference": req.network_reference}
        _network_refs[req.network_reference] = {"payment_token": req.payment_token,
            "merchant_id": req.merchant_id, "amount_cents": req.amount_cents, "response": response}
        add_auth_trace(source="LOCAL_NETWORK_SIMULATOR", mode=FLOW_SIGNED, decision="APPROVED",
                       card=card, merchant=req.merchant_id, amount=req.amount_cents,
                       network_reference=req.network_reference, intent_id=intent["id"])
        log("Simulated issuer authorization", "APPROVED",
            f"{req.network_reference} -> {intent['id']}; ${req.amount_cents / 100:.2f} deducted from demo budget")
        return response


@app.post("/api/network/direct/authorize")
def network_direct_authorize(req: NetworkDirectAuth):
    """Local network-only test. No agent signature or prior payment intent is sent.

    Real Lithic testing should use /api/lithic/simulate and the verified ASA webhook;
    this endpoint demonstrates equivalent decisions offline. Never expose publicly.
    """
    with _lock:
        if _parent.get("ledger_mode") == "LITHIC_LEDGER":
            raise HTTPException(409, "Use Lithic sandbox ASA for financial authorization in LITHIC_LEDGER mode")
        ensure_expirations()
        card = next((c for c in _cards.values()
                     if req.card_reference in (c["id"], c.get("lithic_card_token")) and req.card_reference), None)
        if req.network_reference in _network_refs:
            old = _network_refs[req.network_reference]
            if (old.get("flow") == FLOW_CARD_BOUND and old["card_reference"] == req.card_reference
                    and old["merchant_id"] == req.merchant_id and old["amount_cents"] == req.amount_cents):
                return old["response"]
            fail(409, "Network authorization", "Network reference already used for a different transaction")

        consumed = approval_request = None
        if card is None:
            reason = "CARD_NOT_REGISTERED"
        elif card.get("authorization_flow", FLOW_SIGNED) != FLOW_CARD_BOUND:
            reason = "SIGNED_INTENT_REQUIRED"
        else:
            reason, consumed, approval_request = card_bound_decision(card, req.merchant_id, req.amount_cents)

        if reason:
            response = {"decision": "DECLINED", "reason": reason,
                        "approval_request_id": approval_request if card else None,
                        "attribution": "CARD_BINDING_ONLY" if card else "NONE",
                        "agent_id": card["agent_id"] if card else None,
                        "network_reference": req.network_reference}
        else:
            from occlet_demo_ledger import LedgerRejected
            try:
                reserve_parent_once("local:" + req.network_reference, req.amount_cents,
                                    card_reference=card["id"], merchant_id=req.merchant_id)
            except LedgerRejected as exc:
                reason = exc.code
                if consumed:
                    consumed["status"] = "READY"
                response = {"decision": "DECLINED", "reason": reason,
                            "attribution": "CARD_BINDING_ONLY", "agent_id":card["agent_id"],
                            "network_reference":req.network_reference}
            else:
                card["spent_cents"] += req.amount_cents
                if consumed:
                    consumed["network_reference"] = req.network_reference
                response = {"decision": "APPROVED", "reason": "POLICY_AND_FUNDING_OK",
                        "attribution": "CARD_BINDING_ONLY", "agent_id": card["agent_id"],
                        "network_reference": req.network_reference,
                        "parent_arrangement_id": card.get("parent_arrangement_id")}
        _network_refs[req.network_reference] = {"flow": FLOW_CARD_BOUND,
            "card_reference": req.card_reference, "merchant_id": req.merchant_id,
            "amount_cents": req.amount_cents, "response": response}
        add_auth_trace(source="LOCAL_NETWORK_SIMULATOR", mode=FLOW_CARD_BOUND,
                       decision=response["decision"], card=card, merchant=req.merchant_id,
                       amount=req.amount_cents, network_reference=req.network_reference, reason=reason)
        log("Network-direct authorization", response["decision"],
            f"{req.network_reference}: inferred {response['agent_id'] or 'unknown'} from card binding; "
            f"NO agent signature or intent; {reason or 'policy passed'}")
        return response


@app.post("/api/member/cards/{card_id}/revoke")
def revoke(card_id: str):
    with _lock:
        card = _cards.get(card_id)
        if not card:
            fail(404, "Revoke delegation", "Unknown test card")
        card["status"] = "REVOKED"
        for intent in _intents.values():
            if intent["card_id"] == card_id and intent["status"] in ("READY", "AWAITING_MEMBER_APPROVAL"):
                intent["status"] = "REVOKED"
        card["removed_at"] = datetime.now(timezone.utc).isoformat()
        lithic_state = card.get("lithic_state")
        if card.get("lithic_card_token") and lithic_state != "CLOSED":
            from lithic_client import lithic_request, LithicError
            try:
                lithic_request("PATCH", f"/cards/{card['lithic_card_token']}", payload={"state": "CLOSED"})
                card["lithic_state"] = lithic_state = "CLOSED"
            except LithicError as exc:
                card["lithic_state"] = lithic_state = "CLOSE_FAILED"
                log("Lithic sandbox", "CLOSE FAILED", f"{card['alias']}: {exc}; Occlet still declines it (REVOKED)")
        log("Member revocation", "REVOKED", f"{card['alias']}; all pending payment handles invalidated"
            + (f"; Lithic card {lithic_state}" if lithic_state else ""))
        return {"status": "REVOKED", "lithic_state": lithic_state, "card_id": card_id}


# Lithic sandbox integration. The local member UI is NOT authenticated; keep
# the entire app bound to loopback and expose only lithic_bridge:app separately.

class LithicTestAuth(BaseModel):
    card_id: str
    amount_cents: int = Field(gt=0, le=1000000)
    merchant_id: Literal["GROCERY_101", "ELECTRONICS_202", "TICKETS_303"]


class InternalASARequest(BaseModel):
    webhook_id: str
    authorization: dict


@app.post("/api/member/cards/{card_id}/lithic/create")
def lithic_issue_test_card(card_id: str):
    from lithic_client import LithicError, create_test_card
    with _lock:
        card = _cards.get(card_id)
        if not card or card["status"] != "ACTIVE":
            fail(404, "Lithic card creation", "Demo card must be active")
        if card["lithic_card_token"]:
            return {"card_token": card["lithic_card_token"], "created": False}
        try:
            if _parent["enabled"] and not _parent["lithic_parent_card_token"]:
                fail(409, "Lithic card", "Create parent Lithic card before child card to share its account")
            test = create_test_card(account_token=_parent["lithic_account_token"] if _parent["enabled"] else None,
                                    memo="OCCLET-AGENT-CARD-SANDBOX")
        except LithicError as e:
            raise HTTPException(502, str(e)) from None
        if _parent["enabled"] and test.get("account_token") != _parent["lithic_account_token"]:
            fail(502, "Lithic account mapping", "Card was not placed in the parent Lithic account; refusing link")
        card["lithic_card_token"] = test["token"]
        card["lithic_account_token"] = test.get("account_token")
        card["lithic_last_four"] = test.get("last_four")
        card["alias"] = "LITHIC-" + str(test.get("last_four") or card["id"][-4:])
        log("Lithic sandbox", "CARD CREATED", f"Linked Lithic test card ending {test['last_four']} to {card['alias']}")
        return {"card_token": test["token"], "account_token": test.get("account_token"),
                "last_four": test["last_four"], "created": True}


@app.get("/api/lithic/rules/preview")
def preview_lithic_auth_rules(card_id: str):
    from lithic_native_rules import build_rules
    with _lock:
        card = _cards.get(card_id)
        if not _parent["enabled"] or not _parent["lithic_account_token"]:
            fail(409, "Native rules", "Enable parent and create Lithic parent card first")
        if not card or not card["lithic_card_token"]:
            fail(409, "Native rules", "Create linked Lithic agent card first")
        rules = build_rules(_parent["lithic_account_token"], card["lithic_card_token"],
                            parent_max_cents=_parent["max_transaction_cents"],
                            agent_max_cents=card["per_transaction_cents"],
                            account_monthly_cents=_parent["monthly_policy_limit_cents"],
                            agent_monthly_cents=card["budget_cents"])
        return {"rules": rules, "state": "PREVIEW ONLY", "note": "Lithic executes active native rules BEFORE Occlet ASA."}


@app.post("/api/lithic/rules/create-drafts")
def create_lithic_drafts(card_id: str):
    from lithic_native_rules import create_drafts
    # This operation writes INACTIVE shadow drafts to Lithic Sandbox only.
    preview = preview_lithic_auth_rules(card_id)
    try:
        created = create_drafts(preview["rules"])
    except Exception as exc:
        from lithic_client import LithicError
        if isinstance(exc, LithicError):
            raise HTTPException(502, str(exc)) from None
        raise
    with _lock:
        log("Lithic native authorization rules", "DRAFT CREATED", f"{len(created)} inactive rules in sandbox, NOT promoted")
    return {"drafts": created, "active": False,
            "note": "Drafts run in shadow mode. Review in Lithic Dashboard and promote individually if desired."}


@app.post("/api/lithic/simulate")
def lithic_simulate(req: LithicTestAuth):
    from lithic_client import LithicError, simulate_auth
    with _lock:
        card = _cards.get(req.card_id)
        if not card or not card.get("lithic_card_token"):
            fail(404, "Lithic simulation", "Issue Lithic sandbox card for this demo card first")
        token = card["lithic_card_token"]
    try:
        result = simulate_auth(token, req.amount_cents, req.merchant_id)
    except LithicError as e:
        raise HTTPException(502, str(e)) from None
    # This response only confirms that the simulator request was accepted;
    # the ASA outcome is shown in the separate callback event log.
    with _lock:
        log("Lithic sandbox", "SIMULATED", f"Submitted {req.merchant_id} ${req.amount_cents / 100:.2f}; transaction={result.get('token', 'n/a')}")
    return {"simulation": "submitted", "transaction_token": result.get("token"),
            "note": "Check audit events and Lithic Transactions for the final authorization decision."}


@app.get("/api/lithic/transactions/{transaction_token}")
def lithic_transaction_result(transaction_token: str):
    from lithic_client import get_transaction, LithicError
    try:
        result = get_transaction(transaction_token)
    except LithicError as exc:
        raise HTTPException(502, str(exc)) from None
    with _lock:
        allowed = {c["lithic_card_token"] for c in _cards.values() if c.get("lithic_card_token")}
        if _parent["lithic_parent_card_token"]:
            allowed.add(_parent["lithic_parent_card_token"])
        if result.get("card_token") not in allowed:
            fail(404, "Lithic transaction", "Transaction is not associated with a demo card")
        log("Lithic transaction result", result.get("result") or "UNKNOWN",
            f"{transaction_token}: {result.get('detailed_results') or []}")
    return result


def get_lithic_authorization_details(payload: dict) -> tuple[str, str, int, str, str] | None:
    """Extract the specific ASA fields needed by the demo; fail closed on unknown shapes."""
    if payload.get("event_type") != "card_authorization.approval_request":
        return None
    if payload.get("status") != "AUTHORIZATION":
        return None
    token = payload.get("token")
    card = payload.get("card") or {}
    merchant = payload.get("merchant") or {}
    if not isinstance(card, dict) or not isinstance(merchant, dict):
        return None
    card_token = card.get("token")
    acceptor = merchant.get("acceptor_id")
    merchant_alias = {"GROCERY101": "GROCERY_101", "ELECTRONICS202": "ELECTRONICS_202",
                      "TICKETS303": "TICKETS_303"}.get(acceptor)
    amounts = payload.get("amounts") or {}
    cardholder = amounts.get("cardholder") if isinstance(amounts, dict) else None
    if isinstance(cardholder, dict):
        amount = cardholder.get("amount")
        currency = cardholder.get("currency")
    else:
        # Lithic still includes the deprecated but documented authorization fields.
        amount = payload.get("authorization_amount")
        currency = payload.get("cardholder_currency")
    if not token or not card_token or not merchant_alias or not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0 or currency != "USD":
        return None
    return token, card_token, amount, merchant_alias, acceptor


def decide_lithic_asa(payload: dict) -> dict:
    """Demo policy decision, under _lock. Lithic performs own pre-ASA rules first.

    SIGNED_INTENT_REQUIRED requires an independently signed, ready local intent.
    CARD_BOUND_NETWORK deliberately demonstrates a WEAKER assumption: the
    credential identifies the registered agent via one-to-one card mapping.
    The parent card represents cardholder-origin sandbox spending, without an agent.
    No card is funded independently. Shared parent hold is recorded once per txn.
    """
    ensure_expirations()
    data = get_lithic_authorization_details(payload)
    if not data:
        log("Lithic ASA", "DECLINED", "Unsupported/unrecognized ASA transaction or currency")
        return {"result": "UNAUTHORIZED_MERCHANT"}
    tx, card_token, amount, merchant, acceptor = data
    old = _lithic_refs.get(tx)
    if old:
        if old["card_token"] == card_token and old["amount"] == amount and old["merchant"] == merchant:
            return {"result": old["result"]}
        return {"result": "UNAUTHORIZED_MERCHANT"}

    parent_card = _parent["enabled"] and card_token == _parent["lithic_parent_card_token"]
    card = next((c for c in _cards.values() if c.get("lithic_card_token") == card_token), None)
    reason = None
    layers = None
    if not card and not parent_card:
        reason = "UNKNOWN_CARD"
    else:
        reason = parent_policy(amount, merchant)
    result = "UNAUTHORIZED_MERCHANT"
    if reason:
        result = ("INSUFFICIENT_FUNDS" if reason == "PARENT_AVAILABLE_CREDIT" else
                  "VELOCITY_EXCEEDED" if "LIMIT" in reason else
                  "CARD_PAUSED" if reason == "PARENT_ACCOUNT_PAUSED" else "UNAUTHORIZED_MERCHANT")
    elif parent_card and (layers := w4_rule_layers(amount, merchant)) and layers["decided_by"] == "LEVEL_2":
        reason = layers["reason"]  # Level 2 can only tighten
        result = "UNAUTHORIZED_MERCHANT"
    elif parent_card:
        result = "APPROVED"
        from occlet_demo_ledger import LedgerRejected
        try:
            reserve_parent_once("lithic:" + tx, amount, card_reference="parent_demo_card", merchant_id=merchant)
        except LedgerRejected as exc:
            reason = exc.code
            result = ledger_decline_code(exc)
        if result == "APPROVED":
            log("Lithic ASA parent card", result, f"{tx}: ${amount / 100:.2f}; one shared funding arrangement")
    elif card is None:
        result = "UNAUTHORIZED_MERCHANT"
    elif card.get("authorization_flow", FLOW_SIGNED) == FLOW_CARD_BOUND:
        reason, consumed, _ = card_bound_decision(card, merchant, amount, "LITHIC")
        if reason:
            result = ("INSUFFICIENT_FUNDS" if reason == "PARENT_AVAILABLE_CREDIT"
                      else "VELOCITY_EXCEEDED" if "LIMIT" in reason or "BUDGET" in reason
                      else "CARD_PAUSED" if "PAUSED" in reason or "REVOKED" in reason
                      else "UNAUTHORIZED_MERCHANT")
        else:
            from occlet_demo_ledger import LedgerRejected
            try:
                reserve_parent_once("lithic:" + tx, amount, card_reference=card["id"], merchant_id=merchant)
            except LedgerRejected as exc:
                reason = exc.code
                result = ledger_decline_code(exc)
                if consumed:
                    consumed["status"] = "READY"
            else:
                card["spent_cents"] += amount
                result = "APPROVED"
                if consumed:
                    consumed["lithic_transaction_token"] = tx
                log("Lithic ASA network-only", result,
                    f"{tx}: card token maps to {card['agent_id']} • {card.get('purpose')}; "
                    f"NO signed agent intent; ONE parent hold" + ("; member pre-approved" if consumed else ""))
    elif (scope_reason := card_scope_reason(card, merchant, amount)):
        reason = scope_reason
        result = ("INSUFFICIENT_FUNDS" if reason == "PARENT_AVAILABLE_CREDIT"
                  else "VELOCITY_EXCEEDED" if "LIMIT" in reason or "BUDGET" in reason
                  else "CARD_PAUSED" if "PAUSED" in reason or "REVOKED" in reason
                  else "UNAUTHORIZED_MERCHANT")
    elif card.get("authorization_flow", FLOW_SIGNED) == FLOW_CARD_BOUND:
        if amount > card["approval_threshold_cents"]:
            # No pre-purchase intent or member step-up exists in this mode.
            # A network callback cannot wait for asynchronous member approval.
            reason = "MEMBER_APPROVAL_REQUIRED_BEFORE_PURCHASE"
            result = "UNAUTHORIZED_MERCHANT"
        else:
            from occlet_demo_ledger import LedgerRejected
            try:
                reserve_parent_once("lithic:" + tx, amount, card_reference=card["id"], merchant_id=merchant)
            except LedgerRejected as exc:
                reason = exc.code
                result = ledger_decline_code(exc)
            else:
                card["spent_cents"] += amount
                result = "APPROVED"
                log("Lithic ASA network-only", result,
                f"{tx}: card token maps to {card['agent_id']} • {card.get('purpose')}; "
                f"NO signed agent intent; ONE parent hold")
    else:
        matches = [i for i in _intents.values() if i["card_id"] == card["id"]
                   and i["merchant_id"] == merchant and i["amount_cents"] == amount
                   and i["status"] == "READY"]
        if len(matches) != 1:
            reason = "NO_UNAMBIGUOUS_APPROVED_INTENT"
        elif card["spent_cents"] + amount > card["budget_cents"]:
            reason = "AGENT_BUDGET_EXCEEDED"
            result = "VELOCITY_EXCEEDED"
        else:
            intent = matches[0]
            from occlet_demo_ledger import LedgerRejected
            try:
                reserve_parent_once("lithic:" + tx, amount, card_reference=card["id"], merchant_id=merchant)
            except LedgerRejected as exc:
                reason = exc.code
                result = ledger_decline_code(exc)
            else:
                intent["status"] = "AUTHORIZED"
                intent["lithic_transaction_token"] = tx
                card["spent_cents"] += amount  # policy utilization only, NOT a second financial hold
                result = "APPROVED"
                log("Lithic ASA agent card", result, f"{tx} matched {intent['id']} ({merchant}, ${amount / 100:.2f}); one parent hold")
    mode = (card.get("authorization_flow", FLOW_SIGNED) if card else "PARENT_CARD" if parent_card else "UNKNOWN")
    matched_intent = (intent["id"] if result == "APPROVED" and mode == FLOW_SIGNED and not parent_card else None)
    _lithic_refs[tx] = {"card_token": card_token, "amount": amount,
                        "merchant": merchant, "result": result,
                        "mode": mode, "matched_intent": matched_intent, "reason": reason}
    if parent_card and layers is None:
        layers = w4_rule_layers(amount, merchant)  # parent policy declined before layers ran
    layers = finalize_layers(layers, result, reason) if parent_card else None
    add_auth_trace(source="LITHIC_SANDBOX_ASA", mode=mode, decision=result,
                   card=card, merchant=merchant, amount=amount,
                   network_reference=tx, intent_id=matched_intent, reason=reason, layers=layers)
    if result != "APPROVED":
        log("Lithic ASA", "DECLINED", f"{tx}: {reason or result}; merchant={acceptor}; amount=${amount / 100:.2f}")
    return {"result": result}


@app.post("/api/internal/lithic/authorize")
def internal_lithic_authorize(body: InternalASARequest, request: Request):
    secret = os.environ.get("OCCLET_BRIDGE_SECRET", "")
    remote = request.client.host if request.client else ""
    supplied = request.headers.get("X-Occlet-Bridge-Secret", "")
    allowed_client = remote in ("127.0.0.1", "::1") or (remote == "testclient" and os.environ.get("OCCLET_ALLOW_TESTCLIENT") == "1")
    if not allowed_client or not secret or not hmac.compare_digest(secret, supplied):
        raise HTTPException(403, "Private ASA bridge access only")
    with _lock:
        return decide_lithic_asa(body.authorization)


# -------- W4: member's own card vs protected future trip (yield OFF) --------
class W4ReleaseRequest(BaseModel):
    release_cents: int = Field(gt=0, le=30000)
    release_ref: str = Field(min_length=5, max_length=120)

class W4PurchaseRequest(BaseModel):
    amount_cents: int = Field(gt=0, le=50000)
    reference: str = Field(min_length=8, max_length=120)

@app.post("/api/use-cases/w4/setup")
def setup_w4():
    """Explicit new scenario: single funded parent arrangement, $300 policy protection."""
    from occlet_sql_store import enabled
    from occlet_demo_ledger import initialize, LedgerRejected
    from occlet_w4 import seed_trip
    with _lock:
        if not enabled():
            raise HTTPException(409,"Start PostgreSQL and configure DATABASE_URL")
        if _parent.get('ledger_mode')=='LITHIC_LEDGER':
            raise HTTPException(409,"Do not create a second ledger while Lithic Ledger mode is active")
        # The button explicitly starts a NEW, isolated sandbox scenario. Preserve
        # the previously linked Lithic parent card, but do not reuse old auth refs.
        linked={k:_parent.get(k) for k in
          ('lithic_account_token','lithic_parent_card_token','parent_card_last_four')}
        _agents.clear(); _cards.clear(); _intents.clear(); _nonces.clear()
        _network_refs.clear(); _lithic_refs.clear(); _auth_traces.clear()
        _parent.clear(); _parent.update(default_parent())
        _parent.update({'id':'arr_w4_'+uuid.uuid4().hex[:20],
          'enabled':True,'status':'ACTIVE','credit_limit_cents':50000,
          'max_transaction_cents':50000,'monthly_policy_limit_cents':50000,**linked})
        try:
            initialize(_parent['id'],50000,_parent)
            result=seed_trip(_parent['id'],30000)
        except LedgerRejected as exc:
            raise HTTPException(409,exc.code) from None
        _parent['ledger_mode']='OCCLET_SQL_LEDGER'
        log('W4 setup','READY','One $500 parent SQL book; $300 protected trip; yield disabled; no financial earmark')
        return result

@app.get("/api/use-cases/w4/status")
def w4_status():
    from occlet_w4 import overview
    from occlet_demo_ledger import LedgerRejected
    from occlet_lithic_events import event_history
    with _lock:
        try:
            r=overview(_parent['id'])
        except LedgerRejected as exc:
            raise HTTPException(409,exc.code) from None
        r['lithic_parent_linked']=bool(_parent.get('lithic_parent_card_token'))
        r['lithic_parent_card_last_four']=_parent.get('parent_card_last_four')
        r['lithic_events']=event_history(_parent['id'])
        r['latest_layers']=next((dict(t['layers'],source=t['source'],at=t['at'],network_reference=t['network_reference'])
                                 for t in reversed(_auth_traces) if t.get('layers')),None)
        return r

@app.post("/api/use-cases/w4/release")
def w4_member_release(req: W4ReleaseRequest):
    from occlet_w4 import release_protection
    from occlet_demo_ledger import LedgerRejected
    with _lock:
        if _parent.get('ledger_mode')!='OCCLET_SQL_LEDGER':
            raise HTTPException(409,'W4 requires the Occlet SQL demo ledger')
        try:
            r=release_protection(_parent['id'],req.release_cents,req.release_ref)
        except LedgerRejected as exc:
            raise HTTPException(409,exc.code) from None
        log('W4 member permission','RELEASED',
            f"{req.release_cents} cents of trip protection released; NO financial journal created")
        return r

@app.post("/api/use-cases/w4/authorize")
def w4_simulated_parent_purchase(req: W4PurchaseRequest):
    """Local parent-card authorization, same SQL path invoked by Lithic ASA."""
    from occlet_demo_ledger import LedgerRejected
    from occlet_w4 import overview
    with _lock:
        if _parent.get('ledger_mode')!='OCCLET_SQL_LEDGER':
            raise HTTPException(409,'Initialize the W4 scenario first')
        layers=w4_rule_layers(req.amount_cents,'GROCERY_101')
        reason=parent_policy(req.amount_cents,'GROCERY_101')
        if not reason and layers and layers['decided_by']=='LEVEL_2':
            reason=layers['reason']  # Level 2 can only tighten
        if not reason:
            try:
                reserve_parent_once('w4-local:'+req.reference,req.amount_cents,
                    card_reference='parent_demo_card',merchant_id='GROCERY_101')
            except LedgerRejected as exc:
                reason=exc.code
        decision='DECLINED' if reason else 'APPROVED'
        layers=finalize_layers(layers,decision,reason)
        add_auth_trace(source='W4_LOCAL_PARENT_CARD',mode='PARENT_CARD',
            decision=decision,card=None,merchant='GROCERY_101',amount=req.amount_cents,
            network_reference=req.reference,reason=reason,layers=layers)
        log('W4 parent card auth',decision,
            f"{req.reference}: {reason or 'one SQL hold; protection remains policy-only'}")
        r=overview(_parent['id'])
        return {'decision':decision,'reason':reason or 'POLICY_AND_FUNDING_OK',
                'authorization_ref':('w4-local:'+req.reference if not reason else None),
                'layers':layers,'w4':r}

class InternalLithicEvent(BaseModel):
    webhook_id: str = Field(min_length=5,max_length=180)
    event: dict

@app.post('/api/internal/lithic/event')
def internal_lithic_event(body: InternalLithicEvent,request:Request):
    """ONLY called by signed-webhook verifier on loopback (port 8001)."""
    from occlet_lithic_events import normalize_event,process_signed_event
    from occlet_demo_ledger import LedgerRejected
    secret=os.environ.get('OCCLET_BRIDGE_SECRET','')
    supplied=request.headers.get('X-Occlet-Bridge-Secret','')
    remote=request.client.host if request.client else ''
    allowed=remote in ('127.0.0.1','::1') or (remote=='testclient' and os.environ.get('OCCLET_ALLOW_TESTCLIENT')=='1')
    if not allowed or not secret or not hmac.compare_digest(secret,supplied):
        raise HTTPException(403,'Private event bridge access only')
    with _lock:
        if _parent.get('ledger_mode')!='OCCLET_SQL_LEDGER':
            raise HTTPException(409,'Occlet SQL financial ledger is not active')
        try:
            normalized=normalize_event(body.event)
            tx=normalized['transaction_token']
            asa=_lithic_refs.get(tx)
            if normalized['status']=='DECLINED' and (asa is None or asa.get('result')!='APPROVED'):
                # A declined purchase never placed a hold: acknowledge it so Lithic stops
                # retrying, with no ledger effect. Unknown PENDING/SETTLED still quarantine.
                log('Lithic transaction event','ACKNOWLEDGED',f"{body.webhook_id}: {tx} DECLINED; no hold, no ledger effect")
                return {'status':'IGNORED','action':'NONE','idempotent':True,'transaction_token':tx}
            result=process_signed_event(arrangement_id=_parent['id'],
                event_id=body.webhook_id,envelope=body.event,
                asa_reference=asa,expected_account=_parent.get('lithic_account_token'))
        except LedgerRejected as exc:
            log('Lithic transaction event','QUARANTINED',str(exc))
            raise HTTPException(409,exc.code) from None
        if result['status']=='PROCESSED' and result['action'] in ('CLEAR','RELEASE') and asa:
            # Card budgets are policy utilization, not financial balances.
            for c in _cards.values():
                if c.get('lithic_card_token')==asa['card_token']:
                    from occlet_demo_ledger import summary
                    a=next((a for a in summary(_parent['id'])['authorizations']
                            if a['reference']=='lithic:'+tx),None)
                    if a:
                        reduce_by=(a['amount_cents'] if result['action']=='RELEASE' else
                            a['amount_cents']-(a['clearing_amount_cents'] or 0))
                        if not result.get('idempotent'):
                            c['spent_cents']=max(0,c['spent_cents']-reduce_by)
                    break
        log('Lithic transaction event',result['action'],
            f"{body.webhook_id}: {tx}; ledger effect idempotent={result['idempotent']}")
        return result

# -------- Occlet Postgres DEMO Ledger (not Lithic Ledger) --------
class DemoLedgerInit(BaseModel):
    opening_balance_cents: int = Field(gt=0, le=100_000_000)

class DemoLedgerAdjustment(BaseModel):
    authorization_ref: str = Field(min_length=3,max_length=180)

class DemoLedgerClear(DemoLedgerAdjustment):
    settled_cents: int = Field(gt=0, le=100_000_000)

class DemoLedgerTopUp(BaseModel):
    amount_cents: int = Field(gt=0, le=100_000_000)
    reference: str = Field(min_length=4, max_length=100)

@app.post("/api/demo-ledger/initialize")
def initialize_demo_ledger(req: DemoLedgerInit):
    from occlet_demo_ledger import initialize, LedgerRejected
    from occlet_sql_store import enabled
    with _lock:
        if not enabled():
            raise HTTPException(409, "Start PostgreSQL and set DATABASE_URL")
        if not _parent["enabled"]:
            raise HTTPException(409, "Configure the parent funding arrangement first")
        if _parent["ledger_mode"] == "LITHIC_LEDGER":
            raise HTTPException(409, "Cannot create a second financial ledger while Lithic Ledger mode is active")
        if _parent["holds"] or _lithic_refs or _network_refs:
            raise HTTPException(409, "Reset demo to initialize SQL financial balances with no prior authorizations")
        try:
            result = initialize(_parent["id"], req.opening_balance_cents, _parent)
        except LedgerRejected as exc:
            raise HTTPException(409, exc.code) from None
        _parent["ledger_mode"] = "OCCLET_SQL_LEDGER"
        _parent["credit_limit_cents"] = req.opening_balance_cents
        log("Occlet demo ledger", "INITIALIZED", "One funded parent SQL book. All Lithic test cards remain instruments only.")
        return result

@app.get("/api/demo-ledger/balance")
def demo_ledger_balance():
    from occlet_demo_ledger import summary, LedgerRejected
    with _lock:
        if _parent.get("ledger_mode") != "OCCLET_SQL_LEDGER":
            raise HTTPException(409, "Initialize Occlet PostgreSQL demo ledger first")
        try:
            return summary(_parent["id"])
        except LedgerRejected as exc:
            raise HTTPException(409, exc.code) from None

@app.post("/api/demo-ledger/clear")
def demo_ledger_clear(req: DemoLedgerClear):
    from occlet_demo_ledger import clear, LedgerRejected
    with _lock:
        if _parent.get("ledger_mode") != "OCCLET_SQL_LEDGER":
            raise HTTPException(409, "Occlet demo ledger is not active")
        try:
            result = clear(_parent["id"],req.authorization_ref,req.settled_cents)
        except LedgerRejected as exc:
            raise HTTPException(409, exc.code) from None
        if not result.get("idempotent"):
            auth = result.get("authorization")
            if auth and auth["card_reference"] in _cards:
                c = _cards[auth["card_reference"]]
                c["spent_cents"] = max(0, c["spent_cents"] - (auth["amount_cents"] - req.settled_cents))
        log("Occlet demo clearing", "CLEARED", f"{req.authorization_ref} settled at {req.settled_cents} cents")
        return result

@app.post("/api/demo-ledger/release")
def demo_ledger_release(req: DemoLedgerAdjustment):
    from occlet_demo_ledger import release, LedgerRejected
    with _lock:
        if _parent.get("ledger_mode") != "OCCLET_SQL_LEDGER":
            raise HTTPException(409, "Occlet demo ledger is not active")
        try:
            result = release(_parent["id"],req.authorization_ref)
        except LedgerRejected as exc:
            raise HTTPException(409, exc.code) from None
        if not result.get("idempotent"):
            auth = result.get("authorization")
            if auth and auth["card_reference"] in _cards:
                c = _cards[auth["card_reference"]]
                c["spent_cents"] = max(0, c["spent_cents"] - auth["amount_cents"])
            for intent in _intents.values():
                if intent.get('lithic_transaction_token') and req.authorization_ref == 'lithic:'+intent['lithic_transaction_token']:
                    intent['status'] = 'RELEASED'
        log("Occlet demo reversal", "RELEASED", req.authorization_ref)
        return result

@app.post("/api/demo-ledger/topup")
def demo_ledger_topup(req: DemoLedgerTopUp):
    from occlet_demo_ledger import topup, LedgerRejected
    with _lock:
        if _parent.get("ledger_mode") != "OCCLET_SQL_LEDGER":
            raise HTTPException(409, "Occlet demo ledger is not active")
        try:
            result = topup(_parent["id"],req.amount_cents,req.reference)
        except LedgerRejected as exc:
            raise HTTPException(409, exc.code) from None
        log("Occlet synthetic funding", "TOPUP", f"{req.reference}: {req.amount_cents} cents added to demo book")
        return result

class MemberFunding(BaseModel):
    kind: Literal["TOP_UP", "REPAYMENT", "RESTORE"]
    amount_cents: int | None = Field(default=None, gt=0, le=100_000_000)
    target_available_cents: int | None = Field(default=None, gt=0, le=100_000_000)
    reference: str = Field(min_length=4, max_length=100)


@app.post("/api/member/funding")
def member_funding(req: MemberFunding):
    """Synthetic money in for the active member's parent card.

    TOP_UP adds funds; REPAYMENT pays down the settled amount due (default: all of it);
    RESTORE brings the available balance up to a target, repaying first, then adding funds.
    """
    from occlet_demo_ledger import fund, summary, LedgerRejected
    m = lambda c: f"${c / 100:,.2f}"
    with _lock:
        if _parent.get("ledger_mode") != "OCCLET_SQL_LEDGER":
            raise HTTPException(409, "Set up the Occlet PostgreSQL ledger for this member first")
        arr = _parent["id"]
        book = summary(arr)
        outstanding = max(0, book.get("settled_cents", 0) - book.get("repaid_cents", 0))
        if req.kind == "TOP_UP":
            if not req.amount_cents:
                raise HTTPException(400, "Enter an amount to add")
            plan = [("TOP_UP", req.amount_cents)]
        elif req.kind == "REPAYMENT":
            amount = req.amount_cents or outstanding
            if not amount:
                raise HTTPException(409, "Nothing to repay: there is no settled amount due")
            plan = [("REPAYMENT", amount)]
        else:
            if not req.target_available_cents:
                raise HTTPException(400, "Enter the target available balance")
            needed = req.target_available_cents - book["available_cents"]
            if needed <= 0:
                raise HTTPException(409, f"Available is already {m(book['available_cents'])}")
            repay = min(needed, outstanding)
            plan = [(k, a) for k, a in (("REPAYMENT", repay), ("TOP_UP", needed - repay)) if a > 0]
        done = []
        try:
            for kind, amount in plan:
                fund(arr, amount, f"{req.reference}:{kind.lower()}", kind)
                done.append({"kind": kind, "amount_cents": amount})
        except LedgerRejected as exc:
            raise HTTPException(409, exc.code) from None
        after = summary(arr)
        log("Member funding", "+".join(d["kind"] for d in done),
            ", ".join(f"{d['kind'].lower()} {m(d['amount_cents'])}" for d in done)
            + f"; available now {m(after['available_cents'])} (synthetic demo money)")
        return {"entries": done, "available_cents": after["available_cents"], "cash_cents": after["cash_cents"],
                "held_cents": after["held_cents"], "settled_cents": after.get("settled_cents", 0),
                "repaid_cents": after.get("repaid_cents", 0),
                "amount_due_cents": max(0, after.get("settled_cents", 0) - after.get("repaid_cents", 0)) + after["held_cents"]}


# Link an ALREADY-ISSUED Lithic sandbox parent card without creating another card.
class ExistingLithicParent(BaseModel):
    account_token: uuid.UUID
    card_token: uuid.UUID

class LabMemberCreate(BaseModel):
    display_name: str = Field(min_length=2, max_length=80)
    opening_balance_cents: int = Field(default=50000, gt=0, le=10_000_000)
    trip_protection_cents: int = Field(default=30000, ge=0, le=10_000_000)
    lithic_parent: Literal["NONE", "CREATE", "LINK"] = "NONE"
    account_token: uuid.UUID | None = None
    card_token: uuid.UUID | None = None


def _member_summary(member: dict, snap: dict | None, active: bool) -> dict:
    snap = snap or {}
    p, cards = snap.get("parent") or {}, snap.get("cards") or {}
    live = [c for c in cards.values() if c.get("status") == "ACTIVE"]
    return {"id": member["id"], "name": member["name"], "active": active,
            "arrangement_id": p.get("id") if p.get("enabled") else None,
            "ledger_mode": p.get("ledger_mode"), "parent_card_linked": bool(p.get("lithic_parent_card_token")),
            "parent_card_last_four": p.get("parent_card_last_four"),
            "active_cards": len(live), "lithic_cards": sum(1 for c in live if c.get("lithic_card_token")),
            "removed_cards": sum(1 for c in cards.values() if c.get("status") == "REVOKED")}


def _require_sql():
    from occlet_sql_store import enabled
    if not enabled():
        raise HTTPException(409, "Start PostgreSQL and configure DATABASE_URL")


@app.get("/api/lab/members")
def lab_members():
    from occlet_sql_store import list_members
    _require_sql()
    with _lock:
        out = []
        for r in list_members():
            live = r["id"] == _member["id"]
            out.append(_member_summary(r, {"parent": _parent, "cards": _cards} if live else r["snapshot"], live))
        if not any(m["active"] for m in out):
            out.insert(0, _member_summary(_member, {"parent": _parent, "cards": _cards}, True))
        return {"active_member_id": _member["id"], "members": out}


def _switch_member(member: dict, snapshot: dict | None) -> None:
    """Save the outgoing member, load the incoming one. Caller holds _lock."""
    from occlet_sql_store import set_active_member
    _persist_now()                   # outgoing member's saved copy is now current
    _restore_runtime(snapshot, member)
    _persist_now()                   # incoming member becomes the live runtime copy
    set_active_member(member["id"])


@app.post("/api/lab/members/{member_id}/activate")
def lab_activate_member(member_id: str):
    from occlet_sql_store import list_members
    _require_sql()
    with _lock:
        if member_id != _member["id"]:
            rows = {r["id"]: r for r in list_members()}
            if member_id not in rows:
                raise HTTPException(404, "Unknown test member")
            _switch_member({"id": member_id, "name": rows[member_id]["name"]}, rows[member_id]["snapshot"])
            log("Lab test member", "ACTIVE", f"Switched to {_member['name']} ({member_id})")
        return lab_members()


@app.post("/api/lab/members")
def lab_create_member(req: LabMemberCreate):
    """One step: new member + funding arrangement + Occlet ledger (+ W4 trip) (+ Lithic parent card)."""
    from occlet_sql_store import create_member
    from occlet_demo_ledger import initialize, LedgerRejected
    from occlet_w4 import seed_trip
    from lithic_client import LithicError, create_test_card, lithic_request
    _require_sql()
    if req.lithic_parent == "LINK" and not (req.account_token and req.card_token):
        raise HTTPException(400, "Linking needs both the Lithic account token and card token")
    if req.trip_protection_cents > req.opening_balance_cents:
        raise HTTPException(400, "Trip protection cannot exceed the opening balance")
    with _lock:
        member = {"id": "mbr_" + secrets.token_hex(4), "name": req.display_name.strip()}
        create_member(member)
        _switch_member(member, None)
        _parent.update({"id": "arr_" + member["id"] + "_" + uuid.uuid4().hex[:12], "enabled": True,
                        "status": "ACTIVE", "credit_limit_cents": req.opening_balance_cents,
                        "max_transaction_cents": req.opening_balance_cents,
                        "monthly_policy_limit_cents": req.opening_balance_cents})
        try:
            initialize(_parent["id"], req.opening_balance_cents, _parent)
            if req.trip_protection_cents:
                seed_trip(_parent["id"], req.trip_protection_cents)
        except LedgerRejected as exc:
            raise HTTPException(409, exc.code) from None
        _parent["ledger_mode"] = "OCCLET_SQL_LEDGER"
        warning = None
        try:
            if req.lithic_parent == "CREATE":
                card = create_test_card(memo="OCCLET-PARENT " + member["name"][:40])
                _parent.update(lithic_account_token=card.get("account_token"),
                               lithic_parent_card_token=card["token"], parent_card_last_four=card["last_four"])
            elif req.lithic_parent == "LINK":
                found = lithic_request("GET", f"/cards/{req.card_token}")
                if (str(found.get("account_token")) != str(req.account_token)
                        or found.get("state") not in ("OPEN", "PAUSED")):
                    raise LithicError("Lithic card/account mismatch or card not open")
                _parent.update(lithic_account_token=str(req.account_token),
                               lithic_parent_card_token=str(req.card_token),
                               parent_card_last_four=found.get("last_four"))
        except LithicError as exc:
            warning = f"Member created without a Lithic parent card: {exc}"
        log("Lab test member", "CREATED",
            f"{member['name']} ({member['id']}); ${req.opening_balance_cents / 100:,.2f} ledger; "
            f"trip ${req.trip_protection_cents / 100:,.2f}; Lithic parent "
            + (_parent.get("parent_card_last_four") or "not linked"))
        _persist_now()
        return {"member": member, "warning": warning, **lab_members()}


@app.get("/api/member/parent/lithic/cards")
def list_lithic_parent_candidates():
    """Sandbox cards the member can pick as the parent card. Tokens only; PAN/CVV dropped."""
    from lithic_client import lithic_request, LithicError
    try:
        result = lithic_request("GET", "/cards?page_size=100")
    except LithicError as exc:
        raise HTTPException(502, str(exc)) from None
    cards = []
    for c in result.get("data") or []:
        if c.get("state") not in ("OPEN", "PAUSED") or not c.get("token") or not c.get("account_token"):
            continue
        cards.append({"card_token": c["token"], "account_token": c["account_token"],
                      "last_four": c.get("last_four"), "state": c.get("state"),
                      "type": c.get("type"), "memo": c.get("memo"), "created": c.get("created")})
    with _lock:
        linked = {c["lithic_card_token"] for c in _cards.values() if c.get("lithic_card_token")}
        parent_token = _parent.get("lithic_parent_card_token")
    return {"cards": [c for c in cards if c["card_token"] not in linked],
            "linked_parent_card_token": parent_token}


@app.post("/api/member/parent/lithic/link-existing")
def link_existing_parent_card(req: ExistingLithicParent):
    from lithic_client import lithic_request, LithicError
    with _lock:
        if not _parent["enabled"]:
            raise HTTPException(409, "Configure the parent arrangement first")
        if _parent.get("lithic_parent_card_token") and _parent["lithic_parent_card_token"] != str(req.card_token):
            raise HTTPException(409, "A different parent card is already linked; reset demo to replace")
    try:
        result = lithic_request("GET", f"/cards/{req.card_token}")
    except LithicError as exc:
        raise HTTPException(502, str(exc)) from None
    if (str(result.get("account_token"))!=str(req.account_token)
            or str(result.get("token"))!=str(req.card_token)
            or result.get("state") not in ("OPEN","PAUSED")):
        raise HTTPException(409,"Lithic card/account mismatch or unavailable card")
    with _lock:
        if _parent.get("lithic_account_token") not in (None, str(req.account_token)):
            raise HTTPException(409,"Account doesn't match existing parent")
        _parent["lithic_account_token"] = str(req.account_token)
        _parent["lithic_parent_card_token"] = str(req.card_token)
        _parent["parent_card_last_four"] = result.get("last_four")
        log("Lithic sandbox", "PARENT LINKED", "Existing sandbox parent card verified; PAN/CVV discarded")
    return {"linked":True,"account_token":str(req.account_token),"card_token":str(req.card_token),
            "last_four":result.get("last_four")}

# -------- Lithic-issued financial account: authoritative only if provisioned --------
class LedgerLink(BaseModel):
    financial_account_token: uuid.UUID


@app.get("/api/sql/registry")
def sql_registry():
    from occlet_sql_store import read_registry, enabled
    if not enabled():
        raise HTTPException(409, "Local PostgreSQL is not configured; set DATABASE_URL")
    try:
        return {"source":"OCCLET_POSTGRES_ARRANGEMENT_REGISTRY", "tables":read_registry()}
    except Exception:
        raise HTTPException(503, "Unable to read local arrangement registry") from None


@app.get("/api/sql/status")
def sql_status():
    from occlet_sql_store import health
    return {**health(), "system_of_record": "OCCLET_ARRANGEMENTS_ONLY",
            "finance_ledger": "LITHIC_IF_PROGRAM_PROVISIONED"}


@app.get("/api/lithic/ledger/discover")
def lithic_ledger_discover():
    from lithic_client import LithicError, lithic_request
    with _lock:
        account_token = _parent.get("lithic_account_token")
    if not account_token:
        raise HTTPException(409, "Create parent Lithic sandbox card before discovery")
    try:
        data = lithic_request("GET", f"/financial_accounts?account_token={uuid.UUID(account_token)}")
    except LithicError as exc:
        # A failed API call is not evidence that the sandbox lacks Ledger.
        # Surface the sanitized HTTP category so the user can diagnose permissions.
        raise HTTPException(exc.status_code if exc.status_code in (400, 401, 403, 404, 422, 429) else 502,
                            f"Lithic financial account lookup failed: {exc}") from None
    except ValueError:
        raise HTTPException(400, "Invalid parent Lithic account token") from None
    issuing = [dict(token=x.get('token'),type=x.get('type'),status=x.get('status'),
                    account_token=x.get('account_token'))
               for x in data.get('data', []) if x.get('type') == 'ISSUING'
               and x.get('account_token') == account_token]
    return {"lithic_account_token": account_token, "issuing_financial_accounts": issuing,
            "ledger_provisioned": bool(issuing),
            "note": "An ordinary Lithic account need not have an ISSUING Financial Account; Ledger requires program provisioning."}


@app.get("/api/lithic/ledger/diagnose")
def lithic_ledger_diagnose():
    """Read-only sandbox checks. Return status/counts, never Lithic account data or secrets."""
    from lithic_client import LithicError, lithic_request
    with _lock:
        token = _parent.get('lithic_account_token')
    if not token:
        raise HTTPException(409, "Create the parent Lithic test card first")
    try:
        account_token = str(uuid.UUID(token))
    except (ValueError, TypeError):
        raise HTTPException(400, "Invalid parent Lithic account token") from None

    def probe(path: str) -> dict:
        try:
            response = lithic_request('GET', path)
            if not isinstance(response, dict):
                return {'status': 'INVALID_RESPONSE'}
            rows = response.get('data', [])
            if not isinstance(rows, list):
                return {'status': 'INVALID_RESPONSE'}
            return {
                'status': 'OK',
                'count': len(rows),
                'issuing_count': sum(1 for x in rows if isinstance(x, dict) and x.get('type') == 'ISSUING'),
                'has_more': bool(response.get('has_more')),
            }
        except LithicError as exc:
            return {'status': exc.category, 'http_status': exc.status_code,
                    'message': str(exc)}

    account = probe(f'/financial_accounts?account_token={account_token}')
    program = probe('/financial_accounts')
    if account['status'] != 'OK':
        interpretation = ('The lookup failed; this is NOT proof that Ledger is unprovisioned. '
                          'Use the reported HTTP status to distinguish credentials, access, and connectivity.')
    elif account['issuing_count']:
        interpretation = 'The parent account has at least one ISSUING Financial Account.'
    else:
        interpretation = ('The request succeeded but found no ISSUING Financial Account for this parent. '
                          'It may have been created in a card-only program or without Ledger onboarding.')
    return {
        'parent_account_set': True,
        'parent_financial_accounts': account,
        'program_financial_accounts': program,
        'interpretation': interpretation,
        'credentials_exposed': False,
    }


@app.post("/api/lithic/ledger/link")
def link_issuing_financial_account(body: LedgerLink):
    # Independently verify that the requested ledger account is the ISSUING account
    # owned by the SAME parent account. Never trust an arbitrary client-supplied ID.
    listing = lithic_ledger_discover()
    match = next((x for x in listing['issuing_financial_accounts']
                  if x['token'] == str(body.financial_account_token) and x['status'] == 'OPEN'), None)
    if not match:
        raise HTTPException(409, "ISSUING Financial Account not found/open for this parent's Lithic account")
    with _lock:
        if _parent.get("ledger_mode") == "LITHIC_LEDGER" and _parent.get("lithic_financial_account_token") != match['token']:
            raise HTTPException(409, "Cannot change linked financial account while live ledger mode is active")
        _parent['lithic_financial_account_token'] = match['token']
        log("Lithic financial account", "LINKED", "ISSUING financial account verified; read-only until ledger mode activated")
    return {"financial_account_token": match['token'], "mode": _parent['ledger_mode']}


@app.get("/api/lithic/ledger/balance")
def lithic_authoritative_balance():
    from lithic_client import LithicError, lithic_request
    from occlet_sql_store import save_balance_observation
    with _lock:
        token = _parent.get('lithic_financial_account_token')
        arrangement_id = _parent['id']
    if not token:
        raise HTTPException(409, "Link an ISSUING financial account first")
    try:
        data = lithic_request('GET', f'/financial_accounts/{uuid.UUID(token)}/balances')
    except LithicError as exc:
        raise HTTPException(exc.status_code if exc.status_code in (400, 401, 403, 404, 422, 429) else 502,
                            f"Lithic balance request failed: {exc}") from None
    except ValueError:
        raise HTTPException(400, "Invalid linked financial account token") from None
    balance = data.get('data', [None])[0] if isinstance(data.get('data'), list) else data
    if (not isinstance(balance, dict) or str(balance.get('token')) != token or
        balance.get('type') != 'ISSUING' or not all(isinstance(balance.get(k), int)
            for k in ('available_amount','pending_amount','total_amount'))):
        raise HTTPException(502, "Lithic response did not contain a valid ISSUING balance")
    safe_balance = {k: balance.get(k) for k in ('type','token','currency','available_amount',
                        'pending_amount','total_amount','updated','last_transaction_token')}
    save_balance_observation(arrangement_id, safe_balance)
    with _lock:
        _parent['last_lithic_available_cents'] = safe_balance['available_amount']
    return {"source": "LITHIC_SANDBOX_LEDGER", "authoritative_at_source": True,
            "balance": safe_balance, "note": "Last-observed balance; not a local ledger. Credit balance fields have different semantics than prepaid."}


@app.post("/api/lithic/ledger/activate")
def activate_lithic_ledger_mode():
    if os.environ.get('LITHIC_AUTH_FROM_BALANCE_CONFIRMED') != '1':
        raise HTTPException(409, "Confirm with Lithic that your sandbox program has authorization-from-balance enabled; then set LITHIC_AUTH_FROM_BALANCE_CONFIRMED=1")
    with _lock:
        if _parent['holds'] or _lithic_refs or _parent.get('ledger_mode') == 'OCCLET_SQL_LEDGER':
            raise HTTPException(409, "Reset before switching financial authority from Occlet SQL ledger to Lithic")
        if not _parent.get('lithic_financial_account_token'):
            raise HTTPException(409, "Link and verify the issuing financial account first")
    # Fresh read ensures linked financial account is available.
    observation = lithic_authoritative_balance()
    with _lock:
        _parent['ledger_mode'] = 'LITHIC_LEDGER'
        log('Financial authority', 'LITHIC_LEDGER', 'Lithic ledger owns balances, holds, settlement. Occlet owns delegated policies only.')
    return {"mode":"LITHIC_LEDGER", "balance":observation['balance'],
            "note":"ASA does not query balances. Lithic must run its own pre-ASA authorization-from-balance checks."}


# ------------ Capital One consumer virtual-card checkout HANDOFF ONLY ------------
# These endpoints never contact Capital One, collect PAN/CVV, or approve a real
# network authorization. They gate this local agent's access to a merchant URL.
# No monetary authorization is implied by granting a checkout link.
REAL_MERCHANT_DOMAINS = {
    "GROCERY_101": ("target.com", "www.target.com"),
    "ELECTRONICS_202": ("bestbuy.com", "www.bestbuy.com"),
}


class ExternalCardBinding(BaseModel):
    merchant_id: Literal["GROCERY_101", "ELECTRONICS_202"]
    checkout_url: str = Field(min_length=12, max_length=1600)
    last_four: str = Field(pattern=r"^[0-9]{4}$")


class ExternalCheckoutRequest(BaseModel):
    intent_id: str
    payment_token: str


@app.post("/api/member/cards/{card_id}/capitalone/link")
def bind_capital_one_reference(card_id: str, binding: ExternalCardBinding):
    # In this local prototype, only the member UI can call this endpoint.
    # It is intentionally un-authenticated: never expose port 8000 outside localhost.
    u = urlsplit(binding.checkout_url)
    if (u.scheme != "https" or u.hostname not in REAL_MERCHANT_DOMAINS[binding.merchant_id]
            or u.username or u.password or u.port or u.fragment):
        fail(400, "Merchant registration", "Use an HTTPS checkout/store URL on the merchant's approved domain")
    with _lock:
        card = _cards.get(card_id)
        if not card or card["status"] != "ACTIVE":
            fail(404, "Merchant registration", "No active delegated card found")
        if binding.merchant_id not in card["allowed_merchants"]:
            fail(403, "Merchant registration", "Merchant not permitted in this member delegation")
        card["capitalone_reference"] = {
            "issuer": "Capital One (external checkout only)",
            "last_four": binding.last_four,
            "merchant_id": binding.merchant_id,
            "checkout_url": binding.checkout_url,
        }
        log("External card binding", "LINKED", f"Capital One ****{binding.last_four}; {u.hostname}; no PAN stored")
        return {"status": "LINKED", "card_id": card_id, "last_four": binding.last_four,
                "merchant_domain": u.hostname, "card_control": "OCCLET_PREFLIGHT_ONLY"}


@app.post("/api/external/checkout/handoff")
def external_checkout_handoff(req: ExternalCheckoutRequest):
    with _lock:
        ensure_expirations()
        intent = _intents.get(req.intent_id)
        if not intent:
            fail(404, "External checkout", "Unknown payment intent")
        card = _cards.get(intent["card_id"])
        binding = card.get("capitalone_reference") if card else None
        if (not card or card["status"] != "ACTIVE" or not binding
                or intent["status"] != "READY" or time.time() > intent["expires_at"]
                or intent["amount_cents"] > 2500
                or (intent["amount_cents"] >= 2400 and not intent.get("member_approved", False))
                or not hmac.compare_digest(req.payment_token, intent["payment_token"])
                or binding["merchant_id"] != intent["merchant_id"]
                or intent.get("external_handoff_issued", False)):
            fail(403, "External checkout", "Checkout gate blocked: unapproved, expired, revoked, wrong credential, or previously used")
        # Demo policy deliberately defined as < $24 auto-approve, $24..$25 step-up,
        # and > $25 decline; UI issues the member delegation accordingly.
        intent["external_handoff_issued"] = True
        log("Capital One checkout handoff", "OCCLET RELEASED",
            f"{req.intent_id}: ${intent['amount_cents']/100:.2f}; merchant {binding['merchant_id']}; external issuer NOT contacted")
        return {"decision": "OCCLET_GATE_APPROVED", "checkout_url": binding["checkout_url"],
                "intent_id": req.intent_id, "amount_cents": intent["amount_cents"],
                "card_last_four": binding["last_four"], "issuer_authorized": False,
                "note": "Handoff only: complete merchant checkout manually with Capital One virtual card; no issuer approval was performed."}
