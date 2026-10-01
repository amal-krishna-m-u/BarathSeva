"""End-to-end lifecycle demonstration over the real HTTP API.

Exercises every branch the architecture claims to have: clean intake, the three
department routes, corroboration into a hotspot, each class of evidence hard
failure, the human-review hold, verifier rejection of non-civic noise, an SLA
breach with escalation and amplification, and resolution.

Usage:
    python scripts/demo_lifecycle.py             # against a running API
    python scripts/demo_lifecycle.py --reset     # rebuild the database first
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_test_photo import make_photo  # noqa: E402

BASE = "http://127.0.0.1:8000"
TIMEOUT = 60.0

# Locations inside seeded ward envelopes (half-width ~0.005 deg).
KORAMANGALA = (12.9352, 77.6245)
KORAMANGALA_NEAR = [
    (12.93530, 77.62460),
    (12.93508, 77.62438),
    (12.93541, 77.62471),
    (12.93495, 77.62425),
]
INDIRANAGAR = (12.9719, 77.6412)
JAYANAGAR = (12.9250, 77.5938)
WHITEFIELD = (12.9698, 77.7500)
MUMBAI = (19.0760, 72.8777)

OK = "\033[92m"
WARN = "\033[93m"
FAIL = "\033[91m"
DIM = "\033[2m"
BOLD = "\033[1m"
END = "\033[0m"


def header(text: str) -> None:
    print(f"\n{BOLD}{'=' * 78}\n{text}\n{'=' * 78}{END}")


def step(text: str) -> None:
    print(f"\n{BOLD}{text}{END}")


def capture_token(
    client: httpx.Client, lat: float, lon: float, device: str = "demo-device"
) -> str:
    response = client.post(
        f"{BASE}/api/capture-token",
        json={"device_id": device, "latitude": lat, "longitude": lon},
    )
    response.raise_for_status()
    return response.json()["token"]


def submit(
    client: httpx.Client,
    description: str,
    lat: float,
    lon: float,
    *,
    photo: Optional[bytes] = None,
    token: Optional[str] = None,
    source: str = "camera",
    channel: str = "web",
    accuracy: Optional[float] = 8.0,
    phone: Optional[str] = "+919000000001",
    name: str = "Demo Citizen",
    mock_location: bool = False,
) -> dict[str, Any]:
    data = {
        "description": description,
        "latitude": str(lat),
        "longitude": str(lon),
        "channel": channel,
        "declared_source": source,
        "mock_location": str(mock_location).lower(),
    }
    if token:
        data["capture_token"] = token
    if accuracy is not None:
        data["gps_accuracy_meters"] = str(accuracy)
    if phone:
        data["reporter_phone"] = phone
        data["reporter_name"] = name

    files = {"photo": ("evidence.jpg", photo, "image/jpeg")} if photo else None
    response = client.post(f"{BASE}/api/complaints", data=data, files=files)
    if response.status_code >= 400:
        print(f"{FAIL}HTTP {response.status_code}: {response.text[:300]}{END}")
        response.raise_for_status()
    return response.json()


def show(result: dict[str, Any], label: str) -> None:
    accepted = result["accepted"]
    colour = OK if accepted else FAIL
    verdict = "ACCEPTED" if accepted else "REJECTED"
    if accepted and result.get("needs_human_review"):
        colour, verdict = WARN, "HELD FOR REVIEW"

    print(f"  {label}")
    print(
        f"    {colour}{verdict}{END}  {result['reference']}  "
        f"status={result['status']}  "
        f"authenticity={result['authenticity_score']:.2f} "
        f"({result['authenticity_outcome']})"
    )
    if result.get("category"):
        print(
            f"    {DIM}category={result['category']} priority={result['priority']} "
            f"ward={result['ward_name']} nearby={result['nearby_count']} "
            f"hotspot={result['is_hotspot']}{END}"
        )
    if result.get("external_ticket_id"):
        print(
            f"    {DIM}dept={result['department_code']} "
            f"ticket={result['external_ticket_id']} "
            f"sla_due={result.get('sla_due_at')}{END}"
        )
    if result.get("rejection_reason"):
        print(f"    {FAIL}reason: {result['rejection_reason'][:100]}{END}")
    warn_signals = [
        s for s in result.get("evidence_signals", []) if s["severity"] in {"warn", "fail"}
    ]
    for signal in warn_signals:
        print(f"    {WARN}- {signal['code']} ({signal['delta']:+.2f}){END} {signal['detail'][:72]}")
    if result.get("pipeline_trace"):
        print(f"    {DIM}trace: {' -> '.join(result['pipeline_trace'])}{END}")


def reset_database() -> None:
    import subprocess

    step("Rebuilding database")
    subprocess.run(
        ["docker", "exec", "barathseva-db", "psql", "-U", "barathseva", "-d",
         "barathseva", "-c", "DROP SEQUENCE IF EXISTS complaint_reference_seq;"],
        capture_output=True,
    )
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "init_db.py"), "--drop"],
        capture_output=True,
        text=True,
    )
    print(result.stdout.strip().splitlines()[-1] if result.stdout else result.stderr[-200:])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="rebuild the database first")
    args = parser.parse_args()

    if args.reset:
        reset_database()

    client = httpx.Client(timeout=TIMEOUT)
    try:
        client.get(f"{BASE}/health").raise_for_status()
    except Exception as exc:
        print(f"{FAIL}API not reachable at {BASE}: {exc}{END}")
        print("Start it with: uvicorn app.main:app --reload")
        return 1

    header("BarathSeva AI — end-to-end lifecycle")

    # ---------------------------------------------------------------- happy path
    step("1. Clean in-app capture (pothole, Koramangala)")
    token = capture_token(client, *KORAMANGALA)
    first = submit(
        client,
        "Large pothole near Koramangala 5th Block, very deep, a two wheeler "
        "already skidded here yesterday",
        *KORAMANGALA,
        photo=make_photo(*KORAMANGALA, seed=101),
        token=token,
    )
    show(first, "camera capture + valid token + EXIF time/GPS match")
    primary_reference = first["reference"]

    # ------------------------------------------------------------- corroboration
    step("2. Four more reports at the same spot (corroboration -> hotspot)")
    for index, (lat, lon) in enumerate(KORAMANGALA_NEAR, start=2):
        token = capture_token(client, lat, lon, device=f"demo-device-{index}")
        result = submit(
            client,
            f"Same pothole on the 5th Block service road, getting worse "
            f"(report {index})",
            lat,
            lon,
            photo=make_photo(lat, lon, seed=100 + index * 7),
            token=token,
            phone=f"+91900000{1000 + index}",
            name=f"Citizen {index}",
        )
        show(result, f"corroborating report {index}")

    # ------------------------------------------------------------ other routings
    step("3. Department routing across all three mock agencies")
    token = capture_token(client, *INDIRANAGAR)
    show(
        submit(
            client,
            "Water gushing from a burst pipeline on 100 ft road, whole street "
            "flooded since morning",
            *INDIRANAGAR,
            photo=make_photo(*INDIRANAGAR, seed=301),
            token=token,
            phone="+919000002001",
            name="Indiranagar Citizen",
        ),
        "burst pipeline -> BWSSB",
    )
    token = capture_token(client, *JAYANAGAR)
    show(
        submit(
            client,
            "No electricity in the entire area since 6 hours, transformer near "
            "the park is sparking, very dangerous",
            *JAYANAGAR,
            photo=make_photo(*JAYANAGAR, seed=302),
            token=token,
            phone="+919000002002",
            name="Jayanagar Citizen",
        ),
        "power outage -> BESCOM",
    )
    token = capture_token(client, *WHITEFIELD)
    show(
        submit(
            client,
            "Garbage has not been collected for a week near the main gate, "
            "stray dogs everywhere",
            *WHITEFIELD,
            photo=make_photo(*WHITEFIELD, seed=303),
            token=token,
            phone="+919000002003",
            name="Whitefield Citizen",
        ),
        "garbage -> BBMP",
    )

    # ------------------------------------------------------- evidence hard fails
    header("Evidence authenticity — rejection paths")

    step("4. Reused image (perceptual hash match against an earlier complaint)")
    token = capture_token(client, *INDIRANAGAR)
    show(
        submit(
            client,
            "Huge pothole here, please fix urgently",
            *INDIRANAGAR,
            photo=make_photo(*KORAMANGALA, seed=101),  # same scene as complaint 1
            token=token,
            phone="+919000003001",
            name="Reuse Tester",
        ),
        "same photo resubmitted at a different location",
    )

    step("5. Coordinates outside the serviced city")
    token = capture_token(client, *MUMBAI)
    show(
        submit(
            client,
            "Big pothole on the highway near the junction",
            *MUMBAI,
            photo=make_photo(*MUMBAI, seed=401),
            token=token,
            phone="+919000003002",
            name="Out of Area",
        ),
        "Mumbai coordinates against a Bengaluru deployment",
    )

    step("6. Camera capture claimed without a server-bound token")
    show(
        submit(
            client,
            "Pothole near the bus stop, very deep",
            *KORAMANGALA,
            photo=make_photo(*KORAMANGALA, seed=402),
            token=None,
            source="camera",
            phone="+919000003003",
            name="No Token",
        ),
        "declared_source=camera with no capture token",
    )

    step("7. Replayed capture token")
    token = capture_token(client, *JAYANAGAR)
    submit(
        client,
        "Streetlight not working on the 4th cross, very dark at night",
        *JAYANAGAR,
        photo=make_photo(*JAYANAGAR, seed=403),
        token=token,
        phone="+919000003004",
        name="Replay Tester",
    )
    show(
        submit(
            client,
            "Another streetlight not working on the same road",
            *JAYANAGAR,
            photo=make_photo(*JAYANAGAR, seed=404),
            token=token,  # already consumed
            phone="+919000003004",
            name="Replay Tester",
        ),
        "second upload with an already-consumed token",
    )

    # ------------------------------------------------------------ degraded paths
    header("Evidence authenticity — degraded and held paths")

    step("8. Stale, edited gallery photo with a poor GPS fix")
    show(
        submit(
            client,
            "Drainage is blocked and water is logged on the road",
            *WHITEFIELD,
            photo=make_photo(
                *WHITEFIELD,
                when=datetime.now(timezone.utc) - timedelta(days=3),
                seed=501,
                software="Adobe Photoshop 25.0",
            ),
            token=None,
            source="gallery",
            accuracy=320.0,
            phone="+919000004001",
            name="Gallery Citizen",
        ),
        "3-day-old photo + editing tag + 320 m GPS accuracy",
    )

    step("9. Mock-location spoofing")
    token = capture_token(client, *INDIRANAGAR)
    show(
        submit(
            client,
            "Sewage overflowing from the manhole near the shops, foul smell",
            *INDIRANAGAR,
            photo=make_photo(*INDIRANAGAR, seed=502),
            token=token,
            mock_location=True,
            phone="+919000004002",
            name="Spoof Tester",
        ),
        "device reports a mock location provider",
    )

    step("10. Non-civic noise (verifier rejection, not an evidence failure)")
    token = capture_token(client, *KORAMANGALA)
    show(
        submit(
            client,
            "hello testing this app",
            *KORAMANGALA,
            photo=make_photo(*KORAMANGALA, seed=503),
            token=token,
            phone="+919000004003",
            name="Noise Tester",
        ),
        "greeting with no civic content",
    )

    # ------------------------------------------------------------------ SLA cycle
    header("SLA monitoring, escalation and amplification")

    step("11. Time travel: backdate the first complaint past its deadline")
    from sqlalchemy import text as sql_text

    from app.db import SessionLocal

    with SessionLocal() as db:
        db.execute(
            sql_text(
                "UPDATE complaints SET sla_due_at = now() - interval '5 hours', "
                "sla_escalate_at = now() - interval '9 hours' "
                "WHERE reference = :ref"
            ),
            {"ref": primary_reference},
        )
        db.commit()
    print(f"  {DIM}moved {primary_reference} 5 hours past its SLA deadline{END}")

    step("12. Run the SLA sweep (the Celery beat task, triggered on demand)")
    sweep = client.post(f"{BASE}/api/admin/sla/sweep").json()
    print(f"  checked={sweep['checked']}")
    print(f"  {FAIL}newly breached: {sweep['newly_breached']}{END}")
    print(f"  {WARN}newly escalated: {sweep['newly_escalated']}{END}")

    step("13. Generated accountability content")
    for post in client.get(f"{BASE}/api/admin/social-posts").json()[:4]:
        print(f"  [{post['kind']}] {post['complaint_reference']}: {post['content'][:110]}")
        print(f"    {DIM}{post['eligibility_reason'][:110]}{END}")

    # --------------------------------------------------------------- analytics
    header("Analytics and the command center")

    step("14. Hotspot clusters (PostGIS ST_ClusterDBSCAN)")
    for spot in client.get(f"{BASE}/api/admin/hotspots").json():
        print(
            f"  cluster {spot['cluster_id']}: {spot['complaint_count']} complaints "
            f"in {spot['ward_name']} at ({spot['latitude']:.5f}, "
            f"{spot['longitude']:.5f}) categories={spot['categories']} "
            f"open={spot['open_count']} breached={spot['breached_count']}"
        )

    step("15. Platform statistics")
    stats = client.get(f"{BASE}/api/admin/stats").json()
    print(f"  total={stats['total']} open={stats['open_count']} "
          f"resolved={stats['resolved_count']} rejected={stats['rejected_count']}")
    print(f"  breached={stats['breached_count']} "
          f"pending_review={stats['pending_review_count']} "
          f"hotspot={stats['hotspot_count']}")
    print(f"  by_status={stats['by_status']}")
    print(f"  by_department={stats['by_department']}")
    print(f"  ai_provider={stats['ai_provider']} "
          f"real_model={stats['ai_is_real_model']}")

    # --------------------------------------------------------------- resolution
    header("Resolution")

    step(f"16. Resolve {primary_reference}")
    resolved = client.post(
        f"{BASE}/api/admin/complaints/{primary_reference}/resolve",
        json={
            "resolution_note": "Pothole filled with hot-mix asphalt and the "
            "surface levelled by the BBMP ward engineering team.",
            "field_outcome": "GENUINE_FIXED",
        },
    ).json()
    print(f"  status={resolved['status']} resolved_at={resolved['resolved_at']}")
    print(f"  audit chain intact: {resolved['audit_chain_intact']}")
    print(f"  reporter trust now: {resolved['reporter_trust']}")
    print(f"\n  {BOLD}Citizen message:{END}\n  {resolved['resolution_message']}")

    step(f"17. Citizen tracking view for {primary_reference}")
    tracked = client.get(f"{BASE}/api/complaints/{primary_reference}").json()
    for field in [
        "received", "verified", "status", "category", "priority", "ward_name",
        "department_code", "external_ticket_id", "sla_due_at", "sla_breached",
        "escalation_level", "is_hotspot", "nearby_count", "resolved",
        "authenticity_score", "authenticity_outcome",
    ]:
        print(f"    {field:22} {tracked.get(field)}")

    step(f"18. Full audit trail for {primary_reference}")
    for event in tracked["events"]:
        print(f"    {event['event_type']:17} [{event['actor']:17}] "
              f"{(event['message'] or '')[:52]}")

    step("19. Agent runs recorded for the primary complaint")
    detail = client.get(f"{BASE}/api/admin/complaints/{primary_reference}").json()
    for run in detail["agent_runs"]:
        kind = "AI " if run["is_ai"] else "det"
        conf = f"{run['confidence']:.2f}" if run["confidence"] is not None else "  - "
        print(f"    {run['agent_name']:17} {run['status']:7} {kind} {conf} "
              f"{run['latency_ms'] or 0:4}ms")

    header("Demo complete")
    print(f"  Every stage above ran against PostGIS, Redis and the real HTTP API.")
    print(f"  AI provider: {DIM}{stats['ai_provider']} "
          f"(real model: {stats['ai_is_real_model']}){END}")
    print(f"  Government APIs: {DIM}mock BBMP / BWSSB / BESCOM{END}")
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
