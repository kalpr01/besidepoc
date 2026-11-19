# Occlet arrangement model (SQL / Lithic split)

```text
members (member_id)
   └── funding_arrangements (arrangement_id, product_code, funding_mode)
       ├── Lithic account_token (member's cardholder account)
       ├── Lithic ISSUING financial_account_token (financial ledger, if enabled)
       │        └── balances, holds, financial transactions, clearing — Lithic ONLY
       ├── payment_instruments [PARENT]
       └── payment_instruments [AGENT_VIRTUAL]
                    │
agent_identities ────┼── delegation_grants (agent + virtual card + policy)
                    │            ├── merchant_allowlist, purpose, per-txn cap
                    │            ├── member step-up threshold, budget
                    │            ├── delegation_usage (policy counter; NOT financial balance)
                    │            └── payment_intents (signed purchase request)
                    │
                    └── authorization_decisions (network ref, result, evidence)
```

## Boundaries

- `product_catalog`: member-eligible product, e.g. debit spending. Does not store funds.
- `funding_arrangements`: member-to-Lithic funding relationship and inherited policy; one financial account mapping per parent arrangement in this demo.
- `payment_instruments`: distinct virtual cards, each with its own Lithic card token and a single common `account_token`.
- `agent_identities`: public-key identity. Private signing keys never enter PostgreSQL.
- `delegation_grants`: one agent-instrument grant with member limits and purpose. Versioned.
- `delegation_usage`: spending counters used for rules. No cash, credit or general-ledger postings.
- `payment_intents`: proposed signed purchases. The UI's temporary payment handle is never persisted.
- `authorization_decisions`: append-only authorization evidence. The network transaction token can correlate with Lithic events.
- `lithic_balance_observations`: read-only historical snapshots of financial-account balance API responses. Snapshots must never be treated as authoritative for approving a transaction.

**Invariant:** multiple cards can share one funding arrangement. A permission counter cannot create a second financial hold. In Lithic Ledger-backed mode, Lithic must be configured to perform financial pre-ASA balance checks; Occlet ASA returns only the delegated policy decision.

## Deferred production design items

- Atomic SQL policy-counter reservations under concurrent ASA requests, with unique network authorization IDs and retry handling.
- Lithic webhook consumers (`balance.updated`, `card_transaction.updated`) and reconciliation, including reversal/clearing/refund corrections.
- Card-account ownership and KYC/customer identification beyond the synthetic demo member.
- Wallet token provisioning, key rotation, passkey member authentication and revocation propagation.
- No private PAN, CVC/CVV, PIN, magnetic track or financial journal entries should be stored in Occlet's arrangement database.
