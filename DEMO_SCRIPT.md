# Occlet — Two-Flow Live Demo Script (5–10 minutes)

## Shared setup

- Start the local services using `./start_demo.sh` (see README) and load `http://127.0.0.1:8000`.
- Configure the shared parent funding arrangement: illustrative $5,000 available, $1,000 purchase ceiling and $5,000 parent policy cap.
- If testing real Lithic SANDBOX requests, create the Lithic parent test card once and ensure the existing ASA webhook bridge is enrolled and reachable. Keep the member UI private on localhost. For offline testing, skip Lithic entirely.
- Important: all cards share **the same parent funding arrangement**. The demo's available credit/holds are local models, not actual Lithic financial balances.

## Flow A — registered purpose + signed intent BEFORE card authorization

1. Choose **Flow A — Signed payment intent required**.
2. Choose **Purchase event tickets**. The member enters the purpose `Purchase event tickets within my approved budget`.
3. Configure max purchase **$1,000**, total agent budget **$200**, member automatic approval limit **$25**.
4. Click **Register agent + member's purchase scope**, then **Issue demo card + delegate**. If using Lithic, click **Create Lithic agent card (same account)**.
5. Set merchant **TICKETS_303**, amount **$75**. Click **Sign and request payment**: result `AWAITING_MEMBER_APPROVAL`.
6. Click **Member approve**, creating a READY purchase intent.
7. Click **Send to Lithic sandbox** (or offline **Send network authorization**). Occlet responds only when the card + merchant + amount match one unique READY signed intent. Expected: **APPROVED**, 1 parent hold for $75.
8. Show the **Flow comparison — latest authorization evidence**: `SIGNED_AGENT_INTENT` and a linked `payment_intent_id`.
9. Optional negative: if you skip the signed intent on another Flow A card, Lithic ASA declines. Wrong merchant or expired/revoked intent also fails.

## Flow B — direct network authorization; no prior signed payment intent

1. Keep the same parent arrangement. Choose **Flow B — Network direct** and **Buy groceries**.
2. Member defines goal `Purchase weekly household groceries` and the same $1,000 / $200 / $25 limits. Register a **new** grocery agent and issue a NEW Flow B virtual card.
3. If using Lithic, click **Create Lithic agent card (same account)**.
4. **Do not click Sign and request payment.** Set merchant **GROCERY_101**, amount **$20**.
5. Click **Send network-only auth (local simulation)** or **Send to Lithic sandbox**. Occlet infers the registered agent from the virtual-card binding and evaluates purpose, limits, parent status and shared available funds. Expected: **APPROVED**, 1 additional $20 parent hold.
6. Show the trace: `INFERRED_FROM_CARD_BINDING`, `payment_intent_id = NONE`, no agent signature in the authorization message. Explain: **this is card attribution, not proof of who actually used the card**.
7. Set amount **$75** and resend: expected **DECLINED** (`MEMBER_APPROVAL_REQUIRED_BEFORE_PURCHASE`). The network authorization cannot pause to obtain asynchronous member approval in this demo.
8. Click **Test unknown card**, or pause the parent account. Both should decline.
9. The shared parent hold total should now be **$95** for the two approved purchases ($75 + $20), not twice that amount.

## Close the architecture discussion

- **Shared:** parent funding arrangement, virtual-card controls, member limits, issuer authorization and one hold per authorization.
- **Different:** Flow A has signed per-payment agent authentication plus an approval/intent record before network authorization; Flow B has only instrument-to-agent attribution at issuer time.
- **Lithic native:** simple account/card amount and velocity rules, evaluated before ASA if active.
- **Occlet ASA:** delegated purposes, identity and intent checks, inherited parent controls, member approvals, and the decision trace.
- **Production gap:** Issuer-side merchant/amount correlation in Flow A is not a network-carried cryptographic payment mandate. Flow B assumes exclusive agent control of the card credential; credential theft or reuse by someone else is not detected by agent identity verification.

Run automated tests from the project folder with `PYTHONPATH=. pytest -q`.
