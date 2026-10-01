"""Geospatial operations — all deterministic, all executed in PostGIS.

A ward boundary is a polygon containment test, not an opinion. Keeping this
logic in the database means the same geometry answers the pipeline, the
dashboard and the analytics views.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.core.city import CITY_ENVELOPE_WKT

EARTH_RADIUS_M = 6_371_000.0
DEGREES_PER_METER = 1.0 / 111_320.0


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres. Used where a DB round-trip is wasteful
    (EXIF GPS deltas, reporter velocity checks)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


@dataclass
class WardMatch:
    ward_id: Optional[int]
    ward_name: Optional[str]
    ward_number: Optional[str]
    zone: Optional[str]
    exact: bool
    distance_meters: float


def inside_city_envelope(db: Session, lat: float, lon: float) -> bool:
    """Is the point inside the serviced city at all? A hard-fail check."""
    row = db.execute(
        text(
            "SELECT ST_Contains("
            "  ST_GeomFromText(:envelope, 4326),"
            "  ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)"
            ") AS inside"
        ),
        {"envelope": CITY_ENVELOPE_WKT, "lon": lon, "lat": lat},
    ).first()
    return bool(row and row.inside)


def resolve_ward(
    db: Session, lat: float, lon: float, max_fallback_meters: float = 5000.0
) -> WardMatch:
    """Resolve a point to a ward.

    Exact PostGIS containment is preferred. Because the prototype's ward
    envelopes are approximate stand-ins that do not tessellate the whole city,
    a point falling in a gap is snapped to the nearest ward within
    ``max_fallback_meters`` and reported with ``exact=False`` so callers can
    treat it as an approximation rather than a fact.
    """
    contained = db.execute(
        text(
            "SELECT id, name, ward_number, zone FROM wards "
            "WHERE ST_Contains(boundary, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)) "
            "LIMIT 1"
        ),
        {"lon": lon, "lat": lat},
    ).first()
    if contained:
        return WardMatch(
            ward_id=contained.id,
            ward_name=contained.name,
            ward_number=contained.ward_number,
            zone=contained.zone,
            exact=True,
            distance_meters=0.0,
        )

    nearest = db.execute(
        text(
            "SELECT id, name, ward_number, zone, "
            "       ST_Distance(boundary::geography, "
            "                   ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography)"
            "       AS dist "
            "FROM wards ORDER BY dist ASC LIMIT 1"
        ),
        {"lon": lon, "lat": lat},
    ).first()
    if nearest and nearest.dist is not None and nearest.dist <= max_fallback_meters:
        return WardMatch(
            ward_id=nearest.id,
            ward_name=nearest.name,
            ward_number=nearest.ward_number,
            zone=nearest.zone,
            exact=False,
            distance_meters=float(nearest.dist),
        )
    return WardMatch(None, None, None, None, False, float("inf"))


@dataclass
class NearbyComplaint:
    id: int
    reference: str
    category: Optional[str]
    status: str
    distance_meters: float
    created_at: datetime


def nearby_complaints(
    db: Session,
    lat: float,
    lon: float,
    radius_meters: Optional[float] = None,
    window_hours: Optional[int] = None,
    exclude_complaint_id: Optional[int] = None,
    same_category: Optional[str] = None,
) -> list[NearbyComplaint]:
    """Complaints within a radius and time window — the corroboration query."""
    radius = radius_meters if radius_meters is not None else settings.cluster_radius_meters
    hours = window_hours if window_hours is not None else settings.cluster_window_hours

    sql = (
        "SELECT id, reference, category, status, created_at, "
        "       ST_Distance(location::geography, "
        "                   ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography)"
        "       AS dist "
        "FROM complaints "
        "WHERE ST_DWithin(location::geography, "
        "                 ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, :radius) "
        "  AND created_at >= now() - (:hours || ' hours')::interval "
        "  AND status <> 'REJECTED' "
    )
    params: dict = {"lon": lon, "lat": lat, "radius": radius, "hours": str(hours)}
    if exclude_complaint_id is not None:
        sql += "  AND id <> :exclude "
        params["exclude"] = exclude_complaint_id
    if same_category:
        sql += "  AND category = :category "
        params["category"] = same_category
    sql += "ORDER BY dist ASC LIMIT 200"

    rows = db.execute(text(sql), params).all()
    return [
        NearbyComplaint(
            id=r.id,
            reference=r.reference,
            category=r.category,
            status=r.status,
            distance_meters=float(r.dist),
            created_at=r.created_at,
        )
        for r in rows
    ]


@dataclass
class Hotspot:
    cluster_id: int
    complaint_count: int
    latitude: float
    longitude: float
    categories: list[str]
    ward_name: Optional[str]
    open_count: int
    breached_count: int


def hotspots(
    db: Session,
    radius_meters: Optional[float] = None,
    min_complaints: Optional[int] = None,
    window_hours: int = 720,
) -> list[Hotspot]:
    """Density-based hotspot detection via PostGIS ST_ClusterDBSCAN."""
    radius = radius_meters if radius_meters is not None else settings.cluster_radius_meters
    minpts = min_complaints if min_complaints is not None else settings.hotspot_min_complaints
    eps = radius * DEGREES_PER_METER

    sql = text(
        """
        WITH clustered AS (
            SELECT c.id,
                   c.location,
                   c.category,
                   c.status,
                   c.sla_breached,
                   c.ward_id,
                   ST_ClusterDBSCAN(c.location, :eps, :minpts) OVER () AS cid
            FROM complaints c
            WHERE c.status <> 'REJECTED'
              AND c.created_at >= now() - (:hours || ' hours')::interval
        )
        SELECT cid,
               COUNT(*) AS cnt,
               ST_Y(ST_Centroid(ST_Collect(location))) AS lat,
               ST_X(ST_Centroid(ST_Collect(location))) AS lon,
               ARRAY_AGG(DISTINCT category) FILTER (WHERE category IS NOT NULL) AS cats,
               COUNT(*) FILTER (WHERE status <> 'RESOLVED') AS open_cnt,
               COUNT(*) FILTER (WHERE sla_breached) AS breached_cnt,
               MODE() WITHIN GROUP (ORDER BY ward_id) AS ward_id
        FROM clustered
        WHERE cid IS NOT NULL
        GROUP BY cid
        ORDER BY cnt DESC
        """
    )
    rows = db.execute(
        text(str(sql)), {"eps": eps, "minpts": minpts, "hours": str(window_hours)}
    ).all()

    ward_names: dict[int, str] = {}
    ward_ids = [r.ward_id for r in rows if r.ward_id is not None]
    if ward_ids:
        for w in db.execute(
            text("SELECT id, name FROM wards WHERE id = ANY(:ids)"), {"ids": ward_ids}
        ).all():
            ward_names[w.id] = w.name

    return [
        Hotspot(
            cluster_id=int(r.cid),
            complaint_count=int(r.cnt),
            latitude=float(r.lat),
            longitude=float(r.lon),
            categories=list(r.cats or []),
            ward_name=ward_names.get(r.ward_id),
            open_count=int(r.open_cnt),
            breached_count=int(r.breached_cnt),
        )
        for r in rows
    ]
