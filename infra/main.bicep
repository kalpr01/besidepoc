// Beside / Occlet agentic delegated card POC — Azure infrastructure.
// Two-phase deploy (driven by infra/deploy.sh):
//   phase 1  deployApp=false  -> registry, vault, identity, logs, environment, PostgreSQL
//   phase 2  deployApp=true   -> container app (needs images in ACR + secrets in Key Vault)
targetScope = 'resourceGroup'

@description('Short name used in every resource name.')
param prefix string = 'besidepoc'

@description('Environment label, e.g. dev.')
param envName string = 'dev'

param location string = resourceGroup().location

@description('Create the Container App (phase 2). Requires images in ACR and secrets in Key Vault.')
param deployApp bool = false

@description('Image tag for besidepoc-app and besidepoc-proxy.')
param imageTag string = 'latest'

@description('PostgreSQL administrator login (also used by the app in this POC).')
param postgresAdminLogin string = 'occlet_demo'

@secure()
@description('PostgreSQL administrator password. Letters and digits only so it is URL-safe in DATABASE_URL.')
param postgresAdminPassword string

@description('PostgreSQL compute size. Standard_B* = Burstable, Standard_D* = General Purpose.')
param postgresSku string = 'Standard_B1ms'

@description('Availability zone for PostgreSQL (1, 2, 3). Empty = let Azure choose. Pin one if you get SkuNotAvailable.')
param postgresZone string = ''

@description('Your public IP for psql access to the database. Empty = no client rule.')
param clientIpAddress string = ''

@description('Object ID of the person running the deployment; gets Key Vault Secrets Officer.')
param deployerPrincipalId string

@description('Entra ID app registration client ID for Easy Auth. Empty = no login in front of the UI (not recommended).')
param easyAuthClientId string = ''

@description('Monthly budget in USD for this resource group. 0 = no budget.')
param budgetAmount int = 75

@description('Email that receives budget alerts.')
param budgetContactEmail string = ''

@description('First day of the month the budget starts (yyyy-MM-01). Keep fixed across redeploys.')
param budgetStartDate string = '2026-11-01'

var suffix = take(uniqueString(resourceGroup().id), 6)
var tags = {
  project: prefix
  environment: envName
  purpose: 'agentic-delegated-card-poc'
}
var acrName = 'acr${prefix}${suffix}'
var kvName = 'kv-${prefix}-${suffix}'
var pgName = 'psql-${prefix}-${suffix}'
var appName = 'ca-${prefix}'
var dbName = 'occlet'

// Built-in role definition IDs
var roleAcrPull = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var roleKvSecretsUser = '4633458b-17de-408a-b874-0445c86b69e6'
var roleKvSecretsOfficer = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'

// ---------------------------------------------------------------- logs
resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${prefix}-${envName}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${prefix}-${envName}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
  }
}

// ---------------------------------------------------------------- identity + registry
resource uami 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${prefix}-${envName}'
  location: location
  tags: tags
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: false
  }
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, uami.id, roleAcrPull)
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleAcrPull)
    principalId: uami.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------- key vault
resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: kvName
  location: location
  tags: tags
  properties: {
    tenantId: tenant().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    publicNetworkAccess: 'Enabled'
  }
}

resource kvUami 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kv.id, uami.id, roleKvSecretsUser)
  scope: kv
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleKvSecretsUser)
    principalId: uami.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource kvDeployer 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kv.id, deployerPrincipalId, roleKvSecretsOfficer)
  scope: kv
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleKvSecretsOfficer)
    principalId: deployerPrincipalId
  }
}

// ---------------------------------------------------------------- postgres
resource pg 'Microsoft.DBforPostgreSQL/flexibleServers@2023-12-01-preview' = {
  name: pgName
  location: location
  tags: tags
  sku: {
    name: postgresSku
    tier: startsWith(postgresSku, 'Standard_B') ? 'Burstable' : 'GeneralPurpose'
  }
  properties: {
    version: '16'
    availabilityZone: empty(postgresZone) ? null : postgresZone
    administratorLogin: postgresAdminLogin
    administratorLoginPassword: postgresAdminPassword
    storage: {
      storageSizeGB: 32
      autoGrow: 'Disabled'
    }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: { mode: 'Disabled' }
    network: { publicNetworkAccess: 'Enabled' }
    authConfig: {
      activeDirectoryAuth: 'Disabled'
      passwordAuth: 'Enabled'
    }
  }
}

resource pgDb 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-12-01-preview' = {
  parent: pg
  name: dbName
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

// 0.0.0.0 rule = "Allow public access from any Azure service". POC trade-off; see docs §6.
resource pgAllowAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2023-12-01-preview' = {
  parent: pg
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

resource pgAllowClient 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2023-12-01-preview' = if (!empty(clientIpAddress)) {
  parent: pg
  name: 'DeployerClientIp'
  properties: {
    startIpAddress: clientIpAddress
    endIpAddress: clientIpAddress
  }
}

// DATABASE_URL and the admin password live in Key Vault, written here so they never pass through a shell.
resource secretDbUrl 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'database-url'
  properties: {
    value: 'postgresql://${postgresAdminLogin}:${postgresAdminPassword}@${pg.properties.fullyQualifiedDomainName}:5432/${dbName}?sslmode=require'
  }
}

resource secretPgPassword 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'postgres-admin-password'
  properties: {
    value: postgresAdminPassword
  }
}

// ---------------------------------------------------------------- container apps
resource cae 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${prefix}-${envName}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

var kvSecretNames = [
  'database-url'
  'lithic-api-key'
  'lithic-asa-secret'
  'lithic-event-secret'
  'occlet-bridge-secret'
]
var appSecrets = concat(
  map(kvSecretNames, s => {
    name: s
    keyVaultUrl: '${kv.properties.vaultUri}secrets/${s}'
    identity: uami.id
  }),
  empty(easyAuthClientId) ? [] : [
    {
      name: 'microsoft-provider-authentication-secret'
      keyVaultUrl: '${kv.properties.vaultUri}secrets/easyauth-client-secret'
      identity: uami.id
    }
  ]
)

resource app 'Microsoft.App/containerApps@2024-03-01' = if (deployApp) {
  name: appName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${uami.id}': {} }
  }
  dependsOn: [
    acrPull
    kvUami
    secretDbUrl
  ]
  properties: {
    environmentId: cae.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        transport: 'http'
        allowInsecure: false
      }
      registries: [
        {
          server: acr.properties.loginServer
          identity: uami.id
        }
      ]
      secrets: appSecrets
    }
    template: {
      // Exactly one replica: the ui keeps in-process state (locks, nonces, intents),
      // and Lithic ASA cannot wait for a cold start.
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
      containers: [
        {
          name: 'proxy'
          image: '${acr.properties.loginServer}/besidepoc-proxy:${imageTag}'
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
          probes: [
            {
              type: 'Liveness'
              tcpSocket: { port: 8080 }
              periodSeconds: 15
            }
            {
              type: 'Readiness'
              httpGet: { path: '/healthz', port: 8080 }
              periodSeconds: 10
              failureThreshold: 6
            }
          ]
        }
        {
          name: 'ui'
          image: '${acr.properties.loginServer}/besidepoc-app:${imageTag}'
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'LITHIC_API_KEY', secretRef: 'lithic-api-key' }
            { name: 'OCCLET_BRIDGE_SECRET', secretRef: 'occlet-bridge-secret' }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
          ]
        }
        {
          name: 'bridge'
          image: '${acr.properties.loginServer}/besidepoc-app:${imageTag}'
          command: [ 'uvicorn' ]
          args: [ 'lithic_bridge:app', '--host', '127.0.0.1', '--port', '8001' ]
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
          env: [
            { name: 'LITHIC_ASA_SECRET', secretRef: 'lithic-asa-secret' }
            { name: 'LITHIC_EVENT_SECRET', secretRef: 'lithic-event-secret' }
            { name: 'OCCLET_BRIDGE_SECRET', secretRef: 'occlet-bridge-secret' }
          ]
        }
      ]
    }
  }
}

// Entra ID login in front of everything except the HMAC-protected Lithic webhooks.
resource auth 'Microsoft.App/containerApps/authConfigs@2024-03-01' = if (deployApp && !empty(easyAuthClientId)) {
  parent: app
  name: 'current'
  properties: {
    platform: { enabled: true }
    globalValidation: {
      unauthenticatedClientAction: 'RedirectToLoginPage'
      redirectToProvider: 'azureactivedirectory'
      excludedPaths: [
        '/lithic/asa'
        '/lithic/events'
        '/healthz'
      ]
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: easyAuthClientId
          clientSecretSettingName: 'microsoft-provider-authentication-secret'
          openIdIssuer: '${environment().authentication.loginEndpoint}${tenant().tenantId}/v2.0'
        }
      }
    }
  }
}

// ---------------------------------------------------------------- budget
resource budget 'Microsoft.Consumption/budgets@2023-11-01' = if (budgetAmount > 0 && !empty(budgetContactEmail)) {
  name: 'budget-${prefix}-${envName}'
  properties: {
    category: 'Cost'
    amount: budgetAmount
    timeGrain: 'Monthly'
    timePeriod: { startDate: budgetStartDate }
    notifications: {
      actual80: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 80
        thresholdType: 'Actual'
        contactEmails: [ budgetContactEmail ]
      }
      forecast100: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 100
        thresholdType: 'Forecasted'
        contactEmails: [ budgetContactEmail ]
      }
    }
  }
}

// ---------------------------------------------------------------- outputs
output acrName string = acr.name
output acrLoginServer string = acr.properties.loginServer
output keyVaultName string = kv.name
output postgresHost string = pg.properties.fullyQualifiedDomainName
output environmentName string = cae.name
output containerAppName string = appName
// Predictable before the app exists, so Lithic and Entra ID can be configured in advance.
output appFqdn string = '${appName}.${cae.properties.defaultDomain}'
