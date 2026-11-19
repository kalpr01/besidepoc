"""Lithic V2 Auth Rules: PREVIEW by default; explicit draft creation only.

Native rules can enforce amount, account/card velocity and merchant constraints.
Member-agent identity, consent and payment intent verification stay in Occlet ASA.

No production URLs, no cardholder data, no automatic promotion to ACTIVE.
"""
from __future__ import annotations

import argparse
import json
from lithic_client import lithic_request


def build_rules(account_token: str, agent_card_token: str, *, parent_max_cents: int,
                agent_max_cents: int, account_monthly_cents: int,
                agent_monthly_cents: int) -> list[dict]:
    if not account_token or not agent_card_token:
        raise ValueError("An actual Lithic sandbox account token and agent card token are required")
    if min(parent_max_cents, agent_max_cents, account_monthly_cents, agent_monthly_cents) <= 0:
        raise ValueError("Limits must be positive")

    def conditional(name: str, entity: dict, max_amount: int) -> dict:
        return {
            "name": name, "type": "CONDITIONAL_ACTION", "event_stream": "AUTHORIZATION",
            "entities": {"include": [entity]},
            "parameters": {"action": "DECLINE", "conditions": [{
                "attribute": "TRANSACTION_AMOUNT", "operation": "IS_GREATER_THAN",
                "value": max_amount}]},
        }

    def velocity(name: str, entity: dict, scope: str, max_amount: int) -> dict:
        return {
            "name": name, "type": "VELOCITY_LIMIT", "event_stream": "AUTHORIZATION",
            "entities": {"include": [entity]},
            "parameters": {"scope": scope, "period": {"type": "MONTH"},
                           "limit_amount": max_amount, "limit_count": None},
        }
    acct = {"type": "ACCOUNT", "token": account_token}
    card = {"type": "CARD", "token": agent_card_token}
    return [
        conditional("Occlet demo | Parent max purchase", acct, parent_max_cents),
        velocity("Occlet demo | Shared account monthly", acct, "ACCOUNT", account_monthly_cents),
        conditional("Occlet demo | Agent card purchase ceiling", card, agent_max_cents),
        velocity("Occlet demo | Agent card monthly", card, "CARD", agent_monthly_cents),
    ]


def create_drafts(rules: list[dict]) -> list[dict]:
    """Actual sandbox API write, but drafts ONLY (shadow mode). Never auto-promote."""
    created = []
    for rule in rules:
        response = lithic_request("POST", "/v2/auth_rules", payload=rule)
        created.append({"name": rule["name"], "token": response.get("token"),
                        "state": response.get("state"),
                        "draft": response.get("draft_version") is not None})
    return created


def main():
    parser = argparse.ArgumentParser(description="Occlet Lithic sandbox V2 Auth Rules")
    parser.add_argument("command", choices=["preview", "create-drafts"])
    parser.add_argument("--account-token", required=True)
    parser.add_argument("--card-token", required=True)
    parser.add_argument("--parent-max", type=int, default=100000, help="cents: default $1000")
    parser.add_argument("--agent-max", type=int, default=100000, help="cents: default $1000")
    parser.add_argument("--account-monthly", type=int, default=500000, help="cents: default $5000")
    parser.add_argument("--agent-monthly", type=int, default=20000, help="cents: default $200")
    args = parser.parse_args()
    payloads = build_rules(args.account_token, args.card_token,
                           parent_max_cents=args.parent_max, agent_max_cents=args.agent_max,
                           account_monthly_cents=args.account_monthly,
                           agent_monthly_cents=args.agent_monthly)
    if args.command == "preview":
        print(json.dumps(payloads, indent=2))
    else:
        print("Creating INACTIVE draft rules in Lithic Sandbox (shadow mode). NOT promoting.")
        print(json.dumps(create_drafts(payloads), indent=2))


if __name__ == "__main__":
    main()
