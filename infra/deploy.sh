#!/usr/bin/env bash
# Deploy the Beside / Occlet agentic delegated card POC to Azure.
#
#   ./infra/deploy.sh all          # first time: infra -> secrets -> images -> easyauth -> app
#   ./infra/deploy.sh lithic       # after the app is up: enroll Lithic ASA + Events, store secrets, restart
#   ./infra/deploy.sh github-oidc  # let GitHub Actions deploy kalpr01/besidepoc without passwords
#
# Single steps: infra | secrets | images | easyauth | app | update | lithic | restart | status | github-oidc
#
# Secrets are read with hidden prompts or generated, and go straight into Key Vault through
# temp files (mode 600) that are deleted right away. Nothing secret is echoed or written to the repo.
set -euo pipefail
cd "$(dirname "$0")/.."

# Central US: East US 2 refused PostgreSQL Flexible Server for this subscription (SkuNotAvailable).
RG="${RG:-rg-besidepoc-cus}"
export LOCATION="${LOCATION:-centralus}"
DEPLOYMENT="besidepoc-infra"
PARAMS="infra/main.dev.bicepparam"
GITHUB_REPO="${GITHUB_REPO:-kalpr01/besidepoc}"
EASYAUTH_APP_NAME="besidepoc-easyauth"
OIDC_APP_NAME="besidepoc-github-oidc"
PYTHON="${PYTHON:-python3}"
TAG="${TAG:-$(git rev-parse --short=12 HEAD 2>/dev/null || date -u +%Y%m%d%H%M%S)}"

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

out() {
  local v
  v="$(az deployment group show -g "$RG" -n "$DEPLOYMENT" --query "properties.outputs.$1.value" -o tsv 2>/dev/null)" \
    && [[ -n "$v" ]] || die "No '$DEPLOYMENT' deployment in $RG yet. Run: ./infra/deploy.sh infra"
  printf '%s' "$v"
}

kv_has() { az keyvault secret show --vault-name "$KV" -n "$1" -o none 2>/dev/null; }

kv_get() { az keyvault secret show --vault-name "$KV" -n "$1" --query value -o tsv 2>/dev/null; }

# Reads the secret value from stdin; never on the command line.
kv_put_stdin() {
  local name="$1" tmp
  tmp="$(mktemp)"; chmod 600 "$tmp"
  cat >"$tmp"
  if [[ ! -s "$tmp" ]]; then rm -f "$tmp"; die "Empty value for secret $name"; fi
  az keyvault secret set --vault-name "$KV" -n "$name" --file "$tmp" -o none || { rm -f "$tmp"; die "Could not store $name"; }
  rm -f "$tmp"
  printf '   stored Key Vault secret: %s\n' "$name" >&2
}

prompt_secret() {
  local label="$1" value
  read -r -s -p "$label (hidden): " value </dev/tty; printf '\n' >&2
  printf '%s' "$value"
}

gen_token() { "$PYTHON" -c 'import secrets; print(secrets.token_urlsafe(40), end="")'; }

# Letters + digits only (URL-safe in DATABASE_URL) and meets Azure PG complexity (upper, lower, digit).
gen_pg_password() { "$PYTHON" -c 'import secrets; print("Pg" + secrets.token_hex(16) + "Z9", end="")'; }

ensure_az() {
  az account show -o none 2>/dev/null || die "Run 'az login' first"
  az extension add --name containerapp --upgrade --only-show-errors -y >/dev/null 2>&1 || true
}

load_names() {
  KV="$(out keyVaultName)"
  ACR="$(out acrName)"
  ACR_SERVER="$(out acrLoginServer)"
  APP="$(out containerAppName)"
  FQDN="$(out appFqdn)"
}

easyauth_client_id() {
  az ad app list --display-name "$EASYAUTH_APP_NAME" --query '[0].appId' -o tsv 2>/dev/null || true
}

deploy_bicep() {
  local deploy_app="$1" existing_kv=""
  export DEPLOY_APP="$deploy_app" IMAGE_TAG="$TAG"
  DEPLOYER_OID="$(az ad signed-in-user show --query id -o tsv)"; export DEPLOYER_OID
  CLIENT_IP="${CLIENT_IP:-$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || true)}"; export CLIENT_IP
  EASYAUTH_CLIENT_ID="${EASYAUTH_CLIENT_ID:-$(easyauth_client_id)}"; export EASYAUTH_CLIENT_ID
  export BUDGET_EMAIL="${BUDGET_EMAIL:-}"

  # Reuse the existing Postgres password if Key Vault already has it.
  existing_kv="$(az deployment group show -g "$RG" -n "$DEPLOYMENT" --query properties.outputs.keyVaultName.value -o tsv 2>/dev/null || true)"
  PG_ADMIN_PASSWORD=""
  if [[ -n "$existing_kv" ]]; then
    PG_ADMIN_PASSWORD="$(az keyvault secret show --vault-name "$existing_kv" -n postgres-admin-password --query value -o tsv 2>/dev/null || true)"
  fi
  [[ -n "$PG_ADMIN_PASSWORD" ]] || PG_ADMIN_PASSWORD="$(gen_pg_password)"
  export PG_ADMIN_PASSWORD

  az deployment group create -g "$RG" -n "$DEPLOYMENT" -p "$PARAMS" -o none
  unset PG_ADMIN_PASSWORD
}

cmd_infra() {
  log "Resource group $RG in $LOCATION"
  az group create -n "$RG" -l "$LOCATION" --tags project=besidepoc environment=dev -o none
  log "Phase 1: registry, Key Vault, identity, logs, Container Apps environment, PostgreSQL (5-10 min)"
  deploy_bicep false
  load_names
  printf '   Key Vault: %s\n   ACR: %s\n   App URL (once deployed): https://%s\n' "$KV" "$ACR" "$FQDN"
}

cmd_secrets() {
  load_names
  log "Secrets in $KV (Key Vault role assignments can take a minute to apply on first run)"
  if kv_has lithic-api-key && [[ "${FORCE:-0}" != 1 ]]; then
    echo "   lithic-api-key already set (FORCE=1 to replace)"
  else
    prompt_secret "Lithic SANDBOX API key" | kv_put_stdin lithic-api-key
  fi
  kv_has occlet-bridge-secret || gen_token | kv_put_stdin occlet-bridge-secret
  # Placeholders until Lithic enrollment (cmd_lithic). The bridge rejects every webhook with these.
  kv_has lithic-asa-secret   || printf 'pending' | kv_put_stdin lithic-asa-secret
  kv_has lithic-event-secret || printf 'pending' | kv_put_stdin lithic-event-secret
}

cmd_images() {
  load_names
  log "Build and push images (tag $TAG) to $ACR_SERVER"
  az acr login -n "$ACR" >/dev/null
  docker buildx build --platform linux/amd64 -f Dockerfile \
    -t "$ACR_SERVER/besidepoc-app:$TAG" -t "$ACR_SERVER/besidepoc-app:latest" --push .
  docker buildx build --platform linux/amd64 -f deploy/Dockerfile.proxy \
    -t "$ACR_SERVER/besidepoc-proxy:$TAG" -t "$ACR_SERVER/besidepoc-proxy:latest" --push .
}

cmd_easyauth() {
  load_names
  log "Entra ID login for https://$FQDN"
  local app_id sp_id me redirect="https://$FQDN/.auth/login/aad/callback"
  app_id="$(easyauth_client_id)"
  if [[ -z "$app_id" ]]; then
    app_id="$(az ad app create --display-name "$EASYAUTH_APP_NAME" --sign-in-audience AzureADMyOrg \
      --web-redirect-uris "$redirect" --enable-id-token-issuance true --query appId -o tsv)"
  else
    az ad app update --id "$app_id" --web-redirect-uris "$redirect" --enable-id-token-issuance true
  fi
  sp_id="$(az ad sp show --id "$app_id" --query id -o tsv 2>/dev/null || az ad sp create --id "$app_id" --query id -o tsv)"
  # Only users assigned to the app may sign in.
  az ad sp update --id "$sp_id" --set appRoleAssignmentRequired=true
  me="$(az ad signed-in-user show --query id -o tsv)"
  az rest --method POST --url "https://graph.microsoft.com/v1.0/servicePrincipals/$sp_id/appRoleAssignedTo" \
    --body "{\"principalId\":\"$me\",\"resourceId\":\"$sp_id\",\"appRoleId\":\"00000000-0000-0000-0000-000000000000\"}" \
    -o none 2>/dev/null || echo "   (you are already assigned)"
  if ! kv_has easyauth-client-secret || [[ "${FORCE:-0}" == 1 ]]; then
    az ad app credential reset --id "$app_id" --display-name easyauth --years 1 --query password -o tsv 2>/dev/null \
      | kv_put_stdin easyauth-client-secret
  fi
  echo "   Easy Auth client ID: $app_id"
  echo "   Add demo viewers: Entra ID > Enterprise applications > $EASYAUTH_APP_NAME > Users and groups"
}

cmd_app() {
  load_names
  local s
  for s in database-url lithic-api-key lithic-asa-secret lithic-event-secret occlet-bridge-secret; do
    kv_has "$s" || die "Key Vault secret '$s' missing. Run: ./infra/deploy.sh secrets"
  done
  [[ -n "$(easyauth_client_id)" ]] || echo "WARNING: no Entra ID app found, the member UI will be PUBLIC. Run: ./infra/deploy.sh easyauth"
  log "Phase 2: Container App $APP (image tag $TAG)"
  deploy_bicep true
  smoke_test
  cmd_status
}

# Image-only rollout: one new revision with all three containers updated together.
cmd_update() {
  load_names
  log "Roll out tag $TAG to $APP"
  local app_id tmp
  app_id="$(az containerapp show -n "$APP" -g "$RG" --query id -o tsv)"
  tmp="$(mktemp -d)"
  # Read with the same API version the PATCH uses: `az containerapp show` returns a newer shape
  # (imageType, scale.cooldownPeriod, ...) that 2024-03-01 rejects.
  az rest --method get --url "https://management.azure.com${app_id}?api-version=2024-03-01" -o json > "$tmp/app.json"
  jq --arg a "$ACR_SERVER/besidepoc-app:$TAG" --arg p "$ACR_SERVER/besidepoc-proxy:$TAG" \
    '{properties: {template: (.properties.template
        | .containers |= map(if .name == "proxy" then .image = $p else .image = $a end)
        | del(.revisionSuffix))}}' "$tmp/app.json" > "$tmp/patch.json"
  az rest --method patch --url "https://management.azure.com${app_id}?api-version=2024-03-01" \
    --body "@$tmp/patch.json" -o none
  rm -rf "$tmp"
  # The PATCH is asynchronous: wait until the revision running the new tag is the ready one.
  log "Waiting for the new revision"
  local i state="" latest="" ready="" image=""
  for i in $(seq 1 60); do
    read -r state latest ready image < <(az containerapp show -n "$APP" -g "$RG" \
      --query "[properties.provisioningState, properties.latestRevisionName, properties.latestReadyRevisionName, properties.template.containers[?name=='ui'].image | [0]]" -o tsv 2>/dev/null | tr '\n' ' ') || true
    [[ "$state" == Failed ]] && die "Revision provisioning failed. Check: az containerapp revision list -n $APP -g $RG -o table"
    if [[ "$state" == Succeeded && "$image" == *":$TAG" && -n "$latest" && "$latest" == "$ready" ]]; then
      echo "   $latest is ready"; break
    fi
    sleep 10
  done
  [[ "$latest" == "$ready" && "$image" == *":$TAG" ]] || die "New revision not ready after 10 minutes"
  smoke_test
}

smoke_test() {
  log "Waiting for https://$FQDN/healthz"
  local i
  for i in $(seq 1 36); do
    if curl -fsS --max-time 5 "https://$FQDN/healthz" >/dev/null 2>&1; then
      echo "   healthy"; return 0
    fi
    sleep 10
  done
  die "App did not become healthy in 6 minutes. Check: az containerapp logs show -n $APP -g $RG --container ui"
}

cmd_restart() {
  # A revision *restart* keeps the Key Vault secret values cached for that revision (up to ~30 min),
  # so changed secrets (e.g. the Lithic whsec_ values) are not picked up. Create a new revision instead.
  load_names
  local app_id tmp suffix i latest="" ready="" state=""
  suffix="s$(date -u +%m%d%H%M%S)"
  log "New revision ${APP}--${suffix} (same images, fresh Key Vault secrets)"
  app_id="$(az containerapp show -n "$APP" -g "$RG" --query id -o tsv)"
  tmp="$(mktemp -d)"
  az rest --method get --url "https://management.azure.com${app_id}?api-version=2024-03-01" -o json > "$tmp/app.json"
  jq --arg s "$suffix" '{properties: {template: (.properties.template | .revisionSuffix = $s)}}' "$tmp/app.json" > "$tmp/patch.json"
  az rest --method patch --url "https://management.azure.com${app_id}?api-version=2024-03-01" \
    --body "@$tmp/patch.json" -o none
  rm -rf "$tmp"
  for i in $(seq 1 60); do
    read -r state latest ready < <(az containerapp show -n "$APP" -g "$RG" \
      --query "[properties.provisioningState, properties.latestRevisionName, properties.latestReadyRevisionName]" -o tsv 2>/dev/null | tr '\n' ' ') || true
    [[ "$state" == Failed ]] && die "Revision provisioning failed. Check: az containerapp revision list -n $APP -g $RG -o table"
    if [[ "$latest" == *"--$suffix" && "$latest" == "$ready" ]]; then echo "   $latest is ready"; break; fi
    sleep 10
  done
  [[ "$latest" == *"--$suffix" && "$latest" == "$ready" ]] || die "New revision not ready after 10 minutes"
  smoke_test
}

cmd_lithic() {
  load_names
  [[ -n "${LITHIC_API_KEY:-}" ]] || LITHIC_API_KEY="$(kv_get lithic-api-key)"
  export LITHIC_API_KEY
  local base="https://$FQDN"

  if [[ "$(kv_get lithic-asa-secret)" == pending || "${FORCE:-0}" == 1 ]]; then
    log "Enroll Lithic sandbox ASA at $base/lithic/asa"
    "$PYTHON" lithic_setup.py enroll "$base/lithic/asa"
    "$PYTHON" lithic_setup.py secret | kv_put_stdin lithic-asa-secret
  else
    echo "   ASA already enrolled (FORCE=1 to redo)"
  fi

  if [[ "$(kv_get lithic-event-secret)" == pending || "${FORCE:-0}" == 1 ]]; then
    log "Create Lithic Events subscription at $base/lithic/events"
    "$PYTHON" - "$base/lithic/events" <<'PY' | kv_put_stdin lithic-event-secret
import sys
from lithic_client import lithic_request
sub = lithic_request("POST", "/event_subscriptions", payload={
    "url": sys.argv[1],
    "description": "Occlet W4 ledger reconciliation (Azure)",
    "event_types": ["card_transaction.updated"],
})
token = sub.get("token")
if not token:
    raise SystemExit("Lithic did not return a subscription token")
print("   Events subscription token: " + token, file=sys.stderr)
secret = lithic_request("GET", f"/event_subscriptions/{token}/secret").get("secret", "")
if not secret:
    raise SystemExit("Lithic did not return the subscription secret")
sys.stdout.write(secret)
PY
  else
    echo "   Events subscription already set (FORCE=1 to redo)"
  fi
  unset LITHIC_API_KEY
  cmd_restart
}

cmd_status() {
  load_names
  log "Status"
  echo "   Member UI:       https://$FQDN/member"
  echo "   Engineering lab: https://$FQDN/"
  echo "   Lithic ASA:      https://$FQDN/lithic/asa"
  echo "   Lithic Events:   https://$FQDN/lithic/events"
  curl -fsS --max-time 5 "https://$FQDN/healthz" 2>/dev/null && echo || echo "   (healthz not reachable yet)"
}

cmd_github_oidc() {
  load_names
  log "GitHub Actions OIDC for $GITHUB_REPO (branch main)"
  local app_id sp_id rg_id acr_id
  app_id="$(az ad app list --display-name "$OIDC_APP_NAME" --query '[0].appId' -o tsv)"
  [[ -n "$app_id" ]] || app_id="$(az ad app create --display-name "$OIDC_APP_NAME" --query appId -o tsv)"
  sp_id="$(az ad sp show --id "$app_id" --query id -o tsv 2>/dev/null || az ad sp create --id "$app_id" --query id -o tsv)"
  az ad app federated-credential create --id "$app_id" -o none --parameters "{
    \"name\": \"besidepoc-main\",
    \"issuer\": \"https://token.actions.githubusercontent.com\",
    \"subject\": \"repo:$GITHUB_REPO:ref:refs/heads/main\",
    \"audiences\": [\"api://AzureADTokenExchange\"]}" 2>/dev/null || echo "   (federated credential already exists)"
  rg_id="$(az group show -n "$RG" --query id -o tsv)"
  acr_id="$(az acr show -n "$ACR" --query id -o tsv)"
  az role assignment create --assignee-object-id "$sp_id" --assignee-principal-type ServicePrincipal \
    --role AcrPush --scope "$acr_id" -o none 2>/dev/null || true
  az role assignment create --assignee-object-id "$sp_id" --assignee-principal-type ServicePrincipal \
    --role Contributor --scope "$rg_id" -o none 2>/dev/null || true
  cat <<EOF

   Set these GitHub repository variables (Settings > Secrets and variables > Actions > Variables).
   They are identifiers, not secrets. With the GitHub CLI:

   gh variable set AZURE_CLIENT_ID       -R $GITHUB_REPO --body $app_id
   gh variable set AZURE_TENANT_ID       -R $GITHUB_REPO --body $(az account show --query tenantId -o tsv)
   gh variable set AZURE_SUBSCRIPTION_ID -R $GITHUB_REPO --body $(az account show --query id -o tsv)
   gh variable set AZURE_RESOURCE_GROUP  -R $GITHUB_REPO --body $RG
EOF
}

main() {
  local step="${1:-}"
  [[ -n "$step" ]] || { sed -n '2,12p' "$0"; exit 1; }
  ensure_az
  case "$step" in
    infra) cmd_infra ;;
    secrets) cmd_secrets ;;
    images) cmd_images ;;
    easyauth) cmd_easyauth ;;
    app) cmd_app ;;
    update) cmd_update ;;
    lithic) cmd_lithic ;;
    restart) cmd_restart ;;
    status) cmd_status ;;
    github-oidc) cmd_github_oidc ;;
    all) cmd_infra; cmd_secrets; cmd_images; cmd_easyauth; cmd_app
         log "Next: ./infra/deploy.sh lithic   (enroll ASA + Events against https://$FQDN)" ;;
    *) die "Unknown step '$step'" ;;
  esac
}

main "$@"
