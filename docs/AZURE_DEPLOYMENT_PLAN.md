# Azure Deployment Plan — Beside / Occlet Agentic Delegated Card POC

Target repo: `github.com/kalpr01/besidepoc` · Environment: `dev` (POC, sandbox money only) · Region: **Central US**, resource group `rg-besidepoc-cus`

> **Why not East US 2:** on Nov 19, 2026 Azure rejected PostgreSQL Flexible Server B1ms in East US 2 for this pay-as-you-go subscription (`SkuNotAvailable`). It failed with and without a pinned zone, even though quota (384 vCores) and the capability list were fine. Postgres must sit in the same region as the app, because the ui writes its state on every request, including Lithic ASA callbacks with a 2-second bridge timeout. So the whole stack moved to Central US. `PG_SKU` and `PG_ZONE` can override the Postgres size and zone. The empty `rg-besidepoc-dev` group in East US 2 can be deleted.

## 1. What we are deploying (from the code)

| Runtime piece | Today (local) | Notes that drive the Azure design |
|---|---|---|
| `app:app` — Beside member UI + Occlet APIs + W4 + Occlet ASA decision | `uvicorn` on `127.0.0.1:8000` | **No authentication** on member/setup endpoints → must sit behind a login. Holds in-process state (`_lock`, nonces, intents) → **exactly one replica**. |
| `lithic_bridge:app` — public Lithic ASA + Events responder | `uvicorn` on `127.0.0.1:8001` | Must be **public** for Lithic. Verifies HMAC, then calls `http://127.0.0.1:8000/api/internal/...` → **must share localhost with the UI app** (same pod). |
| PostgreSQL 16 — arrangement registry + Occlet demo ledger (`db/001..003`) | Docker `postgres:16-alpine` | Migrations run automatically at startup (`CREATE ... IF NOT EXISTS`, no extensions). |
| Lithic sandbox | external | Needs `LITHIC_API_KEY`, `LITHIC_ASA_SECRET`, `LITHIC_EVENT_SECRET`. ASA callback has a tight timeout → **no scale-to-zero / cold starts**. |
| `OCCLET_BRIDGE_SECRET` | generated each run by `start_demo.sh` | Both containers need the same value → store once in Key Vault. |

## 2. Target architecture

```
                         Lithic sandbox ──(ASA + Events, HMAC-signed)──┐
                                                                       ▼
Browser (you, demo audience) ──HTTPS──► Container Apps ingress + Entra ID login (Easy Auth)
                                         │   excluded from login: /lithic/asa, /lithic/events, /healthz
                                         ▼
                ┌──────────── Container App "ca-besidepoc" (1 replica, shared localhost) ───────────┐
                │  proxy  (Caddy :8080)  /lithic/* ──► bridge :8001                                  │
                │                        /api/internal/* ──► 403 (blocked from outside)              │
                │                        everything else ──► ui :8000                                │
                │  ui     (uvicorn app:app 127.0.0.1:8000)  ◄── bridge forwards verified ASA/events  │
                │  bridge (uvicorn lithic_bridge:app 127.0.0.1:8001)                                 │
                └────────────────────────────────────────────────────────────────────────────────────┘
                         │ secrets via managed identity                │ TLS (sslmode=require)
                         ▼                                             ▼
                    Key Vault                         Azure Database for PostgreSQL Flexible Server
```

Why one Container App with three containers: the bridge's hard-coded `127.0.0.1:8000` call and the loopback-only check on `/api/internal/*` only work when both apps share a network namespace. Container Apps sidecars give that with no code change, and a single ingress lets Easy Auth protect the UI while leaving the two Lithic webhook paths open (they are protected by their own HMAC signatures).

## 3. Resource list

Naming: `<type>-besidepoc-dev`. ACR, Key Vault and PostgreSQL need globally unique names, so `infra/main.bicep` appends a 6-character suffix derived from the resource group (for example `kv-besidepoc-a1b2c3`). All resources are in one resource group, so one delete tears the POC down.

| # | Resource | SKU / setting | Purpose |
|---|---|---|---|
| 1 | Resource group `rg-besidepoc-cus` | Central US | Holds everything |
| 2 | Log Analytics workspace `log-besidepoc-dev` | PerGB2018, 30-day retention | Required by the Container Apps environment; container stdout/stderr |
| 3 | Application Insights `appi-besidepoc-dev` *(optional)* | Workspace-based | Request traces / failures during demo |
| 4 | Container Registry `acrbesidepocdev` | **Basic**, admin user **disabled** | Stores the app image |
| 5 | User-assigned managed identity `id-besidepoc-dev` | — | `AcrPull` on ACR, `Key Vault Secrets User` on Key Vault |
| 6 | Key Vault `kv-besidepoc-dev` | Standard, RBAC mode, soft-delete on | Secrets: `lithic-api-key`, `lithic-asa-secret`, `lithic-event-secret`, `occlet-bridge-secret`, `database-url` |
| 7 | Container Apps environment `cae-besidepoc-dev` | Consumption (workload profiles) | Hosts the app |
| 8 | Container App `ca-besidepoc` | 3 containers (proxy 0.25 vCPU/0.5 Gi, ui 0.5/1 Gi, bridge 0.25/0.5 Gi); **min = max = 1 replica**; external ingress, target port 8080, HTTPS only | UI + Occlet ASA + Lithic bridge |
| 9 | Container App auth config | Microsoft Entra ID provider, "require authentication", `excludedPaths`: `/lithic/asa`, `/lithic/events`, `/healthz` | Puts a login in front of the unauthenticated member UI |
| 10 | Entra ID app registration `besidepoc-easyauth` | "Assignment required" = Yes; assign yourself + demo viewers | Who may open the UI |
| 11 | PostgreSQL Flexible Server `psql-besidepoc-dev` | **Burstable B1ms**, PG **16**, 32 GiB, no HA, 7-day backups; DB `occlet`, user `occlet_demo` | Arrangement registry + Occlet authoritative demo ledger |
| 12 | Postgres firewall | POC: "Allow Azure services" + your client IP · Hardened: VNet + private access (see §6) | Network access to DB |
| 13 | Entra ID app registration `besidepoc-github-oidc` | Federated credential for `repo:kalpr01/besidepoc:ref:refs/heads/main`; roles `AcrPush` (ACR) + `Contributor` (RG) | Password-less GitHub Actions deploy |
| 14 | Budget `budget-besidepoc-dev` | e.g. $75/month, alerts at 80% / 100% | Cost guardrail |

Not needed for this POC: Front Door / App Gateway, API Management, Redis, AKS, a second region, Lithic Ledger (you are using the Occlet PostgreSQL ledger).

### Rough monthly cost (always-on, list prices — confirm in the Azure pricing calculator)

| Item | ≈ USD/month |
|---|---|
| Container App, 1 vCPU / 2 GiB always-on (after free grant) | 25–55 |
| PostgreSQL B1ms + 32 GiB storage | 15–20 (stop the server between demos to pay storage only) |
| ACR Basic | 5 |
| Log Analytics / App Insights (low volume) | 0–5 |
| Key Vault | < 1 |
| **Total** | **≈ 45–85** |

## 4. Container App configuration

Environment variables (secrets are Key Vault references through the managed identity):

| Variable | ui | bridge | Source |
|---|---|---|---|
| `DATABASE_URL` | ✓ | | KV `database-url` = `postgresql://occlet_demo:<pwd>@psql-besidepoc-dev.postgres.database.azure.com:5432/occlet?sslmode=require` |
| `LITHIC_API_KEY` | ✓ | | KV `lithic-api-key` (**sandbox** key) |
| `OCCLET_BRIDGE_SECRET` | ✓ | ✓ | KV `occlet-bridge-secret` (generate once: `python -c 'import secrets;print(secrets.token_urlsafe(40))'`) |
| `LITHIC_ASA_SECRET` | | ✓ | KV `lithic-asa-secret` (from ASA enrollment, `whsec_...`) |
| `LITHIC_EVENT_SECRET` | | ✓ | KV `lithic-event-secret` (from Events subscription, different from ASA secret) |
| `LITHIC_AUTH_FROM_BALANCE_CONFIRMED` | leave unset | | Only set to `1` once Lithic confirms |

Do **not** set `OCCLET_ALLOW_TESTCLIENT` in Azure.

Health probes: liveness/readiness on `/healthz` (bridge, via proxy) and `/api/demo/version` (ui).

## 5. Deployment files in the repo

| File | Content |
|---|---|
| [`Dockerfile`](../Dockerfile) | `python:3.12-slim`, non-root (uid 10001). One image `besidepoc-app` serves both `ui` (default CMD, `127.0.0.1:8000`) and `bridge` (command override, `127.0.0.1:8001`). |
| [`deploy/Dockerfile.proxy`](../deploy/Dockerfile.proxy) + [`deploy/Caddyfile`](../deploy/Caddyfile) | Image `besidepoc-proxy` on `:8080`. Returns 403 for `/api/internal/*`, sends `/lithic/*` and `/healthz` to the bridge and everything else to the ui. |
| [`.dockerignore`](../.dockerignore) | Keeps `.env`, `.venv`, tests and docs out of the images |
| [`infra/main.bicep`](../infra/main.bicep) + [`infra/main.dev.bicepparam`](../infra/main.dev.bicepparam) | Resources #2–#9, #11, #12 and #14. Phase 1 (`deployApp=false`) creates the platform; phase 2 creates the Container App. Bicep writes `database-url` and `postgres-admin-password` to Key Vault itself. |
| [`infra/deploy.sh`](../infra/deploy.sh) | Steps `infra`, `secrets`, `images`, `easyauth`, `app`, `update`, `lithic`, `restart`, `status`, `github-oidc` and `all`. Creates the Entra ID apps (#10, #13) with `az ad`. |
| [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml) | `pytest -q` on every push and PR. On `main`, once the OIDC variables exist, it runs `deploy.sh images` and then `deploy.sh update`, which creates one new revision with all three containers updated. |

Before the first push: keep `.env` out (already in `.gitignore`) and run a secret scan on the folder, because the repo will hold Lithic integration code.

**Checked locally (Nov 19, 2026):** both images build. With the three containers sharing one network namespace (as in a Container App), `/healthz`, `/member`, `/` and `/api/demo/version` return 200, `/api/internal/*` from outside returns 403, and an unsigned `/lithic/asa` returns 401. The bridge reaches the ui's internal endpoint over loopback (200). Against Postgres 16 the startup migrations created 21 tables and W4 setup opened the $500 book. `pytest`: 80 passed, 2 skipped. **Not yet checked in Azure:** the Bicep deployment itself, the Key Vault references, Easy Auth's `excludedPaths`, the `az rest` PATCH rollout and live Lithic callbacks.

## 6. POC vs hardened network option

- **POC (recommended to start):** Postgres with public endpoint, TLS required, firewall = Azure services + your IP, strong generated password in Key Vault. Fastest to stand up.
- **Hardened (if a credit union reviews it):** VNet `vnet-besidepoc-dev` with two subnets (Container Apps env /23 delegated, Postgres /28 delegated), Postgres private access + private DNS zone `privatelink.postgres.database.azure.com`, Key Vault private endpoint. Adds ~$0–10/month and about half a day of work.

## 7. Deployment steps

Prerequisites: `az login` (as an account that can create Entra ID app registrations), Docker running, and `source .venv/bin/activate` so the Lithic steps can import `httpx`. Optional: `export BUDGET_EMAIL=you@example.com` to create the budget.

1. `./infra/deploy.sh all`. It runs:
   - `infra`: creates the RG and phase-1 resources (5–10 min) and prints the future app URL.
   - `secrets`: a hidden prompt for the Lithic **sandbox** API key. It also generates the bridge secret and writes `pending` placeholders for the two webhook secrets.
   - `images`: builds both images for linux/amd64 and pushes them to ACR.
   - `easyauth`: creates `besidepoc-easyauth`, requires user assignment, assigns you and stores the client secret in Key Vault.
   - `app`: phase 2, the Container App. It then waits for `/healthz`.
2. Open `https://<app-fqdn>/member` and sign in. The startup migration creates all tables (`db/001..003`).
3. `./infra/deploy.sh lithic`. It enrolls ASA at `/lithic/asa`, creates the Events subscription at `/lithic/events`, writes both `whsec_` secrets straight to Key Vault and restarts the revision.
4. `./infra/deploy.sh github-oidc`. It creates `besidepoc-github-oidc` with a federated credential for `kalpr01/besidepoc` `main`, then prints the four `gh variable set` commands. After you set those variables, every push to `main` tests and redeploys.

If phase 2 fails on the first run with a Key Vault access error, the role assignment hasn't propagated yet. Wait a minute and rerun `./infra/deploy.sh app`.

## 8. Demo runbook in Azure (your focus areas)

1. **Member account + card:** `/member` → configure the parent arrangement → link the existing Lithic sandbox parent card (account token + card token only, never the PAN).
2. **Delegated cards:** *Delegated Card* menu → register an agent with purpose and limits → issue the linked Lithic sandbox child card.
3. **Purchase intent:** the agent submits a signed intent; above the auto-approval limit the member approves; then *Send to Lithic sandbox* → ASA → Occlet decision trace shows `SIGNED_AGENT_INTENT`.
4. **W4 (member's own purchase, yield off):** *Use Cases · W4* → Start fresh → $350 groceries via Lithic → `PROTECTED_BY_MEMBER` decline → one-tap release of $150 → retry → approved, one hold in Postgres → Events API clearing $345.

**On Layer 1 and Layer 2 in W4:** in the Q Delegated Card document, W4 is a **Layer 1** decision (errand card check: hard rules against the arrangement and ledger). Layer 2 is the enhanced authorization service, which applies the competing-intent order and can only tighten a decision. Showing both layers in the W4 decision trace (for example Layer 1 decline → release → Layer 1 pass + Layer 2 competing-intent check pass) is an app change. It does not need an Azure resource: both layers run inside the `ui` container's Occlet ASA path.

## 9. Teardown

`az group delete -n rg-besidepoc-cus` removes everything (and `rg-besidepoc-dev` for the leftover East US 2 attempt). Also delete the two Entra ID app registrations and remove the ASA/Events endpoints in the Lithic sandbox dashboard.
