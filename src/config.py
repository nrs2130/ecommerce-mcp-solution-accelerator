"""
Configuration — E-Commerce MCP Solution Accelerator
=====================================================

All settings are loaded from environment variables (or a ``.env`` file
via ``python-dotenv``).  Copy ``.env.example`` to ``.env`` and fill in
your Azure AI Foundry endpoint.

Required environment variables
------------------------------
FOUNDRY_ENDPOINT   – Azure AI Foundry project endpoint URL
                     (e.g. https://<hub>.services.ai.azure.com/api/projects/<project>)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


# ── Azure AI Foundry ────────────────────────────────────────────────────────

@dataclass
class FoundryConfig:
    """Azure AI Foundry project configuration."""

    endpoint: str = os.getenv("FOUNDRY_ENDPOINT", "")
    model: str = os.getenv("FOUNDRY_MODEL", "gpt-5.4")


# ── Locations for Tier 3 (geographic pricing) ───────────────────────────────

@dataclass
class Location:
    """A delivery location for geo-based pricing comparison."""

    code: str        # ZIP (US) or postal code (CA / IN / MX)
    city: str
    country: str     # ISO 3166-1 alpha-2: "US", "CA", "IN", "MX", …

    # Optional lat/lon — used for navigator.geolocation override
    latitude: float | None = None
    longitude: float | None = None

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None


# Pre-built location pool — extend as needed
LOCATION_POOL: list[Location] = [
    # ── United States ──
    Location("10001",  "New York, NY",       "US", 40.7484,  -73.9967),
    Location("90210",  "Beverly Hills, CA",  "US", 34.0901, -118.4065),
    Location("60601",  "Chicago, IL",        "US", 41.8819,  -87.6278),
    Location("77001",  "Houston, TX",        "US", 29.7604,  -95.3698),
    Location("33101",  "Miami, FL",          "US", 25.7617,  -80.1918),
    Location("55401",  "Minneapolis, MN",    "US", 44.9778,  -93.2650),
    Location("19104",  "Philadelphia, PA",   "US", 39.9526,  -75.1652),
    Location("98101",  "Seattle, WA",        "US", 47.6062, -122.3321),
    Location("80201",  "Denver, CO",         "US", 39.7392, -104.9903),
    Location("36104",  "Montgomery, AL",     "US", 32.3792,  -86.3077),
    Location("30301",  "Atlanta, GA",        "US", 33.7490,  -84.3880),
    Location("94101",  "San Francisco, CA",  "US", 37.7749, -122.4194),
    Location("75201",  "Dallas, TX",         "US", 32.7767,  -96.7970),
    # ── Canada ──
    Location("M5V 2T6", "Toronto, ON",   "CA", 43.6426,  -79.3871),
    Location("V6B 1A1", "Vancouver, BC",  "CA", 49.2827, -123.1207),
    Location("T2P 1J9", "Calgary, AB",    "CA", 51.0447, -114.0719),
    Location("H2X 1Y4", "Montreal, QC",   "CA", 45.5017,  -73.5673),
    Location("K1A 0A6", "Ottawa, ON",     "CA", 45.4215,  -75.6972),
    Location("R3C 0A5", "Winnipeg, MB",   "CA", 49.8951,  -97.1384),
    # ── India ──
    Location("110001", "New Delhi",  "IN", 28.6139, 77.2090),
    Location("400001", "Mumbai",     "IN", 19.0760, 72.8777),
    Location("560001", "Bangalore",  "IN", 12.9716, 77.5946),
    # ── Mexico ──
    Location("06600", "Mexico City", "MX", 19.4204, -99.1591),
    Location("44100", "Guadalajara", "MX", 20.6597, -103.3496),
    Location("64000", "Monterrey",   "MX", 25.6866, -100.3161),
]

# Build a quick lookup dict: code → Location
_LOCATION_INDEX: dict[str, Location] = {
    loc.code.strip().upper(): loc for loc in LOCATION_POOL
}
# Also index without spaces (Canadian codes: "M5V2T6" → "M5V 2T6")
for loc in LOCATION_POOL:
    _LOCATION_INDEX[loc.code.strip().upper().replace(" ", "")] = loc


def resolve_location(code: str) -> Location:
    """
    Look up a postal/ZIP code and return a Location with coordinates.

    Falls back to a coordinate-less Location if not in the pool.
    """
    normalized = code.strip().upper()
    if normalized in _LOCATION_INDEX:
        return _LOCATION_INDEX[normalized]
    no_space = normalized.replace(" ", "")
    if no_space in _LOCATION_INDEX:
        return _LOCATION_INDEX[no_space]
    return Location(code=code.strip(), city="Unknown", country="")


# ── Sample product catalog ──────────────────────────────────────────────────

@dataclass
class Product:
    """A product SKU to monitor."""

    name: str
    site: str          # e.g. "amazon.in", "amazon.ca", "walmart.ca"
    url: str = ""      # Direct PDP URL (if known — faster than search)

    def search_url(self) -> str:
        """Build a fallback search URL if no direct PDP URL is set."""
        if self.url:
            return self.url
        return f"https://www.{self.site}/s?k={self.name.replace(' ', '+')}"


# Example catalog — replace with your own SKUs
PRODUCT_CATALOG: list[Product] = [
    Product(
        name="Neutrogena Oil-Free Acne Wash 175 ml",
        site="amazon.in",
        url="https://www.amazon.in/dp/B006LXDMCS",
    ),
    Product(
        name="Neutrogena Hydro Boost Water Gel, Blue, 50g",
        site="amazon.in",
        url="https://www.amazon.in/dp/B00BQFTQW6",
    ),
    Product(
        name="Clean & Clear Facial Wash 150 ml",
        site="amazon.in",
        url="https://www.amazon.in/dp/B00CI3HDMU",
    ),
    Product(
        name="Aveeno Dermexa Emollient Cream",
        site="amazon.ca",
        url="",
    ),
    Product(
        name="Neutrogena Hydro Boost Moisturizer",
        site="walmart.ca",
        url="",
    ),
]
