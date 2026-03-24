#!/usr/bin/env python3
"""
setup_agent.py — Register a persistent Foundry Agent (v2 API)
=============================================================

Creates a named, versioned agent in your Microsoft Foundry project with
all 28 Playwright MCP browser tools pre-registered.  The agent appears
in the Foundry portal under **Agents → New agents** where you can:

- Observe token usage per run
- View tool call history
- Track cost breakdown
- Monitor run success/failure rates

The new API identifies agents by **name + version** (not by ID).  The
agent name is written to your ``.env`` file so subsequent runs of
``run_demo.py`` (and your own code) reuse it automatically.

Usage::

    # First time — creates the agent and writes FOUNDRY_AGENT_NAME to .env
    python setup_agent.py

    # Re-run — creates a new version of the same agent
    python setup_agent.py --update

    # Force re-create (deletes old, creates new)
    python setup_agent.py --recreate

    # Use a different model
    python setup_agent.py --model gpt-4.1

Prerequisites:
    - .env configured with FOUNDRY_ENDPOINT and FOUNDRY_MODEL
    - ``az login`` completed (or env vars for service principal)
    - Node.js 18+ installed (for MCP tool discovery)

Requires:
    pip install azure-ai-projects>=2.0.0 azure-identity python-dotenv mcp
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import platform
import shutil
import sys
import time

from dotenv import load_dotenv, set_key
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    PromptAgentDefinition,
    FunctionTool,
)
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

def mcp_tools_to_function_tools(mcp_tools) -> list[FunctionTool]:
    """Convert MCP tool schemas → azure-ai-projects FunctionTool objects."""
    tools: list[FunctionTool] = []
    for tool in mcp_tools:
        schema = tool.inputSchema or {"type": "object", "properties": {}}
        schema = {k: v for k, v in schema.items() if k != "$schema"}
        tools.append(
            FunctionTool(
                name=tool.name,
                description=(
                    tool.description or f"Playwright MCP tool: {tool.name}"
                ),
                parameters=schema,
                strict=False,
            )
        )
    return tools


async def discover_mcp_tools() -> list[FunctionTool]:
    """Start the Playwright MCP server, list tools, return FunctionTool defs."""
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
            return mcp_tools_to_function_tools(mcp_tools)


# ── Agent management ─────────────────────────────────────────────────────────

def get_existing_agent_name() -> str:
    """Read FOUNDRY_AGENT_NAME from environment / .env."""
    return os.getenv("FOUNDRY_AGENT_NAME", "").strip()


def save_agent_name(agent_name: str) -> None:
    """Write FOUNDRY_AGENT_NAME to the .env file."""
    if os.path.exists(ENV_FILE):
        set_key(ENV_FILE, "FOUNDRY_AGENT_NAME", agent_name)
        logger.info("Saved FOUNDRY_AGENT_NAME=%s to %s", agent_name, ENV_FILE)
    else:
        with open(ENV_FILE, "a") as f:
            f.write(f"\nFOUNDRY_AGENT_NAME={agent_name}\n")
        logger.info("Created %s with FOUNDRY_AGENT_NAME=%s", ENV_FILE, agent_name)


def create_agent(
    project: AIProjectClient,
    model: str,
    tool_defs: list[FunctionTool],
) -> tuple[str, str]:
    """Create a new agent version and return (name, version)."""
    agent = project.agents.create_version(
        agent_name=AGENT_NAME,
        definition=PromptAgentDefinition(
            model=model,
            instructions=build_system_prompt(),
            tools=tool_defs,
        ),
        description=AGENT_DESCRIPTION,
        metadata={
            "created_by": "setup_agent.py",
            "purpose": "e-commerce-price-monitoring",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )
    logger.info(
        "Created agent: name=%s  version=%s  model=%s",
        agent.name, agent.version, model,
    )
    return agent.name, agent.version


def delete_agent(project: AIProjectClient, agent_name: str) -> bool:
    """Delete an agent by name. Returns True if successful."""
    try:
        project.agents.delete(agent_name=agent_name)
        logger.info("Deleted agent %s", agent_name)
        return True
    except Exception as exc:
        logger.warning("Could not delete agent %s: %s", agent_name, exc)
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
        help="Create a new version of the existing agent (refresh tools & instructions)",
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
    project = AIProjectClient(endpoint=endpoint, credential=credential)
    existing_name = get_existing_agent_name()

    # ── Show mode ──
    if args.show:
        name_to_show = existing_name or AGENT_NAME
        try:
            agent = project.agents.get(agent_name=name_to_show)
            print(f"  Agent Name     : {agent.name}")
            desc = getattr(agent, "description", None) or "(none)"
            print(f"  Description    : {desc}")
            # List versions
            versions = list(project.agents.list_versions(agent_name=name_to_show))
            if versions:
                latest = versions[0]
                print(f"  Latest Version : {latest.version}")
                defn = latest.definition
                defn_model = defn.get("model", "N/A") if hasattr(defn, "get") else getattr(defn, "model", "N/A")
                defn_tools = defn.get("tools", []) if hasattr(defn, "get") else getattr(defn, "tools", [])
                print(f"  Model          : {defn_model}")
                print(f"  Tools          : {len(defn_tools)} registered")
                print(f"  Created        : {latest.created_at}")
            print(f"\n  View in portal:")
            print(f"  {endpoint.rsplit('/api/', 1)[0]}")
        except Exception as exc:
            print(f"  Could not fetch agent '{name_to_show}': {exc}")
        sys.exit(0)

    # ── Discover MCP tools ──
    tool_defs = asyncio.run(discover_mcp_tools())
    print(f"  Discovered {len(tool_defs)} Playwright browser tools\n")

    # Print tool names for confirmation
    for i, td in enumerate(tool_defs, 1):
        print(f"    {i:2d}. {td.name}")
    print()

    # ── Recreate mode ──
    if args.recreate and existing_name:
        print(f"  Deleting existing agent '{existing_name}'...")
        delete_agent(project, existing_name)

    # ── Update mode — creates a new version of the same agent ──
    if args.update:
        agent_name = existing_name or AGENT_NAME
        print(f"  Creating new version of '{agent_name}'...")
        name, version = create_agent(project, model, tool_defs)
        save_agent_name(name)
        print(f"\n  Agent updated: {name} v{version}")
        print(f"  View in Microsoft Foundry portal → Agents\n")
        return

    # ── Create mode (default) ──
    if existing_name and not args.recreate:
        # Check if agent actually exists on the server
        try:
            project.agents.get(agent_name=existing_name)
            print(f"  Agent already exists: {existing_name}")
            print(f"  Use --update to create a new version, or --recreate to replace.\n")
            return
        except Exception:
            # Agent was deleted externally — proceed to create
            pass

    print("  Creating persistent agent...")
    name, version = create_agent(project, model, tool_defs)
    save_agent_name(name)

    print(f"\n  {'=' * 56}")
    print(f"  Agent registered successfully!")
    print(f"  {'=' * 56}")
    print(f"  Agent Name : {name}")
    print(f"  Version    : {version}")
    print(f"  Model      : {model}")
    print(f"  Tools      : {len(tool_defs)}")
    print(f"\n  Saved to : {ENV_FILE}")
    print(f"\n  Next steps:")
    print(f"    1. View your agent in the Microsoft Foundry portal → Agents")
    print(f"    2. Run:  python run_demo.py")
    print(f"       (it will reuse this persistent agent automatically)")
    print(f"    3. Check token usage & cost in the portal after runs\n")


if __name__ == "__main__":
    main()
