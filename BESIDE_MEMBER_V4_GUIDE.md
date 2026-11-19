# Beside member experience v4 — authoritative local ledger and member actions

This release preserves the supplied Beside Main HTML and its original pages. It adds a **Delegated Card** page for test member **Ann Gutierrez** while retaining the technical engineering lab for operator/admin activity.

## URLs

- **Member experience (opens Delegated Card after demo login):** http://127.0.0.1:8000/member/delegated
- **Create delegated card:** http://127.0.0.1:8000/member/delegated/setup
- **Existing Beside overview:** http://127.0.0.1:8000/member
- **Admin/engineering lab:** http://127.0.0.1:8000/
- **Member projection API:** http://127.0.0.1:8000/api/member/experience
- **Build/version check:** http://127.0.0.1:8000/api/demo/version (edition `beside-member-ledger-actions-v4`)

## Run

```bash
cd occlet_agentic_card_demo
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
docker compose up -d postgres
export DATABASE_URL='postgresql://occlet_demo:local_occlet_demo_only@127.0.0.1:5432/occlet'
./start_demo.sh
```

Retain your existing Lithic sandbox settings and port 8001 bridge configuration separately. Never embed the Lithic API key in the UI, scripts or ZIP. The tunnel must point **only to the verified port 8001 callback bridge**, not port 8000.

## Member versus admin responsibilities

| Beside member | Admin / engineering lab |
|---|---|
| View account card, SQL ledger cash, available, held balances | Configure parent arrangement and initialize demo ledger |
| See all existing delegated cards and member-approved purposes/limits | Link existing Lithic account and card; issue sandbox delegated credentials |
| Create new Occlet delegated permission | Configure native Lithic rules, test ASA/transaction events |
| See submitted/declined/authorized/cleared transactions by card | Run financial authorizations, clearing and reversals |
| Approve or decline a live pending signed-intent step-up | Review and confirm a *local demo* hold-release request |
| Release W4's protected trip intent *without a money journal* | Set up/reset W4 and test original protection / clearing outcomes |
| Request authorization hold removal *without a money journal* | Check network evidence and confirm reversal/expiry when appropriate |

There is no real login enforcement: Beside's login screen is part of the prototype. **Do not expose port 8000 publicly.**

## Ledger source of truth

In SQL ledger mode, the parent card *available* tile comes from `demo_ledger_accounts` account `AVAILABLE`, cash from `CASH`, and holds from `AUTH_HOLDS`. The child card's *remaining budget* is a **policy counter**, not another financial account. W4 protection is in the arrangement registry and affects **free to spend after protection** but does not reduce financial available until an authorization occurs.

If SQL ledger is not initialized, the member tile says `Not set up`—it does **not** display the demo mirror/credit-limit amount as real balance. Lithic Ledger remains separate; no real Lithic balance is implied.

## Member action and execution flow

1. In the **admin lab**, configure and link the parent account. Start the PostgreSQL local ledger (or W4). Be aware **Start fresh W4 resets existing agent/card demo records**: do this before creating the delegated cards you intend to retain.
2. In **Beside → Delegated Card**, confirm the parent card, balance, and existing cards/policy details. Create a new delegated grocery or tickets card, specifying limits and Flow A or B. The member action creates a policy-bound Occlet record, **not a new financial ledger account and not a Lithic card**.
3. Admin may issue a linked Lithic sandbox card when needed. Member can refresh to see issuance status and last four.
4. To generate a **Flow A step-up**, use the member-page *Demo tool: submit a delegated-agent purchase* for a newly created Flow A card in that browser session, with an amount greater than the auto-approval threshold but at/below card maximum. The member action center lists the pending intent. Approve or decline; the agent must then continue/retry. The agent private signing key intentionally isn't persisted across browser restarts.
5. From the admin W4 tab, attempt the $350 parent-card groceries transaction against $500 with $300 protected. It declines `PROTECTED_BY_MEMBER`. In Beside, use **Release $150 of trip protection**; refresh/admin retry with a fresh authorization reference. Financial cash does not change at release; the approved $350 produces a single PostgreSQL hold.
6. In Beside, **Request hold release** for an active authorization. It creates a review request only, and **does not** release funds. In the admin lab, click **Refresh requests** in *Admin operations*. Admin may confirm reversal for `local:`/`w4-local:` **demo** references. `lithic:` transactions must be reconciled from network/Lithic events rather than arbitrarily reversed only in the Occlet SQL book.
7. Observe settled or released status on the card-attributed transaction list, and filter to the parent card or any delegated card.

## Testing

```bash
PYTHONPATH=. pytest -q
```

Automated tests cover read-only PostgreSQL balances, correct card labels for authorization records, pending step-ups without payment-token leakage, idempotent hold-release requests, legacy backend behaviors and page assets. Database integration tests require a running Docker PostgreSQL; enable them with `OCCLET_RUN_DB_TESTS=1`. A browser-component test uses deterministic mock API data; it does not replace a Mac live end-to-end validation.

## Known prototype boundaries

- Existing Beside certificates, pools and impact examples are unchanged, and **not connected** to the $500 W4 ledger.
- The parent and delegated ledger are demo-only. Lithic transaction callbacks need their configured secure public tunnel, event signing secret, reconciliation and real sandbox tests.
- Financial holds can be released only through ledger processor/admin operations in this demo; actual network reversals/expiry require processor data.
- Demo step-up intents expire quickly (default about 120 seconds); approvals should be completed promptly.
- Transactions are currently a bounded recent history, not a full reporting or regulated recordkeeping solution.
