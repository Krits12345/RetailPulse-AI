"""
Visitor session management and Re-ID.

Re-ID strategy:
  - Each YOLOv8/ByteTrack track_id gets a visitor_id on first appearance.
  - When a visitor EXITs, their track_id → visitor_id mapping is stored in a
    'departed' cache with a 10-minute expiry window.
  - If the same (or similar) track re-enters within the window, it reuses the
    existing visitor_id and emits REENTRY instead of ENTRY.

Staff detection:
  - Heuristic: extract the torso region of the bounding box and compute the
    dominant HSV hue. A narrow hue range (configurable) flags a uniform.
  - Works for simple uniform colours; production would use a trained classifier.

Queue depth:
  - Maintained as a counter: incremented on BILLING_QUEUE_JOIN, decremented
    on BILLING_QUEUE_ABANDON or when a visitor exits the billing zone.
"""
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional
import numpy as np

from pipeline.emit import Event

REENTRY_WINDOW_MINUTES = 10
STAFF_HUE_CENTER = 210       # blue-ish uniform hue (HSV degrees)
STAFF_HUE_TOLERANCE = 30     # ±30 degrees
DWELL_EMIT_INTERVAL_MS = 30_000  # emit ZONE_DWELL every 30 seconds


class VisitorTracker:
    def __init__(self, camera_id: str):
        self.camera_id = camera_id

        # track_id → visitor_id (active tracks)
        self._active: dict[int, str] = {}
        # visitor_id → exit_time (departed visitors eligible for REENTRY)
        self._departed: dict[str, datetime] = {}
        # visitor_id → last zone
        self._last_zone: dict[str, Optional[str]] = {}
        # visitor_id → time entered current zone
        self._zone_enter_time: dict[str, Optional[datetime]] = {}
        # visitor_id → last ZONE_DWELL emit time
        self._last_dwell_emit: dict[str, Optional[datetime]] = {}
        # visitor_id → session event count
        self._session_seq: dict[str, int] = {}

        # Billing queue state
        self._queue_depth: int = 0

    # ── Public API ────────────────────────────────────────────────────────────

    def get_visitor_id(self, track_id: int, timestamp: datetime) -> tuple[str, bool]:
        """
        Returns (visitor_id, is_reentry).
        Reuses a departed visitor_id if a recently-exited visitor returns.
        """
        if track_id in self._active:
            return self._active[track_id], False

        # Check if any recently departed visitor matches (same entry direction = likely same person)
        # In production this would use appearance embeddings; here we use a time-based heuristic.
        reentry_candidate = self._find_reentry_candidate(timestamp)
        if reentry_candidate:
            self._active[track_id] = reentry_candidate
            del self._departed[reentry_candidate]
            return reentry_candidate, True

        visitor_id = f"VIS_{hashlib.md5(f'{track_id}{timestamp.isoformat()}'.encode()).hexdigest()[:6]}"
        self._active[track_id] = visitor_id
        self._session_seq[visitor_id] = 0
        return visitor_id, False

    def update(
        self,
        track_id: int,
        visitor_id: str,
        is_reentry: bool,
        zone: Optional[str],
        direction: Optional[str],
        confidence: float,
        is_staff: bool,
        timestamp: datetime,
        store_id: str,
        sku_zone: Optional[str],
    ) -> list[Event]:
        """
        Process one frame's worth of tracking data for a single person.
        Returns a list of events to emit (may be empty for most frames).
        """
        events: list[Event] = []
        seq = self._session_seq.get(visitor_id, 0)

        def make_event(**kwargs) -> Event:
            nonlocal seq
            seq += 1
            self._session_seq[visitor_id] = seq
            return Event(
                store_id=store_id,
                camera_id=self.camera_id,
                visitor_id=visitor_id,
                timestamp=timestamp,
                is_staff=is_staff,
                confidence=confidence,
                session_seq=seq,
                **kwargs,
            )

        # ── Direction-based entry/exit (entry camera only) ─────────────────
        if direction == "ENTRY":
            if is_reentry:
                events.append(make_event(event_type="REENTRY", zone_id=None))
            else:
                events.append(make_event(event_type="ENTRY", zone_id=None))

        elif direction == "EXIT":
            events.append(make_event(event_type="EXIT", zone_id=None))
            if track_id in self._active:
                self._departed[visitor_id] = timestamp
                del self._active[track_id]
            return events

        # ── Zone transitions ───────────────────────────────────────────────
        prev_zone = self._last_zone.get(visitor_id)

        if zone != prev_zone:
            if prev_zone and prev_zone != "ENTRY":
                dwell_ms = self._compute_dwell_ms(visitor_id, timestamp)
                events.append(make_event(event_type="ZONE_EXIT", zone_id=prev_zone, dwell_ms=dwell_ms, sku_zone=sku_zone))
                if prev_zone == "BILLING":
                    self._queue_depth = max(0, self._queue_depth - 1)

            if zone and zone != "ENTRY":
                events.append(make_event(event_type="ZONE_ENTER", zone_id=zone, sku_zone=sku_zone))
                if zone == "BILLING":
                    if self._queue_depth > 0:
                        # Someone already at billing — this visitor is joining a queue
                        events.append(make_event(
                            event_type="BILLING_QUEUE_JOIN",
                            zone_id=zone,
                            queue_depth=self._queue_depth,
                            sku_zone=sku_zone,
                        ))
                    self._queue_depth += 1  # Always count people in billing zone

            self._last_zone[visitor_id] = zone
            self._zone_enter_time[visitor_id] = timestamp
            self._last_dwell_emit[visitor_id] = None

        # ── Periodic ZONE_DWELL emission ───────────────────────────────────
        elif zone and zone != "ENTRY":
            enter_time = self._zone_enter_time.get(visitor_id)
            last_emit = self._last_dwell_emit.get(visitor_id)
            if enter_time:
                elapsed_ms = (timestamp - enter_time).total_seconds() * 1000
                if elapsed_ms >= DWELL_EMIT_INTERVAL_MS:
                    since_last = (timestamp - last_emit).total_seconds() * 1000 if last_emit else elapsed_ms
                    if since_last >= DWELL_EMIT_INTERVAL_MS:
                        events.append(make_event(
                            event_type="ZONE_DWELL",
                            zone_id=zone,
                            dwell_ms=int(elapsed_ms),
                            sku_zone=sku_zone,
                        ))
                        self._last_dwell_emit[visitor_id] = timestamp

        return events

    def is_staff(self, frame: "np.ndarray", bbox: "np.ndarray") -> bool:
        """
        Heuristic staff detection: check if the torso region has a dominant hue
        matching the configured uniform colour.
        Requires OpenCV to be available.
        """
        try:
            import cv2
            x1, y1, x2, y2 = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
            h = y2 - y1
            torso_y1 = y1 + h // 3
            torso_y2 = y1 + (2 * h) // 3
            torso = frame[torso_y1:torso_y2, x1:x2]
            if torso.size == 0:
                return False
            hsv = cv2.cvtColor(torso, cv2.COLOR_BGR2HSV)
            hue = hsv[:, :, 0].mean() * 2  # OpenCV hue is 0-179 → scale to 0-359
            diff = abs(hue - STAFF_HUE_CENTER)
            diff = min(diff, 360 - diff)
            return diff <= STAFF_HUE_TOLERANCE
        except Exception:
            return False

    def finalize_sessions(self, store_id: str, emitter) -> None:
        """
        At end of clip: emit BILLING_QUEUE_ABANDON for visitors who were in
        the billing zone but had no POS transaction follow them.
        (Full POS correlation happens in the API; here we just emit for visitors
        still in billing queue at clip end.)
        """
        # visitors still in billing zone at end of clip
        for visitor_id, zone in self._last_zone.items():
            if zone == "BILLING":
                # These may be abandons — actual determination is done in the API
                from datetime import datetime, timezone
                emitter.emit(Event(
                    store_id=store_id,
                    camera_id=self.camera_id,
                    visitor_id=visitor_id,
                    event_type="BILLING_QUEUE_ABANDON",
                    timestamp=datetime.now(timezone.utc),
                    zone_id="BILLING",
                    confidence=0.7,
                    session_seq=self._session_seq.get(visitor_id, 1),
                ))

    # ── Private helpers ───────────────────────────────────────────────────────

    def _find_reentry_candidate(self, now: datetime) -> Optional[str]:
        """Return the most recently departed visitor within the reentry window."""
        cutoff = now - timedelta(minutes=REENTRY_WINDOW_MINUTES)
        candidates = [
            (vid, ts) for vid, ts in self._departed.items() if ts >= cutoff
        ]
        if not candidates:
            return None
        # Pick the one who left most recently
        return max(candidates, key=lambda x: x[1])[0]

    def _compute_dwell_ms(self, visitor_id: str, now: datetime) -> int:
        enter = self._zone_enter_time.get(visitor_id)
        if not enter:
            return 0
        return int((now - enter).total_seconds() * 1000)
