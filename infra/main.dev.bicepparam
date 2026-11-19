// Values that change per run come from environment variables set by infra/deploy.sh.
// Nothing secret is stored in this file.
using './main.bicep'

param prefix = 'besidepoc'
param envName = 'dev'
param location = readEnvironmentVariable('LOCATION', 'eastus2')

param deployApp = bool(readEnvironmentVariable('DEPLOY_APP', 'false'))
param imageTag = readEnvironmentVariable('IMAGE_TAG', 'latest')

param postgresAdminLogin = 'occlet_demo'
param postgresAdminPassword = readEnvironmentVariable('PG_ADMIN_PASSWORD')
param clientIpAddress = readEnvironmentVariable('CLIENT_IP', '')

param deployerPrincipalId = readEnvironmentVariable('DEPLOYER_OID')
param easyAuthClientId = readEnvironmentVariable('EASYAUTH_CLIENT_ID', '')

param budgetAmount = int(readEnvironmentVariable('BUDGET_AMOUNT', '75'))
param budgetContactEmail = readEnvironmentVariable('BUDGET_EMAIL', '')
param budgetStartDate = '2026-11-01'
