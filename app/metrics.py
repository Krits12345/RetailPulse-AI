"""
GET /stores/{store_id}/metrics
Real-time store metrics computed from ingested events.
Staff sessions (is_staff=true) are excluded from all customer metrics.
"""
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, distinct

from app.database import get_db, StoreEvent, POSTransaction
from app.models import StoreMetrics, ZoneDwell
from app.logging_config import get_logger

router = APIRouter()
logger = get_logger(__name__)

CONVERSION_WINDOW_MINUTES = 5


@router.get("/stores/{store_id}/metrics", response_model=StoreMetrics)
def get_metrics(store_id: str, db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    base = (
        db.query(StoreEvent)
        .filter(
            StoreEvent.store_id == store_id,
            StoreEvent.timestamp >= today_start,
            StoreEvent.timestamp <= now,
            StoreEvent.is_staff == False,
        )
    )

    # Unique visitors today (by ENTRY events; REENTRY events share same visitor_id)
    unique_visitors = (
        base.filter(StoreEvent.event_type.in_(["ENTRY", "REENTRY"]))
        .with_entities(distinct(StoreEvent.visitor_id))
        .count()
    )

    # Average dwell per zone
    dwell_rows = (
        base.filter(StoreEvent.event_type == "ZONE_DWELL", StoreEvent.zone_id != None)
        .with_entities(
            StoreEvent.zone_id,
            func.avg(StoreEvent.dwell_ms).label("avg_dwell"),
            func.count(StoreEvent.id).label("visit_count"),
        )
        .group_by(StoreEvent.zone_id)
        .all()
    )
    avg_dwell_per_zone = [
        ZoneDwell(zone_id=r.zone_id, avg_dwell_ms=round(r.avg_dwell, 2), visit_count=r.visit_count)
        for r in dwell_rows
    ]

    # Conversion rate: visitor in BILLING zone within 5 min before a POS transaction
    conversion_rate = _compute_conversion_rate(db, store_id, today_start, now)

    # Current queue depth (live: join events minus exits/purchases)
    current_queue_depth = _current_queue_depth(db, store_id)

    # Abandonment rate
    total_billing = (
        base.filter(StoreEvent.event_type == "BILLING_QUEUE_JOIN").count()
    )
    abandoned = (
        base.filter(StoreEvent.event_type == "BILLING_QUEUE_ABANDON").count()
    )
    abandonment_rate = round(abandoned / total_billing, 4) if total_billing else 0.0

    return StoreMetrics(
        store_id=store_id,
        as_of=now,
        unique_visitors=unique_visitors,
        conversion_rate=conversion_rate,
        avg_dwell_per_zone=avg_dwell_per_zone,
        current_queue_depth=current_queue_depth,
        abandonment_rate=abandonment_rate,
    )


def _compute_conversion_rate(
    db: Session, store_id: str, since: datetime, until: datetime
) -> float:
    """
    A visitor session is 'converted' if that visitor_id had a ZONE_ENTER/ZONE_DWELL
    event in the BILLING zone within 5 minutes before any POS transaction timestamp.
    Returns converted_sessions / total_unique_sessions.
    """
    pos_txns = (
        db.query(POSTransaction)
        .filter(
            POSTransaction.store_id == store_id,
            POSTransaction.timestamp >= since,
            POSTransaction.timestamp <= until,
        )
        .all()
    )

    unique_visitors = (
        db.query(distinct(StoreEvent.visitor_id))
        .filter(
            StoreEvent.store_id == store_id,
            StoreEvent.timestamp >= since,
            StoreEvent.is_staff == False,
            StoreEvent.event_type.in_(["ENTRY", "REENTRY"]),
        )
        .count()
    )

    if not unique_visitors:
        return 0.0

    converted_visitors: set[str] = set()
    window = timedelta(minutes=CONVERSION_WINDOW_MINUTES)

    for txn in pos_txns:
        window_start = txn.timestamp - window
        billing_visitors = (
            db.query(distinct(StoreEvent.visitor_id))
            .filter(
                StoreEvent.store_id == store_id,
                StoreEvent.zone_id == "BILLING",
                StoreEvent.is_staff == False,
                StoreEvent.timestamp >= window_start,
                StoreEvent.timestamp <= txn.timestamp,
            )
            .all()
        )
        for (vid,) in billing_visitors:
            converted_visitors.add(vid)

    return round(len(converted_visitors) / unique_visitors, 4)


def _current_queue_depth(db: Session, store_id: str) -> int:
    """
    Rough live queue estimate: last recorded queue_depth in BILLING_QUEUE_JOIN events
    within the past 30 minutes.
    """
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=30)
    row = (
        db.query(StoreEvent.queue_depth)
        .filter(
            StoreEvent.store_id == store_id,
            StoreEvent.event_type == "BILLING_QUEUE_JOIN",
            StoreEvent.timestamp >= cutoff,
            StoreEvent.queue_depth != None,
        )
        .order_by(StoreEvent.timestamp.desc())
        .first()
    )
    return row[0] if row and row[0] is not None else 0
