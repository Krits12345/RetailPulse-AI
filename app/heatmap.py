"""
GET /stores/{store_id}/heatmap
Zone visit frequency + avg dwell, normalised 0–100.
Includes data_confidence=False when fewer than 20 sessions in window.
"""
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct

from app.database import get_db, StoreEvent
from app.models import HeatmapResponse, ZoneHeatCell

router = APIRouter()

MIN_SESSIONS_FOR_CONFIDENCE = 20


@router.get("/stores/{store_id}/heatmap", response_model=HeatmapResponse)
def get_heatmap(store_id: str, db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    rows = (
        db.query(
            StoreEvent.zone_id,
            StoreEvent.sku_zone,
            func.count(StoreEvent.id).label("visit_count"),
            func.avg(StoreEvent.dwell_ms).label("avg_dwell"),
        )
        .filter(
            StoreEvent.store_id == store_id,
            StoreEvent.timestamp >= today_start,
            StoreEvent.timestamp <= now,
            StoreEvent.is_staff == False,
            StoreEvent.event_type == "ZONE_ENTER",
            StoreEvent.zone_id != None,
            StoreEvent.zone_id != "ENTRY",
        )
        .group_by(StoreEvent.zone_id, StoreEvent.sku_zone)
        .all()
    )

    if not rows:
        return HeatmapResponse(store_id=store_id, as_of=now, zones=[], data_confidence=False)

    max_visits = max(r.visit_count for r in rows) or 1

    zones = [
        ZoneHeatCell(
            zone_id=r.zone_id,
            sku_zone=r.sku_zone,
            visit_frequency=r.visit_count,
            avg_dwell_ms=round(r.avg_dwell or 0, 2),
            normalised_score=round((r.visit_count / max_visits) * 100, 2),
        )
        for r in rows
    ]

    total_sessions = (
        db.query(distinct(StoreEvent.visitor_id))
        .filter(
            StoreEvent.store_id == store_id,
            StoreEvent.timestamp >= today_start,
            StoreEvent.timestamp <= now,
            StoreEvent.is_staff == False,
            StoreEvent.event_type.in_(["ENTRY", "REENTRY"]),
        )
        .count()
    )

    return HeatmapResponse(
        store_id=store_id,
        as_of=now,
        zones=sorted(zones, key=lambda z: z.normalised_score, reverse=True),
        data_confidence=total_sessions >= MIN_SESSIONS_FOR_CONFIDENCE,
    )
