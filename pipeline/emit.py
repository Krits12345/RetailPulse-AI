"""
Event schema construction and emission.
Events are written to a JSONL file and optionally POSTed to the API.
"""
import json
import uuid
import requests
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Event:
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: str
    timestamp: datetime
    zone_id: Optional[str] = None
    dwell_ms: int = 0
    is_staff: bool = False
    confidence: float = 1.0
    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: int = 0

    def to_api_payload(self) -> dict:
        return {
            "event_id": str(uuid.uuid4()),
            "store_id": self.store_id,
            "camera_id": self.camera_id,
            "visitor_id": self.visitor_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "zone_id": self.zone_id,
            "dwell_ms": self.dwell_ms,
            "is_staff": self.is_staff,
            "confidence": round(self.confidence, 4),
            "metadata": {
                "queue_depth": self.queue_depth,
                "sku_zone": self.sku_zone,
                "session_seq": self.session_seq,
            },
        }


class EventEmitter:
    def __init__(self, output_path: str, api_url: Optional[str] = None, batch_size: int = 50):
        self.output_path = Path(output_path)
        self.api_url = api_url
        self.batch_size = batch_size
        self._buffer: list[dict] = []
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: Event) -> None:
        payload = event.to_api_payload()
        with open(self.output_path, "a") as f:
            f.write(json.dumps(payload) + "\n")

        self._buffer.append(payload)
        if len(self._buffer) >= self.batch_size:
            self._flush_to_api()

    def _flush_to_api(self) -> None:
        if not self.api_url or not self._buffer:
            return
        try:
            resp = requests.post(
                f"{self.api_url}/events/ingest",
                json={"events": self._buffer},
                timeout=10,
            )
            resp.raise_for_status()
        except Exception as exc:
            print(f"[EventEmitter] API flush failed: {exc}")
        finally:
            self._buffer.clear()

    def flush(self) -> None:
        """Call at end of clip processing to drain the buffer."""
        self._flush_to_api()
