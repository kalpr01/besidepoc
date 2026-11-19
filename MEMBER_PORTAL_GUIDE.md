# Occlet Member Experience — demo walkthrough

**New entry point:** `http://127.0.0.1:8000/member`

The original engineering lab still lives at `http://127.0.0.1:8000/`. Both UIs call the **same FastAPI application** and see the same in-memory agent state plus the PostgreSQL demo ledger. This is an integrated UI, not a standalone prototype or a separate source of money.

## Run locally

```bash
cd occlet_agentic_card_demo
docker compose up -d postgres
python3 -m venv .venv        # only for a new extraction
source .venv/bin/activate
pip install -r requirements.txt    # only for a new extraction
export DATABASE_URL='postgresql://occlet_demo:local_occlet_demo_only@127.0.0.1:5432/occlet'
./start_demo.sh
```

`start_demo.sh` prompts for the Lithic sandbox API key, ASA secret and optionally the separate signed-Events secret. If you're demonstrating local SQL only, leave the Lithic secrets blank. The original ASA and signed Events bridge still runs on `127.0.0.1:8001` and can be reached over your already-created HTTPS tunnel. **Keep port 8000 bound to localhost.** The member UI is deliberately not authenticated.

## Recommended seamless member demo

1. Open `/member` → **Overview**. When SQL has not been initialized, select **Initialize member account**. This calls the existing W4 initializer: $500 synthetic cash, one $300 protected trip arrangement, `yield=false`. If you already initialized W4 in the original app, the member UI displays that existing state automatically. Starting fresh W4 clears the old in-memory agent registry; do this **before** onboarding new agents.
2. Open **Arrangements** to see the member → parent funding arrangement → parent card → member policies relationship. If your parent card has not been linked, click **Link Lithic parent card** and provide the *Lithic account token* and *card token UUID*. Do **not** enter full PAN, expiration, CVV or production card credentials. The application checks the account/card association through Lithic.
3. Open **My agents** → **New agent**. Choose **Groceries** or **Event tickets**, describe the standing purchase goal and set per-transaction, total-budget and auto-approval threshold. Choose **A** for signed pre-purchase intent or **B** for network-direct card attribution. Selecting **Register agent & issue card** calls the existing `/api/agents` and `/api/member/cards` endpoints in sequence. The resulting delegated virtual card uses the **existing parent funding arrangement**, not an independent account.
4. On **My agents**, select the agent and test a $20 purchase through Local simulated network. In **A**, the browser signs the intent with its Ed25519 key; Occlet verifies the signed request, then the second step simulates the network authorization. Above the auto-approval threshold, the member must first click **Member approve intent**. In **B**, the transaction is routed by the card binding without a signed purchase intent; card attribution is **not** agent authentication.
5. To test Lithic, first create a sandbox agent virtual card using **Issue Lithic sandbox virtual card for selected agent**, then choose the Lithic route for that card. Its Lithic card is created under the linked parent's account token. A submitted simulation is not necessarily a confirmed approval: inspect **Activity & evidence**, the Lithic result, and the signed ASA bridge logs.
6. Open **W4 walkthrough**. Attempt $350 groceries against $500 cash with $300 protected. The expected decision is `PROTECTED_BY_MEMBER`, with **no ledger hold**. Click **Release $150**; this changes protection from $300 to $150 without moving money. Retry the $350 purchase and expect **one SQL financial authorization hold**. Settle at $345; $5 of the hold is released.
7. Use **Activity & evidence** for actual issuer decision traces, member events and W4 SQL journal evidence. For low-level setup, Lithic Ledger diagnostics, native-rule previews and direct endpoint access, use the **Engineering lab** link.

## New member UI files

- `static/member.html` — standalone member banking shell with Overview, Arrangements, My agents, W4 and Activity.
- `static/member.css` — responsive member-facing styles.
- `static/member.js` — authenticated-by-Ed25519 agent enrollment, existing authorization APIs, and W4 walkthrough.
- `GET /member` and `GET /member/assets/{filename}` — member page and its static files. No other new public-write API is created.

## Security and lifecycle limitations

- This **local demonstration** is not an authenticated member-banking application. Never expose port 8000 to the internet.
- New agent private signing keys are non-exportable and held **only in this browser page session**, not in SQL/localStorage. After a page reload, the registration remains visible but signed intent requests with the original agent cannot be made. Create a fresh test agent to repeat Flow A; production would use registered long-term hardware-backed agent identity and key recovery/rotation.
- A parent Lithic account token is an account relationship, **not proof that Lithic Ledger is available**. The demo uses the SQL ledger when `OCCLET_SQL_LEDGER` is active. No production network funds move.
- W4's protected-trip amount is **permission accounting**, not a second financial reserve. All holds are posted exactly once in the shared SQL funding book.
- The UI depends on the already-existing Python/FastAPI backend, PostgreSQL schema, and Lithic bridge. It has not been deployed to your Mac by this ZIP.

## Tests

```bash
PYTHONPATH=. pytest -q
```

The 2 PostgreSQL-dependent integration tests are skipped unless they run against your Docker DB. With Postgres ready:

```bash
OCCLET_RUN_DB_TESTS=1 PYTHONPATH=. pytest -q \
  tests/test_occlet_postgres_integration.py \
  tests/test_w4_postgres_integration.py
```

The member UI is included in `tests/test_member_ui.py`. Browser layout/nav/visibility were checked with a mocked API fixture. A real Lithic authorization and settlement callback must still be exercised in your own sandbox.
