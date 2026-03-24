#!/usr/bin/env python3
"""
setup_agent.py — Register a persistent Foundry Agent
=====================================================

Creates a named, persistent agent in your Azure AI Foundry project with
all 28 Playwright MCP browser tools pre-registered.  The agent appears
in the Foundry portal under **Agents** where you can observe:

- Token usage per run
- Tool call history
- Cost breakdown
- Run success/failure rates

The agent ID is written to your ``.env`` file so subsequent runs of
``run_demo.py`` (and your own code) reuse it — no more ephemeral
create/delete per query.

Usage::

    # First time — creates the agent and writes FOUNDRY_AGENT_ID to .env
    python setup_agent.py

    # Re-run — updates the existing agent's tools & instructions
    python setup_agent.py --update

    # Force re-create (deletes old, creates new)
    python setup_agent.py --recreate

    # Use a different model
    python setup_agent.py --model gpt-4.1

Prerequisites:
    - .env configured with FOUNDRY_ENDPOINT and FOUNDRY_MODEL
    - ``az login`` completed (or env vars for service principal)
    - Node.js 18+ installed (for MCP tool discovery)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import platform
import re
import shutil
import sys
import time

from dotenv import load_dotenv, set_key
from azure.identity import DefaultAzureCredential
from azure.ai.agents import AgentsClient
from mcp import StdioServerParameters, ClientSession
from mcp.client.stdio import stdio_client

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

AGENT_NAME = "ecommerce-mcp-price-monitor"
AGENT_DESCRIPTION = (
    "Persistent e-commerce pricing agent with Playwright MCP browser tools. "
    "Autonomously navigates, interacts with, and extracts pricing data from "
    "any e-commerce site — zero site-specific code."
)
ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


# ── System prompt (same as agent.py) ─────────────────────────────────────────

def build_system_prompt() -> str:
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


# ── MCP tool discovery ──────────────────────────────────────────────────────

def mcp_tools_to_function_defs(mcp_tools) -> list[dict]:
    """Convert MCP tool schemas → Azure FunctionToolDefinition dicts."""
    defs: list[dict] = []
    for tool in mcp_tools:
        schema = tool.inputSchema or {"type": "object", "properties": {}}
        schema = {k: v for k, v in schema.items() if k != "$schema"}
        defs.append({
            "type": "function",
            "function": {
                "name": tool.name,
                "description": (
                    tool.description or f"Playwright MCP tool: {tool.name}"
                ),
                "parameters": schema,
            },
        })
    return defs


async def discover_mcp_tools() -> list[dict]:
    """Start the Playwright MCP server, list tools, return Foundry defs."""
    if platform.system() == "Windows":
        npx_cmd = r"C:\Program Files\nodejs\npx.cmd"
        if not os.path.isfile(npx_cmd):
            npx_cmd = shutil.which("npx.cmd") or shutil.which("npx") or "npx.cmd"
    else:
        npx_cmd = shutil.which("npx") or "npx"

    env = os.environ.copy()
    node_dir = os.path.dirname(npx_cmd)
    if node_dir and node_dir not in env.get("PATH", ""):
        env["PATH"] = node_dir + os.pathsep + env.get("PATH", "")

    server_params = StdioServerParameters(
        command=npx_cmd,
        args=["-y", "@playwright/mcp@latest", "--browser", "chrome",
              "--caps", "vision", "--isolated", "--no-sandbox", "--headless"],
        env=env,
    )

    logger.info("Starting Playwright MCP server to discover tools...")
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            mcp_tools = (await session.list_tools()).tools
            logger.info("Discovered %d MCP tools", len(mcp_tools))
            return mcp_tools_to_function_defs(mcp_tools)


# ── Agent management ─────────────────────────────────────────────────────────

def get_existing_agent_id() -> str:
    """Read FOUNDRY_AGENT_ID from environment / .env."""
    return os.getenv("FOUNDRY_AGENT_ID", "").strip()


def save_agent_id(agent_id: str) -> None:
    """Write FOUNDRY_AGENT_ID to the .env file."""
    if os.path.exists(ENV_FILE):
        set_key(ENV_FILE, "FOUNDRY_AGENT_ID", agent_id)
        logger.info("Saved FOUNDRY_AGENT_ID=%s to %s", agent_id, ENV_FILE)
    else:
        # Create .env if it doesn't exist
        with open(ENV_FILE, "a") as f:
            f.write(f"\nFOUNDRY_AGENT_ID={agent_id}\n")
        logger.info("Created %s with FOUNDRY_AGENT_ID=%s", ENV_FILE, agent_id)


def create_agent(
    client: AgentsClient,
    model: str,
    tool_defs: list[dict],
) -> str:
    """Create a new persistent agent and return its ID."""
    agent = client.create_agent(
        model=model,
        name=AGENT_NAME,
        instructions=build_system_prompt(),
        tools=tool_defs,
        metadata={
            "created_by": "setup_agent.py",
            "purpose": "e-commerce-price-monitoring",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )
    logger.info(
        "Created persistent agent: id=%s  name=%s  model=%s",
        agent.id, agent.name, model,
    )
    return agent.id


def update_agent(
    client: AgentsClient,
    agent_id: str,
    model: str,
    tool_defs: list[dict],
) -> str:
    """Update an existing agent's tools and instructions."""
    agent = client.update_agent(
        agent_id=agent_id,
        model=model,
        name=AGENT_NAME,
        instructions=build_system_prompt(),
        tools=tool_defs,
        metadata={
            "updated_by": "setup_agent.py",
            "purpose": "e-commerce-price-monitoring",
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )
    logger.info("Updated agent %s — tools and instructions refreshed", agent_id)
    return agent.id


def delete_agent(client: AgentsClient, agent_id: str) -> bool:
    """Delete an agent by ID. Returns True if successful."""
    try:
        client.delete_agent(agent_id)
        logger.info("Deleted agent %s", agent_id)
        return True
    except Exception as exc:
        logger.warning("Could not delete agent %s: %s", agent_id, exc)
        return False


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Register a persistent Foundry Agent for e-commerce price monitoring"
    )
    parser.add_argument(
        "--model", type=str, default="",
        help="Model deployment name (default: from FOUNDRY_MODEL env var)",
    )
    parser.add_argument(
        "--update", action="store_true",
        help="Update the existing agent (refresh tools & instructions)",
    )
    parser.add_argument(
        "--recreate", action="store_true",
        help="Delete the existing agent and create a new one",
    )
    parser.add_argument(
        "--show", action="store_true",
        help="Show current agent details and exit",
    )
    args = parser.parse_args()

    # ── Validate config ──
    endpoint = os.getenv("FOUNDRY_ENDPOINT", "").strip()
    model = args.model or os.getenv("FOUNDRY_MODEL", "gpt-5.4")

    if not endpoint:
        print("\n  ERROR: FOUNDRY_ENDPOINT is not set.")
        print("  Copy .env.example to .env and fill in your endpoint.\n")
        sys.exit(1)

    # ── Connect to Foundry ──
    print(f"\n  Endpoint : {endpoint}")
    print(f"  Model    : {model}")
    print()

    credential = DefaultAzureCredential()
    client = AgentsClient(endpoint=endpoint, credential=credential)
    existing_id = get_existing_agent_id()

    # ── Show mode ──
    if args.show:
        if not existing_id:
            print("  No FOUNDRY_AGENT_ID found in .env")
            sys.exit(0)
        try:
            agent = client.get_agent(existing_id)
            print(f"  Agent ID    : {agent.id}")
            print(f"  Name        : {agent.name}")
            print(f"  Model       : {agent.model}")
            print(f"  Tools       : {len(agent.tools)} registered")
            print(f"  Created     : {agent.created_at}")
            print(f"\n  View in portal:")
            print(f"  {endpoint.rsplit('/api/', 1)[0]}")
        except Exception as exc:
            print(f"  Could not fetch agent {existing_id}: {exc}")
        sys.exit(0)

    # ── Discover MCP tools ──
    tool_defs = asyncio.run(discover_mcp_tools())
    print(f"  Discovered {len(tool_defs)} Playwright browser tools\n")

    # Print tool names for confirmation
    for i, td in enumerate(tool_defs, 1):
        print(f"    {i:2d}. {td['function']['name']}")
    print()

    # ── Recreate mode ──
    if args.recreate and existing_id:
        print(f"  Deleting existing agent {existing_id}...")
        delete_agent(client, existing_id)
        existing_id = ""

    # ── Update mode ──
    if args.update and existing_id:
        print(f"  Updating agent {existing_id}...")
        agent_id = update_agent(client, existing_id, model, tool_defs)
        save_agent_id(agent_id)
        print(f"\n  Agent updated: {agent_id}")
        print(f"  View in Azure AI Foundry portal → Agents\n")
        return

    # ── Create mode (default) ──
    if existing_id and not args.recreate:
        print(f"  Agent already exists: {existing_id}")
        print(f"  Use --update to refresh tools, or --recreate to replace.\n")
        return

    print("  Creating persistent agent...")
    agent_id = create_agent(client, model, tool_defs)
    save_agent_id(agent_id)

    print(f"\n  {'=' * 56}")
    print(f"  Agent registered successfully!")
    print(f"  {'=' * 56}")
    print(f"  Agent ID : {agent_id}")
    print(f"  Model    : {model}")
    print(f"  Tools    : {len(tool_defs)}")
    print(f"  Name     : {AGENT_NAME}")
    print(f"\n  Saved to : {ENV_FILE}")
    print(f"\n  Next steps:")
    print(f"    1. View your agent in the Azure AI Foundry portal → Agents")
    print(f"    2. Run:  python run_demo.py")
    print(f"       (it will reuse this persistent agent automatically)")
    print(f"    3. Check token usage & cost in the portal after runs\n")


if __name__ == "__main__":
    main()
