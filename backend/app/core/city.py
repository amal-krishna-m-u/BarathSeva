"""City-specific configuration for the Bengaluru prototype.

IMPORTANT — these ward boundaries are *approximate prototype stand-ins*, not
official BBMP ward geometry. They are square envelopes around well-known
locality centroids, sized to be disjoint, and exist so the PostGIS containment
and clustering logic can be exercised end to end. A production deployment
replaces this module with imported official ward shapefiles; see "Prototype vs
Production" in the README.
"""

from __future__ import annotations

from dataclasses import dataclass

# Bengaluru service envelope (approximate). Used only to decide whether a
# report falls inside the serviced city at all — a hard-fail check. Ward
# assignment is a separate, finer-grained operation.
CITY_ENVELOPE_WKT = (
    "POLYGON((77.40 12.72, 77.86 12.72, 77.86 13.22, 77.40 13.22, 77.40 12.72))"
)

# Half-width of each generated ward square, in degrees (~0.55 km half / 1.1 km span).
WARD_HALF_DEGREES = 0.005


@dataclass(frozen=True)
class WardSeed:
    ward_number: str
    name: str
    zone: str
    lat: float
    lon: float


# Locality centroids chosen to be mutually disjoint at WARD_HALF_DEGREES.
WARD_SEEDS: list[WardSeed] = [
    WardSeed("151", "Koramangala", "Bommanahalli", 12.9352, 77.6245),
    WardSeed("080", "Indiranagar", "East", 12.9719, 77.6412),
    WardSeed("084", "Domlur", "East", 12.9609, 77.6387),
    WardSeed("150", "BTM Layout", "Bommanahalli", 12.9166, 77.6101),
    WardSeed("174", "HSR Layout", "Bommanahalli", 12.9116, 77.6474),
    WardSeed("170", "Bellandur", "Mahadevapura", 12.9304, 77.6784),
    WardSeed("084B", "Marathahalli", "Mahadevapura", 12.9591, 77.6974),
    WardSeed("083", "Whitefield", "Mahadevapura", 12.9698, 77.7500),
    WardSeed("154", "Jayanagar", "South", 12.9250, 77.5938),
    WardSeed("167", "Basavanagudi", "South", 12.9422, 77.5736),
    WardSeed("099", "Rajajinagar", "West", 12.9916, 77.5522),
    WardSeed("045", "Malleshwaram", "West", 13.0035, 77.5647),
    WardSeed("064", "Shivajinagar", "East", 12.9850, 77.6050),
    WardSeed("112", "Electronic City", "Bommanahalli", 12.8452, 77.6602),
    WardSeed("008", "Yelahanka", "Yelahanka", 13.1007, 77.5963),
    WardSeed("022", "Hebbal", "Yelahanka", 13.0358, 77.5970),
    WardSeed("038", "Rajarajeshwari Nagar", "RR Nagar", 12.9256, 77.5180),
    WardSeed("198", "Kengeri", "RR Nagar", 12.9060, 77.4850),
]


def ward_polygon_wkt(seed: WardSeed, half: float = WARD_HALF_DEGREES) -> str:
    """Build the MULTIPOLYGON WKT envelope for a ward seed."""
    lo_lon, hi_lon = seed.lon - half, seed.lon + half
    lo_lat, hi_lat = seed.lat - half, seed.lat + half
    ring = (
        f"{lo_lon} {lo_lat}, {hi_lon} {lo_lat}, "
        f"{hi_lon} {hi_lat}, {lo_lon} {hi_lat}, {lo_lon} {lo_lat}"
    )
    return f"MULTIPOLYGON((({ring})))"


DEPARTMENT_SEEDS = [
    {
        "code": "BBMP",
        "name": "BBMP",
        "full_name": "Bruhat Bengaluru Mahanagara Palike",
        "categories": [
            "POTHOLE",
            "ROAD_DAMAGE",
            "DRAINAGE",
            "GARBAGE",
            "STREETLIGHT",
            "OTHER",
        ],
        "contact_email": "mock-bbmp@barathseva.local",
        "api_base_url": "mock://bbmp",
    },
    {
        "code": "BWSSB",
        "name": "BWSSB",
        "full_name": "Bangalore Water Supply and Sewerage Board",
        "categories": ["WATER_LEAK", "PIPELINE_BURST", "SEWAGE"],
        "contact_email": "mock-bwssb@barathseva.local",
        "api_base_url": "mock://bwssb",
    },
    {
        "code": "BESCOM",
        "name": "BESCOM",
        "full_name": "Bangalore Electricity Supply Company Limited",
        "categories": ["POWER_OUTAGE"],
        "contact_email": "mock-bescom@barathseva.local",
        "api_base_url": "mock://bescom",
    },
]


# resolution_hours per (category, priority). Escalation fires at ~60% elapsed.
SLA_RESOLUTION_HOURS: dict[str, dict[str, int]] = {
    "POTHOLE": {"P1": 24, "P2": 48, "P3": 96, "P4": 168},
    "ROAD_DAMAGE": {"P1": 24, "P2": 48, "P3": 96, "P4": 168},
    "WATER_LEAK": {"P1": 6, "P2": 12, "P3": 24, "P4": 48},
    "PIPELINE_BURST": {"P1": 4, "P2": 8, "P3": 24, "P4": 48},
    "DRAINAGE": {"P1": 12, "P2": 24, "P3": 48, "P4": 96},
    "SEWAGE": {"P1": 8, "P2": 24, "P3": 48, "P4": 96},
    "GARBAGE": {"P1": 12, "P2": 24, "P3": 48, "P4": 72},
    "STREETLIGHT": {"P1": 24, "P2": 48, "P3": 96, "P4": 168},
    "POWER_OUTAGE": {"P1": 4, "P2": 8, "P3": 24, "P4": 48},
    "OTHER": {"P1": 24, "P2": 48, "P3": 96, "P4": 168},
}
