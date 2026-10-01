"""Create the PostGIS extension, all tables, and seed reference data.

Safe to re-run: table creation uses checkfirst and seeding is idempotent.
Pass --drop to rebuild from scratch (destructive).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import Base, SessionLocal, enable_postgis, engine  # noqa: E402
from app import models  # noqa: F401,E402  (registers metadata)
from app.core.ids import ensure_sequence  # noqa: E402
from app.seed import seed_all  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--drop", action="store_true", help="drop all tables first (destructive)"
    )
    args = parser.parse_args()

    print(f"→ database: {engine.url.render_as_string(hide_password=True)}")
    enable_postgis()
    print("→ postgis extension ready")

    if args.drop:
        Base.metadata.drop_all(bind=engine)
        print("→ dropped existing tables")

    Base.metadata.create_all(bind=engine, checkfirst=True)
    print(f"→ tables ready ({len(Base.metadata.tables)})")

    with SessionLocal() as db:
        ensure_sequence(db)
        db.commit()
        created = seed_all(db)
    print("→ seeded:", ", ".join(f"{k}+{v}" for k, v in created.items()))
    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
