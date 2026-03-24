"""
Playwright MCP Agent — E-Commerce Solution Accelerator
=======================================================

A unified AI agent that controls a real Playwright browser via Model
Context Protocol (MCP) tools.  GPT decides *autonomously* how to
navigate, click, type, screenshot, and extract data from **any**
e-commerce page — no CSS selectors, no site-specific code.

Architecture
------------
1. **Playwright MCP Server** (Node.js ``@playwright/mcp``)
   → exposes ~28 browser tools (navigate, click, snapshot, screenshot, …)
   over stdio.
2. **Python MCP SDK** (client)
   → connects to the server, lists tools, executes tool calls.
3. **Azure AI Foundry Agents SDK**
   → GPT-5.4 agent with ``FunctionTool`` definitions derived from the
   MCP tool schemas.  The model emits tool calls → we execute them
   via MCP → return results → model reasons → repeat.

Per-tier configuration
~~~~~~~~~~~~~~~~~~~~~~
* **Tier 1 (Public Price):** Default browser, no special context.  Deep
  navigation (search → click → sub-pages) if no direct URL is given.
* **Tier 2 (Geo Pricing):** ``--grant-permissions geolocation`` +
  ``--init-script`` JS that overrides ``navigator.geolocation``.
  Plus a fully generic prompt that tells the model to find any
  location picker on the page.
* **Tier 3 (Device Comparison):** ``--device "iPhone 14"`` or
  ``--viewport-size "1920x1080"`` per device profile — real device
  emulation at the browser level.

Links
-----
* Playwright MCP: https://github.com/microsoft/playwright-mcp
* Azure AI Foundry Agents: https://learn.microsoft.com/azure/ai-services/agents/
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from azure.identity import DefaultAzureCredential
from azure.ai.agents import AgentsClient
from azure.ai.agents.models import (
    ToolOutput,
    RequiredFunctionToolCall,
    ListSortOrder,
)

from mcp import StdioServerParameters, ClientSession
from mcp.client.stdio import stdio_client

from .config import FoundryConfig, LOCATION_POOL, Location, resolve_location

logger = logging.getLogger(__name__)

SCREENSHOTS_DIR = Path("screenshots")
SCREENSHOTS_DIR.mkdir(exist_ok=True)


# ── Device profiles for Tier 3 ─────────────────────────────────────────────

DEVICE_PROFILES: dict[str, dict[str, str]] = {
    "desktop": {
        "label": "Desktop/Web",
        "viewport": "1920x1080",
        "device": "",
    },
    "iphone": {
        "label": "Mobile/iPhone",
        "viewport": "",
        "device": "iPhone 14",
    },
    "android": {
        "label": "Mobile/Android",
        "viewport": "",
        "device": "Pixel 5",
    },
}


# ═════════════════════════════════════════════════════════════════════════════
#  Data classes
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class MCPResult:
    """Result from a single Playwright MCP Agent query."""

    query: str = ""
    site: str = ""
    tier: int = 0
    product_name: str = ""
    price: str = ""
    promotions: str = ""
    rating: str = ""
    review_count: str = ""
    availability: str = ""
    seller: str = ""
    seller_name: str = ""
    description: str = ""
    url: str = ""
    asin: str = ""
    confirmed_location: str = ""
    device_info: str = ""
    screenshot_path: str = ""
    raw_response: str = ""
    tool_calls_made: int = 0
    mode: str = "playwright_mcp"
    success: bool = False
    error: str = ""
    elapsed_seconds: float = 0.0


# ═════════════════════════════════════════════════════════════════════════════
#  Agent
# ═════════════════════════════════════════════════════════════════════════════

class PlaywrightMCPAgent:
    """
    Unified AI agent with full Playwright browser control via MCP.

    The GPT model receives Playwright browser tools (navigate, click,
    type, snapshot, screenshot, …) and *autonomously* decides how to
    interact with any webpage to extract pricing data.

    Quick-start::

        from src.agent import PlaywrightMCPAgent

        agent = PlaywrightMCPAgent()
        agent.connect()

        # Tier 1 — public price
        results = agent.run_tier(
            tier=1,
            product_name="Neutrogena Hydro Boost Water Gel",
            site="amazon.in",
            url="https://www.amazon.in/dp/B00BQFTQW6",
        )
        for r in results:
            print(r.price, r.product_name)
    """

    def __init__(
        self,
        config: FoundryConfig | None = None,
        model: str = "",
    ):
        self.config = config or FoundryConfig()
        self.model = model or self.config.model
        self._credential: DefaultAzureCredential | None = None
        self._agents_client: AgentsClient | None = None
        self._connected = False

    # ─── Connection ──────────────────────────────────────────────────

    def connect(self) -> bool:
        """Initialise Azure credentials and Agents client.

        Uses ``DefaultAzureCredential`` which supports:
        - ``az login`` (local dev)
        - Managed Identity (prod / Azure VM / Container)
        - Environment variables (``AZURE_CLIENT_ID``, etc.)

        Docs: https://learn.microsoft.com/python/api/azure-identity/azure.identity.defaultazurecredential
        """
        self._credential = DefaultAzureCredential()
        self._agents_client = AgentsClient(
            endpoint=self.config.endpoint,
            credential=self._credential,
        )
        self._connected = True
        logger.info(
            "PlaywrightMCPAgent connected — model=%s endpoint=%s",
            self.model,
            self.config.endpoint,
        )
        return True

    def disconnect(self):
        """No persistent resources to clean up."""
        self._connected = False

    # ─── MCP server parameters ───────────────────────────────────────

    @staticmethod
    def _build_server_args(
        *,
        device: str = "",
        viewport: str = "",
        user_agent: str = "",
        geolocation: dict | None = None,
        init_script: str = "",
        headless: bool = False,
    ) -> list[str]:
        """Build CLI arguments for ``npx @playwright/mcp``.

        See configuration reference:
        https://github.com/microsoft/playwright-mcp#configuration
        """
        args: list[str] = [
            "-y", "@playwright/mcp@latest",
            "--browser", "chrome",
            "--caps", "vision",
            "--isolated",
            "--no-sandbox",
        ]
        if headless:
            args.append("--headless")
        if device:
            args.extend(["--device", device])
        elif viewport:
            args.extend(["--viewport-size", viewport])
        if user_agent:
            args.extend(["--user-agent", user_agent])
        if geolocation:
            args.extend(["--grant-permissions", "geolocation"])
        if init_script:
            args.extend(["--init-script", init_script])
        return args

    # ─── Geo init-script helper ──────────────────────────────────────

    @staticmethod
    def _create_geo_init_script(lat: float, lon: float) -> str:
        """Write a temp JS file that overrides ``navigator.geolocation``.

        This is a *bonus signal* for sites that read the Geolocation API
        (e.g. Instacart, store locators).  Amazon ignores it — the
        generic prompt handles Amazon's GLUX popup instead.
        """
        js = (
            "// Playwright MCP geo-override\n"
            "const __mockPos = {\n"
            f"  coords: {{ latitude: {lat}, longitude: {lon}, accuracy: 50,\n"
            "             altitude: null, altitudeAccuracy: null,\n"
            "             heading: null, speed: null }},\n"
            "  timestamp: Date.now(),\n"
            "};\n"
            "navigator.geolocation.getCurrentPosition = "
            "function(ok) { ok(__mockPos); };\n"
            "navigator.geolocation.watchPosition = "
            "function(ok) { ok(__mockPos); return 1; };\n"
        )
        fd, path = tempfile.mkstemp(suffix=".js", prefix="geo_mcp_")
        with os.fdopen(fd, "w") as fh:
            fh.write(js)
        return path

    # ─── MCP ↔ FunctionToolDefinition conversion ─────────────────────

    @staticmethod
    def _mcp_tools_to_function_defs(mcp_tools) -> list[dict]:
        """Convert MCP tool schemas → Azure FunctionToolDefinition dicts.

        Shape: ``{"type": "function", "function": {"name", "description", "parameters"}}``
        """
        defs: list[dict] = []
        for tool in mcp_tools:
            schema = tool.inputSchema or {"type": "object", "properties": {}}
            # Strip $schema — Azure AI Foundry rejects it
            schema = {k: v for k, v in schema.items() if k != "$schema"}
            defs.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": (
                        tool.description
                        or f"Playwright MCP tool: {tool.name}"
                    ),
                    "parameters": schema,
                },
            })
        return defs

    # ─── Async execution (thread-safe for Streamlit / notebooks) ─────

    @staticmethod
    def _unwrap_exception_group(exc: BaseException) -> BaseException:
        """Recursively unwrap ExceptionGroup to get the root cause."""
        while hasattr(exc, "exceptions") and exc.exceptions:
            exc = exc.exceptions[0]
        return exc

    def _run_async(self, coro):
        """Run an async coroutine from synchronous context.

        Spawns a dedicated thread with its own event loop — safe for
        Streamlit, Jupyter notebooks, or any sync caller.
        """
        result_box: list = [None]
        error_box: list = [None]

        def _worker():
            try:
                loop = asyncio.new_event_loop()
                try:
                    result_box[0] = loop.run_until_complete(coro)
                finally:
                    loop.close()
            except BaseException as exc:
                error_box[0] = self._unwrap_exception_group(exc)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join(timeout=300)  # 5-min hard ceiling

        if error_box[0] is not None:
            raise error_box[0]
        return result_box[0]

    # ─── Core agent loop ─────────────────────────────────────────────

    async def _run_with_mcp(
        self,
        prompt: str,
        server_args: list[str],
        screenshot_tag: str = "",
    ) -> tuple[str, int, str]:
        """
        Start an MCP server, discover tools, create an ephemeral
        Foundry agent, and handle the tool-call loop.

        Returns ``(response_text, tool_call_count, screenshot_path)``.
        """
        import platform
        import shutil

        # ── Resolve npx path ──
        if platform.system() == "Windows":
            npx_cmd = r"C:\Program Files\nodejs\npx.cmd"
            if not os.path.isfile(npx_cmd):
                npx_cmd = shutil.which("npx.cmd") or shutil.which("npx") or "npx.cmd"
        else:
            npx_cmd = shutil.which("npx") or "npx"
        logger.info("Using npx command: %s", npx_cmd)

        # Ensure Node.js is on PATH for the subprocess
        env = os.environ.copy()
        node_dir = os.path.dirname(npx_cmd)
        if node_dir and node_dir not in env.get("PATH", ""):
            env["PATH"] = node_dir + os.pathsep + env.get("PATH", "")

        server_params = StdioServerParameters(
            command=npx_cmd,
            args=server_args,
            env=env,
        )

        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                # ── Discover MCP tools ──
                mcp_tools = (await session.list_tools()).tools
                tool_defs = self._mcp_tools_to_function_defs(mcp_tools)
                logger.info(
                    "MCP server ready — %d tools discovered", len(mcp_tools)
                )

                # ── Create ephemeral Foundry agent ──
                agent = self._agents_client.create_agent(
                    model=self.model,
                    name=f"mcp-agent-{int(time.time())}",
                    instructions=self._build_system_prompt(),
                    tools=tool_defs,
                )
                agent_id = agent.id

                try:
                    # ── Thread + message ──
                    thread = self._agents_client.threads.create()
                    self._agents_client.messages.create(
                        thread_id=thread.id,
                        role="user",
                        content=prompt,
                    )

                    # ── Create run ──
                    run = self._agents_client.runs.create(
                        thread_id=thread.id,
                        agent_id=agent_id,
                    )

                    # ── Poll / tool-call loop ──
                    tool_call_count = 0
                    screenshot_path = ""
                    max_iters = 60

                    for _ in range(max_iters):
                        run = self._agents_client.runs.get(
                            thread_id=thread.id,
                            run_id=run.id,
                        )

                        if run.status == "completed":
                            break
                        if run.status == "failed":
                            logger.error("Run failed: %s", run.last_error)
                            break

                        if run.status == "requires_action":
                            tool_outputs: list[ToolOutput] = []
                            tool_calls = (
                                run.required_action
                                .submit_tool_outputs
                                .tool_calls
                            )

                            for tc in tool_calls:
                                if not isinstance(tc, RequiredFunctionToolCall):
                                    continue

                                name = tc.function.name
                                args = (
                                    json.loads(tc.function.arguments)
                                    if tc.function.arguments
                                    else {}
                                )
                                tool_call_count += 1
                                logger.info(
                                    "MCP tool #%d: %s(%s)",
                                    tool_call_count,
                                    name,
                                    json.dumps(args)[:120],
                                )

                                try:
                                    mcp_res = await session.call_tool(
                                        name, args
                                    )
                                    output = self._serialise_mcp_result(
                                        mcp_res, name, screenshot_tag
                                    )
                                    if name == "browser_take_screenshot":
                                        sp = self._save_screenshot(
                                            mcp_res, screenshot_tag
                                        )
                                        if sp:
                                            screenshot_path = sp
                                except Exception as exc:
                                    output = json.dumps({"error": str(exc)})
                                    logger.warning(
                                        "MCP tool error %s: %s", name, exc
                                    )

                                tool_outputs.append(
                                    ToolOutput(
                                        tool_call_id=tc.id,
                                        output=output,
                                    )
                                )

                            self._agents_client.runs.submit_tool_outputs(
                                thread_id=thread.id,
                                run_id=run.id,
                                tool_outputs=tool_outputs,
                            )
                        else:
                            await asyncio.sleep(1)

                    # ── Extract final response ──
                    msgs = self._agents_client.messages.list(
                        thread_id=thread.id,
                        order=ListSortOrder.ASCENDING,
                    )
                    response = ""
                    for msg in msgs:
                        if msg.role != "assistant":
                            continue
                        if hasattr(msg, "text_messages") and msg.text_messages:
                            for tm in msg.text_messages:
                                response += tm.text.value

                    return response, tool_call_count, screenshot_path

                finally:
                    try:
                        self._agents_client.delete_agent(agent_id)
                    except Exception:
                        pass

    # ─── MCP result serialisation ────────────────────────────────────

    @staticmethod
    def _serialise_mcp_result(mcp_res, tool_name: str, tag: str) -> str:
        """Convert an MCP tool result to a JSON string for the model."""
        if not hasattr(mcp_res, "content"):
            return str(mcp_res)

        parts: list[str] = []
        for item in mcp_res.content:
            ctype = getattr(item, "type", "text")
            if ctype == "text":
                parts.append(getattr(item, "text", ""))
            elif ctype == "image":
                parts.append("[screenshot captured]")
            else:
                parts.append(str(item))

        return "\n".join(parts) if parts else "OK"

    @staticmethod
    def _save_screenshot(mcp_res, tag: str) -> str:
        """Save a screenshot image from MCP result to disk."""
        if not hasattr(mcp_res, "content"):
            return ""
        for item in mcp_res.content:
            if getattr(item, "type", "") == "image":
                data = getattr(item, "data", "")
                if data:
                    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", tag)[:80]
                    fname = f"mcp_{safe}_{int(time.time())}.png"
                    fpath = SCREENSHOTS_DIR / fname
                    fpath.write_bytes(base64.b64decode(data))
                    logger.info("Screenshot saved: %s", fpath)
                    return str(fpath)
        return ""

    # ═════════════════════════════════════════════════════════════════
    #  Prompts — system + per-tier
    # ═════════════════════════════════════════════════════════════════

    @staticmethod
    def _build_system_prompt() -> str:
        """System prompt loaded into every ephemeral agent."""
        return (
            "You are an expert e-commerce pricing research agent.\n\n"
            "You have access to Playwright browser tools via MCP. Use them to:\n"
            "1. Navigate to product pages\n"
            "2. Read the page snapshot to find pricing data\n"
            "3. Take a screenshot as visual proof\n"
            "4. Extract ALL available pricing and product details\n\n"
            "ALWAYS take a screenshot (browser_take_screenshot) after loading "
            "the product page — this serves as an audit trail.\n\n"
            "At the END of your response, output structured KEY: VALUE "
            "lines in this exact format:\n\n"
            "PRODUCT_NAME: [value]\n"
            "PRICE: [value with currency symbol]\n"
            "PROMOTIONS: [value or None]\n"
            "RATING: [value]\n"
            "REVIEW_COUNT: [value]\n"
            "SELLER: [value]\n"
            "AVAILABILITY: [value]\n"
            "URL: [direct product page URL]\n"
            "CONFIRMED_LOCATION: [delivery location shown on page, or N/A]\n"
            "DEVICE: [Desktop/iPhone 14/Pixel 5 based on the viewport]\n"
        )

    @staticmethod
    def _build_t1_prompt(product: str, site: str, url: str) -> str:
        """Tier 1 — public price extraction (with deep navigation)."""
        return (
            f"Navigate to {url}\n"
            f"Find the product '{product}' on {site}.\n"
            f"If this is a search page, click the most relevant result.\n"
            f"Take a screenshot of the product page.\n"
            f"Extract the price, promotions, rating, review count, "
            f"availability, and seller name.\n"
        )

    @staticmethod
    def _build_t2_prompt(
        product: str,
        site: str,
        url: str,
        location_label: str,
        postal_code: str = "",
    ) -> str:
        """Tier 2 — geographic pricing.

        This prompt is **fully generic** — suitable for ANY e-commerce
        site.  The model reads the page snapshot and figures out how the
        site's location picker works (Amazon GLUX popup, Walmart store
        picker, Instacart address bar, etc.).
        """
        return (
            f"Navigate to {url}\n"
            f"Find the product '{product}' on {site}.\n"
            f"If this is a search page, click the most relevant result.\n\n"
            f"YOUR GOAL: Verify that the site is showing pricing for "
            f"delivery to **{location_label}** "
            f"(postal / ZIP code: **{postal_code}**).\n\n"
            f"The browser's geolocation coordinates have already been set, "
            f"which works for sites that read navigator.geolocation. "
            f"However, many e-commerce sites use their OWN location picker "
            f"(a button, popup, or address field) instead of the browser API.\n\n"
            f"Steps:\n"
            f"1. Use browser_snapshot to read the current page.\n"
            f"2. Look for any delivery location / address indicator on the "
            f"page (e.g. 'Deliver to …', 'Your area', a ZIP/postal code "
            f"display, a store location banner).\n"
            f"3. If the page already shows the correct location "
            f"({location_label}), proceed to extraction.\n"
            f"4. If the page shows a DIFFERENT location or no location, "
            f"find and click the site's location picker / address button. "
            f"Enter the postal code **{postal_code}** in whatever input "
            f"field appears, submit it, and wait for the page to update.\n"
            f"5. After the location updates, navigate back to the product "
            f"page if needed ({url}).\n\n"
            f"Once the correct location is confirmed on the page:\n"
            f"- Take a screenshot showing the delivery location.\n"
            f"- Extract the price, promotions, rating, review count, "
            f"availability, and seller.\n"
            f"- Report CONFIRMED_LOCATION as exactly what the page "
            f"displays (e.g. 'Deliver to New Delhi 110001'), "
            f"NOT what was requested.\n"
        )

    @staticmethod
    def _build_t3_prompt(
        product: str,
        site: str,
        url: str,
        device_label: str,
        viewport_desc: str = "",
    ) -> str:
        """Tier 3 — device/channel comparison."""
        return (
            f"Navigate to {url}\n"
            f"Find the product '{product}' on {site}.\n"
            f"If this is a search page, click the most relevant result.\n\n"
            f"The browser is emulating a **{device_label}** device"
            f"{f' ({viewport_desc})' if viewport_desc else ''}.\n"
            f"Take a screenshot of the product page — this proves the "
            f"{device_label} layout with its viewport size.\n"
            f"Before extracting data, use browser_snapshot to read the "
            f"page accessibility tree, which includes layout/structure "
            f"info for the current viewport.\n\n"
            f"Extract the price, promotions, rating, review count, "
            f"availability, and seller name.\n"
            f"Report the device as: {device_label}\n"
        )

    # ═════════════════════════════════════════════════════════════════
    #  Public API
    # ═════════════════════════════════════════════════════════════════

    def run_tier(
        self,
        tier: int,
        product_name: str,
        site: str,
        url: str = "",
        postal_codes: list[str] | None = None,
    ) -> list[MCPResult]:
        """Run a pricing query for the given tier.

        Parameters
        ----------
        tier : int
            1 = public price, 2 = geographic pricing, 3 = device comparison.
        product_name : str
            Human-readable product name (used in prompts and search).
        site : str
            Target site domain, e.g. ``"amazon.in"``, ``"walmart.ca"``.
        url : str, optional
            Direct product page URL.  If empty, builds a search URL.
        postal_codes : list[str], optional
            For tier 2 only — list of ZIP/postal codes to compare.
            If omitted, picks 3 random locations from LOCATION_POOL
            matching the site's country.

        Returns
        -------
        list[MCPResult]
            One result per location (T2) or device (T3).
            T1 returns a single-element list.
        """
        if not self._connected:
            self.connect()

        if tier == 1:
            return [self._run_tier1(product_name, site, url)]
        elif tier == 2:
            return self._run_tier2(product_name, site, url, postal_codes)
        elif tier == 3:
            return self._run_tier3(product_name, site, url)
        else:
            return [self._run_tier1(product_name, site, url)]

    # ── Tier 1 ───────────────────────────────────────────────────────

    def _run_tier1(self, product: str, site: str, url: str = "") -> MCPResult:
        """Tier 1 — public price extraction (with deep navigation)."""
        result = MCPResult(query=product, site=site, tier=1)
        start = time.time()

        target = url or f"https://www.{site}/s?k={product.replace(' ', '+')}"
        prompt = self._build_t1_prompt(product, site, target)
        tag = f"t1_{site}_{product[:30]}"
        server_args = self._build_server_args()

        try:
            resp, tc_count, ss = self._run_async(
                self._run_with_mcp(prompt, server_args, tag)
            )
            result.raw_response = resp
            result.tool_calls_made = tc_count
            result.screenshot_path = ss
            self._parse_response(result)
            result.success = True
        except Exception as exc:
            result.error = str(exc)
            logger.error("MCP T1 failed: %s", exc)

        result.elapsed_seconds = time.time() - start
        return result

    # ── Tier 2 ───────────────────────────────────────────────────────

    def _run_tier2(
        self,
        product: str,
        site: str,
        url: str = "",
        postal_codes: list[str] | None = None,
    ) -> list[MCPResult]:
        locations: list[Location] = []
        if postal_codes:
            locations = [resolve_location(pc) for pc in postal_codes]
        else:
            import random
            # Infer country from site TLD
            if site.endswith(".in"):
                country = "IN"
            elif site.endswith(".ca"):
                country = "CA"
            elif site.endswith(".mx") or "mercadolibre" in site:
                country = "MX"
            else:
                country = "US"
            pool = [
                loc for loc in LOCATION_POOL if loc.country == country
            ] or list(LOCATION_POOL)
            locations = random.sample(pool, min(3, len(pool)))

        target = url or f"https://www.{site}/s?k={product.replace(' ', '+')}"
        results: list[MCPResult] = []

        for loc in locations:
            result = MCPResult(query=product, site=site, tier=2)
            start = time.time()

            lat = loc.latitude or 39.7392
            lon = loc.longitude or -104.9903
            geo_js = self._create_geo_init_script(lat, lon)

            location_label = f"{loc.city}, {loc.country} ({loc.code})"
            prompt = self._build_t2_prompt(
                product, site, target, location_label,
                postal_code=loc.code,
            )
            tag = f"t2_{loc.code}_{product[:20]}"
            server_args = self._build_server_args(
                geolocation={"latitude": lat, "longitude": lon},
                init_script=geo_js,
            )

            try:
                resp, tc_count, ss = self._run_async(
                    self._run_with_mcp(prompt, server_args, tag)
                )
                result.raw_response = resp
                result.tool_calls_made = tc_count
                result.screenshot_path = ss
                result.confirmed_location = location_label
                self._parse_response(result)
                result.success = True
            except Exception as exc:
                result.error = str(exc)
                logger.error("MCP T2 failed for %s: %s", loc.code, exc)
            finally:
                try:
                    os.unlink(geo_js)
                except OSError:
                    pass

            result.elapsed_seconds = time.time() - start
            results.append(result)

        return results

    # ── Tier 3 ───────────────────────────────────────────────────────

    def _run_tier3(self, product: str, site: str, url: str = "") -> list[MCPResult]:
        target = url or f"https://www.{site}/s?k={product.replace(' ', '+')}"
        results: list[MCPResult] = []

        for profile_key, profile in DEVICE_PROFILES.items():
            result = MCPResult(query=product, site=site, tier=3)
            start = time.time()

            label = profile["label"]
            if profile["device"]:
                viewport_desc = f"device preset: {profile['device']}"
            elif profile["viewport"]:
                viewport_desc = f"viewport: {profile['viewport']}"
            else:
                viewport_desc = ""

            prompt = self._build_t3_prompt(
                product, site, target, label,
                viewport_desc=viewport_desc,
            )
            tag = f"t3_{profile_key}_{product[:20]}"
            server_args = self._build_server_args(
                device=profile["device"],
                viewport=profile["viewport"],
            )

            try:
                resp, tc_count, ss = self._run_async(
                    self._run_with_mcp(prompt, server_args, tag)
                )
                result.raw_response = resp
                result.tool_calls_made = tc_count
                result.screenshot_path = ss
                result.device_info = label
                self._parse_response(result)
                result.success = True
            except Exception as exc:
                result.error = str(exc)
                logger.error("MCP T3 failed for %s: %s", label, exc)

            result.elapsed_seconds = time.time() - start
            results.append(result)

        return results

    # ═════════════════════════════════════════════════════════════════
    #  Response parsing
    # ═════════════════════════════════════════════════════════════════

    @staticmethod
    def _parse_response(result: MCPResult) -> None:
        """Parse structured ``KEY: VALUE`` lines from the model response."""
        text = result.raw_response
        if not text:
            return

        text_clean = re.sub(r"【[^】]*】", "", text)

        field_map = {
            "PRODUCT_NAME:": "product_name",
            "PRICE:": "price",
            "PROMOTIONS:": "promotions",
            "DESCRIPTION:": "description",
            "RATING:": "rating",
            "REVIEW_COUNT:": "review_count",
            "SELLER:": "seller",
            "AVAILABILITY:": "availability",
            "URL:": "url",
            "ASIN:": "asin",
            "CONFIRMED_LOCATION:": "confirmed_location",
            "DEVICE:": "device_info",
        }

        for line in text_clean.split("\n"):
            stripped = line.strip().replace("**", "").replace("*", "")
            for prefix, attr in field_map.items():
                if stripped.upper().startswith(prefix):
                    value = stripped[len(prefix):].strip()
                    if value and value.lower() not in ("n/a", "none", ""):
                        setattr(result, attr, value)
                    break

        if result.seller and not result.seller_name:
            result.seller_name = result.seller

        # Fallback — pull price from unstructured text
        if not result.price:
            prices = re.findall(
                r"(?:CA)?\$\d[\d,]*(?:\.\d{2})?|₹\s*\d[\d,]*(?:\.\d{2})?",
                text_clean,
            )
            if prices:
                result.price = prices[0].strip()

    # ─── Output helpers ──────────────────────────────────────────────

    @staticmethod
    def result_to_dict(result: MCPResult) -> dict:
        """Convert an MCPResult to a flat dict for display or export."""
        data = {
            "Query": result.query,
            "Site": result.site,
            "Tier": result.tier,
            "Product Name": result.product_name or "N/A",
            "Price": result.price or "N/A",
            "Promotions": result.promotions or "N/A",
            "Rating": result.rating or "N/A",
            "Review Count": result.review_count or "N/A",
            "Availability": result.availability or "N/A",
            "Seller Name": result.seller_name or result.seller or "N/A",
            "URL": result.url or "N/A",
            "Mode": result.mode,
            "Tool Calls": result.tool_calls_made,
            "Elapsed (s)": round(result.elapsed_seconds, 2),
            "Success": result.success,
            "Error": result.error or "",
        }
        if result.confirmed_location:
            data["Confirmed Location"] = result.confirmed_location
        if result.device_info:
            data["Device Info"] = result.device_info
        if result.screenshot_path:
            data["Screenshot"] = result.screenshot_path
        return data
