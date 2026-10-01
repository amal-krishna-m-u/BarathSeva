"""Geocoding proxy over OpenStreetMap Nominatim.

Proxied rather than called from the browser for three reasons:

  * Nominatim's usage policy requires an identifying User-Agent, which a
    browser cannot set.
  * It allows at most ~1 request/second. One shared server-side limiter can
    honour that; N browser tabs cannot.
  * Results are cacheable. The same street searched by fifty citizens should
    cost one upstream request, not fifty.

Results are bounded to the serviced city, so a citizen searching "MG Road"
gets Bengaluru rather than a street on another continent.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import settings
from app.core.city import CITY_BOUNDS, CITY_CENTER
from app.core.geo import inside_city_envelope, resolve_ward
from app.db import get_db
from app.schemas import GeocodeResult, ReverseGeocodeResult

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/geocode", tags=["geocoding"])

CACHE_PREFIX = "barathseva:geocode:"
RATE_KEY = "barathseva:geocode:rate"


class _Cache:
    """Redis cache with an in-process fallback and a shared rate limiter."""

    def __init__(self) -> None:
        self._memory: dict[str, tuple[float, Any]] = {}
        self._calls: list[float] = []
        self._redis = None
        try:
            import redis

            self._redis = redis.Redis.from_url(
                settings.redis_url, decode_responses=True, socket_connect_timeout=2
            )
            self._redis.ping()
        except Exception as exc:  # pragma: no cover - optional path
            logger.warning("Redis unavailable for geocode cache (%s)", exc)
            self._redis = None

    def get(self, key: str) -> Optional[Any]:
        full = CACHE_PREFIX + key
        if self._redis is not None:
            try:
                raw = self._redis.get(full)
                return json.loads(raw) if raw else None
            except Exception:
                pass
        entry = self._memory.get(full)
        if entry and time.time() - entry[0] < settings.geocode_cache_ttl_seconds:
            return entry[1]
        return None

    def set(self, key: str, value: Any) -> None:
        full = CACHE_PREFIX + key
        if self._redis is not None:
            try:
                self._redis.setex(
                    full, settings.geocode_cache_ttl_seconds, json.dumps(value)
                )
                return
            except Exception:
                pass
        self._memory[full] = (time.time(), value)

    def allow_upstream_call(self) -> bool:
        """Shared budget across all callers, so the policy is actually honoured."""
        limit = settings.geocode_rate_limit_per_minute
        if self._redis is not None:
            try:
                count = self._redis.incr(RATE_KEY)
                if count == 1:
                    self._redis.expire(RATE_KEY, 60)
                return count <= limit
            except Exception:
                pass
        now = time.time()
        self._calls = [t for t in self._calls if now - t < 60]
        if len(self._calls) >= limit:
            return False
        self._calls.append(now)
        return True


cache = _Cache()


def _nominatim(path: str, params: dict[str, Any]) -> Any:
    if not cache.allow_upstream_call():
        raise HTTPException(
            status_code=429,
            detail=(
                "Geocoding is rate limited to respect the OpenStreetMap "
                "Nominatim usage policy. Try again shortly, or place the pin "
                "directly on the map."
            ),
        )
    try:
        response = httpx.get(
            f"{settings.nominatim_base_url}{path}",
            params=params,
            headers={
                "User-Agent": settings.nominatim_user_agent,
                "Accept-Language": "en",
            },
            timeout=settings.nominatim_timeout_seconds,
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        logger.warning("nominatim %s failed: %s", path, exc)
        raise HTTPException(
            status_code=503,
            detail=(
                "Address lookup is unavailable right now. You can still place "
                "the pin directly on the map."
            ),
        )


@router.get("/search", response_model=list[GeocodeResult])
def search(
    q: str = Query(..., min_length=3, max_length=200),
    limit: int = Query(6, ge=1, le=10),
    db: Session = Depends(get_db),
) -> list[GeocodeResult]:
    """Find a place by name, bounded to the serviced city."""
    query = q.strip()
    cache_key = f"search:{query.lower()}:{limit}"

    payload = cache.get(cache_key)
    if payload is None:
        min_lon, min_lat, max_lon, max_lat = CITY_BOUNDS
        payload = _nominatim(
            "/search",
            {
                "q": query,
                "format": "jsonv2",
                "limit": limit,
                "countrycodes": "in",
                "viewbox": f"{min_lon},{max_lat},{max_lon},{min_lat}",
                "bounded": 1,
                "addressdetails": 1,
            },
        )
        cache.set(cache_key, payload)

    results: list[GeocodeResult] = []
    for item in payload or []:
        try:
            latitude = float(item["lat"])
            longitude = float(item["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        results.append(
            GeocodeResult(
                display_name=item.get("display_name", query),
                latitude=latitude,
                longitude=longitude,
                category=item.get("category") or item.get("class"),
                type=item.get("type"),
                importance=item.get("importance"),
                inside_service_area=inside_city_envelope(db, latitude, longitude),
            )
        )
    return results


@router.get("/reverse", response_model=ReverseGeocodeResult)
def reverse(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    db: Session = Depends(get_db),
) -> ReverseGeocodeResult:
    """Describe a coordinate, and say which ward it falls in.

    The ward comes from PostGIS, not from Nominatim: ward assignment is the
    platform's own authoritative geometry, and must not depend on a third
    party being reachable.
    """
    ward = resolve_ward(db, lat, lon)
    inside = inside_city_envelope(db, lat, lon)

    cache_key = f"reverse:{lat:.5f}:{lon:.5f}"
    payload = cache.get(cache_key)
    if payload is None:
        try:
            payload = _nominatim(
                "/reverse",
                {"lat": lat, "lon": lon, "format": "jsonv2", "addressdetails": 1},
            )
            cache.set(cache_key, payload)
        except HTTPException:
            # Address text is a convenience; the ward is what actually matters,
            # so a Nominatim outage must not block the citizen.
            payload = {}

    address = (payload or {}).get("address", {}) or {}
    return ReverseGeocodeResult(
        display_name=(payload or {}).get("display_name"),
        road=address.get("road") or address.get("pedestrian"),
        suburb=address.get("suburb") or address.get("neighbourhood"),
        city=address.get("city") or address.get("town") or address.get("state_district"),
        postcode=address.get("postcode"),
        latitude=lat,
        longitude=lon,
        inside_service_area=inside,
        ward_name=ward.ward_name,
        ward_number=ward.ward_number,
    )


@router.get("/defaults")
def defaults() -> dict[str, Any]:
    """Map starting position and bounds for the picker."""
    min_lon, min_lat, max_lon, max_lat = CITY_BOUNDS
    return {
        "center": {"latitude": CITY_CENTER[0], "longitude": CITY_CENTER[1]},
        "bounds": {
            "min_latitude": min_lat,
            "min_longitude": min_lon,
            "max_latitude": max_lat,
            "max_longitude": max_lon,
        },
        "city": settings.city,
        "attribution": "© OpenStreetMap contributors",
    }
