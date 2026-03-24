# Architecture — Deep Technical Walkthrough

## Component Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        run_demo.py                              │
│                    (or your application)                        │
│                                                                 │
│  agent = PlaywrightMCPAgent(config)                             │
│  agent.connect()                                                │
│  results = agent.run_tier(tier=2, product_name=..., site=...)   │
└─────────────┬───────────────────────────────────────────────────┘
              │
              │ 1. connect() → DefaultAzureCredential + AgentsClient
              │ 2. run_tier() → _run_tier2() → _run_async()
              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    _run_with_mcp() [async]                      │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  3. StdioServerParameters(                               │   │
│  │       command="npx",                                     │   │
│  │       args=["-y", "@playwright/mcp@latest",              │   │
│  │             "--browser", "chrome",                        │   │
│  │             "--caps", "vision",                           │   │
│  │             "--isolated", "--no-sandbox",                 │   │
│  │             "--grant-permissions", "geolocation",         │   │
│  │             "--init-script", "/tmp/geo_mcp_xxx.js"]       │   │
│  │     )                                                    │   │
│  └──────────────────────────────────────────────────────────┘   │
│                          │                                      │
│                          │ 4. stdio_client() → session          │
│                          ▼                                      │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Playwright MCP Server (Node.js)                         │   │
│  │                                                          │   │
│  │  - Spawns Chromium (headed or headless)                  │   │
│  │  - Exposes ~28 tools over stdio                          │   │
│  │  - Each tool = one Playwright action                     │   │
│  │                                                          │   │
│  │  Tools: browser_navigate, browser_click,                 │   │
│  │         browser_type, browser_fill,                      │   │
│  │         browser_snapshot, browser_take_screenshot,        │   │
│  │         browser_select_option, browser_hover,             │   │
│  │         browser_drag, browser_resize,                     │   │
│  │         browser_tab_new, browser_wait, ...               │   │
│  └──────────────────────────────────────────────────────────┘   │
│                          │                                      │
│  5. session.list_tools() → 28 tool schemas                      │
│  6. _mcp_tools_to_function_defs() → strip $schema, reshape      │
│                          │                                      │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  7. Create ephemeral Foundry Agent                       │   │
│  │     model="gpt-5.4"                                      │   │
│  │     tools=[28 function definitions]                      │   │
│  │     instructions=_build_system_prompt()                  │   │
│  └──────────────────────────────────────────────────────────┘   │
│                          │                                      │
│  8. Create thread + message (the tier prompt)                   │
│  9. Create run                                                  │
│                          │                                      │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  10. TOOL-CALL LOOP (max 60 iterations)                  │   │
│  │                                                          │   │
│  │  while run.status != "completed":                        │   │
│  │    poll run.status                                       │   │
│  │    if "requires_action":                                 │   │
│  │      for each tool_call:                                 │   │
│  │        → session.call_tool(name, args)  [MCP server]     │   │
│  │        ← result (text / screenshot)                      │   │
│  │        → submit_tool_outputs(result)    [Foundry]        │   │
│  │    sleep(1)                                              │   │
│  └──────────────────────────────────────────────────────────┘   │
│                          │                                      │
│  11. Read assistant messages → raw text                          │
│  12. _parse_response() → MCPResult dataclass                    │
│  13. Cleanup:                                                   │
│      - Persistent mode: agent stays, run history retained       │
│      - Ephemeral mode: agent deleted (finally block)            │
└─────────────────────────────────────────────────────────────────┘
```

## Persistent vs Ephemeral Agent Modes

| Mode | How | Observability | Best For |
|------|-----|---------------|----------|
| **Persistent** | `python setup_agent.py` → sets `FOUNDRY_AGENT_ID` in `.env` | Full: token usage, tool calls, cost, run history in Foundry portal | Production, cost tracking, auditing |
| **Ephemeral** | Default (no `FOUNDRY_AGENT_ID`) | None: agent deleted after each query | Quick testing, one-off runs |

In persistent mode, `setup_agent.py` creates the agent once with all 28
MCP tools registered. Each query creates a new **thread** and **run**
under that same agent — the Foundry portal accumulates all run history
for cost analysis and debugging.

## Tool Schema Conversion

The MCP server returns tool schemas in MCP format. Azure AI Foundry
expects a different shape. The conversion:

```
MCP format:                          Foundry format:
{                                    {
  "name": "browser_click",            "type": "function",
  "description": "Click...",          "function": {
  "inputSchema": {                      "name": "browser_click",
    "$schema": "...",    ← REMOVED      "description": "Click...",
    "type": "object",                   "parameters": {
    "properties": {...}                   "type": "object",
  }                                       "properties": {...}
}                                       }
                                      }
                                     }
```

The `$schema` key **must** be stripped — Azure AI Foundry rejects it.

## Tier 2: Generic Location Prompt

The T2 prompt is the most important design decision. It is **fully
generic** — no site-specific CSS selectors, no Amazon/Walmart/Instacart
branching:

```
YOUR GOAL: Verify that the site is showing pricing for
delivery to **Mumbai, IN (400001)** (postal / ZIP code: **400001**).

The browser's geolocation coordinates have already been set, which
works for sites that read navigator.geolocation. However, many
e-commerce sites use their OWN location picker (a button, popup,
or address field) instead of the browser API.

Steps:
1. Use browser_snapshot to read the current page.
2. Look for any delivery location / address indicator on the page.
3. If the page already shows the correct location, proceed.
4. If the page shows a DIFFERENT location or no location, find and
   click the site's location picker. Enter the postal code **400001**
   in whatever input field appears, submit it, and wait for update.
5. After the location updates, navigate back to the product page.
```

The model reads the accessibility tree and figures out what to click.
On Amazon it finds the GLUX popup. On Instacart it finds the address
bar. On Walmart it finds the store picker. Same prompt, any site.

## Authentication Flow

```
DefaultAzureCredential checks (in order):
1. Environment variables (AZURE_CLIENT_ID, AZURE_TENANT_ID, AZURE_CLIENT_SECRET)
2. Managed Identity (Azure VM, Container App, App Service)
3. Azure CLI (az login)
4. Azure PowerShell (Connect-AzAccount)
5. Visual Studio Code
6. Interactive browser login
```

For local development: `az login` is sufficient.
For production: use Managed Identity — no secrets to manage.

## Error Handling

| Error | Cause | Mitigation |
|-------|-------|------------|
| `WinError 2` / `npx not found` | Node.js not on PATH | Agent tries `C:\Program Files\nodejs\npx.cmd` then `shutil.which()` |
| `ExceptionGroup` | Python 3.11+ asyncio wrapping | `_unwrap_exception_group()` peels to root cause |
| `$schema` rejection | Foundry doesn't accept JSON Schema `$schema` key | Stripped in `_mcp_tools_to_function_defs()` |
| Run status `failed` | Model error, token limit, etc. | Logged and returned as `MCPResult.error` |
| Geo init-script leaked | Temp file not cleaned up | `finally` block with `os.unlink()` |
