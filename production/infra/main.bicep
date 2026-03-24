// ═══════════════════════════════════════════════════════════════
//  main.bicep — Azure infrastructure for E-Commerce MCP Server
// ═══════════════════════════════════════════════════════════════
//
//  Deploys:
//    • Azure Container Registry (ACR)
//    • Container Apps Environment + Log Analytics workspace
//    • Container App running the Playwright MCP server
//
//  Deploy:
//    az deployment group create \
//      --resource-group rg-ecommerce-mcp \
//      --template-file main.bicep \
//      --parameters containerAppName=playwright-mcp \
//                   acrName=ecommercemcpacr \
//                   imageName=playwright-mcp-server:latest
//

@description('Name of the Container App')
param containerAppName string = 'playwright-mcp'

@description('Name of the Azure Container Registry')
param acrName string = 'ecommercemcpacr'

@description('Docker image name (tag included)')
param imageName string = 'playwright-mcp-server:latest'

@description('Azure region for all resources')
param location string = resourceGroup().location

@description('CPU cores for the Container App')
param cpuCores string = '1'

@description('Memory (Gi) for the Container App')
param memoryGi string = '2Gi'

@description('Minimum number of replicas')
param minReplicas int = 1

@description('Maximum number of replicas')
param maxReplicas int = 3

// ── Log Analytics Workspace ───────────────────────────────────
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${containerAppName}-logs'
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

// ── Azure Container Registry ──────────────────────────────────
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: true
  }
}

// ── Container Apps Environment ────────────────────────────────
resource containerAppEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${containerAppName}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

// ── Container App ─────────────────────────────────────────────
resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: containerAppName
  location: location
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8080
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: acr.properties.loginServer
          username: acr.listCredentials().username
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        {
          name: 'acr-password'
          value: acr.listCredentials().passwords[0].value
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'playwright-mcp'
          image: '${acr.properties.loginServer}/${imageName}'
          resources: {
            cpu: json(cpuCores)
            memory: memoryGi
          }
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'http-scale'
            http: {
              metadata: {
                concurrentRequests: '10'
              }
            }
          }
        ]
      }
    }
  }
}

// ── Outputs ───────────────────────────────────────────────────
output containerAppFqdn string = containerApp.properties.configuration.ingress.fqdn
output mcpServerUrl string = 'https://${containerApp.properties.configuration.ingress.fqdn}/mcp'
output acrLoginServer string = acr.properties.loginServer
