# Production Path — Foundry-Native MCP Agent

> **Zero local dependencies.** Foundry calls the Playwright MCP server directly.
> No Node.js, no local tool proxy, no Chromium on your machine.

## Architecture

```
┌──────────────────────┐
│  Your Python Code    │
│  run_demo.py         │
│  (thin API client)   │
└──────────┬───────────┘
           │  Responses API (HTTPS)
           ▼
┌──────────────────────────────────────────┐
│         Microsoft Foundry                │
│                                          │
│  ┌────────────────────────────────────┐  │
│  │  GPT-5.4 Agent                     │  │
│  │  (persistent — name + version)     │  │
│  │                                    │  │
│  │  MCPTool: playwright-browser       │  │
│  │  → calls remote MCP server         │  │
│  └──────────────┬─────────────────────┘  │
│                 │                         │
└─────────────────┼─────────────────────────┘
                  │  MCP over Streamable HTTP
                  ▼
┌──────────────────────────────────────────┐
│  Azure Container Apps                    │
│  ┌────────────────────────────────────┐  │
│  │  @playwright/mcp                   │  │
│  │  Headless Chromium                 │  │
│  │  Port 8080, auto-scale 1–3        │  │
│  └────────────────────────────────────┘  │
└──────────────────────────────────────────┘
```

## How it differs from the dev path

| Aspect | Dev (`../run_demo.py`) | Production (`production/run_demo.py`) |
|--------|----------------------|--------------------------------------|
| MCP server | Local `npx` (stdio) | Azure Container Apps (HTTPS) |
| Agent SDK | `azure-ai-agents` 1.x (classic) | `azure-ai-projects` 2.x (v2) |
| Tool registration | 28 × `FunctionTool` defs | 1 × `MCPTool(server_url=...)` |
| Tool execution | Your code proxies every call | Foundry calls MCP server directly |
| Chat API | Agents polling loop | OpenAI Responses API |
| Local deps | Node.js + Chromium | Python only |
| Portal visibility | Classic agents | New agents + MCP tool connection |

## Quick Start

### Prerequisites

- Azure subscription with Contributor access
- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) installed
- Docker installed (for image builds)
- Python 3.11+
- `az login` completed
- Microsoft Foundry project with Agent Service enabled

### Step 1 — Deploy the MCP server

```bash
cd infra
chmod +x deploy.sh
./deploy.sh
```

This creates:
- Azure Container Registry
- Container Apps Environment
- Container App running `@playwright/mcp` with headless Chromium

The script prints the MCP server URL. Add it to your `.env`:

```bash
# Copy the template
cp .env.example .env

# Edit .env and set:
#   FOUNDRY_ENDPOINT=https://...
#   PLAYWRIGHT_MCP_URL=https://playwright-mcp.<id>.azurecontainerapps.io/mcp
```

**Alternative (Bicep):**

```bash
az group create --name rg-ecommerce-mcp --location eastus2

az deployment group create \
  --resource-group rg-ecommerce-mcp \
  --template-file infra/main.bicep \
  --parameters containerAppName=playwright-mcp acrName=ecommercemcpacr
```

### Step 2 — Install Python dependencies

```bash
pip install -r requirements.txt
```

### Step 3 — Register the Foundry agent

```bash
python setup_agent.py
```

This creates a named, versioned agent with an `MCPTool` pointing to your
Container App. The agent name is saved to `.env`.

### Step 4 — Run the demo

```bash
# Tier 1 — public price extraction
python run_demo.py --tier 1

# Tier 2 — geographic pricing
python run_demo.py --tier 2 --postal-codes "110001,400001,560001"

# Tier 3 — device comparison
python run_demo.py --tier 3

# Custom query
python run_demo.py --query "Go to amazon.ca and find the price of Aveeno lotion"
```

## File Structure

```
production/
├── mcp-server/
│   ├── Dockerfile              # Playwright MCP container image
│   └── .dockerignore
├── infra/
│   ├── deploy.sh               # One-click Azure deployment (CLI)
│   └── main.bicep              # Infrastructure as Code (Bicep)
├── setup_agent.py              # Register agent with MCPTool
├── run_demo.py                 # Chat via Responses API
├── requirements.txt            # Python dependencies
├── .env.example                # Environment variables template
└── README.md                   # This file
```

## Agent Management

```bash
# Show agent details
python setup_agent.py --show

# Create a new version (update tools/model)
python setup_agent.py --update

# Delete and recreate
python setup_agent.py --recreate

# Use a different model
python setup_agent.py --model gpt-4.1

# Require human approval for MCP tool calls
python setup_agent.py --require-approval always
```

## Security Considerations

| Concern | Recommendation |
|---------|---------------|
| MCP server access | Add Container Apps ingress IP restrictions or use a VNET |
| Authentication | Use Managed Identity or API key headers for the MCP endpoint |
| Data in transit | HTTPS enforced by default on Container Apps |
| Screenshot data | Screenshots stay within the MCP server; add Azure Blob Storage for persistence |
| Secrets management | Store keys in Azure Key Vault, not in `.env` files |

## Clean Up

```bash
# Delete all Azure resources
az group delete --name rg-ecommerce-mcp --yes --no-wait
```

## Documentation

| Topic | Link |
|-------|------|
| Build & register MCP servers | [Microsoft Learn](https://learn.microsoft.com/azure/foundry/mcp/build-your-own-mcp-server) |
| Host MCP on Azure Functions | [Tutorial](https://learn.microsoft.com/azure/azure-functions/functions-mcp-tutorial?pivots=programming-language-python) |
| Connect agents to MCP servers | [MCP tool docs](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/model-context-protocol) |
| MCP server authentication | [Auth patterns](https://learn.microsoft.com/azure/foundry/agents/how-to/mcp-authentication) |
| Azure Functions MCP template | [GitHub](https://github.com/Azure-Samples/remote-mcp-functions-python) |
| Foundry quickstart (v2 SDK) | [Get started](https://learn.microsoft.com/azure/foundry/quickstarts/get-started-code?tabs=python) |
| Playwright MCP | [GitHub](https://github.com/microsoft/playwright-mcp) |
| Container Apps docs | [Overview](https://learn.microsoft.com/azure/container-apps/) |
