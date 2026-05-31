# DESIGN.md — Store Intelligence System

## Architecture Overview

The system is a four-stage pipeline that converts raw CCTV footage into queryable retail analytics.

```
CCTV Clips → Detection Pipeline → Events (JSONL / HTTP) → FastAPI → SQLite → React Dashboard
```

### Component Breakdown

**1. Detection Pipeline (`pipeline/`)**

Processes video clips frame-by-frame using YOLOv8n for person detection and ByteTrack (built into Ultralytics) for multi-object tracking. Each tracked person is assigned a `visitor_id` via a lightweight Re-ID mechanism described below.

Key pipeline stages per frame:
- Person detection (YOLOv8, class=0, conf≥0.3)
- Track ID assignment (ByteTrack, persistent across frames)
- Zone classification (ray-casting point-in-polygon against store_layout.json)
- Direction determination (vertical position delta across the entry line for entry cameras)
- Staff heuristic (dominant HSV hue of torso region compared to configured uniform colour)
- State machine update → emit events on transitions

The pipeline processes every 3rd frame (5fps effective at 15fps source) to balance accuracy with throughput. This was validated not to miss entry/exit events since people cross the entry line over several seconds at walking pace.

**2. Event Schema**

Events are written to `.jsonl` files and optionally streamed to the API via HTTP batches. The schema is defined in `pipeline/emit.py` and mirrors the spec exactly, with `metadata.queue_depth`, `metadata.sku_zone`, and `metadata.session_seq` populated as appropriate.

**3. Intelligence API (`app/`)**

FastAPI with SQLAlchemy ORM on SQLite. The API is stateless — all metrics are computed on query from the event store. This avoids stale aggregates at the cost of query-time computation; acceptable at this scale (single store, ~hours of data).

Request/response flow:
```
HTTP Request
  → CORS Middleware
  → Logging Middleware (trace_id injection)
  → FastAPI Router
  → SQLAlchemy Query (SQLite)
  → Pydantic Response Model
  → JSON Response
```

Structured logs are emitted as JSON on stdout, containing `trace_id`, `store_id`, `endpoint`, `latency_ms`, `status_code`, and `event_count` (for ingest).

**4. Live Dashboard (`dashboard/`)**

React SPA built with Vite. Polls API endpoints every 5 seconds. Uses Recharts for the conversion funnel bar chart. Served by nginx which also proxies `/api/*` to the FastAPI backend, so no CORS issues in production.

---

## Re-ID Strategy

**Problem:** ByteTrack assigns track IDs per video run. When a person exits and re-enters, ByteTrack assigns a new track ID. We need to recognise this as a REENTRY event, not a new ENTRY.

**Approach:**
1. On first appearance, `track_id → visitor_id` is recorded in an active map.
2. On EXIT, `visitor_id` moves to a `departed` cache with a timestamp.
3. On new track appearance, the most recently departed visitor (within 10 minutes) is treated as a REENTRY candidate.

**Why this works in practice:** In a retail store, the same person re-entering is the most common cause of rapid new track appearances at the entry camera. The 10-minute window covers quick round-trips (e.g., went back to car, came back). This is a simplified heuristic; production would use appearance embedding similarity (OSNet) for cross-clip Re-ID.

**Known limitation:** If two different people enter in quick succession within the window, the second person may incorrectly inherit the first's visitor_id. This is the "same direction 3 seconds later" edge case mentioned in the challenge examples — we acknowledge it and in production would add appearance distance as a secondary gate.

---

## Cross-Camera Deduplication

The floor camera partially overlaps the entry camera field of view. A person visible in both would generate two sets of zone events with different track IDs (one from each video).

Mitigation: each camera runs a separate pipeline invocation with a separate tracker. Zone events are deduplicated at the API layer by `visitor_id`. If the same physical person appears in both cameras near-simultaneously, their Re-ID tokens would diverge (different track IDs = different visitor_ids). This is a known limitation of running cameras independently — production would solve it by either cross-camera track matching via appearance embeddings, or by setting camera coverage zones that don't overlap.

---

## Conversion Rate Computation

```
conversion_rate = converted_sessions / unique_sessions
```

A session is "converted" if that `visitor_id` had a BILLING zone event within the 5-minute window before any POS transaction for the same store. This is a probabilistic approximation — POS data has no customer_id, so we can't link individual transactions to specific visitors directly. The 5-minute window is configurable in `app/metrics.py`.

---

## Known Limitations

**Re-ID temporal heuristic (directly relevant to follow-up question 7 in the spec):**
The current Re-ID associates a new track with the most recently departed visitor within 10 minutes. If two different people enter in quick succession (e.g., person A exits, person B enters 3 seconds later), person B will inherit person A's `visitor_id` and appear as a REENTRY. In production, this is fixed by adding appearance embedding distance (OSNet cosine similarity) as a second gate: only claim REENTRY if `departure_time < 10 min AND appearance_similarity > 0.7`. The heuristic is still useful as a pre-filter because embedding comparison is expensive and can be skipped when no recent departure exists.

**POS correlation is probabilistic:**
There is no `customer_id` in POS data. Attribution is purely temporal: "visitor in BILLING zone within 5 minutes before transaction." A store with high foot traffic and rapid transaction throughput will over-count conversions (multiple visitors in the billing window before each transaction). At 40 live stores with high transaction rates, this could inflate conversion rate by 5-15%. Mitigation: shrink the window or require the visitor to have a BILLING_QUEUE_JOIN (rather than any BILLING zone event) before the POS timestamp.

**Staff detection accuracy:**
The HSV hue method only works if the uniform colour is configured correctly in `STAFF_HUE_CENTER` and the store has consistent uniform colours. Under mixed lighting (the clip spec mentions fluorescent + natural), HSV hue shifts. A tuned colour profile per store and per lighting condition would be needed in production. Alternatively: train a binary classifier on uniform vs. non-uniform clothing — 200 labelled examples would give >90% precision.

**Scaling bottleneck at 40 stores:**
At 40 live stores sending events in real time, synchronous SQLAlchemy on SQLite becomes the first bottleneck. SQLite's writer lock means only one ingest request can write at a time. At 40 stores × ~10 events/second = 400 writes/second — SQLite handles ~500, so it's near the limit. Migration: PostgreSQL connection pool (already supported via `DATABASE_URL` env var), async SQLAlchemy, and a single-writer ingest queue per store.

**Cross-camera deduplication:**
The floor camera overlaps with the entry camera. The current system runs independent per-camera pipelines, so a person visible in both generates two `visitor_id` values. There is no cross-camera deduplication at the pipeline level. Mitigation used: zone assignments are made per camera, so the visitor's ENTRY comes from the entry camera and zone events come from the floor camera — they have different `visitor_id`s. The API counts unique visitors by ENTRY events only, so the floor camera's zone events for the same person don't inflate the visitor count, but dwell/zone analytics are affected.

---

## AI-Assisted Decisions

**1. Detection model selection**

I asked Claude to compare YOLOv8n vs YOLOv8m vs RT-DETR for this use case. The key insight from the AI: at 1080p/15fps, YOLOv8n runs at 30-40fps on a GPU and 5-8fps on CPU. For offline batch processing of 20-minute clips, throughput is more important than sub-frame latency, so YOLOv8n was the right starting point. I agreed with this and implemented it, but overrode the suggestion to use the default confidence threshold (0.5) — I lowered it to 0.3 per the spec requirement to "not suppress low-confidence events."

**2. Re-ID without embeddings**

Claude suggested using OSNet (torchreid) for appearance-based Re-ID. I evaluated this and decided not to: installing torchreid adds ~600MB of dependencies and requires a GPU for reasonable throughput. For a take-home challenge submission that must run via docker compose up on any machine, I chose the trajectory/time-based approach with an explicit note of the limitation. The AI was right about OSNet being production-superior, but wrong about it being practical here.

**3. Synchronous SQLAlchemy with FastAPI**

The AI (correctly) noted that using synchronous SQLAlchemy with async FastAPI can block the event loop under high concurrency. It suggested switching to `asyncpg` + `async SQLAlchemy`. I chose to keep synchronous SQLAlchemy with SQLite because: (a) the challenge explicitly says SQLite is acceptable, (b) for single-store testing the throughput is adequate, and (c) the async migration would double the database layer complexity without changing the API surface. This is the right call for a hackathon submission; a production deployment at 40 stores would use PostgreSQL + async SQLAlchemy.
