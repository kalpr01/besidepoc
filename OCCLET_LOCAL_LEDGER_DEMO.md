# Occlet Agentic Cards — PostgreSQL Financial Ledger Demo

**Mode:** `OCCLET_SQL_LEDGER` (local demo authority, not Lithic Ledger)

This extension preserves both existing flows (A: signed intent before authorization; B: network-direct card binding), all agent/card policies, and the Lithic Auth Stream Access (ASA) sandbox bridge. **When enabled**, Occlet's PostgreSQL ledger is the local source of truth for the member's available funds and held authorizations. Lithic's sandbox can still independently issue or decline payments; these SQL balances are not Lithic balances.

## Quick start on Mac (Docker Desktop must be running)

```bash
cd occlet_agentic_card_demo
docker compose up -d postgres
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL='postgresql://occlet_demo:local_occlet_demo_only@127.0.0.1:5432/occlet'
./start_demo.sh
```

Then open **http://127.0.0.1:8000** (localhost only).

If your Docker volume already exists from the earlier demo, you **do not** need to delete it. The app applies `db/002_occlet_local_ledger.sql` on startup even for existing volumes. The new `docker-compose.yml` also initializes both SQL schemas for new volumes.

- In **Step 0**, configure parent funding and limits. For example: parent credit/cap display $500, transaction maximum $500, monthly policy limit $500. This is initially configuration, not funded money.
- In **Step 0c**, select **$500** opening funds and click **Initialize demo ledger**. PostgreSQL writes a double-entry opening journal. The display shows $500 cash, $500 available, $0 authorization holds.
- **Reuse the parent Lithic test card you already created:** in Step 0c paste the existing Lithic `account_token` and the **card token** (UUID, **not** the PAN). Click **Link existing parent card**. Occlet validates both identifiers with the Lithic sandbox and discards the PAN/CVV from the response. Do not paste card PAN/CVV into any UI input or logs.
- Create one Grocery agent in **Flow B**, a dedicated agentic virtual card, and its Lithic sandbox card under the *same* Lithic account. Authorize $20 using a local network simulation or Lithic ASA, and click **Refresh SQL balances and journals**. Available becomes $480, holds $20, cash remains $500.
- Create a Ticket agent in **Flow A**, with its own member-defined goal, signed purchase intents and optional member step-up. A successful $75 authorization results in the **same parent arrangement** holding $95 in total if the grocery hold remains open.
- Choose the grocery hold in Step 0c and **Clear + settle** for $18. PostgreSQL releases $2 unused, moves $18 out of demo cash, and updates the agent's policy utilization. Or **Release hold** to reverse an unsettled authorization. Refreshed journal lines show both sides of every journal.
- **Synthetic top-up** creates a balanced cash/available journal in PostgreSQL; it does not call Lithic or move money.

**For ASA:** if you have already enrolled a public endpoint, run the existing `cloudflared` tunnel to port 8001 (the signed bridge) and leave your `LITHIC_ASA_SECRET` available locally. Never expose port 8000; its member approval endpoints have no login. The network authorization decision uses the member's PostgreSQL funds and inherited policies. Lithic itself is **not** funded by this operation.

## Domain relationship

```text
Occlet Member
  └─ FundingArrangement "arr_parent_demo" (one monetary authority)
       └─ PostgreSQL Ledger Book (USD)
            ├─ CASH                  asset, debit normal
            ├─ AVAILABLE             liability, credit normal
            └─ AUTH_HOLDS            liability, credit normal
       ├─ Lithic parent virtual card (payment credential)
       ├─ Lithic Grocery Agent Card → registered agent + delegation (NO ledger account)
       └─ Lithic Ticket Agent Card  → registered agent + delegation (NO ledger account)
```

This is a debit/pre-funded demo; `CASH` is synthetic settlement cash and the credited `AVAILABLE` amount represents a payable balance to the member. In a credit-card product, credit exposure, receivables, limits and settlement accounting are different and need a separate model.

## Double-entry posting examples

| Event | Debit | Credit | Result |
|---|---|---|---|
| Fund $500 | `CASH` $500 | `AVAILABLE` $500 | Available $500 |
| Auth $80 | `AVAILABLE` $80 | `AUTH_HOLDS` $80 | Available $420, held $80 |
| Reverse $80 | `AUTH_HOLDS` $80 | `AVAILABLE` $80 | Available $500, held $0 |
| Clear $75 against $80 hold | `AUTH_HOLDS` $80 | `CASH` $75 + `AVAILABLE` $5 | Available $425, held $0; demo settlement paid $75 |

Every journal balances debits and credits, and its entries plus account balances are written in **one SQL transaction**. The journal reference and authorization reference are unique. A `SELECT ... FOR UPDATE` lock on the shared ledger book serializes concurrent authorizations across different agent cards; the funds check and hold posting happen in the same SQL transaction.

**Example:** opening funds $100. Grocery agent and Ticket agent simultaneously request $80 and $70. Exactly one should receive an `AUTHORIZED` ledger hold; the other is declined for insufficient funds. A real PostgreSQL integration test exercises this.

## APIs

| Endpoint | Purpose |
|---|---|
| `POST /api/demo-ledger/initialize` | Establish a single opening-funds journal, idempotently |
| `GET /api/demo-ledger/balance` | Read authoritative local funds, holds, recent journal lines |
| `POST /api/demo-ledger/release` | Reverse an unsettled hold |
| `POST /api/demo-ledger/clear` | Settle (simplified) and release any difference |
| `POST /api/demo-ledger/topup` | Synthetic opening/funding adjustment |
| `POST /api/member/parent/lithic/link-existing` | Validate/reuse a Lithic sandbox parent card token |
| `POST /api/network/authorize` | Offline Flow A authorization, now writes SQL financial hold |
| `POST /api/network/direct/authorize` | Offline Flow B authorization, now writes SQL financial hold |
| `POST /api/internal/lithic/authorize` | ASA adapter invokes the SAME SQL financial hold service |

### Relevant curl examples (local only)

```bash
# After configuring the parent in the UI:
curl -sS -X POST http://127.0.0.1:8000/api/demo-ledger/initialize \
  -H 'Content-Type: application/json' -d '{"opening_balance_cents":50000}'

curl -sS http://127.0.0.1:8000/api/demo-ledger/balance

# Example: only after a successful authorization has created that reference.
curl -sS -X POST http://127.0.0.1:8000/api/demo-ledger/clear \
  -H 'Content-Type: application/json' \
  -d '{"authorization_ref":"local:my-network-reference","settled_cents":1800}'
```

## Real PostgreSQL concurrency test (optional)

With Docker PostgreSQL running, dependencies installed, and DATABASE_URL set, run ONLY this targeted test:

```bash
OCCLET_RUN_DB_TESTS=1 PYTHONPATH=. pytest -q tests/test_occlet_postgres_integration.py
```

It creates a unique test funding arrangement, concurrently races two charges against a $100 balance, verifies one winner, then validates release, clearing, idempotency and balanced SQL journals. It cleans up only its own test arrangement.

Run the rest of the unit tests with `DATABASE_URL` **unset**, as they reset demo state:

```bash
env -u DATABASE_URL PYTHONPATH=. pytest -q
```

## Important scope and limitations

- **Local authority only.** Lithic's card-issuing account and its optional financial account have their own independent state. We do not claim PostgreSQL funds Lithic; no two ledgers are synchronized.
- **No independently funded child cards.** Both child cards share one parent ledger book. Agent spending budgets are policies, not financial deposit subaccounts.
- **Clearing endpoint is a DEMO simulation.** It combines clearing and settlement; the demo does not yet subscribe to Lithic clearing and reversal events. It supports one final clearing per auth, no partial captures, incremental authorizations, FX or chargebacks yet.
- **Distributed transaction limitation.** Occlet's SQL hold commits before Lithic learns the ASA decision; timeouts or network stand-in can create mismatches. Production needs reconciliation, pending/confirmed states, and webhook/event ingestion to release orphan holds. The old in-memory agent policy cache and SQL snapshot are not a production-grade multi-worker architecture.
- **Security:** Member UI has no authentication, agent keys are ephemeral in-browser for the demo, and credentials must remain in the sandbox. The ASA signing verifier is the only endpoint to expose via a tunnel. Never store PAN or CVV in Occlet SQL.
- **Ledger modes are exclusive.** `OCCLET_SQL_LEDGER` and `LITHIC_LEDGER` cannot both be active; demo reset is required before changing financial authority.
- **If you configure an Occlet SQL ledger and restart, keep DATABASE_URL configured.** Otherwise the app cannot load persisted local financial state. For a production service, require an explicit ledger-mode configuration and never silently fall back to a default mode.
