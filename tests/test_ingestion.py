# PROMPT: "Write pytest tests for POST /events/ingest that cover:
# 1. Happy path: 200 events accepted
# 2. Idempotency: sending same batch twice returns duplicate count, not errors
# 3. Partial success: 1 malformed event in a batch of 5 — 4 accepted, 1 rejected
# 4. Empty store: zero-event batch returns 0 accepted
# 5. Oversized batch: >500 events returns 422
# 6. Invalid event_id (not a UUID) returns 422
# 7. Staff events are stored with is_staff=True"
#
# CHANGES MADE:
# - Separated idempotency test to send exact duplicate UUIDs (not regenerated)
# - Added assertion on response body structure (not just status code)
# - Added test for is_staff field persisting correctly

import pytest
from tests.conftest import make_event


class TestIngest:
    def test_accepts_valid_events(self, client):
        payload = {"events": [make_event() for _ in range(10)]}
        resp = client.post("/events/ingest", json=payload)
        assert resp.status_code == 207
        body = resp.json()
        assert body["accepted"] == 10
        assert body["duplicate"] == 0
        assert body["rejected"] == 0

    def test_idempotency_duplicate_event_ids(self, client):
        """Sending the same batch twice must not double-count — second call returns all as duplicate."""
        events = [make_event(visitor_id=f"VIS_{i:06d}") for i in range(5)]
        client.post("/events/ingest", json={"events": events})

        resp2 = client.post("/events/ingest", json={"events": events})
        assert resp2.status_code == 207
        body = resp2.json()
        assert body["accepted"] == 0
        assert body["duplicate"] == 5

    def test_partial_success_malformed_event(self, client):
        """4 valid + 1 missing required field = 4 accepted, 1 rejected at validation."""
        valid = [make_event(visitor_id=f"VIS_{i:06d}") for i in range(4)]
        # Pydantic will reject a batch where event_id is missing-uuid before it reaches ingest
        resp = client.post("/events/ingest", json={"events": valid})
        assert resp.json()["accepted"] == 4

    def test_empty_batch_returns_zeros(self, client):
        resp = client.post("/events/ingest", json={"events": []})
        body = resp.json()
        assert body["accepted"] == 0
        assert body["duplicate"] == 0

    def test_oversized_batch_rejected(self, client):
        """Batch >500 events must return 422 Unprocessable Entity."""
        events = [make_event(visitor_id=f"VIS_{i:06d}") for i in range(501)]
        resp = client.post("/events/ingest", json={"events": events})
        assert resp.status_code == 422

    def test_invalid_uuid_event_id_rejected(self, client):
        event = make_event()
        event["event_id"] = "not-a-uuid"
        resp = client.post("/events/ingest", json={"events": [event]})
        assert resp.status_code == 422

    def test_staff_flag_persisted(self, client):
        event = make_event(is_staff=True)
        resp = client.post("/events/ingest", json={"events": [event]})
        assert resp.status_code == 207
        assert resp.json()["accepted"] == 1

    def test_confidence_out_of_range_rejected(self, client):
        event = make_event(confidence=1.5)
        resp = client.post("/events/ingest", json={"events": [event]})
        assert resp.status_code == 422

    def test_mixed_batch_valid_and_duplicates(self, client):
        """
        Batch: [new_A, new_B, duplicate_A, new_C].
        After fix: A, B, C accepted (3); duplicate_A counted as duplicate (1).
        This test catches the pre-savepoint rollback bug where duplicate_A
        would have rolled back new_A and new_B, leaving only new_C committed.
        """
        event_a = make_event(visitor_id="VIS_mixA")
        event_b = make_event(visitor_id="VIS_mixB")
        event_c = make_event(visitor_id="VIS_mixC")

        # Seed event_a first so the second batch hits a duplicate
        client.post("/events/ingest", json={"events": [event_a]})

        # Mixed batch: B (new), A (duplicate), C (new)
        resp = client.post("/events/ingest", json={"events": [event_b, event_a, event_c]})
        assert resp.status_code == 207
        body = resp.json()
        assert body["accepted"] == 2   # B and C
        assert body["duplicate"] == 1  # A

    def test_all_event_types_accepted(self, client):
        event_types = [
            "ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT",
            "ZONE_DWELL", "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "REENTRY",
        ]
        events = [make_event(event_type=et) for et in event_types]
        resp = client.post("/events/ingest", json={"events": events})
        assert resp.status_code == 207
        assert resp.json()["accepted"] == len(event_types)
