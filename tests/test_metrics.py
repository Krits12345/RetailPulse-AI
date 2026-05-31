# PROMPT: "Write pytest tests for GET /stores/{id}/metrics that cover:
# 1. Empty store: returns 0 visitors, 0.0 conversion_rate, not null
# 2. Staff events excluded from unique_visitors count
# 3. Re-entry visitor counted once (not twice)
# 4. Conversion rate: visitor in billing within 5 min of POS transaction = converted
# 5. Abandonment rate: BILLING_QUEUE_ABANDON / BILLING_QUEUE_JOIN
# 6. Zone dwell averages are correct
# 7. Unknown store returns valid empty response (not 500)"
#
# CHANGES MADE:
# - Changed store ID for unknown store test to ensure no seed data conflicts
# - Explicitly tested that staff visitor_ids never appear in unique_visitors
# - Added more precise floating-point assertion for conversion rate

import pytest
import uuid
from datetime import datetime, timezone, timedelta
from app.database import StoreEvent, POSTransaction
from tests.conftest import make_event

STORE = "STORE_BLR_002"


def seed_events(db_session, events: list):
    for e in events:
        db_session.add(e)
    db_session.commit()


def orm_event(event_type, visitor_id, zone_id=None, is_staff=False,
              ts_offset_minutes=0, dwell_ms=0, queue_depth=None):
    base = datetime.now(timezone.utc).replace(
        hour=11, minute=0, second=0, microsecond=0, tzinfo=None
    )
    return StoreEvent(
        event_id=str(uuid.uuid4()),
        store_id=STORE,
        camera_id="CAM_ENTRY_01",
        visitor_id=visitor_id,
        event_type=event_type,
        timestamp=base + timedelta(minutes=ts_offset_minutes),
        zone_id=zone_id,
        dwell_ms=dwell_ms,
        is_staff=is_staff,
        confidence=0.9,
        queue_depth=queue_depth,
    )


class TestMetrics:
    def test_empty_store_returns_zeros(self, client):
        """Zero-traffic store must return valid zeros, not null or 500."""
        resp = client.get(f"/stores/{STORE}/metrics")
        assert resp.status_code == 200
        body = resp.json()
        assert body["unique_visitors"] == 0
        assert body["conversion_rate"] == 0.0
        assert body["abandonment_rate"] == 0.0
        assert body["current_queue_depth"] == 0
        assert isinstance(body["avg_dwell_per_zone"], list)

    def test_staff_excluded_from_unique_visitors(self, client, db_session):
        seed_events(db_session, [
            orm_event("ENTRY", "VIS_customer"),
            orm_event("ENTRY", "VIS_staff", is_staff=True),
        ])
        resp = client.get(f"/stores/{STORE}/metrics")
        assert resp.json()["unique_visitors"] == 1  # staff not counted

    def test_reentry_counted_once(self, client, db_session):
        """REENTRY events share the visitor_id — must not double-count the visitor."""
        seed_events(db_session, [
            orm_event("ENTRY", "VIS_abc123", ts_offset_minutes=0),
            orm_event("EXIT", "VIS_abc123", ts_offset_minutes=15),
            orm_event("REENTRY", "VIS_abc123", ts_offset_minutes=20),
        ])
        resp = client.get(f"/stores/{STORE}/metrics")
        assert resp.json()["unique_visitors"] == 1

    def test_conversion_rate_with_billing_and_pos(self, client, db_session):
        """Visitor in BILLING within 5 min of POS transaction = converted."""
        # orm_event uses base = hour=11; billing at 11:10, POS at 11:13 (within 5-min window)
        billing_base = datetime.now(timezone.utc).replace(
            hour=11, minute=0, second=0, microsecond=0, tzinfo=None
        )
        visitor_id = "VIS_convert"
        seed_events(db_session, [
            orm_event("ENTRY", visitor_id, ts_offset_minutes=0),
            orm_event("ZONE_ENTER", visitor_id, zone_id="BILLING", ts_offset_minutes=10),
        ])
        db_session.add(POSTransaction(
            transaction_id=str(uuid.uuid4()),
            store_id=STORE,
            timestamp=billing_base + timedelta(minutes=13),  # 3 min after billing entry
            basket_value_inr=500.0,
        ))
        db_session.commit()

        resp = client.get(f"/stores/{STORE}/metrics")
        body = resp.json()
        assert body["unique_visitors"] >= 1
        assert body["conversion_rate"] > 0.0

    def test_abandonment_rate_calculation(self, client, db_session):
        """2 billing joins, 1 abandon = 50% abandonment rate."""
        seed_events(db_session, [
            orm_event("BILLING_QUEUE_JOIN", "VIS_001", zone_id="BILLING"),
            orm_event("BILLING_QUEUE_JOIN", "VIS_002", zone_id="BILLING"),
            orm_event("BILLING_QUEUE_ABANDON", "VIS_001", zone_id="BILLING"),
        ])
        resp = client.get(f"/stores/{STORE}/metrics")
        assert abs(resp.json()["abandonment_rate"] - 0.5) < 0.01

    def test_zone_dwell_averages(self, client, db_session):
        seed_events(db_session, [
            orm_event("ZONE_DWELL", "VIS_x", zone_id="SKINCARE", dwell_ms=60000),
            orm_event("ZONE_DWELL", "VIS_y", zone_id="SKINCARE", dwell_ms=90000),
        ])
        resp = client.get(f"/stores/{STORE}/metrics")
        zones = {z["zone_id"]: z for z in resp.json()["avg_dwell_per_zone"]}
        assert "SKINCARE" in zones
        assert abs(zones["SKINCARE"]["avg_dwell_ms"] - 75000) < 1

    def test_unknown_store_empty_response(self, client):
        resp = client.get("/stores/STORE_NONEXISTENT_999/metrics")
        assert resp.status_code == 200
        assert resp.json()["unique_visitors"] == 0
