"""
Store Intelligence API — FastAPI entry point.
Starts with: uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
import uuid
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from app.database import init_db
from app.logging_config import get_logger, setup_logging
from app import ingestion, metrics, funnel, anomalies, health, heatmap, pos

setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_db()
        logger.info("database_ready")
    except Exception as exc:
        logger.error("database_init_failed", extra={"error": str(exc)})
    yield
    logger.info("shutdown")


app = FastAPI(
    title="Store Intelligence API",
    description="Real-time retail analytics from CCTV footage — Apex Retail",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    trace_id = str(uuid.uuid4())[:8]
    request.state.trace_id = trace_id
    start = time.monotonic()

    response = await call_next(request)

    latency_ms = round((time.monotonic() - start) * 1000, 2)
    store_id = request.path_params.get("store_id", "")
    logger.info(
        "http_request",
        extra={
            "trace_id": trace_id,
            "store_id": store_id,
            "endpoint": request.url.path,
            "method": request.method,
            "status_code": response.status_code,
            "latency_ms": latency_ms,
        },
    )
    response.headers["X-Trace-Id"] = trace_id
    return response


@app.exception_handler(OperationalError)
async def db_error_handler(request: Request, exc: OperationalError):
    logger.error("db_unavailable", extra={"error": str(exc)})
    return JSONResponse(
        status_code=503,
        content={
            "error": "service_unavailable",
            "message": "Database is temporarily unavailable. Please retry.",
        },
    )


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception):
    logger.error("unhandled_exception", extra={"error": str(exc), "path": request.url.path})
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "message": "An unexpected error occurred."},
    )


app.include_router(ingestion.router, tags=["Events"])
app.include_router(metrics.router, tags=["Analytics"])
app.include_router(funnel.router, tags=["Analytics"])
app.include_router(heatmap.router, tags=["Analytics"])
app.include_router(anomalies.router, tags=["Analytics"])
app.include_router(health.router, tags=["System"])
app.include_router(pos.router, tags=["POS"])
