"""
Pydantic models for API request/response validation.
All event types mirror the required output schema from the challenge spec.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional, List, Literal
from pydantic import BaseModel, Field, field_validator
import uuid

EVENT_TYPES = Literal[
    "ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT",
    "ZONE_DWELL", "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "REENTRY"
]


class EventMetadata(BaseModel):
    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: Optional[int] = None


class EventIn(BaseModel):
    event_id: str = Field(..., description="UUID-v4, globally unique")
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: EVENT_TYPES
    timestamp: datetime
    zone_id: Optional[str] = None
    dwell_ms: int = 0
    is_staff: bool = False
    confidence: float = Field(..., ge=0.0, le=1.0)
    metadata: EventMetadata = Field(default_factory=EventMetadata)

    @field_validator("event_id")
    @classmethod
    def validate_uuid(cls, v: str) -> str:
        try:
            uuid.UUID(v)
        except ValueError:
            raise ValueError("event_id must be a valid UUID")
        return v


class EventBatch(BaseModel):
    events: List[EventIn] = Field(..., max_length=500)


class IngestResult(BaseModel):
    accepted: int
    duplicate: int
    rejected: int
    errors: List[dict] = []


# ── Metrics ──────────────────────────────────────────────────────────────────

class ZoneDwell(BaseModel):
    zone_id: str
    avg_dwell_ms: float
    visit_count: int


class StoreMetrics(BaseModel):
    store_id: str
    as_of: datetime
    unique_visitors: int
    conversion_rate: float
    avg_dwell_per_zone: List[ZoneDwell]
    current_queue_depth: int
    abandonment_rate: float


# ── Funnel ────────────────────────────────────────────────────────────────────

class FunnelStage(BaseModel):
    stage: str
    count: int
    dropoff_pct: float


class FunnelResponse(BaseModel):
    store_id: str
    as_of: datetime
    stages: List[FunnelStage]
    total_sessions: int


# ── Heatmap ───────────────────────────────────────────────────────────────────

class ZoneHeatCell(BaseModel):
    zone_id: str
    sku_zone: Optional[str]
    visit_frequency: int
    avg_dwell_ms: float
    normalised_score: float = Field(..., ge=0.0, le=100.0)


class HeatmapResponse(BaseModel):
    store_id: str
    as_of: datetime
    zones: List[ZoneHeatCell]
    data_confidence: bool = Field(
        ..., description="False when fewer than 20 sessions in window"
    )


# ── Anomalies ─────────────────────────────────────────────────────────────────

SEVERITY = Literal["INFO", "WARN", "CRITICAL"]
ANOMALY_TYPES = Literal[
    "BILLING_QUEUE_SPIKE", "CONVERSION_DROP", "DEAD_ZONE", "STALE_FEED"
]


class Anomaly(BaseModel):
    anomaly_id: str
    anomaly_type: ANOMALY_TYPES
    severity: SEVERITY
    description: str
    suggested_action: str
    detected_at: datetime
    metadata: dict = {}


class AnomalyResponse(BaseModel):
    store_id: str
    as_of: datetime
    anomalies: List[Anomaly]


# ── Health ────────────────────────────────────────────────────────────────────

class StoreHealth(BaseModel):
    store_id: str
    last_event_at: Optional[datetime]
    status: Literal["OK", "STALE_FEED", "NO_DATA"]
    lag_seconds: Optional[float]


class HealthResponse(BaseModel):
    status: Literal["healthy", "degraded"]
    version: str
    checked_at: datetime
    stores: List[StoreHealth]
    db_status: Literal["ok", "unavailable"]
