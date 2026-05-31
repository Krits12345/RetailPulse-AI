"""
GET /stores/{store_id}/anomalies
Detects three operational anomalies:
  - BILLING_QUEUE_SPIKE  : current queue depth > threshold
  - CONVERSION_DROP      : today's rate < 80% of 7-day average
  - DEAD_ZONE            : a known zone has zero visits in 30 minutes
"""
import uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct

from app.database import get_db, StoreEvent
from app.models import AnomalyResponse, Anomaly
from app.logging_config import get_logger

router = APIRouter()
logger = get_logger(__name__)

QUEUE_WARN_THRESHOLD = 5
QUEUE_CRITICAL_THRESHOLD = 10
CONVERSION_DROP_WARN = 0.80
CONVERSION_DROP_CRITICAL = 0.50
DEAD_ZONE_MINUTES = 30


@router.get("/stores/{store_id}/anomalies", response_model=AnomalyResponse)
def get_anomalies(store_id: str, db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    anomalies: list[Anomaly] = []

    anomalies.extend(_check_queue_spike(db, store_id, now))
    anomalies.extend(_check_conversion_drop(db, store_id, now))
    anomalies.extend(_check_dead_zones(db, store_id, now))

    return AnomalyResponse(store_id=store_id, as_of=now, anomalies=anomalies)


def _check_queue_spike(db: Session, store_id: str, now: datetime) -> list[Anomaly]:
    cutoff = now - timedelta(minutes=5)
    row = (
        db.query(func.max(StoreEvent.queue_depth))
        .filter(
            StoreEvent.store_id == store_id,
            StoreEvent.event_type == "BILLING_QUEUE_JOIN",
            StoreEvent.timestamp >= cutoff,
            StoreEvent.timestamp <= now,
        )
        .scalar()
    )
    depth = row or 0

    if depth >= QUEUE_CRITICAL_THRESHOLD:
        return [Anomaly(
            anomaly_id=str(uuid.uuid4()),
            anomaly_type="BILLING_QUEUE_SPIKE",
            severity="CRITICAL",
            description=f"Billing queue depth is {depth} (threshold: {QUEUE_CRITICAL_THRESHOLD})",
            suggested_action="Open additional billing counters immediately. Alert floor manager.",
            detected_at=now,
            metadata={"queue_depth": depth},
        )]
    if depth >= QUEUE_WARN_THRESHOLD:
        return [Anomaly(
            anomaly_id=str(uuid.uuid4()),
            anomaly_type="BILLING_QUEUE_SPIKE",
            severity="WARN",
            description=f"Billing queue depth is {depth} (threshold: {QUEUE_WARN_THRESHOLD})",
            suggested_action="Monitor billing queue. Consider opening a second counter.",
            detected_at=now,
            metadata={"queue_depth": depth},
        )]
    return []


def _check_conversion_drop(db: Session, store_id: str, now: datetime) -> list[Anomaly]:
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    today_rate = _conversion_rate(db, store_id, today_start, now)

    # 7-day historical average (excluding today)
    week_start = today_start - timedelta(days=7)
    hist_rate = _conversion_rate(db, store_id, week_start, today_start)

    # Not enough historical data to make a comparison
    if hist_rate is None or hist_rate == 0.0:
        return []

    ratio = today_rate / hist_rate

    if ratio < CONVERSION_DROP_CRITICAL:
        return [Anomaly(
            anomaly_id=str(uuid.uuid4()),
            anomaly_type="CONVERSION_DROP",
            severity="CRITICAL",
            description=f"Conversion rate {today_rate:.1%} is {ratio:.0%} of 7-day avg {hist_rate:.1%}",
            suggested_action="Urgent: review floor layout, check pricing, escalate to store manager.",
            detected_at=now,
            metadata={"today_rate": today_rate, "historical_avg": hist_rate},
        )]
    if ratio < CONVERSION_DROP_WARN:
        return [Anomaly(
            anomaly_id=str(uuid.uuid4()),
            anomaly_type="CONVERSION_DROP",
            severity="WARN",
            description=f"Conversion rate {today_rate:.1%} is {ratio:.0%} of 7-day avg {hist_rate:.1%}",
            suggested_action="Review promotions and billing queue wait times.",
            detected_at=now,
            metadata={"today_rate": today_rate, "historical_avg": hist_rate},
        )]
    return []


def _check_dead_zones(db: Session, store_id: str, now: datetime) -> list[Anomaly]:
    """
    Flag individual zones that have gone quiet while the rest of the store
    is still receiving traffic.

    Precondition: the store must have at least one customer event (any type)
    within the window.  If *no* events exist in the window, every zone would
    appear dead — that is a STALE_FEED condition, not a dead-zone condition,
    and is handled by the health endpoint.  Emitting DEAD_ZONE for every zone
    when the feed is globally idle creates noise that operators learn to ignore.
    """
    cutoff = now - timedelta(minutes=DEAD_ZONE_MINUTES)

    # Guard: only run the per-zone check when the store-level feed is alive.
    feed_alive = db.query(StoreEvent.id).filter(
        StoreEvent.store_id == store_id,
        StoreEvent.timestamp >= cutoff,
        StoreEvent.timestamp <= now,
        StoreEvent.is_staff == False,
    ).first()

    if not feed_alive:
        return []

    active_zones = set(
        z for (z,) in db.query(distinct(StoreEvent.zone_id))
        .filter(
            StoreEvent.store_id == store_id,
            StoreEvent.event_type == "ZONE_ENTER",
            StoreEvent.timestamp >= cutoff,
            StoreEvent.timestamp <= now,
            StoreEvent.zone_id != None,
            StoreEvent.zone_id != "ENTRY",
            StoreEvent.is_staff == False,
        )
        .all()
    )

    all_zones = set(
        z for (z,) in db.query(distinct(StoreEvent.zone_id))
        .filter(
            StoreEvent.store_id == store_id,
            StoreEvent.timestamp <= now,
            StoreEvent.zone_id != None,
            StoreEvent.zone_id != "ENTRY",
        )
        .all()
    )

    dead = all_zones - active_zones
    anomalies = []
    for zone_id in dead:
        anomalies.append(Anomaly(
            anomaly_id=str(uuid.uuid4()),
            anomaly_type="DEAD_ZONE",
            severity="INFO",
            description=f"Zone '{zone_id}' has had no customer visits in {DEAD_ZONE_MINUTES} minutes",
            suggested_action=f"Check if zone '{zone_id}' display is stocked. Consider a promotion.",
            detected_at=now,
            metadata={"zone_id": zone_id},
        ))
    return anomalies


def _conversion_rate(
    db: Session, store_id: str, since: datetime, until: datetime
) -> float:
    """Simplified conversion rate for anomaly comparison (based on event data only)."""
    from app.database import POSTransaction
    from datetime import timedelta

    unique_visitors = (
        db.query(distinct(StoreEvent.visitor_id))
        .filter(
            StoreEvent.store_id == store_id,
            StoreEvent.timestamp >= since,
            StoreEvent.timestamp < until,
            StoreEvent.is_staff == False,
            StoreEvent.event_type.in_(["ENTRY", "REENTRY"]),
        )
        .count()
    )
    if not unique_visitors:
        return 0.0

    pos_txns = (
        db.query(POSTransaction)
        .filter(
            POSTransaction.store_id == store_id,
            POSTransaction.timestamp >= since,
            POSTransaction.timestamp < until,
        )
        .all()
    )

    converted: set = set()
    window = timedelta(minutes=5)
    for txn in pos_txns:
        rows = (
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
        for (vid,) in rows:
            converted.add(vid)

    return round(len(converted) / unique_visitors, 4)
