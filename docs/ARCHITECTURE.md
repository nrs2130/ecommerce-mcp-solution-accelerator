# Architecture — Deep Technical Walkthrough

## Component Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        run_demo.py                              │
│                    (or your application)                        │
│                                                                 │
│  agent = PlaywrightMCPAgent(config)                             │
│  agent.connect()                                                │
│  results = agent.run_tier(tier=3, product_name=..., site=...)   │
└─────────────┬───────────────────────────────────────────────────┘
              │
              │ 1. connect() → DefaultAzureCredential + AgentsClient
              │ 2. run_tier() → _run_tier3() → _run_async()
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
│  13. Delete ephemeral agent (finally block)                     │
└─────────────────────────────────────────────────────────────────┘
```

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

## Tier 3: Generic Location Prompt

The T3 prompt is the most important design decision. It is **fully
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

## Token Economics

Per-query token breakdown (approximate):

| Component | Input Tokens | Output Tokens |
|-----------|-------------|---------------|
| 28 tool schemas | ~3,000–5,000 | — |
| System prompt | ~200 | — |
| Tier prompt | ~200–400 | — |
| Each `browser_snapshot` response | ~2,000–10,000 | — |
| Model reasoning per iteration | — | ~200–500 |
| Final structured response | — | ~300–800 |
| **T1 total (3–5 tool calls)** | **~20,000–40,000** | **~1,000–3,000** |
| **T3 total per location (5–10 calls)** | **~40,000–80,000** | **~2,000–5,000** |
| **T5 total per device (3–5 calls)** | **~20,000–40,000** | **~1,000–3,000** |

Context accumulates across the tool-call loop — each iteration carries
all prior snapshot responses in context. The 60-iteration cap prevents
runaway costs.
