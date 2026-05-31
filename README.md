# Store Intelligence System — Apex Retail


Live Demo

Frontend Dashboard:
https://retail-pulse-ai-zeta.vercel.app

Backend API:
https://retailpulse-ai-0ph9.onrender.com/docs

## Problem Statement

Built an AI-powered Store Intelligence System that transforms CCTV footage into actionable retail analytics. The platform detects and tracks customers, generates structured events, calculates real-time business metrics, identifies anomalies, and visualizes insights through a live dashboard.

## Key Features

- Real-time visitor tracking and session analytics
- Conversion funnel analysis
- Zone-wise heatmap generation
- Queue monitoring and abandonment detection
- Anomaly detection for conversion drops and queue spikes
- REST APIs with interactive Swagger documentation
- Live dashboard with auto-refreshing analytics
- End-to-end deployment on Render and Vercel
End-to-end pipeline: CCTV footage → structured events → real-time analytics API → live dashboard.

**Stack:** Python · FastAPI · SQLite · React · Docker

---

## Quick Start (5 commands)

```bash
# 1. Clone and enter the repo
git clone <repo-url> && cd store-intelligence

# 2. Start the API + Dashboard
docker compose up --build

# 3. Run the detection pipeline (or simulation if no clips present)
./pipeline/run.sh --api http://localhost:8000

# 4. Open the live dashboard
open http://localhost:3000

# 5. Verify the API is working
curl http://localhost:8000/stores/STORE_BLR_002/metrics
```

The dashboard auto-refreshes every 5 seconds.

---

## Project Structure

```
store-intelligence/
├── pipeline/              # CCTV detection + event emission
│   ├── detect.py          # YOLOv8 + ByteTrack detection script
│   ├── tracker.py         # Visitor Re-ID and session state
│   ├── zone_classifier.py # Polygon-based zone assignment
│   ├── emit.py            # Event schema + file/API emission
│   ├── simulate.py        # Synthetic event generator (no clips needed)
│   └── run.sh             # One-command clip processor
│
├── app/                   # FastAPI backend
│   ├── main.py            # App entrypoint, middleware, error handlers
│   ├── database.py        # SQLite setup, ORM models, DB init
│   ├── models.py          # Pydantic request/response schemas
│   ├── ingestion.py       # POST /events/ingest
│   ├── metrics.py         # GET /stores/{id}/metrics
│   ├── funnel.py          # GET /stores/{id}/funnel
│   ├── heatmap.py         # GET /stores/{id}/heatmap
│   ├── anomalies.py       # GET /stores/{id}/anomalies
│   ├── health.py          # GET /health
│   └── logging_config.py  # Structured JSON logging
│
├── dashboard/             # React frontend (Vite + Recharts)
│   └── src/
│       ├── App.jsx        # Root component, polling loop
│       └── components/    # MetricsPanel, FunnelChart, HeatmapGrid, AnomalyFeed, HealthStatus
│
├── tests/                 # Pytest integration tests (>70% coverage)
│   ├── conftest.py        # Fixtures: in-memory DB, TestClient, event factory
│   ├── test_ingestion.py  # Ingest, idempotency, partial success
│   ├── test_metrics.py    # Metrics, staff exclusion, re-entry dedup
│   └── test_anomalies.py  # Anomaly detection, health endpoint
│
├── data/
│   ├── store_layout.json  # Zone definitions and camera coverage
│   └── pos_transactions.csv
│
├── docs/
│   ├── DESIGN.md          # Architecture + AI-assisted decisions
│   └── CHOICES.md         # Model, schema, and API architecture decisions
│
├── docker-compose.yml
├── Dockerfile.api
├── Dockerfile.dashboard
└── nginx.conf
```

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/events/ingest` | Ingest up to 500 events (idempotent by event_id) |
| `GET`  | `/stores/{id}/metrics` | Visitors, conversion rate, dwell, queue, abandonment |
| `GET`  | `/stores/{id}/funnel` | Entry → Zone → Billing → Purchase funnel |
| `GET`  | `/stores/{id}/heatmap` | Zone visit frequency, normalised 0–100 |
| `GET`  | `/stores/{id}/anomalies` | Queue spike, conversion drop, dead zones |
| `GET`  | `/health` | Service health + STALE_FEED warnings per store |

Full interactive docs at `http://localhost:8000/docs` after startup.

---

## Running the Detection Pipeline

### With Real CCTV Clips

Place clips in `data/clips/<STORE_ID>/<CAMERA_ID>.mp4`:

```
data/clips/
└── STORE_BLR_002/
    ├── CAM_ENTRY_01.mp4
    ├── CAM_FLOOR_01.mp4
    └── CAM_BILLING_01.mp4
```

Then run:

```bash
# Process all clips and stream events to API
API_URL=http://localhost:8000 ./pipeline/run.sh --real

# Or process a single clip manually:
python -m pipeline.detect \
  --video data/clips/STORE_BLR_002/CAM_ENTRY_01.mp4 \
  --store STORE_BLR_002 \
  --camera CAM_ENTRY_01 \
  --output data/events/output.jsonl \
  --api http://localhost:8000 \
  --clip-start 2026-03-03T10:00:00Z
```

### Without Clips — Simulation Mode

```bash
# Batch: generate 50 customer sessions + write to JSONL + upload to API
python -m pipeline.simulate \
  --store STORE_BLR_002 \
  --visitors 50 \
  --output data/events/sim.jsonl \
  --api http://localhost:8000

# Live: stream events in simulated real time at 10x speed (Part E dashboard)
python -m pipeline.simulate \
  --store STORE_BLR_002 \
  --visitors 50 \
  --api http://localhost:8000 \
  --live \
  --speedup 10
```

---

## Running Tests

```bash
# Install dependencies
pip install -r requirements.txt

# Run tests with coverage report
pytest tests/ --cov=app --cov-report=term-missing -v

# Run a specific test file
pytest tests/test_ingestion.py -v
```

Expected output: >70% statement coverage across `app/`.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite:///./data/store_intelligence.db` | SQLAlchemy database URL |
| `LOG_LEVEL` | `INFO` | Logging level |

To switch to PostgreSQL: `DATABASE_URL=postgresql://user:pass@host:5432/dbname`

---

## Running Pipeline Inside Docker

If you want to run the detection pipeline against clips from inside Docker:

```bash
# Copy clips into the db_data volume or mount them
docker run --rm \
  -v $(pwd)/data/clips:/app/data/clips \
  -v store_intelligence_db_data:/app/data \
  --network store-intelligence_default \
  store-intelligence-api-pipeline \
  python -m pipeline.simulate --store STORE_BLR_002 --visitors 40 --api http://api:8000
```

Or just run the simulation from your host machine pointed at `http://localhost:8000`.

---

## Live Dashboard (Part E)

Open `http://localhost:3000` while the simulation is streaming:

```bash
# Terminal 1: Start services
docker compose up --build

# Terminal 2: Stream live events (10x real time)
python -m pipeline.simulate \
  --store STORE_BLR_002 --visitors 60 \
  --api http://localhost:8000 --live
```

Watch the Visitors, Conversion Rate, Queue Depth, and Anomaly Feed update in real time.

---

## Design Notes

See [docs/DESIGN.md](docs/DESIGN.md) for architecture decisions and AI-assisted design choices.
See [docs/CHOICES.md](docs/CHOICES.md) for model selection, schema design, and API architecture trade-offs.
