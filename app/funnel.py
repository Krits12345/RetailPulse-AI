"""
GET /stores/{store_id}/funnel
Conversion funnel: Entry → Zone Visit → Billing Queue → Purchase
Session is the unit — re-entries do NOT create a new session for the same visitor_id.
"""
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import distinct

from app.database import get_db, StoreEvent, POSTransaction
from app.models import FunnelResponse, FunnelStage

router = APIRouter()


@router.get("/stores/{store_id}/funnel", response_model=FunnelResponse)
def get_funnel(store_id: str, db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Stage 1: unique customer sessions entered today
    # A session = unique visitor_id that fired ENTRY (REENTRY counts as same visitor)
    entered = set(
        vid
        for (vid,) in db.query(distinct(StoreEvent.visitor_id))
        .filter(
            StoreEvent.store_id == store_id,
            StoreEvent.timestamp >= today_start,
            StoreEvent.timestamp <= now,
            StoreEvent.is_staff == False,
            StoreEvent.event_type.in_(["ENTRY", "REENTRY"]),
        )
        .all()
    )

    # Stage 2: visitors who entered at least one named zone (not ENTRY zone)
    visited_zone = set(
        vid
        for (vid,) in db.query(distinct(StoreEvent.visitor_id))
        .filter(
            StoreEvent.store_id == store_id,
            StoreEvent.timestamp >= today_start,
            StoreEvent.timestamp <= now,
            StoreEvent.is_staff == False,
            StoreEvent.event_type == "ZONE_ENTER",
            StoreEvent.zone_id != None,
            StoreEvent.zone_id != "ENTRY",
        )
        .all()
        if vid in entered
    )

    # Stage 3: visitors who joined billing queue
    reached_billing = set(
        vid
        for (vid,) in db.query(distinct(StoreEvent.visitor_id))
        .filter(
            StoreEvent.store_id == store_id,
            StoreEvent.timestamp >= today_start,
            StoreEvent.timestamp <= now,
            StoreEvent.is_staff == False,
            StoreEvent.event_type.in_(["BILLING_QUEUE_JOIN", "ZONE_ENTER"]),
            StoreEvent.zone_id == "BILLING",
        )
        .all()
        if vid in entered
    )

    # Stage 4: visitors who completed a purchase (billing zone within 5 min of POS)
    purchased = _get_purchased_visitors(db, store_id, today_start, now, entered)

    total = len(entered)

    def drop(prev: int, curr: int) -> float:
        if prev == 0:
            return 0.0
        return round((prev - curr) / prev * 100, 2)

    stages = [
        FunnelStage(stage="Entry", count=total, dropoff_pct=0.0),
        FunnelStage(stage="Zone Visit", count=len(visited_zone), dropoff_pct=drop(total, len(visited_zone))),
        FunnelStage(stage="Billing Queue", count=len(reached_billing), dropoff_pct=drop(len(visited_zone), len(reached_billing))),
        FunnelStage(stage="Purchase", count=len(purchased), dropoff_pct=drop(len(reached_billing), len(purchased))),
    ]

    return FunnelResponse(
        store_id=store_id,
        as_of=now,
        stages=stages,
        total_sessions=total,
    )


def _get_purchased_visitors(
    db: Session, store_id: str, since: datetime, until: datetime, visitor_pool: set
) -> set:
    """Visitors who were in BILLING within 5 min before a POS transaction."""
    from datetime import timedelta

    pos_txns = (
        db.query(POSTransaction)
        .filter(
            POSTransaction.store_id == store_id,
            POSTransaction.timestamp >= since,
            POSTransaction.timestamp <= until,
        )
        .all()
    )

    purchased: set[str] = set()
    window = timedelta(minutes=5)

    for txn in pos_txns:
        billing_visitors = (
            db.query(StoreEvent.visitor_id)
            .filter(
                StoreEvent.store_id == store_id,
                StoreEvent.zone_id == "BILLING",
                StoreEvent.is_staff == False,
                StoreEvent.timestamp >= txn.timestamp - window,
                StoreEvent.timestamp <= txn.timestamp,
            )
            .all()
        )
        for (vid,) in billing_visitors:
            if vid in visitor_pool:
                purchased.add(vid)

    return purchased
