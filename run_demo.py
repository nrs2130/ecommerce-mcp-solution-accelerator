#!/usr/bin/env python3
"""
run_demo.py — Quick-start demo for the E-Commerce MCP Solution Accelerator
===========================================================================

Runs all three tiers against a sample product and prints results.

Usage::

    # Make sure .env is configured (see .env.example)
    python run_demo.py

    # Or override model / product inline:
    python run_demo.py --model gpt-5.4 --tier 1 --site amazon.in \
        --product "Neutrogena Hydro Boost Water Gel" \
        --url "https://www.amazon.in/dp/B00BQFTQW6"
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from src.agent import PlaywrightMCPAgent, MCPResult
from src.config import FoundryConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-30s  %(levelname)-8s  %(message)s",
)
logger = logging.getLogger(__name__)


def print_result(r: MCPResult) -> None:
    """Pretty-print a single MCPResult."""
    d = PlaywrightMCPAgent.result_to_dict(r)
    print("\n" + "=" * 60)
    for k, v in d.items():
        print(f"  {k:20s}: {v}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="E-Commerce MCP Solution Accelerator — demo runner"
    )
    parser.add_argument(
        "--tier", type=int, default=0,
        help="Tier to run: 1 (public price), 3 (geo pricing), 5 (device). "
             "Default: run all three.",
    )
    parser.add_argument("--product", type=str, default="")
    parser.add_argument("--site", type=str, default="")
    parser.add_argument("--url", type=str, default="")
    parser.add_argument("--model", type=str, default="")
    parser.add_argument(
        "--postal-codes", type=str, default="",
        help="Comma-separated postal codes for Tier 3 "
             "(e.g. '110001,400001,560001')",
    )
    args = parser.parse_args()

    # ── Defaults ──
    product = args.product or "Neutrogena Hydro Boost Water Gel, Blue, 50g"
    site = args.site or "amazon.in"
    url = args.url or "https://www.amazon.in/dp/B00BQFTQW6"
    postal_codes = (
        [pc.strip() for pc in args.postal_codes.split(",") if pc.strip()]
        if args.postal_codes
        else None
    )

    # ── Init agent ──
    config = FoundryConfig()
    agent = PlaywrightMCPAgent(config=config, model=args.model or "")
    agent.connect()

    tiers_to_run = [args.tier] if args.tier else [1, 3, 5]

    all_results: list[MCPResult] = []

    for tier in tiers_to_run:
        print(f"\n{'━' * 60}")
        print(f"  TIER {tier}")
        print(f"{'━' * 60}")

        results = agent.run_tier(
            tier=tier,
            product_name=product,
            site=site,
            url=url,
            postal_codes=postal_codes if tier == 3 else None,
        )

        for r in results:
            print_result(r)

        all_results.extend(results)

    # ── Summary ──
    successes = sum(1 for r in all_results if r.success)
    print(f"\n✅ {successes}/{len(all_results)} queries succeeded")
    print(f"📸 Screenshots saved to: screenshots/")

    # ── Optional: export to JSON ──
    export = [PlaywrightMCPAgent.result_to_dict(r) for r in all_results]
    with open("results.json", "w") as f:
        json.dump(export, f, indent=2)
    print(f"📄 Full results exported to: results.json")

    agent.disconnect()


if __name__ == "__main__":
    main()
