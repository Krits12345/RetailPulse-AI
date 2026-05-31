"""
GET /health
Returns service status, last event timestamp per store, and STALE_FEED warnings.
This is the first endpoint an on-call engineer checks.
"""
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db, StoreEvent
from app.models import HealthResponse, StoreHealth

router = APIRouter()

STALE_FEED_MINUTES = 10
VERSION = "1.0.0"


@router.get("/health", response_model=HealthResponse)
def get_health(db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db_status = "ok"
    overall = "healthy"
    store_healths: list[StoreHealth] = []

    try:
        store_ids = [
            sid
            for (sid,) in db.query(func.distinct(StoreEvent.store_id)).all()
        ]

        for store_id in store_ids:
            last_ts = (
                db.query(func.max(StoreEvent.timestamp))
                .filter(StoreEvent.store_id == store_id)
                .scalar()
            )

            if last_ts is None:
                store_healths.append(StoreHealth(
                    store_id=store_id,
                    last_event_at=None,
                    status="NO_DATA",
                    lag_seconds=None,
                ))
                overall = "degraded"
                continue

            lag = (now - last_ts).total_seconds()
            if lag > STALE_FEED_MINUTES * 60:
                status = "STALE_FEED"
                overall = "degraded"
            else:
                status = "OK"

            store_healths.append(StoreHealth(
                store_id=store_id,
                last_event_at=last_ts,
                status=status,
                lag_seconds=round(lag, 1),
            ))

    except Exception as exc:
        db_status = "unavailable"
        overall = "degraded"

    return HealthResponse(
        status=overall,
        version=VERSION,
        checked_at=now,
        stores=store_healths,
        db_status=db_status,
    )
