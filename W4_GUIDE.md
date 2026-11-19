# Occlet W4 — member-first protection / yield off

Source: **Q Delegated Card** master page, section **4b: Competing intents and running short**, W4, page 9 (Nov 18, 2026). Example amounts only.

## Expected scenario

- One parent funding arrangement with **$500** synthetic debit funds in the Occlet PostgreSQL demo ledger.
- One protected **TRIP** purpose/intent in the arrangement registry: **$300**, `yield_enabled=false`.
- Member's **own parent card** attempts to buy **$350 of groceries**.
- First result: `DECLINED / PROTECTED_BY_MEMBER`. No authorization hold or financial journal is created.
- Member taps **Release $150 protection**. The protected amount becomes $150. No financial journal is created.
- Member retries with a **new network transaction reference**. Result `APPROVED`; the funding arrangement gets **one $350 hold**. $500 cash, $150 available, $350 held, $150 trip still protected.
- Example clearing **$345** releases the unused $5. Final cash **$155**, available **$155**, holds $0, and trip protection remains **$150**.

The trip is a permission-based protection, **not** a second financial account, not a sub-balance, and not an actual bank reservation. The debit ledger remains authoritative for the *local demo only*.

## Requirements and start

```bash
cd occlet_agentic_card_demo

docker compose up -d postgres
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL='postgresql://occlet_demo:local_occlet_demo_only@127.0.0.1:5432/occlet'
./start_demo.sh
```

Then open `http://127.0.0.1:8000`, click **Use Cases · W4**. The startup migration creates `db/003_w4_protection_and_events.sql` automatically.

### Local run

1. Click **Start fresh W4**. This starts a new isolated demo book and clears previous in-memory agents/authorization traces; existing SQL books remain unchanged.
2. Click **Attempt $350 groceries**. Expect `PROTECTED_BY_MEMBER`; $500 cash, $0 hold, $200 free room.
3. Click **Member releases $150 trip protection**. Expect $150 remaining protected; **no** financial journal.
4. Click **Retry $350 purchase**. Expect APPROVED; one SQL hold, $150 available.
5. Click **Settle $345** (or alternatively **Reverse**). After clearing, $155 cash and available, no hold; still $150 protected.
6. Inspect the *authorization decisions and financial evidence* section. Repeated W4 decision IDs cannot create extra money movements.

## Real Lithic sandbox ASA → Occlet PostgreSQL

This flow does **not** require Lithic Ledger. Lithic is the card-authorization sandbox; Occlet PostgreSQL owns the demo balances and holds.

1. Before starting W4, configure the parent and link your **existing SANDBOX parent card** under **Lithic Sandbox** using its `account_token` and card **token** (UUIDs only, never the PAN/CVV). The W4 scenario preserves this link.
2. Ensure Lithic ASA is enrolled at `https://YOUR-TUNNEL/lithic/asa` and that `LITHIC_ASA_SECRET` is configured. Only expose port 8001, never 8000.
3. Start W4. Click **Send $350 parent card to Lithic**. On the first attempt, Occlet ASA responds with a card decline because of `PROTECTED_BY_MEMBER` (the provider result maps to `UNAUTHORIZED_MERCHANT`, and the detailed Occlet reason remains in the decision trace).
4. Release $150 of trip protection in W4. Click **Send $350 parent card to Lithic** again; this creates a new transaction. Occlet ASA should respond `APPROVED` and create a **single** `lithic:<transaction-token>` hold in PostgreSQL.
5. For real sandbox transaction-event reconciliation, create a **separate** Events API subscription to `https://YOUR-TUNNEL/lithic/events` using a *different* HMAC secret. Only the signed Events API callback can post a Lithic clearing/reversal; manual local clearing does not represent a Lithic provider event.

### Create the signed Events API subscription

Keep the tunnel running, then in another local terminal using your **rotated sandbox API key**:

```bash
export LITHIC_API_KEY='YOUR_ROTATED_SANDBOX_KEY'
python lithic_events_setup.py 'https://YOUR-TUNNEL'
```

The command prints the subscription token and a private curl command to obtain its **`whsec_...`** secret. Retrieve it locally and set `LITHIC_EVENT_SECRET` in your environment. Restart `./start_demo.sh`, ensuring it uses the same ASA and Events secrets as your sandbox enrollment. You can alternatively configure them before starting, avoiding interactive prompts.

`card_transaction.updated` events are **not** sent to the ASA endpoint: they go to `/lithic/events`, with independent signature verification. An event with status `PENDING` only confirms the existing hold; `VOIDED`/`DECLINED`/`EXPIRED` releases it; supported `SETTLED` debits clear it. Events are correlated by Lithic transaction token, card token, account token and webhook message ID.

**Unsupported, intentionally quarantined:** multiple partial completions, returns/credits, clearing without matching ASA, settlement greater than the original hold, inconsistent account/card information, and transactions from different sandbox runs. These need further financial-message lifecycle handling before production.

## Tests

```bash
pytest -q

# Optional: executes the real PostgreSQL journal/row-lock tests with Docker:
OCCLET_RUN_DB_TESTS=1 pytest -q tests/test_occlet_postgres_integration.py tests/test_w4_postgres_integration.py
```

The automated tests that do not require Docker use in-memory adapters/mocks; they do **not** claim to validate live Lithic network/API delivery or PostgreSQL protocol behavior.

## Security limits

- The member setup/release endpoints intentionally do **not** have production authentication. Keep port `8000` on loopback; never expose it.
- The public port `8001` validates ASA and transaction-event HMAC signatures and forwards them via a loopback-only secret-protected bridge.
- Signing keys and sandbox credentials must stay in environment variables, not the ZIP or frontend.
- This is not a certified issuer or PCI-compliant card vault. No live PAN/CVV should be entered into this app.
- The app is single-process and demo-grade. Production issuance needs durable transaction correlation, authorization-expiry recovery, webhook ordering, strong member authentication, and end-to-end financial reconciliation.
