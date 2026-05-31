"""
Synthetic event generator for testing without real CCTV footage.

Generates realistic visitor sessions for a store and either:
  - Writes events to a JSONL file, or
  - Streams them to the API in simulated real time

Usage:
  # Batch mode (all at once):
  python -m pipeline.simulate --store STORE_BLR_002 --visitors 40 --output data/events/sim.jsonl

  # Live mode (streams to API at 10x speed):
  python -m pipeline.simulate --store STORE_BLR_002 --visitors 40 --api http://localhost:8000 --live
"""
import argparse
import time
import uuid
import random
import json
import math
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

ZONES = ["SKINCARE", "MAKEUP", "HAIRCARE", "FRAGRANCES", "BILLING"]
SKU_MAP = {
    "SKINCARE": "MOISTURISER",
    "MAKEUP": "FOUNDATION",
    "HAIRCARE": "SHAMPOO",
    "FRAGRANCES": "PERFUME",
    "BILLING": None,
}
CAMERA_MAP = {
    "ENTRY": "CAM_ENTRY_01",
    "SKINCARE": "CAM_FLOOR_01",
    "MAKEUP": "CAM_FLOOR_01",
    "HAIRCARE": "CAM_FLOOR_01",
    "FRAGRANCES": "CAM_FLOOR_01",
    "BILLING": "CAM_BILLING_01",
}


def _make_event(
    store_id: str, visitor_id: str, event_type: str,
    ts: datetime, zone_id=None, dwell_ms=0, is_staff=False,
    confidence=0.92, queue_depth=None, sku_zone=None, session_seq=0
) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": store_id,
        "camera_id": CAMERA_MAP.get(zone_id or "ENTRY", "CAM_FLOOR_01"),
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": is_staff,
        "confidence": round(confidence, 4),
        "metadata": {
            "queue_depth": queue_depth,
            "sku_zone": sku_zone or SKU_MAP.get(zone_id),
            "session_seq": session_seq,
        },
    }


def generate_session(
    store_id: str, start_time: datetime, is_staff: bool = False
) -> tuple[list[dict], list[dict]]:
    """
    Generate all events and any POS transactions for one visitor session.
    Returns (events, pos_transactions).
    A POS transaction is generated when a non-staff visitor reaches billing and doesn't abandon.
    """
    events = []
    pos_txns = []
    t = start_time
    visitor_id = f"VIS_{uuid.uuid4().hex[:6]}"
    seq = 0
    confidence = random.uniform(0.72, 0.98)

    def emit(event_type, zone_id=None, dwell_ms=0, **kwargs):
        nonlocal seq
        seq += 1
        events.append(_make_event(
            store_id=store_id,
            visitor_id=visitor_id,
            event_type=event_type,
            ts=t,
            zone_id=zone_id,
            dwell_ms=dwell_ms,
            is_staff=is_staff,
            confidence=confidence,
            session_seq=seq,
            **kwargs,
        ))

    emit("ENTRY")

    if is_staff:
        for zone in ZONES[:-1]:
            t += timedelta(seconds=random.randint(60, 180))
            emit("ZONE_ENTER", zone_id=zone)
            t += timedelta(seconds=random.randint(120, 600))
            dwell = int((t - start_time).total_seconds() * 1000)
            emit("ZONE_DWELL", zone_id=zone, dwell_ms=dwell)
            emit("ZONE_EXIT", zone_id=zone, dwell_ms=dwell)
        t += timedelta(seconds=30)
        emit("EXIT")
        return events, pos_txns

    visited_zones = random.sample(ZONES[:-1], k=random.randint(1, 4))
    goes_to_billing = random.random() < 0.45
    abandons_queue = goes_to_billing and random.random() < 0.20

    for zone in visited_zones:
        t += timedelta(seconds=random.randint(15, 60))
        emit("ZONE_ENTER", zone_id=zone)
        dwell_seconds = random.randint(30, 300)
        dwell_emitted = 0
        for _ in range(30, dwell_seconds, 30):
            t += timedelta(seconds=30)
            dwell_emitted += 30_000
            emit("ZONE_DWELL", zone_id=zone, dwell_ms=dwell_emitted)
        remaining = dwell_seconds - (dwell_emitted // 1000)
        t += timedelta(seconds=max(0, remaining))
        emit("ZONE_EXIT", zone_id=zone, dwell_ms=dwell_seconds * 1000)

    if goes_to_billing:
        queue_depth = random.randint(0, 8)
        billing_enter_time = t + timedelta(seconds=random.randint(10, 30))
        t = billing_enter_time
        emit("ZONE_ENTER", zone_id="BILLING")

        if queue_depth > 0:
            emit("BILLING_QUEUE_JOIN", zone_id="BILLING", queue_depth=queue_depth)

        if abandons_queue:
            t += timedelta(seconds=random.randint(60, 180))
            emit("BILLING_QUEUE_ABANDON", zone_id="BILLING")
        else:
            t += timedelta(seconds=random.randint(60, 240))
            dwell = random.randint(60_000, 300_000)
            emit("ZONE_DWELL", zone_id="BILLING", dwell_ms=dwell)
            emit("ZONE_EXIT", zone_id="BILLING", dwell_ms=dwell)
            # POS transaction occurs within 3 minutes of billing exit
            txn_time = t + timedelta(seconds=random.randint(30, 180))
            pos_txns.append({
                "transaction_id": f"TXN_{uuid.uuid4().hex[:8].upper()}",
                "store_id": store_id,
                "timestamp": txn_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "basket_value_inr": round(random.uniform(200, 4000), 2),
            })

    t += timedelta(seconds=random.randint(10, 60))
    emit("EXIT")
    return events, pos_txns


def run_simulation(
    store_id: str,
    num_visitors: int,
    output_path: str = None,
    api_url: str = None,
    live: bool = False,
    speedup: float = 10.0,
) -> None:
    # Anchor the simulation 3 hours ago.  All activity is confined to a
    # SIM_WINDOW_MINUTES-long window starting at sim_start, so every event
    # and POS transaction is guaranteed to be in the past at query time.
    SIM_WINDOW_MINUTES = 60
    MAX_SESSION_MINUTES = 53   # worst-case staff session (4 zones × (180+600)s + 30s)
    sim_start = datetime.now(timezone.utc) - timedelta(
        minutes=SIM_WINDOW_MINUTES + MAX_SESSION_MINUTES + 30  # 30-min safety buffer
    )
    cutoff_ts = datetime.now(timezone.utc) - timedelta(seconds=30)

    all_events: list[dict] = []
    all_pos: list[dict] = []

    num_staff = max(2, num_visitors // 10)
    # Distribute staff evenly across the window so the last session finishes in the past.
    staff_spacing_minutes = SIM_WINDOW_MINUTES / num_staff

    for i in range(num_staff):
        start = sim_start + timedelta(minutes=i * staff_spacing_minutes)
        evs, txns = generate_session(store_id, start, is_staff=True)
        all_events.extend(evs)
        all_pos.extend(txns)

    arrival_gap_seconds = (SIM_WINDOW_MINUTES * 60) / max(num_visitors, 1)
    for i in range(num_visitors):
        jitter = random.uniform(-arrival_gap_seconds * 0.5, arrival_gap_seconds * 0.5)
        start = sim_start + timedelta(seconds=i * arrival_gap_seconds + jitter)
        evs, txns = generate_session(store_id, start, is_staff=False)
        all_events.extend(evs)
        all_pos.extend(txns)

    all_events.sort(key=lambda e: e["timestamp"])

    # Hard clamp: ensure no generated timestamp sneaks past now.
    # Handles any remaining edge cases (e.g. jitter pushing an event forward).
    cutoff_str = cutoff_ts.strftime("%Y-%m-%dT%H:%M:%SZ")
    all_events = [
        {**e, "timestamp": min(e["timestamp"], cutoff_str)} for e in all_events
    ]
    all_pos = [
        {**t, "timestamp": min(t["timestamp"], cutoff_str)} for t in all_pos
    ]

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            for ev in all_events:
                f.write(json.dumps(ev) + "\n")
        print(f"[simulate] {len(all_events)} events + {len(all_pos)} POS transactions written to {output_path}")

    if api_url:
        _stream_to_api(all_events, api_url, live=live, speedup=speedup)
        if all_pos:
            _ingest_pos(all_pos, api_url)


def _stream_to_api(events: list[dict], api_url: str, live: bool, speedup: float) -> None:
    batch_size = 50
    total = len(events)
    sent = 0

    if not live:
        # Batch upload all at once
        for i in range(0, total, batch_size):
            batch = events[i:i + batch_size]
            resp = requests.post(f"{api_url}/events/ingest", json={"events": batch}, timeout=15)
            sent += len(batch)
            print(f"[simulate] Ingested {sent}/{total} events (status={resp.status_code})")
        return

    # Live streaming: simulate real time at speedup factor
    prev_ts_str = events[0]["timestamp"]
    prev_ts = datetime.fromisoformat(prev_ts_str.replace("Z", "+00:00"))
    buffer: list[dict] = []

    for ev in events:
        ev_ts = datetime.fromisoformat(ev["timestamp"].replace("Z", "+00:00"))
        gap = (ev_ts - prev_ts).total_seconds() / speedup
        if gap > 0.05 and buffer:
            resp = requests.post(f"{api_url}/events/ingest", json={"events": buffer}, timeout=15)
            sent += len(buffer)
            print(f"\r[simulate] Streamed {sent}/{total} events", end="", flush=True)
            buffer.clear()
            time.sleep(gap)
        buffer.append(ev)
        prev_ts = ev_ts

    if buffer:
        requests.post(f"{api_url}/events/ingest", json={"events": buffer}, timeout=15)
        sent += len(buffer)

    print(f"\n[simulate] Done — {sent} events streamed to {api_url}")


def _ingest_pos(pos_txns: list[dict], api_url: str) -> None:
    try:
        resp = requests.post(
            f"{api_url}/pos/ingest",
            json={"transactions": pos_txns},
            timeout=15,
        )
        if resp.status_code == 207:
            data = resp.json()
            print(f"[simulate] POS: {data['accepted']} accepted, {data['duplicate']} duplicate")
        else:
            print(f"[simulate] POS ingest FAILED — HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as exc:
        print(f"[simulate] POS ingest failed: {exc}")


def main():
    parser = argparse.ArgumentParser(description="Synthetic event generator")
    parser.add_argument("--store", default="STORE_BLR_002", help="Store ID")
    parser.add_argument("--visitors", type=int, default=40, help="Number of customer sessions")
    parser.add_argument("--output", default=None, help="Output JSONL file path")
    parser.add_argument("--api", default=None, help="API URL to stream events to")
    parser.add_argument("--live", action="store_true", help="Stream in simulated real time")
    parser.add_argument("--speedup", type=float, default=10.0, help="Speedup factor for live mode")
    args = parser.parse_args()

    if not args.output and not args.api:
        parser.error("Provide --output, --api, or both")

    run_simulation(
        store_id=args.store,
        num_visitors=args.visitors,
        output_path=args.output,
        api_url=args.api,
        live=args.live,
        speedup=args.speedup,
    )


if __name__ == "__main__":
    main()
