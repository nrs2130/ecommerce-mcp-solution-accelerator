#!/usr/bin/env python3
"""
run_demo.py — Production Demo (Foundry-Native MCP Agent)
=========================================================

Sends pricing queries to a Foundry agent that has a cloud-hosted
Playwright MCP server connected via ``MCPTool``.  All browser
automation happens server-side — this script is a thin API client.

Architecture::

    This script
        │  Responses API (HTTPS)
        ▼
    Microsoft Foundry Agent Service
        │  MCPTool (Streamable HTTP)
        ▼
    Azure Container Apps
        (Playwright MCP + headless Chromium)

Usage::

    # Run the default demo (Tier 1 — single product)
    python run_demo.py

    # Custom query
    python run_demo.py --query "Navigate to https://www.amazon.in/dp/B00BQFTQW6 and extract the price"

    # All tiers
    python run_demo.py --tier 1
    python run_demo.py --tier 2 --postal-codes "110001,400001"
    python run_demo.py --tier 3

Prerequisites:
    - .env configured (FOUNDRY_ENDPOINT, FOUNDRY_MODEL, FOUNDRY_AGENT_NAME)
    - Agent registered via setup_agent.py
    - MCP server deployed (infra/deploy.sh)

Requires:
    pip install -r requirements.txt
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
logger = logging.getLogger(__name__)


# ── Query templates ──────────────────────────────────────────────────────────

TIER_1_TEMPLATE = (
    "Navigate to {url} and extract all pricing information.\n"
    "Product: {product}\n"
    "Site: {site}\n\n"
    "Take a screenshot of the product page, then extract:\n"
    "- Current price (with currency symbol)\n"
    "- Any promotions or discounts\n"
    "- Star rating and review count\n"
    "- Seller name\n"
    "- Availability status"
)

TIER_2_TEMPLATE = (
    "Navigate to {url} and extract pricing for delivery to postal code {postal_code}.\n"
    "Product: {product}\n"
    "Site: {site}\n\n"
    "Steps:\n"
    "1. Load the product page\n"
    "2. Look for a delivery location / ZIP code input and change it to {postal_code}\n"
    "3. Wait for the page to update with location-specific pricing\n"
    "4. Take a screenshot showing the price for that location\n"
    "5. Extract the price and confirm the delivery location"
)

TIER_3_TEMPLATE = (
    "Navigate to {url} and extract pricing information.\n"
    "Product: {product}\n"
    "Site: {site}\n\n"
    "Take a screenshot and extract all pricing details.\n"
    "Note what device/viewport you are viewing from."
)


# ── Demo products ────────────────────────────────────────────────────────────

DEMO_PRODUCTS = {
    "neutrogena-hydro-boost": {
        "product": "Neutrogena Hydro Boost Water Gel, Blue, 50g",
        "site": "amazon.in",
        "url": "https://www.amazon.in/dp/B00BQFTQW6",
    },
    "neutrogena-acne-wash": {
        "product": "Neutrogena Oil-Free Acne Wash 175 ml",
        "site": "amazon.in",
        "url": "https://www.amazon.in/dp/B006LXDMCS",
    },
}


def create_query(tier: int, product: dict, postal_code: str = "") -> str:
    """Build a natural-language query for the given tier."""
    if tier == 2 and postal_code:
        return TIER_2_TEMPLATE.format(postal_code=postal_code, **product)
    elif tier == 3:
        return TIER_3_TEMPLATE.format(**product)
    else:
        return TIER_1_TEMPLATE.format(**product)


# ── Foundry Responses API client ─────────────────────────────────────────────

def run_query(
    project: AIProjectClient,
    agent_name: str,
    query: str,
) -> dict:
    """
    Send a query to the Foundry agent via the Responses API.

    Returns a dict with the agent's response and metadata.
    """
    start = time.time()

    # Get the OpenAI-compatible client from the project
    openai_client = project.get_openai_client()

    # Create a conversation (session) for this query
    conversation = openai_client.conversations.create()
    logger.info("Created conversation: %s", conversation.id)

    # Send the query using the Responses API with agent_reference
    response = openai_client.responses.create(
        conversation=conversation.id,
        input=query,
        extra_body={
            "agent_reference": {
                "name": agent_name,
                "type": "agent_reference",
            }
        },
    )

    elapsed = time.time() - start

    # Extract the response text
    output_text = getattr(response, "output_text", None)
    if output_text is None:
        # Fallback: iterate through output items
        output_text = ""
        for item in getattr(response, "output", []):
            if hasattr(item, "text"):
                output_text += item.text
            elif hasattr(item, "content"):
                for c in item.content:
                    if hasattr(c, "text"):
                        output_text += c.text

    return {
        "conversation_id": conversation.id,
        "response_id": getattr(response, "id", ""),
        "output_text": output_text,
        "elapsed_seconds": round(elapsed, 1),
        "model": getattr(response, "model", ""),
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="E-Commerce MCP Solution Accelerator — Production Demo"
    )
    parser.add_argument(
        "--tier", type=int, default=1,
        help="Tier: 1 (public price), 2 (geo pricing), 3 (device). Default: 1",
    )
    parser.add_argument(
        "--query", type=str, default="",
        help="Custom free-text query (overrides tier templates)",
    )
    parser.add_argument(
        "--product", type=str, default="neutrogena-hydro-boost",
        choices=list(DEMO_PRODUCTS.keys()),
        help="Demo product to query",
    )
    parser.add_argument(
        "--postal-codes", type=str, default="110001,400001",
        help="Comma-separated postal codes for Tier 2",
    )
    parser.add_argument(
        "--output", type=str, default="results.json",
        help="Path to write JSON results",
    )
    args = parser.parse_args()

    # ── Validate config ──
    endpoint = os.getenv("FOUNDRY_ENDPOINT", "").strip()
    agent_name = os.getenv("FOUNDRY_AGENT_NAME", "").strip()

    if not endpoint:
        print("\n  ERROR: FOUNDRY_ENDPOINT is not set.")
        print("  Copy .env.example to .env and fill in your values.\n")
        sys.exit(1)

    if not agent_name:
        print("\n  ERROR: FOUNDRY_AGENT_NAME is not set.")
        print("  Run setup_agent.py first to register the agent.\n")
        sys.exit(1)

    # ── Connect ──
    credential = DefaultAzureCredential()
    project = AIProjectClient(endpoint=endpoint, credential=credential)

    print(f"\n  Endpoint   : {endpoint}")
    print(f"  Agent      : {agent_name}")
    print(f"  Tier       : {args.tier}")
    print()

    product = DEMO_PRODUCTS[args.product]
    results = []

    if args.query:
        # ── Custom query mode ──
        print(f"  Query: {args.query[:80]}...")
        print(f"  {'─' * 56}")
        result = run_query(project, agent_name, args.query)
        results.append(result)
        print(f"\n{result['output_text']}")
        print(f"\n  ⏱  {result['elapsed_seconds']}s")

    elif args.tier == 2:
        # ── Tier 2: one query per postal code ──
        codes = [c.strip() for c in args.postal_codes.split(",") if c.strip()]
        for code in codes:
            query = create_query(2, product, postal_code=code)
            print(f"  Tier 2 — postal code {code}")
            print(f"  {'─' * 56}")
            result = run_query(project, agent_name, query)
            result["postal_code"] = code
            results.append(result)
            print(f"\n{result['output_text'][:500]}...")
            print(f"\n  ⏱  {result['elapsed_seconds']}s\n")

    else:
        # ── Tier 1 or 3 ──
        query = create_query(args.tier, product)
        print(f"  Tier {args.tier} — {product['product']}")
        print(f"  {'─' * 56}")
        result = run_query(project, agent_name, query)
        results.append(result)
        print(f"\n{result['output_text']}")
        print(f"\n  ⏱  {result['elapsed_seconds']}s")

    # ── Export results ──
    with open(args.output, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  Results saved to: {args.output}")

    # ── Summary ──
    total_time = sum(r["elapsed_seconds"] for r in results)
    print(f"  Total queries   : {len(results)}")
    print(f"  Total time      : {total_time:.1f}s")
    print()


if __name__ == "__main__":
    main()
