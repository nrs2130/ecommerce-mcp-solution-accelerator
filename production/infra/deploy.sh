#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  deploy.sh — Deploy the Playwright MCP server to Azure
# ═══════════════════════════════════════════════════════════════
#
# This script deploys:
#   1. A Resource Group
#   2. An Azure Container Registry (ACR)
#   3. A Container Apps Environment
#   4. A Container App running the Playwright MCP server
#
# Prerequisites:
#   - Azure CLI (az) installed and logged in
#   - Docker installed (for building the image)
#
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh
#
# After deployment, the script prints the MCP server URL.
# Use that URL in setup_agent.py (PLAYWRIGHT_MCP_URL in .env).
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

# ── Configuration (override via environment) ────────────────────
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-ecommerce-mcp}"
LOCATION="${LOCATION:-eastus2}"
ACR_NAME="${ACR_NAME:-ecommercemcpacr}"
ENVIRONMENT_NAME="${ENVIRONMENT_NAME:-mcp-env}"
APP_NAME="${APP_NAME:-playwright-mcp}"
IMAGE_NAME="playwright-mcp-server"
IMAGE_TAG="latest"

echo ""
echo "  ╔══════════════════════════════════════════════════════╗"
echo "  ║  E-Commerce MCP — Production Deployment             ║"
echo "  ╚══════════════════════════════════════════════════════╝"
echo ""
echo "  Resource Group : $RESOURCE_GROUP"
echo "  Location       : $LOCATION"
echo "  ACR            : $ACR_NAME"
echo "  Container App  : $APP_NAME"
echo ""

# ── Step 1: Resource Group ──────────────────────────────────────
echo "  [1/6] Creating resource group..."
az group create \
    --name "$RESOURCE_GROUP" \
    --location "$LOCATION" \
    --output none

# ── Step 2: Azure Container Registry ───────────────────────────
echo "  [2/6] Creating Azure Container Registry..."
az acr create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$ACR_NAME" \
    --sku Basic \
    --admin-enabled true \
    --output none

# ── Step 3: Build & Push Docker image ──────────────────────────
echo "  [3/6] Building and pushing Docker image to ACR..."
az acr build \
    --registry "$ACR_NAME" \
    --image "${IMAGE_NAME}:${IMAGE_TAG}" \
    --file mcp-server/Dockerfile \
    mcp-server/

# ── Step 4: Container Apps Environment ─────────────────────────
echo "  [4/6] Creating Container Apps environment..."
az containerapp env create \
    --name "$ENVIRONMENT_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --location "$LOCATION" \
    --output none

# ── Step 5: Get ACR credentials ────────────────────────────────
echo "  [5/6] Retrieving ACR credentials..."
ACR_SERVER=$(az acr show --name "$ACR_NAME" --query loginServer --output tsv)
ACR_USERNAME=$(az acr credential show --name "$ACR_NAME" --query username --output tsv)
ACR_PASSWORD=$(az acr credential show --name "$ACR_NAME" --query "passwords[0].value" --output tsv)

# ── Step 6: Deploy Container App ───────────────────────────────
echo "  [6/6] Deploying Container App..."
az containerapp create \
    --name "$APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --environment "$ENVIRONMENT_NAME" \
    --image "${ACR_SERVER}/${IMAGE_NAME}:${IMAGE_TAG}" \
    --registry-server "$ACR_SERVER" \
    --registry-username "$ACR_USERNAME" \
    --registry-password "$ACR_PASSWORD" \
    --target-port 8080 \
    --ingress external \
    --cpu 1 --memory 2Gi \
    --min-replicas 1 \
    --max-replicas 3 \
    --output none

# ── Get the deployed URL ───────────────────────────────────────
FQDN=$(az containerapp show \
    --name "$APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --query properties.configuration.ingress.fqdn \
    --output tsv)

MCP_URL="https://${FQDN}/mcp"

echo ""
echo "  ╔══════════════════════════════════════════════════════╗"
echo "  ║  Deployment Complete!                                ║"
echo "  ╚══════════════════════════════════════════════════════╝"
echo ""
echo "  Container App FQDN : $FQDN"
echo "  MCP Server URL     : $MCP_URL"
echo ""
echo "  Next steps:"
echo "    1. Add to your .env file:"
echo "         PLAYWRIGHT_MCP_URL=$MCP_URL"
echo ""
echo "    2. Register the Foundry agent:"
echo "         python setup_agent.py"
echo ""
echo "    3. Run the demo:"
echo "         python run_demo.py"
echo ""
echo "  Test the MCP endpoint:"
echo "    npx @modelcontextprotocol/inspector $MCP_URL"
echo ""
