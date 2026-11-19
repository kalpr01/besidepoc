# UI navigation: Local vs. Lithic

The Occlet browser UI now separates **execution environments** into two accessible tabs while keeping shared registration, delegation, and signed-intent setup above them.

## Shared setup (applies to both tabs)

0. Choose the card's flow: **Flow A — Signed payment intent required** or **Flow B — Card-bound network attribution**. The flow is set when you issue a virtual demo card.
1. Configure the parent arrangement's inherited transaction and account policy controls. This does **not** create a Lithic balance or initialize the PostgreSQL ledger.
2. Register an agent for **GROCERIES** or **TICKETS** and define what it may purchase.
3. Issue a virtual delegated card with merchant, per-transaction, budget and member approval limits.
4. **Flow A only:** sign a purchase intent and obtain member approval if needed. Flow B skips this step.

## Local Demo tab

- **L1 · PostgreSQL ledger:** Start Docker/PostgreSQL (see `OCCLET_LOCAL_LEDGER_DEMO.md`), then initialize synthetic funding. This makes PostgreSQL the authoritative *demo-only* financial book. Read cash, available funds, and holds; inspect journals; add demo funds; release or clear authorizations; inspect arrangement records.
- **L2 · Signed-intent network simulation (Flow A):** Use the READY intent from shared Step 4 and send the separate local network authorization. A mismatched merchant should decline.
- **L3 · Network direct (Flow B):** Send an unsigned network-like message, above-limit test, or unknown card. It attributes the agent from the dedicated card binding without proving the initiating agent's cryptographic identity.
- **L4 · Capital One (optional):** Preserves the pre-checkout **handoff only**; Occlet does not control Capital One's actual issuer authorization.

## Lithic Sandbox tab

- **S1 · Parent card:** Link an already-issued Lithic parent card with the account and card **UUIDs** (not card number or CVV), or create a new sandbox parent card. You can also run a sandbox parent-card authorization simulation.
- **S2 · Agentic card + ASA:** Create the Lithic agent card under the same account token as the parent. For Flow A, prepare and approve the signed intent under shared Step 4; for Flow B, skip it. Simulate an authorization via Lithic and inspect the result. Native rule creation remains draft-only unless you explicitly promote rules in Lithic.
- **S3 · Lithic Ledger access:** Optional financial-account discovery and diagnostics. The card-only sandbox can still operate even when the Lithic `ISSUING` financial account is unavailable. Do **not** interpret PostgreSQL balances as Lithic Ledger balances.

## Shared audit

The latest API decision, Flow A/Flow B evidence comparison, and activity/audit trace are available below either execution tab.

## Navigation and safety

The tabs are view-only: changing the selected tab **does not** change funding authority, issue cards, or initiate an authorization. The URL fragment (`#local` or `#lithic`) remembers the selected tab across reloads. Use the Left/Right arrow keys on focused tabs to switch views. Existing card and agent data are not duplicated by tab navigation.

Keep the member/dashboard server bound to `127.0.0.1` and do not expose it via a public tunnel. For Lithic ASA callbacks, expose only the dedicated signed webhook listener as described in the setup guide. No real PAN, CVV, or live API credentials belong in screenshots or project files.
