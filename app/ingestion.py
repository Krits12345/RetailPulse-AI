"""
POST /events/ingest — accepts batches up to 500 events.
Idempotent by event_id (duplicate → counted, not rejected).
Partial success: malformed events are collected in errors list.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from pydantic import ValidationError

from app.database import get_db, StoreEvent
from app.models import EventBatch, EventIn, IngestResult
from app.logging_config import get_logger

router = APIRouter()
logger = get_logger(__name__)


def _to_orm(event: EventIn) -> StoreEvent:
    return StoreEvent(
        event_id=event.event_id,
        store_id=event.store_id,
        camera_id=event.camera_id,
        visitor_id=event.visitor_id,
        event_type=event.event_type,
        timestamp=event.timestamp.replace(tzinfo=None),
        zone_id=event.zone_id,
        dwell_ms=event.dwell_ms,
        is_staff=event.is_staff,
        confidence=event.confidence,
        queue_depth=event.metadata.queue_depth,
        sku_zone=event.metadata.sku_zone,
        session_seq=event.metadata.session_seq,
    )


@router.post("/events/ingest", response_model=IngestResult, status_code=207)
def ingest_events(batch: EventBatch, db: Session = Depends(get_db)):
    """
    Accepts up to 500 events per call. Returns counts of accepted/duplicate/rejected.
    Status 207 (Multi-Status) because partial success is possible.
    """
    accepted = duplicate = rejected = 0
    errors: list[dict] = []

    for raw in batch.events:
        # Savepoint per event so a duplicate doesn't roll back previously-accepted events
        try:
            sp = db.begin_nested()
            db.add(_to_orm(raw))
            db.flush()
            sp.commit()
            accepted += 1
        except IntegrityError:
            sp.rollback()
            duplicate += 1
        except Exception as exc:
            sp.rollback()
            rejected += 1
            errors.append({"event_id": getattr(raw, "event_id", "unknown"), "error": str(exc)})

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("ingest_commit_failed", extra={"error": str(exc)})
        raise

    logger.info(
        "ingest_complete",
        extra={"accepted": accepted, "duplicate": duplicate, "rejected": rejected, "event_count": len(batch.events)},
    )
    return IngestResult(accepted=accepted, duplicate=duplicate, rejected=rejected, errors=errors)
