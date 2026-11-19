# Beside Member Experience — Delegated Card (November 2026)

The default member URL is now **`http://127.0.0.1:8000/member`**, using the exact uploaded **Beside Main** layout and pages with the **Ann Gutierrez** test member. The new **Delegated Card** navigation item lets the member create purpose-bound agent delegations and limits, explicitly issue linked Lithic sandbox cards, and run the W4 protected-trip demonstration. The existing certificate demonstration is unchanged and separate from the $500 Occlet SQL spending-pocket example.

- **New Beside member experience:** `/member` (also `/beside`)
- **Earlier technical member portal:** `/member/legacy`
- **Engineering lab:** `/`
- **Setup and workflow:** [`BESIDE_DELEGATED_CARD_GUIDE.md`](BESIDE_DELEGATED_CARD_GUIDE.md)

---

> **UI UPDATE — November 18, 2026:** The browser now has separate **Local Demo** and **Lithic Sandbox** tabs. Shared member/agent setup stays visible above both. Local includes the PostgreSQL demo ledger and local Flow A/Flow B network simulators; Lithic includes sandbox parent-card linking, agent-card creation, ASA simulations, rule previews and Ledger diagnostics. See [UI_TABS_GUIDE.md](UI_TABS_GUIDE.md). Switching tabs is only UI navigation; it does not change financial authority. The project retains the same backend APIs and authorization behavior.

> **NEW: Occlet PostgreSQL authoritative DEMO ledger.** See [OCCLET_LOCAL_LEDGER_DEMO.md](OCCLET_LOCAL_LEDGER_DEMO.md) for setup and the double-entry model. This supersedes older statements below saying PostgreSQL stores arrangements only. Lithic Ledger remains a separate option and is not enabled in your current sandbox. Both agentic authorization flows remain intact.

> **NEW: Lithic Ledger + Docker PostgreSQL arrangements extension.** See [LEDGER_SQL_GUIDE.md](LEDGER_SQL_GUIDE.md) for the Docker setup, SQL schema, Ledger discovery, and limitations. The two previous authorization flows remain available.

# Occlet Two-Flow Agentic Card Demonstration — Lithic Sandbox

**New: two different execution and trust models, sharing one parent funding arrangement.** This is an extension of the earlier Occlet shared-parent / Lithic sandbox demonstration. It keeps all existing local and Lithic features, including the signed-agent flow, native Auth Rules previews, optional Capital One pre-checkout handoff, and single parent-account hold tracking.

**Validation:** 31 local automated tests passed. Existing Lithic sandbox enrollment can be reused; no real Lithic calls were made while preparing the update. No API credentials are embedded.

## Flow A — member registers a purpose; agent requests a signed intent FIRST

1. **Step 0**: Enable the illustrative parent arrangement ($5,000 available, $1,000 purchase limit). Create a Lithic parent sandbox card using your existing enrollment if you want to run the Lithic path.
2. In **Choose the trust and execution model**, select **Flow A — Signed payment intent required**. In **Step 1**, choose **Purchase event tickets** or **Buy groceries**. Enter a natural-language purchase goal. Set the delegated limits in Step 2 **before** registering the agent (example: $1,000 maximum, $200 budget, $25 automatic approval). Click **Register agent + member's purchase scope**. The API saves the member-approved purpose and maximum limits with the cryptographic agent identity.
3. Click **Issue demo card + delegate**. A card cannot be issued with a broader merchant scope or higher limits than the member authorized at registration. Create a Lithic sandbox child card if using Lithic; its account token must match the parent.
4. The agent sends a **signed payment-intent request** in Step 3 (for example a $75 ticket). Above $25 it requires member approval; click **Member approve**. Only then does a short-lived READY intent exist.
5. Execute the separate authorization via **Send to Lithic sandbox** in Step 5. Lithic sends card + merchant + amount, **not** the agent signature. Occlet ASA requires exactly one matching READY signed payment intent and checks inherited parent limits. Its decision trace identifies `SIGNED_AGENT_INTENT`. The sandbox payment credential is not a cryptographically network-bound intent; same-card, same-merchant, same-amount correlation is only a demo approximation.
6. No READY intent, revoked card, wrong merchant/purpose, exceeded limits, or no member approval = **decline**.

## Flow B — network authorization arrives DIRECTLY; agent inferred from the card

1. Keep the same parent arrangement. Select **Flow B — Network direct (card-bound agent attribution)** and choose **Buy groceries** in Step 1. Register a **new** grocery agent, issue its delegated card in Step 2 and create its Lithic sandbox child card if desired.
2. **Skip Step 3 entirely.** A payment message arrives with only a card reference, amount and merchant. Use the Flow B buttons (**Send network-only auth**) for an offline example, or **Send to Lithic sandbox** in Step 5 for a real sandbox ASA callback.
3. The issuer resolves card → registered agent → member-approved grocery purpose → account and card policy. An authorized $20 grocery purchase can be approved and adds **one** parent hold, **without** any payment intent. The audit trace displays `INFERRED_FROM_CARD_BINDING`, and an explicit warning that this does **not** prove the agent initiated the transaction.
4. A $75 grocery purchase (above $25 automatic approval) **declines**; no live member approval can be awaited inside the network callback. Wrong merchant, unknown/revoked card, or parent pause also **decline**.

### The essential comparison

| Test | Flow A: Signed intent FIRST | Flow B: Network direct |
|---|---|---|
| Member purpose captured when registering agent | Yes | Yes |
| Agent authenticated on each attempted purchase | Signed Ed25519 API request | **No**; identity inferred from dedicated card |
| Purchase-specific payment intent required | **Yes**, or decline | **No** |
| Member approval above $25 | Before network authorization | Decline unless a separate preapproval/challenge integration is built |
| Network message contains agent signature | No | No |
| Lithic ASA authorizes if parent/card/policy pass | Yes, with exactly one READY matching intent | Yes, if within auto-approved member policy |
| Parent funding / financial hold | Same shared parent / one hold per approved transaction | Same shared parent / one hold per approved transaction |
| Security caveat | Merchant/amount/card correlation is not cryptographic network intent binding | Card binding alone cannot detect stolen credentials or prove who used them |

**Policy placement:** Lithic native Account/Card Authorization Rules can enforce account/card limits and velocities, and can decline transactions **before ASA**. The member's agent identity, registered purpose, signed intent verification, and conditional approval decision are enforced in **Occlet ASA**. Keep Lithic native rules inactive during the ASA demonstration if you want Occlet to show the decline reason.

**Sandbox-only**. Real network messages do not supply an arbitrary agent ID unless a supported, explicitly integrated network signal exists. The card-to-agent mapping used in Flow B is a deliberate demo assumption, not cryptographic authentication. The local parent `available_cents`/holds are illustrative and do not read or update Lithic's actual ledger. `purchase_goal` is human-readable/audit metadata; the demo enforces a purpose-to-merchant allowlist, not basket-level product detection. The member interface has no authentication. Do not expose port 8000.

---

# Occlet Shared-Parent Agentic Card — Lithic Sandbox v2

**What's new (November 2026):** Extends the previously working Occlet–Lithic agent demo with a **shared parent funding arrangement**, a Lithic **parent reference card**, and one or more **agentic virtual cards linked to the same Lithic `account_token`**. Adds inherited member limits, parent pause/unpause, local shared authorization holds without double-counting, a Lithic V2 native-Auth-Rules generator, optional creation of **INACTIVE draft rules**, and a transaction-result viewer that distinguishes Lithic rule declines from Occlet ASA declines.

**Historical base version status:** 24 local automated tests passed (including same-account linkage checks, parent holds, native-rule preview and fail-closed authorization scenarios). **No live Lithic API calls, external webhook enrollment, remote rule creation or production transactions were performed in preparing this package.** The user's sandbox key has not been embedded, stored or reused in this ZIP.

## Executive architecture

```text
MEMBER (local UI - not authenticated: demo only)
   |
   +--> Parent funding arrangement / account (one financial authorization state)
   |      example $5,000 available; parent maximum $1,000/txn
   |         |
   |         +--> Parent Lithic test virtual card ----+
   |         |                                       | SAME Lithic account_token
   |         +--> Agentic Lithic test virtual card --+
   |                    |
   |                    +-- Agent ID + delegated grant (Occlet)
   |                    +-- Agent spend budget / per-txn cap (Occlet)
   |                    +-- Member approval above $25 (Occlet)
   |                    +-- Cryptographically signed payment intent (Occlet)
   |
   +--> Lithic V2 Account and Card Auth Rules (optional; draft/then promote)
           | BEFORE ASA
           v
       Lithic Sandbox Auth Stream Access (ASA)
           |  Only if native rules didn't already decline
           v
       Occlet policy engine (existing signature, merchant and intent checks)
           | approved: ONE local illustrative parent financial hold
           v
       Lithic sandbox transaction result (APPROVED / DECLINED)
```

**Absolutely no new funding is created by the agentic card.** Lithic parent and agentic test cards are issued using the same card account token. The local `credit_limit_cents`, `held_cents` and `available_cents` demonstrate the authorization/ledger design only; they are **NOT** read from Lithic's actual ledger and **not** actual available credit. Real programs require Lithic-supported funding/balance configuration or Occlet's issuer ledger. The demo's holds are not cleared, reversed or expired automatically, and reset wipes them.

## One-page live sandbox demo

1. Install and start the two servers using `./start_demo.sh`, enroll the ASA responder through `lithic_setup.py` and expose **only port 8001** via an HTTPS tunnel (see the original instructions below). Keep the member UI on `http://127.0.0.1:8000` and never publish it.
2. Open the UI. Under **Step 0 — Shared parent funding**, set illustrative credit **$5,000**, shared account policy cap **$5,000**, parent per-txn maximum **$1,000**. Click **Enable parent arrangement**.
3. Click **Create Lithic parent test card**. This captures the parent's Lithic sandbox `account_token`. It is the account, not the parent's PAN, that defines the financial relationship.
4. Under **Step 1**, register the Ed25519 agent. Under **Step 2**, issue a delegated virtual card using per-txn limit **$1,000**, delegated budget **$200**, member approval threshold **$25**, and grocery merchant only. Click **Create Lithic agent card (same account)** in Step 5. The backend fails closed if Lithic returns an account token other than the parent's.
5. Optional: **Preview Lithic native rules** to inspect four account/card scoped policies. **Create inactive rule drafts** writes them to Lithic in shadow mode, **not active**. Review in the Lithic Dashboard and promote them individually only if you want Lithic to decline those cases **before** Occlet ASA. For an educational ASA-first demo, leave them as drafts.
6. Run these signed agent payment-intent scenarios, then click **Send to Lithic sandbox** for those with valid signed intents:

   | Signed agent purchase | Occlet policy | Lithic sandbox outcome (assuming ASA enrolled and no conflicting native rules) |
   |---|---|---|
   | $20 grocery | READY (no member approval) | Can APPROVE after ASA matches intent |
   | $75 grocery | AWAITING_MEMBER_APPROVAL | Without member approval: ASA declines; after approving the intent: ASA can approve |
   | $1,100 grocery | DENIED by parent max $1,000 | Do not send (native active parent rule could also decline this attempt) |
   | $10 unregistered agent | HTTP 401 | No signed intent, so ASA will not approve any later matching transaction |
   | $20 electronics | DENIED by agent merchant restriction | No READY intent; ASA declines |
   | $20 after parent PAUSED | DENIED inherited parent status | ASA declines even for otherwise approved card/intents |

7. Click **Check last Lithic result** after a simulation. It retrieves a sanitized transaction record and returns the network-sandbox `result`, `detailed_results`, and `rule_results`. If Lithic declines via **active** native rules first, **no Occlet ASA callback is expected**.
8. Optionally send a parent-card sandbox purchase via Step 0. Compare the **one shared parent hold** and the agent card's **separate policy usage counter**. Both use the same underlying Lithic account token. In this prototype a parent-card transaction is treated as a local cardholder-initiated simulation; a real issuer would still apply the normal physical/virtual-cardholder verification process.
9. For sibling agent cards, issue another delegated card for the same registered agent in Step 2, link its Lithic card, and switch among the cards using the **Active delegated card** selector. Each card has its own policy budget, but both share the parent transaction/available-credit mirror.

### What can be deployed as Lithic-native rules?

Generated by `lithic_native_rules.py` using the `POST /v2/auth_rules` endpoint:

- `ACCOUNT`-scoped `CONDITIONAL_ACTION` to decline > parent max per transaction.
- `ACCOUNT`-scoped `VELOCITY_LIMIT` (scope `ACCOUNT`) across **all cards** for an illustrative monthly ceiling.
- `CARD`-scoped `CONDITIONAL_ACTION` to decline > this agentic virtual card's cap.
- `CARD`-scoped `VELOCITY_LIMIT` (scope `CARD`) for delegated-card monthly use.

**Scope caveat:** Native rules for parent credit/max are program-specific; the shared account must truly be the correct account for all cards. Velocity limits are **eventually consistent**, not an authoritative ledger. They must never substitute for atomic funds availability. The demo's local agent `budget_cents` is cumulative until reset, while the generated native rule's period is monthly; these are not an exact lifetime-vs-calendar-period semantic match.

**Member approval is *not* implemented as a native Lithic challenge here.** Agent identity, signed intent, member approval and agent-purpose matching remain in Occlet. Lithic offers `CHALLENGE` with its Fraud Command feature subject to program eligibility; it has a separate decline/challenge/retry lifecycle. The demo uses **pre-approval before network authorization** instead, requiring no specific challenge entitlement. Native rule actions that decline early will not call ASA.

### Rule generator (can run without writing to Lithic)

```bash
python lithic_native_rules.py preview \
  --account-token "YOUR_LITHIC_SANDBOX_ACCOUNT_TOKEN" \
  --card-token "YOUR_AGENT_LITHIC_SANDBOX_CARD_TOKEN"
```

After reviewing and confirming the payloads, you may create four **INACTIVE SHADOW** rules:

```bash
python lithic_native_rules.py create-drafts \
  --account-token "YOUR_LITHIC_SANDBOX_ACCOUNT_TOKEN" \
  --card-token "YOUR_AGENT_LITHIC_SANDBOX_CARD_TOKEN"
```

This **does not promote** any rule. Use the Lithic Dashboard to manage promotions deliberately. Do not create duplicate drafts repeatedly. For real deployments, support rule update/deactivation, robust idempotency, re-sync, rule-evaluation telemetry and proper member authentication.

### Limitations and security

- The member UI has **no login**, the agent's signing key lives in browser memory, and all app state lives in process memory. **This is an intentionally local demo, not production authorization software.** Never expose port 8000. Expose only HMAC-verified bridge endpoint on port 8001.
- The ASA matcher uses card token + merchant acceptor + amount + exactly one READY signed intent. The network message does **not** cryptographically attest which agent initiated that merchant transaction; credential theft plus matching conditions is still a risk. Real agent identity at payment execution requires stronger transaction-specific credentials/proofs.
- Parent card reference is a separate sandbox card. A parent PAN is not what holds funds. Cards are financially related by their shared `account_token` and the program's funding account.
- In-memory parent holds are a *single booking per authorization reference*, not real Lithic balances or clearing/settlement bookkeeping. Native velocity limits can be exceeded slightly under concurrency and don't guarantee true available credit.
- Some Lithic sandbox accounts use a default account; others require an enrolled `account_token` to create cards. This package **verifies** the returned token and refuses mismatched account linkage. Your sandbox API entitlements and program model may require changing the provisioning step.
- The rule creation code only targets `https://sandbox.lithic.com` and never switches to production. No API keys or webhook secrets should be committed, recorded or pasted into the UI.
- The old Capital One merchant handoff example is preserved in Step 6, but is unrelated to this Lithic shared-parent scenario.

## Existing Lithic integration setup and reference instructions

# Occlet Agentic Virtual Card — Lithic sandbox integration

**Status:** Working local integration and automated tests. **No live Lithic account was accessed or ASA endpoint enrolled while building this ZIP.** The linked Lithic sandbox is a test environment, not a real card network. Requires your *own* Lithic sandbox API key, working outbound internet, and a public HTTPS callback URL.

## Architecture

```text
BROWSER/LOCAL UI (http://127.0.0.1:8000) — NOT public
  register agent Ed25519 key --> create member delegation
  signed agent payment intent --> READY/step-up/rejected
  create Lithic test virtual card --> link Lithic card token
  simulate transaction via Lithic sandbox API
                                     |
                                     v
                             Lithic sandbox ASA
                                     |
                                     v
                    HTTPS tunnel -> isolated public bridge
                         POST /lithic/asa (port 8001)
                         verify Lithic HMAC signature
                                     |
                                     v
                 loopback + shared-secret authenticated call
                 127.0.0.1:8000/api/internal/lithic/authorize
                                     |
                                     v
                    correlate linked card token + verified
                    merchant acceptor ID + USD amount +
                       exactly one READY signed intent
                                     |
                                     v
                       APPROVED or DECLINED to Lithic
```

**Critical limit:** the ASA message does **not** carry the agent's Ed25519 signature or the payment intent ID. An approved agent intent is a *prerequisite* to issuer approval, but possession of a test card and a matching amount/merchant could still execute that intent. To prove agent origin **for a specific network transaction**, a single-use payment credential, token binding or network-carried cryptographic reference is required. The demo fails closed on ambiguous matches but is **not production agent authentication at the network boundary**.

## Requirements

- Python 3.11+ and a Mac or Linux terminal
- **Lithic sandbox issuing API key** with permission to create virtual cards and simulate transactions
- Public HTTPS ingress such as Cloudflare's `cloudflared` **only to the bridge on port 8001**
- API key and secrets in shell environment; no `.env` file committed. `LITHIC_API_KEY` is *not* the webhook HMAC secret.
- **Do not use a Capital One PAN, real card details, a production Lithic key, or real account/member data.**

## 1. Install and start both servers (Mac)

Extract the ZIP and open Terminal inside the extracted `occlet_agentic_card_demo` folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
./start_demo.sh
```

The launcher prompts for your **new Lithic sandbox key** without echoing it. On the first run, press **Enter** at the ASA HMAC secret prompt (you won't have that secret until enrollment). It starts:

- `http://127.0.0.1:8000` — local browser UI and private decision engine
- `http://127.0.0.1:8001/lithic/asa` — isolated public-facing bridge (fails closed without HMAC secret)

The launcher generates and shares a random `OCCLET_BRIDGE_SECRET` between the two processes, without storing it. It binds **both servers only to loopback**. Never expose port 8000: the member/delegation UI has no login.

## 2. Prepare to enroll

Keep the launcher running. Open the UI at **http://127.0.0.1:8000**. The bridge is correctly not ready for signed authorizations yet. You will enroll its public URL next, obtain Lithic's *different* ASA HMAC secret, and then restart the launcher with that secret.

## 4. Expose ONLY port 8001 over HTTPS

Install [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) on your Mac (for example `brew install cloudflared`). In a **third Terminal**:

```bash
cloudflared tunnel --url http://127.0.0.1:8001
```

Copy the generated public HTTPS URL, for example `https://<random>.trycloudflare.com`. The registered ASA URL must end with `/lithic/asa`.

## 5. Enroll ASA and activate webhook verification

In another Terminal, activate `.venv` and securely enter your **new sandbox API key**:

```bash
source .venv/bin/activate
export LITHIC_API_KEY="$(python -c 'import getpass; print(getpass.getpass("Lithic SANDBOX key: "))')"
python lithic_setup.py enroll https://YOUR-TUNNEL.trycloudflare.com/lithic/asa
python lithic_setup.py status
```

Get Lithic's *per-program ASA HMAC verification secret* (not the API key). You can copy it to your password manager without printing it. To inspect it locally use `python lithic_setup.py secret` (this prints a secret, so be careful with terminal capture); or use a secure shell substitution if running the services in that same shell:

```bash
export LITHIC_ASA_SECRET="$(python lithic_setup.py secret)"
```

Now **stop** `./start_demo.sh` using Ctrl+C and **restart** it. At the prompt, enter the sandbox API key and then the retrieved **ASA HMAC secret**. This populates the bridge verifier. You can also start with both exported environment variables to bypass the prompts.

```bash
curl http://127.0.0.1:8001/healthz
```

Expected: `"configured": true`. Lithic may take a few minutes to start including signature headers after the HMAC key is first requested, so retry when the headers are active. The responder never approves unsigned requests.

**Note:** Restarting clears all in-memory agent, card and intent state. Create them after this step. If you restart the quick tunnel its URL may change, and you'll need to re-enroll the new URL.

## 6. Run the end-to-end agentic authorization demo

1. In browser, **Register agent** (browser creates Ed25519 private key).
2. **Issue demo card**. Set per-transaction limit to `$120`, overall budget `$300`, member approval above `$80`, and **Grocery only**.
3. Click **Create Lithic test card**. This uses your `LITHIC_API_KEY` locally and links the returned Lithic `card.token` to this demo card. The UI only displays token and last four; PAN/CVC are not stored in Occlet.
4. Enter `$49`, Grocery, then **Sign and request payment**. Server verifies signature and delegation. Expect `READY`.
5. Click **Send to Lithic sandbox**. The local server retrieves the sandbox PAN temporarily from Lithic, posts `POST /v1/simulate/authorize`; Lithic forwards a signed ASA request to the public bridge, which verifies the signature, calls the private Occlet decision endpoint and returns `{ "result": "APPROVED" }` on exact match.
6. Refresh/inspect the **Audit and transaction activity** section. Check Lithic's sandbox transaction dashboard for the same authorization.
7. Try **$95**: must click **Member approve** before sending. Without approval, ASA should reject.
8. Try a **$150** signed intent: declined before network request. To demonstrate a network-side decline, create a new ready Grocery `$49` intent but simulate **$50** or Electronics using a separate test request. Both fail matching.
9. Repeat a spent authorization with a new transaction token: decline. Exact retries of the same transaction token receive the cached decision.

Merchant mappings are intentionally rigid for the sandbox:

| UI merchant | Lithic `merchant_acceptor_id` | MCC | Merchant descriptor |
|---|---|---|---|
| `GROCERY_101` | `GROCERY101` | `5411` | `OCCLET GROCERY` |
| `ELECTRONICS_202` | `ELECTRONICS202` | `5732` | `OCCLET ELECTRONICS` |

The backend does **not** use merchant descriptor alone to identify merchant. It matches the acceptor ID, card token and amount.

## Running automated tests

```bash
python -m pytest -q
```

Expected: **24 passed**. Tests cover delegated payment flows, account inheritance, simulated financial holds and signed Lithic ASA payloads; they **do not** call the Lithic API.

## Safety and implementation limitations

- The member UI and key registry have **no login**; localhost access only. Public bridge is a separate ASGI app, only `/lithic/asa` and `/healthz`.
- ASA HMAC verifier checks `webhook-id`, `webhook-timestamp` (5-minute tolerance), and `webhook-signature` over raw body with HMAC-SHA256. Disabled/missing signature => HTTP 401, **never approve**.
- Lithic recommends ASA decisions within **3 seconds** and can time out after **6 seconds**. Internal bridge calls must be fast.
- In-memory state is lost on process restart; key bindings, audit events, idempotency and reservations require durable storage and atomic concurrency controls in production.
- This is a single-message authorization demonstration. No full clearing, refunds, disputes, auth reversals, partial capture or genuine ledger posting. `spent_cents` is educational *authorization hold-like* tracking, not financial settlement.
- No real card-network payments. Only Lithic sandbox simulated transactions. No real agent credential is sent to the network.
- Rotating/terminating a grant in this local demo does not automatically pause or delete a Lithic sandbox card. A real system must synchronize card-state controls across issuer and network.
- Successful `/simulate/authorize` HTTP response indicates accepted simulation request, **not necessarily final approval**. Inspect ASA audit event and Lithic's Transaction API/dashboard.
- Sandbox card creation and simulation require API-key privileges; Lithic's pre-ASA checks can decline before the endpoint is called.
- Cloudflare's quick tunnels are useful for local testing, not high-availability authorization workloads. Check your tunnel provider's security policies for financial test data.

### Source documentation

- https://docs.lithic.com/docs/auth-stream-access-asa
- https://docs.lithic.com/docs/simulating-transactions
- https://docs.lithic.com/docs/events-api
- https://docs.lithic.com/reference/postresponderendpoints
- https://docs.lithic.com/reference/getauthstreamsecret


---

## 7. Capital One consumer virtual-card handoff (optional; real payment is manual)

**IMPORTANT: The Capital One path is an agent-policy gate and browser handoff, NOT a Capital One issuing/authorization integration.** This application has no verified public API to receive a Capital One network authorization or return a real issuer decline. Occlet never calls Capital One, does not take a payment, and cannot guarantee that the merchant amount matches the signed request. The actual payment can be made only at a separate legitimate merchant checkout, which Capital One decides independently. Don't describe a local policy decline as a bank decline.

### Threat model and safety

- **Do not enter the card number, CVV, expiration, bank password or OTP anywhere in Occlet.** The optional last four digits are display-only. The server does not transmit them to a merchant.
- The agent **does not receive or store** the card credentials; it only receives an Occlet policy decision and, if approved, a pre-configured merchant website URL. You as member finalize payment manually at the merchant's HTTPS website using the Capital One virtual card.
- Expose neither port 8000 nor the member-control endpoints to the internet. No authentication is implemented for the member UI. The Lithic ASA test bridge (port 8001) must never be repurposed to expose this app.
- Never initiate purchases at your own merchant account to move money between accounts or generate artificial card payments. Prefer a legitimate low-dollar item you actually want; keep shipping/taxes below the intended amount.
- You can optionally set the virtual card's **bank-side spending limit** in Capital One's own card controls if your particular card supports that feature. It is independent of Occlet and not programmatically controlled here.
- The preflight uses agent-asserted price, not a merchant-signed cart or payment authorization. **You must inspect and confirm the final merchant total**. Do not rely on this demo for unattended shopping.
- If the AI agent holds the actual card PAN/CVV or can freely operate a browser with saved payment credentials, it can bypass Occlet entirely. For real enforcement, use issuer-side spend controls and/or merchant checkout integration where the card details are withheld from the agent.
- The same link to a merchant is public once opened; revoking a local grant cannot prevent someone from navigating to that merchant and paying with a previously stolen credential. The handoff token is only a one-time authorization to the *gateway*, not a one-time card credential.

### Demo walkthrough

1. Start `./start_demo.sh` as before, then open http://127.0.0.1:8000 (Lithic is not required for this path; press Enter to leave its sandbox key and ASA secret empty when prompted).
2. **Register a cryptographic agent** (Step 1): this creates an Ed25519 key in browser memory.
3. Under Step 6, click **Apply Capital One policy**, then in Step 2 click **Issue demo card + delegate**. It creates an **Occlet logical card/delegation**, not a new Capital One card.
4. Enter **only the last four** of your existing Capital One virtual card and an approved merchant HTTPS URL. The demonstration allows `target.com` for `GROCERY_101` and `bestbuy.com` for `ELECTRONICS_202`. Click **Bind external card reference**.
5. Click **Try $23.99**. Occlet verifies the agent signature, merchant and policy. When the payment intent is `READY`, click **Request real merchant checkout handoff**. A link appears for the configured merchant. Inspect your actual cart's final price, and if you want to complete a purchase, **manually** use the real virtual card on the merchant website. This is a real payment only when the merchant and Capital One complete it; Occlet cannot observe that result.
6. Click **Try $25.01**. Occlet refuses to create the intent (`403`); no handoff is released. **This is not a Capital One bank decline**.
7. Click **Try unknown agent**. An unknown agent ID is rejected (`401`); no handoff is released. You can also test `Replay signed request` in Step 3 for anti-replay behavior.
8. Click **Try $24.50**. It becomes `AWAITING_MEMBER_APPROVAL`. Click **Member approve** in Step 3 to move it to `READY`; then request checkout handoff.
9. Inspect **Audit and transaction activity** for every acceptance/decline and the issuer-not-contacted note.

### Backend policy, enforced independently of UI configuration

| Signed amount | Decision at Occlet | Capital One decision |
|---|---|---|
| Below $24.00 | Auto-allow if delegated, approved merchant and sufficient demo budget | Not invoked |
| $24.00–$25.00 inclusive | Requires member approval | Not invoked |
| Above $25.00 | Reject | Not invoked |
| Unknown agent or invalid signature | Reject 401 | Not invoked |

The gateway enforces a **second backstop** for the $25 limit and member approval when releasing a merchant link, including when the member's demo card settings were accidentally configured too loosely. The checkout handoff is one-time and expires with the signed intent (120 seconds). The backend does not process PCI data.

### Why the implementation has a hard limit

The merchant's card transaction is routed via the acquirer and Visa/Mastercard to Capital One. Our local app is **not in that network authorization path**. Capital One consumer virtual cards may offer lock/unlock and sometimes spend limits, but a local application cannot claim to enforce a transaction-level network decline unless Capital One exposes an eligible authorization control integration for your specific account. The credible end-to-end issuer-controlled demo remains the **Lithic ASA sandbox flow** described in Steps 1–6 above.

To evolve from a manual handoff to an unattended agent purchase, you would need a **merchant/PSP integration with a verified server-generated order total and secure payment credential handoff**, or an **issuer/network token control service** that can bind the specific transaction to the delegated agent and authorize it in real time.

Official references:
- Capital One virtual cards: https://www.capitalone.com/help-center/credit-cards/using-virtual-credit-cards/
- Capital One virtual-card terms: https://www.capitalone.com/digital/tools/digital-payments/virtual-cards/terms/

---

## NEW: W4 member-protected trip / parent card authorization

See [W4_GUIDE.md](W4_GUIDE.md). The **Use Cases · W4** tab demonstrates
`$500` shared funds, a `$300` protected trip with yield OFF, a `$350`
member's own grocery purchase declined until `$150` protection is explicitly
released, and the subsequent single parent hold and clearing lifecycle.
The same W4 policy is evaluated in the Lithic ASA path when the **parent**
sandbox card is linked. A separate signed `/lithic/events` webhook reconciles
subsequent transaction outcomes into the *existing* local SQL ledger journal.
Lithic Ledger access is NOT required for this demo.

## New member-facing portal

Open **http://127.0.0.1:8000/member** for a separate member experience that
shares the same Occlet APIs and PostgreSQL demo ledger as the engineering lab.
Use **Overview → Arrangements → My agents → W4 walkthrough → Activity** to present
the complete journey. See `MEMBER_PORTAL_GUIDE.md`. The engineering lab remains
at `/`. Never expose the unauthenticated member UI to the internet.
