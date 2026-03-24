# E-Commerce MCP Solution Accelerator

An AI-powered e-commerce price monitoring system that uses **GPT-5.4** and
the **Playwright MCP Server** to autonomously navigate, interact with, and
extract pricing data from any e-commerce website — with zero site-specific
code.

## What It Does

| Tier | Capability | How It Works |
|------|-----------|--------------|
| **Tier 1** | Public price extraction | Navigate to a product page (with deep navigation if necessary), read the snapshot, extract price/rating/seller |
| **Tier 2** | Geographic pricing comparison | Change the delivery location via the site's own UI, then extract the price per location |
| **Tier 3** | Device/channel comparison | Emulate iPhone, Android, and Desktop viewports — real device profiles, not prompt-only |

**Key differentiator:** The agent uses one generic prompt for all sites.
It reads the page's accessibility tree (`browser_snapshot`) and
autonomously figures out how to interact with Amazon's GLUX popup,
Walmart's store picker, Instacart's address bar, or any other site's
location mechanism.

---

## Architecture

```
┌───────────────────────────────────────────────────────────┐
│                    Your Python Code                       │
│                  (run_demo.py / your app)                 │
└──────────────────┬────────────────────────────────────────┘
                   │  HTTP (Azure AI Foundry Agents API)
                   ▼
┌───────────────────────────────────────────────────────────┐
│              Azure AI Foundry (cloud)                     │
│                                                           │
│  ┌─────────────────────────────────────────────────────┐  │
│  │  GPT-5.4 Agent                                      │  │
│  │  (ephemeral — created per query, deleted after)     │  │
│  │                                                     │  │
│  │  Receives 28 browser tools as callable functions    │  │
│  │  Emits tool calls: browser_navigate, browser_click, │  │
│  │  browser_snapshot, browser_fill, browser_take_      │  │
│  │  screenshot, etc.                                   │  │
│  └─────────────────────────────────────────────────────┘  │
└──────────────────┬────────────────────────────────────────┘
                   │  Tool call results (text / screenshots)
                   ▼
┌───────────────────────────────────────────────────────────┐
│           Playwright MCP Server                           │
│                                                           │
│  LOCAL (dev/testing):                                     │
│    npx @playwright/mcp@latest  (stdio transport)          │
│    Runs Chromium on your machine — free, fast iteration   │
│                                                           │
│  CLOUD (production):                                      │
│    Docker container on Azure Container Apps               │
│    SSE transport (--port 3000) over HTTPS                 │
│    See "Moving to Production" section below               │
│                                                           │
└───────────────────────────────────────────────────────────┘
```

---

## Prerequisites

You need **two things**: an Azure AI Foundry project (cloud) and Node.js
(local).

### 1. Azure AI Foundry + GPT Model Deployment

This is where your GPT-5.4 model runs. You need:

- An **Azure subscription**
- An **Azure AI Foundry project** (the portal creates underlying
  resources automatically)
- A **GPT model deployment** (e.g. `gpt-5.4`)

#### Step-by-step setup

| Step | Action | Link |
|------|--------|------|
| 1 | **Sign in to Azure AI Foundry** at [ai.azure.com](https://ai.azure.com) | [Azure AI Foundry portal](https://ai.azure.com) |
| 2 | **Create a new project** — the portal provisions the required resources for you | [Create a project](https://learn.microsoft.com/azure/ai-foundry/how-to/create-projects) |
| 3 | **Deploy a GPT model** (e.g. `gpt-5.4` or `gpt-4.1`) | [Deploy a model](https://learn.microsoft.com/azure/ai-foundry/how-to/deploy-models-openai) |
| 4 | **Copy your project endpoint** | Found in AI Foundry portal → Project → Overview → "Project endpoint" |
| 5 | **Authenticate** | `az login` for local dev, or Managed Identity for production. See [DefaultAzureCredential](https://learn.microsoft.com/python/api/azure-identity/azure.identity.defaultazurecredential) |

> **Your endpoint** will look like:
> `https://<your-project>.services.ai.azure.com/api/projects/<project-name>`

#### Azure AI Foundry Agents documentation

| Topic | Link |
|-------|------|
| What are Azure AI Foundry Agents? | [Overview](https://learn.microsoft.com/azure/ai-services/agents/overview) |
| Quickstart: Create an agent | [Quickstart](https://learn.microsoft.com/azure/ai-services/agents/quickstart) |
| Agents SDK for Python | [Python SDK](https://learn.microsoft.com/python/api/overview/azure/ai-agents-readme) |
| Function calling with agents | [Function tools](https://learn.microsoft.com/azure/ai-services/agents/how-to/tools/function-calling) |
| Authentication & identity | [DefaultAzureCredential](https://learn.microsoft.com/python/api/azure-identity/azure.identity.defaultazurecredential) |

### 2. Node.js + Playwright MCP Server (Local — for Development & Testing)

The Playwright MCP Server is an **npm package** that runs locally during
development.  It spawns a Chromium browser and exposes it as MCP tools
over stdio.

> **For initial testing and development, running locally is the
> recommended starting point.** No cloud browser infrastructure is
> required. See [Moving to Production](#moving-to-production-cloud-hosted-mcp)
> below when you're ready to deploy.

#### Install Node.js

| Platform | Command |
|----------|---------|
| **Windows** | Download from [nodejs.org](https://nodejs.org/) (LTS recommended, v18+) |
| **macOS** | `brew install node` |
| **Ubuntu/Debian** | `sudo apt install nodejs npm` |

Verify: `node --version` (must be 18+) and `npx --version`.

#### How Playwright MCP works

The MCP server is **auto-installed** via `npx` on first run — you don't
need to install it separately. When the agent runs, it executes:

```bash
npx -y @playwright/mcp@latest --browser chrome --caps vision --isolated --no-sandbox
```

This downloads `@playwright/mcp` (if not cached), starts Chromium, and
exposes ~28 browser tools over stdio.

#### Playwright MCP documentation

| Topic | Link |
|-------|------|
| Playwright MCP repo | [github.com/microsoft/playwright-mcp](https://github.com/microsoft/playwright-mcp) |
| Configuration flags | [Configuration reference](https://github.com/microsoft/playwright-mcp#configuration) |
| Device emulation (`--device`) | [Playwright devices](https://playwright.dev/docs/emulation#devices) |
| Geolocation (`--grant-permissions`) | [Playwright geolocation](https://playwright.dev/docs/emulation#geolocation) |
| MCP protocol spec | [modelcontextprotocol.io](https://modelcontextprotocol.io/) |

### 3. VS Code MCP Integration

If your team uses **GitHub Copilot** in VS Code, you can register
Playwright MCP as a tool server so Copilot can interact with real
browsers during chat sessions.

#### Point-and-click setup

1. Open VS Code → **Settings** (Ctrl+, / Cmd+,)
2. Search for `mcp` in the settings search bar
3. Under **Chat > MCP**, click **Edit in settings.json**
4. Add the Playwright MCP server:

```json
// .vscode/mcp.json  (workspace-level)
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": ["@playwright/mcp@latest"]
    }
  }
}
```

5. Restart VS Code. In the Copilot Chat panel, you'll see the Playwright
   tools listed under the MCP tools icon.

> **Tip:** You can also create this file at the workspace root
> (`.vscode/mcp.json`) and commit it so every team member gets the
> same MCP configuration automatically.

#### VS Code MCP documentation

| Topic | Link |
|-------|------|
| VS Code MCP support | [Use MCP servers in VS Code](https://code.visualstudio.com/docs/copilot/chat/mcp-servers) |
| Copilot Chat tools | [Chat with tools](https://code.visualstudio.com/docs/copilot/chat/chat-tools) |
| Playwright MCP in VS Code | [Playwright MCP README](https://github.com/microsoft/playwright-mcp#vs-code) |

### 4. Python Environment

| Requirement | Version |
|-------------|---------|
| Python | 3.10+ |
| pip packages | See `requirements.txt` |

---

## Quick Start

### 1. Clone the repo

```bash
git clone https://github.com/nrs2130/ecommerce-mcp-solution-accelerator.git
cd ecommerce-mcp-solution-accelerator
```

### 2. Create a virtual environment

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS/Linux
source .venv/bin/activate
```

### 3. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment

```bash
cp .env.example .env
# Edit .env with your Azure AI Foundry endpoint and model name
```

### 5. Authenticate with Azure

```bash
az login
```

This sets up `DefaultAzureCredential` for local development. For
production, use [Managed Identity](https://learn.microsoft.com/entra/identity/managed-identities-azure-resources/overview).

### 6. Register the Foundry Agent (recommended)

This creates a **persistent, named agent** in your Azure AI Foundry
project and saves its ID to `.env`:

```bash
python setup_agent.py
```

The script will:
1. Start the Playwright MCP server to discover all 28 browser tools
2. Create a registered agent in your Foundry project with those tools
3. Save `FOUNDRY_AGENT_ID` to your `.env` file

**Why do this?** A persistent agent shows up in the Azure AI Foundry
portal under **Agents**, where you can observe:

- Token usage per run (input / output / total)
- Tool call history and frequency
- Run success / failure rates
- Cost breakdown over time

> You can skip this step — `run_demo.py` will still work by creating
> ephemeral (temporary) agents. But ephemeral agents are deleted after
> each query, so their usage data is not retained in the portal.

**Other `setup_agent.py` commands:**

```bash
# Update the agent's tools & instructions (e.g. after MCP version bump)
python setup_agent.py --update

# Delete and recreate the agent
python setup_agent.py --recreate

# Show current agent details
python setup_agent.py --show
```

### 7. Run the demo

```bash
# Run all 3 tiers against a sample product
python run_demo.py

# Or target a specific tier
python run_demo.py --tier 1 --site amazon.in \
    --product "Neutrogena Hydro Boost Water Gel" \
    --url "https://www.amazon.in/dp/B00BQFTQW6"

# Tier 2 with specific postal codes
python run_demo.py --tier 2 --site amazon.in \
    --product "Clean & Clear Facial Wash 150 ml" \
    --url "https://www.amazon.in/dp/B00CI3HDMU" \
    --postal-codes "110001,400001,560001"
```

---

## Project Structure

```
ecommerce-mcp-solution-accelerator/
├── README.md                 ← You are here
├── .env.example              ← Template for environment variables
├── .gitignore
├── .vscode/
│   └── mcp.json              ← VS Code MCP server config (Playwright)
├── requirements.txt          ← Python dependencies
├── setup_agent.py            ← Register a persistent agent in Foundry
├── run_demo.py               ← Quick-start demo script
├── src/
│   ├── __init__.py
│   ├── config.py             ← Configuration, locations, product catalog
│   └── agent.py              ← PlaywrightMCPAgent (the core engine)
├── docs/
│   ├── ARCHITECTURE.md       ← Deep technical walkthrough
│   └── images/               ← Diagrams / screenshots for docs
└── screenshots/              ← Auto-populated by the agent (proof PNGs)
```

---

## How the Agent Works (Step by Step)

### The MCP Tool-Call Loop

For every query, the agent:

1. **Starts a Playwright MCP server** — a Node.js process that spawns
   Chromium and exposes ~28 browser tools over stdio.

2. **Discovers tools** — calls `session.list_tools()` which returns
   tools like `browser_navigate`, `browser_click`, `browser_type`,
   `browser_snapshot`, `browser_take_screenshot`, `browser_fill`, etc.

3. **Creates an ephemeral GPT agent** in Azure AI Foundry with all 28
   tools registered as callable functions.

4. **Sends the prompt** (product name, URL, tier-specific instructions).

5. **Enters the tool-call loop** (up to 60 iterations):
   - Polls the run status
   - When GPT wants to call a tool → executes it via MCP → returns
     the result to GPT
   - When GPT takes a screenshot → saves the image locally
   - Repeats until GPT says "completed"

6. **Parses the response** — extracts `KEY: VALUE` lines (price, rating,
   seller, etc.) from the model's final message.

7. **Cleans up** — if using ephemeral mode, deletes the agent. If using
   a persistent agent (via `setup_agent.py`), the agent stays registered
   and all run history is retained for observability.

### Observability in Azure AI Foundry Portal

When you use a **persistent agent** (created by `setup_agent.py`), all
runs are tracked under that agent in the Foundry portal:

| What You Can See | Where |
|-----------------|-------|
| Token usage (input/output/total) per run | Agent → Runs → select a run |
| Tool calls made and their payloads | Agent → Runs → Run details → Steps |
| Run duration and status (success/failed) | Agent → Runs list |
| Aggregate usage over time | Project → Usage & Quotas |
| Model cost breakdown | Azure Portal → Cost Management |

> **Tip:** Each query creates a new **thread** and **run** under the
> same agent. You can filter runs by time range to compare batches.

### Tier 1: Public Price

- Default browser, no special flags
- **Supports deep navigation:** if the URL leads to a search page or
  category page, the agent will click through to the correct product
  detail page autonomously
- Prompt: "Navigate to {url}, find the product, take a screenshot,
  extract the price"
- GPT calls `browser_navigate` → `browser_snapshot` →
  `browser_take_screenshot` → returns structured data

### Tier 2: Geographic Pricing

For each postal code:

- **MCP flags:** `--grant-permissions geolocation` + `--init-script`
  (JS that overrides `navigator.geolocation`)
- **Prompt is fully generic:** "Verify that the site shows pricing for
  delivery to {location}. If not, find the site's location picker, enter
  {postal_code}, and submit."
- GPT reads `browser_snapshot`, finds the location picker (Amazon's
  "Deliver to" popup, Walmart's store selector, etc.), fills it,
  verifies the update, then extracts pricing
- **This works on any site** — no CSS selectors, no site-specific code

### Tier 3: Device Comparison

For each device (Desktop, iPhone 14, Pixel 5):

- **MCP flags:** `--device "iPhone 14"` or `--viewport-size 1920x1080`
- This is **real device emulation** at the browser level — the site
  receives a genuine mobile User-Agent and renders for the actual
  viewport width
- Screenshots prove the layout (wide desktop vs. narrow mobile)

---

## Customisation Guide

### Adding Your Own Products

Edit `src/config.py` → `PRODUCT_CATALOG`:

```python
PRODUCT_CATALOG: list[Product] = [
    Product(
        name="Your Product Name",
        site="amazon.com",
        url="https://www.amazon.com/dp/B0XXXXXXXX",  # Direct URL (faster)
    ),
    Product(
        name="Another Product",
        site="walmart.com",
        url="",  # Empty = agent will search for it
    ),
]
```

### Adding Locations

Edit `src/config.py` → `LOCATION_POOL`:

```python
LOCATION_POOL.append(
    Location("WC2N 5DU", "London", "GB", 51.5074, -0.1278)
)
```

### Adding Device Profiles

Edit `src/agent.py` → `DEVICE_PROFILES`:

```python
DEVICE_PROFILES["ipad"] = {
    "label": "Tablet/iPad",
    "viewport": "",
    "device": "iPad Pro 11",
}
```

See [Playwright device list](https://github.com/AzureAD/microsoft-authentication-library-for-js/blob/dev/lib/msal-browser/docs/device-bound-tokens.md)
for available device names.

### Using a Different Model

Set `FOUNDRY_MODEL` in `.env`:

```
FOUNDRY_MODEL=gpt-4.1
```

Or pass `--model` to the demo script:

```bash
python run_demo.py --model gpt-4.1
```

---

## FAQ

### Do I need to create an Azure Playwright resource?

**No.** The Playwright MCP server runs locally via `npx` — it's an
open-source npm package. Azure Playwright Testing is a separate service
for running browsers in the cloud at scale, but it is **not required**
for this solution.

### Do I need a Bing Custom Search resource?

**No.** The MCP agent navigates directly to product URLs or performs
searches autonomously. Bing is optional for faster URL discovery but
not required.

### What Azure resources do I need?

Just one: an **Azure AI Foundry project** with a deployed GPT model.
That's it. No Playwright resource, no Bing resource, no browser VMs.

### Does it work on Linux/macOS?

Yes. The agent auto-detects the platform and resolves `npx` accordingly.
Node.js 18+ is required on all platforms.

### Can I run it headless?

Yes — pass `headless=True` to `_build_server_args()` or add
`--headless` to the MCP server flags. Useful for CI/CD and server
environments.

### How do I add a new e-commerce site?

Just add a `Product` entry with the new site's domain.
**No code changes needed.** The generic Tier 2 prompt handles any site's
location picker, and Tier 1/Tier 3 work out of the box.

---

## Additional Microsoft Learn Documentation

| Topic | Link |
|-------|------|
| Azure AI Foundry overview | [learn.microsoft.com/azure/ai-foundry/](https://learn.microsoft.com/azure/ai-foundry/) |
| Deploy OpenAI models | [Deploy models](https://learn.microsoft.com/azure/ai-foundry/how-to/deploy-models-openai) |
| Agents overview | [Agents overview](https://learn.microsoft.com/azure/ai-services/agents/overview) |
| Agents quickstart (Python) | [Quickstart](https://learn.microsoft.com/azure/ai-services/agents/quickstart) |
| Function calling | [Function tools](https://learn.microsoft.com/azure/ai-services/agents/how-to/tools/function-calling) |
| DefaultAzureCredential | [Identity docs](https://learn.microsoft.com/python/api/azure-identity/azure.identity.defaultazurecredential) |
| Managed Identity | [Managed identities](https://learn.microsoft.com/entra/identity/managed-identities-azure-resources/overview) |
| Playwright MCP (GitHub) | [microsoft/playwright-mcp](https://github.com/microsoft/playwright-mcp) |
| MCP protocol | [modelcontextprotocol.io](https://modelcontextprotocol.io/) |
| Playwright device emulation | [Playwright emulation](https://playwright.dev/docs/emulation) |
| VS Code MCP servers | [Use MCP servers in VS Code](https://code.visualstudio.com/docs/copilot/chat/mcp-servers) |
| Azure Container Apps | [Container Apps docs](https://learn.microsoft.com/azure/container-apps/) |
| Playwright Docker images | [Playwright Docker](https://playwright.dev/docs/docker) |

---

## License

MIT
