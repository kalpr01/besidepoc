# Beside member experience — Delegated Card demo

This package preserves the user-supplied `Beside Main (1).html` as the primary member application, including all original pages, design, embedded art, and test persona **Ann Gutierrez**. It adds a first-class **Delegated Card** navigation page, without replacing or conflating the existing $100,000 share-certificate example.

## Run

```bash
cd occlet_agentic_card_demo
docker compose up -d postgres
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL='postgresql://occlet_demo:local_occlet_demo_only@127.0.0.1:5432/occlet'
./start_demo.sh
```

Open **http://127.0.0.1:8000/member** and choose **Delegated Card** from the Beside sidebar.

Direct links (avoid hidden/sidebar navigation entirely):

- **http://127.0.0.1:8000/member/delegated** — Delegated Card page, all controls.
- **http://127.0.0.1:8000/member/delegated/setup** — Delegated Card page with the **Create delegated card** form expanded.
- **http://127.0.0.1:8000/api/demo/version** — confirms which ZIP version your currently running server is serving. Expect edition `beside-delegated-login-return-v3`.

If the tab or setup page is missing, stop the existing `start_demo.sh` in Terminal with Ctrl+C. Extract this ZIP into a **new, clean folder**, activate/install dependencies, restart the app, and hard-refresh your browser (Cmd+Shift+R). Check `lsof -nP -iTCP:8000 -sTCP:LISTEN` to locate older services occupying port 8000.


The original engineering lab remains at `/` and the old member walkthrough is retained at `/member/legacy`. `/beside` also opens the new Beside version.

## Demonstration

1. **Member & account arrangements** displays the `member_demo` test relationship under Ann Gutierrez, the Occlet funding arrangement, linked Lithic account and parent card (if previously linked), and current SQL cash/holds. The original share certificate is *not* funding the W4 pocket.
2. **Initialize $500 W4 demo** when PostgreSQL is configured. This establishes $500 synthetic cash and a $300 protected trip (yield OFF). **Warning:** starting a fresh W4 scenario clears previously registered demo agents and open demo intents, but preserves linked Lithic parent identifiers. Initialize before creating delegated cards.
3. Click **Create delegated card**. Configure a name, purpose (groceries or tickets), purchase goal, per-transaction limit, total budget, auto-approval threshold, and authorization policy (signed agent intent vs card-bound network attribution). Submit. The browser registers an Ed25519 public key, agent delegation and local delegated-card instrument using the existing FastAPI APIs. A private key remains only in the current browser tab, not persisted or exported. Refreshing the page loses the signed-intent signing key; network-direct cards remain usable for this demonstration.
4. The new card appears in **My delegated cards** and **Your intents & protections**. Use **Issue Lithic sandbox card** explicitly to request a corresponding Lithic sandbox card under the linked parent account. No PAN/CVV is requested or displayed. `Create delegated card` by itself does not create a Lithic card.
5. Use **Test a delegated purchase** for local authorization or Lithic sandbox simulation. A signed Flow A request might require member approval if the purchase exceeds its configured threshold; network-direct Flow B attributes spending to the assigned credential and is not cryptographic authentication of the agent.
6. W4 example: **Attempt $350 groceries** (decline `PROTECTED_BY_MEMBER`) → **Release $150** (policy update only, no journal) → **Retry $350** (single $350 authorization hold) → **Settle $345** (return $5 unused hold). Follow balances in the page. This member-own-card W4 path is separate from delegated-card spending.

## Financial boundaries and security

- PostgreSQL is the financial authority for the **local synthetic demo only**. The original certificate and any real credit-union deposits are unrelated.
- Lithic remains the sandbox issuer/ASA integration; `Issue Lithic sandbox card` is an explicit additional action. A linked Lithic card does not imply Lithic Ledger provisioning.
- Beside displays a simulated member sign-in (including demo MFA), not real authentication. The Occlet member APIs are unauthenticated and unsafe to expose on the public Internet. Only the narrow `8001` webhook bridge should be tunnelled for Lithic events, not the member portal or all API routes.
- The existing engineering demo and original Beside page are preserved. The new integration consists of `static/beside.html` (unaltered source + two external asset tags), `static/beside-delegated.js`, `static/beside-delegated.css`, and additional FastAPI routes.

## Validation

```bash
PYTHONPATH=. python -m pytest -q
```

Live PostgreSQL and Lithic sandbox callbacks require their respective environments; local browser checks and non-DB tests do not prove live webhook reconciliation.

## Sign-in and navigation (v3 fix)

Beside intentionally displays its sample Ann Gutierrez username/password and a demonstration verification code. This is a fake login only, not a security boundary. The v3 update preserves `/member/delegated/setup` across that login; it no longer silently redirects to Overview.

- **Direct setup:** http://127.0.0.1:8000/member/delegated/setup — complete the Beside demo login; the setup form is expanded automatically.
- **Member home:** http://127.0.0.1:8000/member — after login, select **Delegated Card** in the sidebar. Navigation stays in Beside without a full-page reload.
- **Version:** http://127.0.0.1:8000/api/demo/version — edition must be `beside-delegated-login-return-v3`. If not, stop the old server, unpack this ZIP into a fresh directory and restart.
- **Local app:** port 8000 is private; expose only the appropriate callback bridge endpoints on 8001 to Lithic.

If the sidebar or setup page doesn't appear after updating, use a hard refresh (`Cmd + Shift + R`) and verify the version API in the same browser.
