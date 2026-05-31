# PROMPT: "Write pytest tests for GET /stores/{id}/funnel and GET /stores/{id}/heatmap.
# For funnel: test empty store (all zeros), test that re-entries don't double-count,
# test that Purchase count is non-zero when visitors reach BILLING within 5 min of POS.
# For heatmap: test empty store returns empty zones list, test normalised_score is 0-100,
# test data_confidence=False when fewer than 20 sessions."
#
# CHANGES MADE:
# - Added explicit assertion that Entry count == total_sessions (invariant)
# - Added test for drop-off monotonicity (each stage count <= prior stage)
# - Used consistent hour=13 base for funnel POS to avoid day-boundary issues

import pytest
import uuid
from datetime import datetime, timezone, timedelta
from app.database import StoreEvent, POSTransaction
from tests.conftest import make_event

STORE = "STORE_BLR_002"


def orm_event(event_type, visitor_id, zone_id=None, is_staff=False, ts_offset_minutes=0, dwell_ms=0):
    base = datetime.now(timezone.utc).replace(hour=13, minute=0, second=0, microsecond=0, tzinfo=None)
    return StoreEvent(
        event_id=str(uuid.uuid4()),
        store_id=STORE,
        camera_id="CAM_FLOOR_01",
        visitor_id=visitor_id,
        event_type=event_type,
        timestamp=base + timedelta(minutes=ts_offset_minutes),
        zone_id=zone_id,
        dwell_ms=dwell_ms,
        is_staff=is_staff,
        confidence=0.9,
    )


class TestFunnel:
    def test_empty_store_all_zeros(self, client):
        resp = client.get(f"/stores/{STORE}/funnel")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_sessions"] == 0
        assert all(s["count"] == 0 for s in body["stages"])

    def test_entry_count_matches_total_sessions(self, client, db_session):
        for i in range(5):
            db_session.add(orm_event("ENTRY", f"VIS_{i:04d}"))
        db_session.commit()

        resp = client.get(f"/stores/{STORE}/funnel")
        body = resp.json()
        entry_stage = next(s for s in body["stages"] if s["stage"] == "Entry")
        assert entry_stage["count"] == body["total_sessions"] == 5

    def test_reentry_does_not_double_count(self, client, db_session):
        """One visitor who re-enters should count as 1 unique session, not 2."""
        db_session.add(orm_event("ENTRY", "VIS_reentr", ts_offset_minutes=0))
        db_session.add(orm_event("EXIT", "VIS_reentr", ts_offset_minutes=10))
        db_session.add(orm_event("REENTRY", "VIS_reentr", ts_offset_minutes=15))
        db_session.commit()

        resp = client.get(f"/stores/{STORE}/funnel")
        assert resp.json()["total_sessions"] == 1

    def test_stage_counts_are_monotonically_decreasing(self, client, db_session):
        """Each funnel stage must have count <= prior stage."""
        for i in range(10):
            db_session.add(orm_event("ENTRY", f"VIS_{i:04d}", ts_offset_minutes=i))
            if i < 7:
                db_session.add(orm_event("ZONE_ENTER", f"VIS_{i:04d}", zone_id="SKINCARE", ts_offset_minutes=i + 2))
            if i < 4:
                db_session.add(orm_event("ZONE_ENTER", f"VIS_{i:04d}", zone_id="BILLING", ts_offset_minutes=i + 5))
        db_session.commit()

        resp = client.get(f"/stores/{STORE}/funnel")
        counts = [s["count"] for s in resp.json()["stages"]]
        for j in range(1, len(counts)):
            assert counts[j] <= counts[j - 1], f"Stage {j} count {counts[j]} > prior {counts[j-1]}"

    def test_purchase_stage_with_pos_transaction(self, client, db_session):
        """Visitor in BILLING within 5 min of POS = counted in Purchase stage."""
        # Use past timestamps so they fall before the handler's current "now"
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        billing_ts = now_utc - timedelta(minutes=20)
        entry_ts = billing_ts - timedelta(minutes=10)
        pos_ts = billing_ts + timedelta(minutes=3)  # 3 min after billing entry

        visitor_id = "VIS_buyer"
        db_session.add(StoreEvent(
            event_id=str(uuid.uuid4()), store_id=STORE, camera_id="CAM_ENTRY_01",
            visitor_id=visitor_id, event_type="ENTRY", timestamp=entry_ts,
            is_staff=False, confidence=0.9,
        ))
        db_session.add(StoreEvent(
            event_id=str(uuid.uuid4()), store_id=STORE, camera_id="CAM_BILLING_01",
            visitor_id=visitor_id, event_type="ZONE_ENTER", timestamp=billing_ts,
            zone_id="BILLING", is_staff=False, confidence=0.9,
        ))
        db_session.add(POSTransaction(
            transaction_id=str(uuid.uuid4()),
            store_id=STORE,
            timestamp=pos_ts,
            basket_value_inr=1500.0,
        ))
        db_session.commit()

        resp = client.get(f"/stores/{STORE}/funnel")
        purchase_stage = next(s for s in resp.json()["stages"] if s["stage"] == "Purchase")
        assert purchase_stage["count"] >= 1

    def test_staff_excluded_from_funnel(self, client, db_session):
        db_session.add(orm_event("ENTRY", "VIS_cust"))
        db_session.add(orm_event("ENTRY", "VIS_staff", is_staff=True))
        db_session.commit()

        resp = client.get(f"/stores/{STORE}/funnel")
        assert resp.json()["total_sessions"] == 1


class TestHeatmap:
    def test_empty_store_returns_empty_list(self, client):
        resp = client.get(f"/stores/{STORE}/heatmap")
        assert resp.status_code == 200
        body = resp.json()
        assert body["zones"] == []
        assert body["data_confidence"] is False

    def test_normalised_score_range(self, client, db_session):
        """All normalised_score values must be between 0 and 100."""
        for zone in ["SKINCARE", "MAKEUP", "HAIRCARE"]:
            for i in range(3):
                db_session.add(StoreEvent(
                    event_id=str(uuid.uuid4()),
                    store_id=STORE,
                    camera_id="CAM_FLOOR_01",
                    visitor_id=f"VIS_{i:04d}",
                    event_type="ZONE_ENTER",
                    timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
                    zone_id=zone,
                    is_staff=False,
                    confidence=0.9,
                ))
        db_session.commit()

        resp = client.get(f"/stores/{STORE}/heatmap")
        for zone in resp.json()["zones"]:
            assert 0.0 <= zone["normalised_score"] <= 100.0

    def test_most_visited_zone_has_score_100(self, client, db_session):
        """The zone with the most visits should have normalised_score=100."""
        base = datetime.now(timezone.utc).replace(tzinfo=None)
        for i in range(5):
            db_session.add(StoreEvent(
                event_id=str(uuid.uuid4()), store_id=STORE,
                camera_id="CAM_FLOOR_01", visitor_id=f"VIS_{i:04d}",
                event_type="ZONE_ENTER", timestamp=base, zone_id="SKINCARE",
                is_staff=False, confidence=0.9,
            ))
        for i in range(2):
            db_session.add(StoreEvent(
                event_id=str(uuid.uuid4()), store_id=STORE,
                camera_id="CAM_FLOOR_01", visitor_id=f"VIS_m{i:04d}",
                event_type="ZONE_ENTER", timestamp=base, zone_id="MAKEUP",
                is_staff=False, confidence=0.9,
            ))
        db_session.commit()

        resp = client.get(f"/stores/{STORE}/heatmap")
        zones = {z["zone_id"]: z for z in resp.json()["zones"]}
        assert zones["SKINCARE"]["normalised_score"] == 100.0
        assert zones["MAKEUP"]["normalised_score"] < 100.0

    def test_data_confidence_false_below_20_sessions(self, client, db_session):
        """data_confidence must be False when fewer than 20 unique visitors today."""
        for i in range(5):
            db_session.add(StoreEvent(
                event_id=str(uuid.uuid4()), store_id=STORE,
                camera_id="CAM_ENTRY_01", visitor_id=f"VIS_{i:04d}",
                event_type="ENTRY", timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
                zone_id=None, is_staff=False, confidence=0.9,
            ))
        db_session.commit()

        resp = client.get(f"/stores/{STORE}/heatmap")
        assert resp.json()["data_confidence"] is False
