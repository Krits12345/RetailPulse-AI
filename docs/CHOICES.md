# CHOICES.md — Key Technical Decisions

## Decision 1: Detection Model — YOLOv8n + ByteTrack

### Options Considered

| Option | Pros | Cons |
|---|---|---|
| YOLOv8n (chosen) | Fast (30fps GPU / 5fps CPU), easy Docker deployment, ByteTrack built-in | Lower mAP than larger variants |
| YOLOv8m | Better accuracy, handles partial occlusion better | 3x slower, ~200MB model |
| RT-DETR | State-of-art transformer-based detector | Requires complex setup, slow CPU |
| MediaPipe Pose | Very lightweight, runs on mobile | Designed for single person, poor group handling |

### What AI Suggested

Claude suggested RT-DETR as the best-accuracy option for a production deployment and YOLOv8m as the practical choice balancing accuracy vs. speed. It specifically flagged that partial occlusion (one of the edge cases in the challenge) is where larger models improve significantly.

### What I Chose and Why

**YOLOv8n** with the plan to swap to YOLOv8m if accuracy is insufficient. My reasoning:
1. CPU inference speed: YOLOv8n runs at 5-8fps on CPU, which is sufficient for offline batch processing of 20-minute clips and fast enough for near-real-time detection on a CPU-only Docker host. YOLOv8m is 3x slower — on CPU-only infrastructure it would process clips slower than real-time.
2. ByteTrack is built into Ultralytics, which eliminates a separate tracking dependency and reduces Docker image size.
3. `conf=0.3` threshold (lower than default 0.5) recovers most partially-occluded people at the cost of some false positives — a better trade-off than missing them entirely for a system that must never silently fail.
4. For the group entry edge case, YOLOv8n handles simultaneous detections well since it's a one-stage detector that processes all detections in a single forward pass, not serialised.

If I had real GPU infrastructure and the 20-minute clips took hours to process, I would upgrade to YOLOv8m or RT-DETR.

### On VLM Usage for Staff Detection

I considered using Claude Vision or GPT-4V to classify staff by describing the frame context ("Are any people wearing a blue retail uniform?"). I evaluated this and chose a rule-based HSV hue approach instead for two reasons:
1. VLM API calls per-frame are impractical at video throughput (would cost ~$5/minute of footage at GPT-4V pricing).
2. The heuristic works when uniform colours are known (configurable `STAFF_HUE_CENTER` in tracker.py).

**Where I did use a VLM:** I used Claude to help me reason about the zone classification logic — specifically, whether a polygon-intersection approach or a model-based approach was better for zone assignment. Claude argued for polygon-intersection (rule-based) because zone boundaries are sharp geometric lines, not fuzzy image features. I agreed and implemented it in `pipeline/zone_classifier.py`. A VLM would be useful for *unknown* zone layouts, but since we have `store_layout.json`, explicit polygons are more accurate and cost-free.

---

## Decision 2: Event Schema Design

### Options Considered

**Option A (chosen):** Flat event with metadata sub-object
```json
{
  "event_type": "BILLING_QUEUE_JOIN",
  "metadata": { "queue_depth": 6, "sku_zone": "...", "session_seq": 5 }
}
```

**Option B:** Separate tables per event type (normalised)
```
entry_events, zone_events, billing_events…
```

**Option C:** Event sourcing with separate state projection tables

### What AI Suggested

Claude suggested Option C (CQRS/event sourcing) for maximum query flexibility. The argument: separating the immutable event log from computed state makes analytics queries faster and easier to replay.

### What I Chose and Why

**Option A** — flat schema with a metadata sub-object. Reasons:
1. Option C is significantly more complex to implement correctly in 48 hours and adds infrastructure (event bus, projections).
2. The flat schema with JSON metadata is easily extensible without schema migrations — we can add new fields to `metadata` without touching existing consumers.
3. The challenge requires POST /events/ingest to validate events against a schema. A single Pydantic model is simpler than a union type or polymorphic schema.
4. SQLite doesn't support JSONB, but we store the metadata fields as flat columns (`queue_depth`, `sku_zone`, `session_seq`) for queryability while the API returns them nested.

**Where I disagreed with AI:** Claude suggested using `event_type` as a discriminator field for polymorphic Pydantic models (one model per event type). I rejected this because: the spec shows a single unified schema for all event types, and polymorphism would make the ingest endpoint's validation error messages confusing ("none of the 8 union variants matched"). The current approach validates all events against one schema with optional fields.

---

## Decision 3: API Architecture — SQLite + Synchronous SQLAlchemy

### Options Considered

| Option | Throughput | Complexity | Docker-compose simplicity |
|---|---|---|---|
| SQLite + sync SQLAlchemy (chosen) | ~500 events/s | Low | Excellent — no extra service |
| PostgreSQL + async SQLAlchemy | ~10k events/s | Medium | Requires postgres container |
| Redis + SQLite | ~5k events/s | Medium | Requires redis container |
| DuckDB (OLAP) | Very fast reads | Low | Excellent |

### What AI Suggested

Claude strongly recommended PostgreSQL for production, citing connection pooling, concurrent write throughput, and proper JSON column support. It also suggested DuckDB as a creative alternative for a read-heavy analytics API.

### What I Chose and Why

**SQLite + synchronous SQLAlchemy** for the submission:
1. `docker compose up` works with zero external services.
2. The ingest volume in this challenge is bounded: 5 stores × 3 cameras × 20 minutes, with at most a few thousand events. SQLite handles this trivially.
3. All analytics queries in the API are simple aggregations (GROUP BY, COUNT, MAX). SQLite executes these in milliseconds.

**Production path:** The `DATABASE_URL` environment variable in docker-compose.yml makes switching to PostgreSQL a one-line change: `DATABASE_URL=postgresql://user:pass@db/store_intelligence`. No code changes required — SQLAlchemy handles both backends with the same ORM.

The synchronous SQLAlchemy choice does mean each request holds the event loop during DB I/O. At 40 stores + real-time, this would become a bottleneck. Mitigation: `run_in_executor` wrapper around synchronous calls, or a full migration to `asyncpg`. I document this limitation in DESIGN.md.
