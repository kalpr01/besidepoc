> **Update:** PostgreSQL can now host an **Occlet-authoritative local demo ledger** in addition to the arrangement registry. See [OCCLET_LOCAL_LEDGER_DEMO.md](OCCLET_LOCAL_LEDGER_DEMO.md). The older Lithic-ledger sections below apply only to the alternative Lithic-provisioned financial account mode. Do not activate both.

# Occlet Agentic Card — Lithic Financial Ledger + Local SQL Arrangements

This package extends the **previous two-flow Lithic sandbox demo**, retaining:

* Flow A — member-defined agent purchase scope, Ed25519-signed payment intent, then Lithic ASA authorization
* Flow B — direct network authorization attributed to the registered agent **from the dedicated card token** (not proof of agent identity)
* Shared parent/virtual-card funding relationship, member approval workflow, native Lithic rule draft preview, and audit trace

It adds two independent systems of record:

| Responsibility | Source of truth |
| --- | --- |
| Money, balance availability, pending financial holds, clearing | **Lithic ISSUING Financial Account**, if the sandbox program has Lithic Ledger enabled and is configured for authorization from balance |
| Product and funding arrangement metadata, parent/child card binding, member delegation, agent policy, intent status | **PostgreSQL 16** in local Docker |
| Network transaction lifecycle | Lithic; Occlet stores references and decision evidence, but **never posts its own financial journal entry** |
| Original demo-only fake $5,000 credit/holds | Still available in `DEMO_MIRROR` mode for non-Ledger sandbox programs, explicitly marked as not authoritative |

### Important Lithic distinction

`account_token` identifies a Lithic account/cardholder grouping. It is **not** a `financial_account_token`. Programs not using Lithic Ledger may not have any `ISSUING` Financial Account. Lithic documents that, after a program has been configured for Ledger, it generally creates this account during account-holder onboarding. **You cannot create an `ISSUING` account merely by POSTing an arbitrary financial account**: the public Create Financial Account endpoint documents `OPERATING` as its allowed type. Ask Lithic to provision Ledger on your sandbox program if discovery returns no `ISSUING` account.

**References**:
- https://docs.lithic.com/docs/financial-accounts
- https://docs.lithic.com/docs/quick-start-consumer-prepaid-ledger
- https://docs.lithic.com/docs/balances
- https://docs.lithic.com/docs/auth-stream-access-asa

## Run on a Mac with Docker Desktop

Requirements: Docker Desktop, Python 3.11+, Lithic **sandbox** API key, and the existing ASA signing secret if using a public tunnel.

```bash
cd occlet_agentic_card_demo
docker compose up -d postgres
docker compose ps
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL='postgresql://occlet_demo:local_occlet_demo_only@127.0.0.1:5432/occlet'
./start_demo.sh
```

The launcher prompts for Lithic sandbox credentials without saving them in source code. Do **not** put real card details or production API keys in this demo. The original API key disclosed in chat should be rotated.

Open http://127.0.0.1:8000 and create the parent arrangement, parent Lithic card, agents and delegated cards as before.

To connect to the SQL shell:

```bash
docker compose exec postgres psql -U occlet_demo -d occlet
```

Useful queries:

```sql
SELECT arrangement_id, funding_mode, lithic_account_token,
       lithic_issuing_financial_account_token, parent_policy
FROM funding_arrangements;

SELECT instrument_id, instrument_role, agent_id, instrument_status,
       lithic_card_token, arrangement_id
FROM payment_instruments;

SELECT grant_id, agent_id, purpose, authorization_flow,
       merchant_allowlist, policy, policy_version
FROM delegation_grants;

SELECT i.intent_id, i.status, i.amount_cents, i.merchant_id, d.agent_id
FROM payment_intents i JOIN delegation_grants d ON i.grant_id=d.grant_id;

SELECT network_reference, source, decision, reason, created_at
FROM authorization_decisions ORDER BY id DESC LIMIT 20;

SELECT financial_account_token, available_amount, pending_amount,
       total_amount, fetched_at FROM lithic_balance_observations
ORDER BY fetched_at DESC LIMIT 20;
```

The SQL schema lives at `db/001_arrangements.sql`. Tables are automatically created at startup with `DATABASE_URL`; the compose initialization script also creates them on a new database volume. The app uses a single-process demo cache loaded from a SQL snapshot at startup; the relational tables are maintained on successful changes. Payment bearer handles are **not persisted**; unexecuted signed intents expire on restart.

## Discover whether your current Lithic program supports Ledger

1. Create a Lithic parent test card through the UI (Step 0).
2. Click **Discover Lithic ISSUING account** (Step 0b). This calls:
   `GET /v1/financial_accounts?account_token=<parent Lithic account token>`.
3. If an `OPEN` `ISSUING` financial account exists, the UI links it after independently verifying that it belongs to the same Lithic cardholder account.
4. Click **Refresh Lithic ledger balance**. This calls
   `GET /v1/financial_accounts/{issuing_financial_account_token}/balances`.
   The response is a **live Lithic balance observation**, also recorded read-only in Postgres.
5. Do **not** activate Lithic-ledger authorization until Lithic confirms **authorization from balance** is enabled for your program. Lithic runs native security, spending, and configured balance checks **before** it calls ASA, so the Occlet ASA code must not create another financial hold.
6. If Lithic confirmed it, start a *fresh* demo and export `LITHIC_AUTH_FROM_BALANCE_CONFIRMED=1` before the launcher. Repeat discovery, then press **Use Lithic ledger mode**. This stops the local financial mirror from performing balance authorization or writing fake financial holds. It still tracks agent POLICY utilization separately.

Example REST APIs:

```bash
curl -s http://127.0.0.1:8000/api/sql/status
curl -s http://127.0.0.1:8000/api/sql/registry
curl -s http://127.0.0.1:8000/api/lithic/ledger/discover
curl -s http://127.0.0.1:8000/api/lithic/ledger/balance
```

If discovery returns no `ISSUING` Financial Account, **this existing Lithic sandbox cannot yet demonstrate an authoritative Lithic customer ledger**. PostgreSQL will still store Occlet arrangements, but the old $5,000 balance remains a visibly simulated demo value until Lithic configures Ledger.

### Sandbox funding

For a **prepaid** Ledger program, start with a zero balance and load **sandbox test funds using the funding mechanism authorized by your Lithic program**. Lithic's prepaid quick-start shows Lithic-processed ACH and externally-held (modular) funding models; they differ. Do not attempt to post an arbitrary balance or journal entry through Occlet. The payment program's implementation determines which API and funding model is available.

For **credit** Ledger programs, Lithic balance fields have different semantics (amount owed vs money available to spend). This demo is designed first for the **debit/prepaid parent arrangement** and will not infer available credit from the credit account's `available_amount`.

## End-to-end demo

```
Member account / Lithic Ledger-enabled program
 ├─ Parent card token (instrument)
 ├─ Grocery virtual card token (instrument)
 └─ Tickets virtual card token (instrument)
           │ all share one Lithic account_token
           ▼
Occlet agent registration + delegation limits (Postgres)
           │
     [A] signed intent  OR  [B] direct card-bound network auth
           │
           ▼
Lithic sandbox simulated card authorization
           │
    Lithic native balance checks (only if configured)
           │
           ▼
Occlet ASA verifies merchant, amount, agent delegation + policy
           │
           ▼
Lithic ISSUING Financial Account: authorization hold / clearing
           │
           ▼
Occlet: read-only balance refresh / transaction correlation
```

### Engineering caveats before production

* **Not a PCI production system.** Local member APIs have no login; expose **only port 8001** through a tunnel, never `8000` or the Postgres port.
* The demo SQL state is loaded into **one FastAPI process** and written back on successful POST. It is **not** a full transactionally consistent multi-instance authorization engine. Production must do atomic policy utilization updates, authorization idempotency, and durable event processing in SQL with concurrency control.
* The relationship between authorization intent, Lithic transaction, adjustments, clearing, reversals, and refunds needs reliable inbound Lithic webhooks and a reconciliation worker. This package adds **read-only manual balance observations**, not a production event-sourced synchronization pipeline.
* Lithic's official `balance.updated` and `card_transaction.updated` events should feed an asynchronous projection service. Occlet's SQL should never post extra financial holds.
* The original local simulated parent holds remain **demo-only** when Lithic Ledger is not enabled. Never call them authoritative.
* `LITHIC_AUTH_FROM_BALANCE_CONFIRMED=1` is a user-controlled assertion of program configuration, not something this prototype can independently certify.
* Docker and authenticated Lithic Ledger API calls were **not run inside the build environment**. Run the above checks in your own sandbox.

### Reset

Click **Reset lab** to remove the local test state (including SQL registry and observations); it does not delete cards, financial accounts, or transactions already created at Lithic. To destroy the local Postgres volume entirely: `docker compose down -v` (irreversible for demo data).

## Discovery error troubleshooting (Nov 18 fix)

If the **Discover Lithic ISSUING account** action fails, use **Diagnose Lithic access**.
The original demo incorrectly displayed a generic 'confirm Ledger entitlement' message for *every* error.
The new endpoint is read-only and reports HTTP status categories without disclosing the API key or financial account details.

- `401 INVALID_API_KEY`: restart the demo with a valid **sandbox** API key. Rotate any key previously posted into a chat.
- `403 ACCESS_DENIED`: ask Lithic whether the key/program can read Financial Accounts / Ledger.
- `404 NOT_FOUND`: verify account token and program access; a resource/endpoint was not found.
- `429 RATE_LIMITED`: slow down and retry later.
- `CONNECTION_ERROR`: check networking, proxies and DNS.
- `OK` with `issuing_count: 0`: the API call succeeded, but that parent does not have an ISSUING Financial Account. It may have been issued in a card-only program, without Ledger account-holder onboarding.

Lithic's documentation says `ISSUING` financial accounts are typically created automatically
when an account holder is onboarded to a **Ledger-configured** program; creating a standalone virtual card does not provision one.
Never manufacture an `ISSUING` account by invoking the `POST /financial_accounts` API: its public creation type is `OPERATING`.

References:
- https://docs.lithic.com/reference/getfinancialaccounts
- https://docs.lithic.com/docs/financial-accounts

The November 18 patch also removes the unnecessary empty `Idempotency-Key` header
from GET requests, which could lead API gateways to reject otherwise valid reads.
