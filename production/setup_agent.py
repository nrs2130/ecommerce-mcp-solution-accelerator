#!/usr/bin/env python3
"""
setup_agent.py — Register a Foundry-Native MCP Agent (Production)
=================================================================

Creates a Foundry agent that connects **directly** to a cloud-hosted
Playwright MCP server via ``MCPTool``.  Unlike the dev setup (which
uses 28 ``FunctionTool`` definitions + a local tool-call proxy), this
production agent lets Foundry call the MCP server itself — no local
relay, no Node.js dependency, zero code in the hot path.

Architecture::

    Your Code                Microsoft Foundry              Azure
    ─────────                ─────────────────              ─────
    Responses API  ────►  Agent (MCPTool)  ────►  Container Apps
                          GPT-5.4 reasons          (Playwright MCP
                          & emits tool calls        + headless Chrome)

Prerequisites:
    - .env configured with FOUNDRY_ENDPOINT, FOUNDRY_MODEL, PLAYWRIGHT_MCP_URL
    - Azure Container Apps deployed (run infra/deploy.sh first)
    - ``az login`` completed

Requires:
    pip install -r requirements.txt

Usage::

    # First time — creates agent + writes FOUNDRY_AGENT_NAME to .env
    python setup_agent.py

    # Update agent (new version with same name)
    python setup_agent.py --update

    # Recreate from scratch
    python setup_agent.py --recreate

    # Show current agent info
    python setup_agent.py --show
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from dotenv import load_dotenv, set_key
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    PromptAgentDefinition,
    MCPTool,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

AGENT_NAME = "ecommerce-mcp-price-monitor"
AGENT_DESCRIPTION = (
    "Production e-commerce pricing agent. Foundry calls a cloud-hosted "
    "Playwright MCP server directly via MCPTool — no local tool proxy."
)
ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


# ── System prompt ────────────────────────────────────────────────────────────

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


# ── Agent management ─────────────────────────────────────────────────────────

def get_existing_agent_name() -> str:
    return os.getenv("FOUNDRY_AGENT_NAME", "").strip()


def save_agent_name(name: str) -> None:
    if os.path.exists(ENV_FILE):
        set_key(ENV_FILE, "FOUNDRY_AGENT_NAME", name)
    else:
        with open(ENV_FILE, "a") as f:
            f.write(f"\nFOUNDRY_AGENT_NAME={name}\n")
    logger.info("Saved FOUNDRY_AGENT_NAME=%s to %s", name, ENV_FILE)


def create_agent(
    project: AIProjectClient,
    model: str,
    mcp_url: str,
    require_approval: str = "never",
) -> tuple[str, str]:
    """Create a Foundry agent with an MCPTool pointing to the remote server."""

    mcp_tool = MCPTool(
        server_label="playwright-browser",
        server_url=mcp_url,
        require_approval=require_approval,
    )

    agent = project.agents.create_version(
        agent_name=AGENT_NAME,
        definition=PromptAgentDefinition(
            model=model,
            instructions=build_system_prompt(),
            tools=[mcp_tool],
        ),
        description=AGENT_DESCRIPTION,
        metadata={
            "created_by": "production/setup_agent.py",
            "purpose": "e-commerce-price-monitoring",
            "mcp_server_url": mcp_url,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )

    logger.info(
        "Created agent: name=%s  version=%s  model=%s  mcp_url=%s",
        agent.name, agent.version, model, mcp_url,
    )
    return agent.name, agent.version


def delete_agent(project: AIProjectClient, name: str) -> bool:
    try:
        project.agents.delete(agent_name=name)
        logger.info("Deleted agent %s", name)
        return True
    except Exception as exc:
        logger.warning("Could not delete agent %s: %s", name, exc)
        return False


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Register a Foundry-Native MCP Agent (production path)"
    )
    parser.add_argument("--model", type=str, default="")
    parser.add_argument("--mcp-url", type=str, default="",
                        help="Override PLAYWRIGHT_MCP_URL from env")
    parser.add_argument("--update", action="store_true",
                        help="Create a new version of the existing agent")
    parser.add_argument("--recreate", action="store_true",
                        help="Delete + recreate the agent")
    parser.add_argument("--show", action="store_true",
                        help="Show current agent details and exit")
    parser.add_argument("--require-approval", type=str, default="never",
                        choices=["never", "always"],
                        help="MCP tool approval policy (default: never)")
    args = parser.parse_args()

    # ── Validate config ──
    endpoint = os.getenv("FOUNDRY_ENDPOINT", "").strip()
    model = args.model or os.getenv("FOUNDRY_MODEL", "gpt-5.4")
    mcp_url = args.mcp_url or os.getenv("PLAYWRIGHT_MCP_URL", "").strip()

    if not endpoint:
        print("\n  ERROR: FOUNDRY_ENDPOINT is not set.")
        print("  Copy .env.example to .env and fill in your values.\n")
        sys.exit(1)

    print(f"\n  Endpoint : {endpoint}")
    print(f"  Model    : {model}")
    print(f"  MCP URL  : {mcp_url or '(not set)'}")
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
            versions = list(project.agents.list_versions(agent_name=name_to_show))
            if versions:
                latest = versions[0]
                print(f"  Latest Version : {latest.version}")
                defn = latest.definition
                defn_model = (defn.get("model", "N/A") if hasattr(defn, "get")
                              else getattr(defn, "model", "N/A"))
                defn_tools = (defn.get("tools", []) if hasattr(defn, "get")
                              else getattr(defn, "tools", []))
                print(f"  Model          : {defn_model}")
                print(f"  Tools          : {len(defn_tools)} registered")
                # Show MCPTool URLs
                for t in defn_tools:
                    url = (t.get("server_url") if hasattr(t, "get")
                           else getattr(t, "server_url", None))
                    if url:
                        print(f"  MCP Server URL : {url}")
                print(f"  Created        : {latest.created_at}")
            print(f"\n  View in Foundry portal:")
            print(f"  {endpoint.rsplit('/api/', 1)[0]}")
        except Exception as exc:
            print(f"  Could not fetch agent '{name_to_show}': {exc}")
        sys.exit(0)

    # ── Validate MCP URL ──
    if not mcp_url:
        print("  ERROR: PLAYWRIGHT_MCP_URL is not set.")
        print("  Deploy the MCP server first (infra/deploy.sh) then add")
        print("  the URL to your .env file.\n")
        sys.exit(1)

    # ── Recreate mode ──
    if args.recreate and existing_name:
        print(f"  Deleting existing agent '{existing_name}'...")
        delete_agent(project, existing_name)

    # ── Update mode ──
    if args.update:
        agent_name = existing_name or AGENT_NAME
        print(f"  Creating new version of '{agent_name}'...")
        name, version = create_agent(
            project, model, mcp_url, args.require_approval
        )
        save_agent_name(name)
        print(f"\n  Agent updated: {name} v{version}\n")
        return

    # ── Create mode (default) ──
    if existing_name and not args.recreate:
        try:
            project.agents.get(agent_name=existing_name)
            print(f"  Agent already exists: {existing_name}")
            print("  Use --update for a new version, or --recreate to replace.\n")
            return
        except Exception:
            pass

    print("  Creating Foundry-native MCP agent...")
    name, version = create_agent(
        project, model, mcp_url, args.require_approval
    )
    save_agent_name(name)

    print(f"\n  {'=' * 56}")
    print(f"  Agent registered successfully!")
    print(f"  {'=' * 56}")
    print(f"  Agent Name     : {name}")
    print(f"  Version        : {version}")
    print(f"  Model          : {model}")
    print(f"  MCP Server URL : {mcp_url}")
    print(f"  Tool Approval  : {args.require_approval}")
    print(f"\n  Saved to: {ENV_FILE}")
    print(f"\n  Next steps:")
    print(f"    1. View agent in Microsoft Foundry portal → Agents")
    print(f"    2. Run:  python run_demo.py")
    print()


if __name__ == "__main__":
    main()
