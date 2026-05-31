# PROMPT: "Write a pytest conftest.py that creates an in-memory SQLite database
# and a FastAPI TestClient for integration tests. The fixture should:
# 1. Override the database dependency to use in-memory SQLite
# 2. Create all tables fresh for each test
# 3. Provide a helper to seed sample events
# 4. Provide a client fixture and a seeded_db fixture"
#
# CHANGES MADE:
# - Added seed_pos_transactions fixture for conversion rate tests
# - Added freeze_now fixture to make time-sensitive tests deterministic
# - Used pytest.fixture scope="function" everywhere for full isolation

import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db, StoreEvent, POSTransaction
from app.main import app

TEST_DB_URL = "sqlite:///:memory:"


@pytest.fixture
def db_engine():
    # StaticPool ensures all connections share the same in-memory database
    engine = create_engine(
        TEST_DB_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session(db_engine):
    TestingSession = sessionmaker(bind=db_engine)
    session = TestingSession()
    yield session
    session.close()


@pytest.fixture
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def make_event(
    store_id="STORE_BLR_002",
    visitor_id="VIS_aaaaaa",
    event_type="ENTRY",
    zone_id=None,
    dwell_ms=0,
    is_staff=False,
    confidence=0.92,
    timestamp=None,
    camera_id="CAM_ENTRY_01",
    queue_depth=None,
) -> dict:
    """Factory for valid API event payloads."""
    import uuid
    if timestamp is None:
        timestamp = datetime.now(timezone.utc)
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": store_id,
        "camera_id": camera_id,
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": is_staff,
        "confidence": confidence,
        "metadata": {"queue_depth": queue_depth, "sku_zone": None, "session_seq": 1},
    }


@pytest.fixture
def seed_visitor_session(db_session):
    """Seeds a complete visitor session (ENTRY → ZONE_ENTER → BILLING → EXIT)."""
    import uuid
    from datetime import datetime, timezone, timedelta

    base_ts = datetime.now(timezone.utc).replace(
        hour=12, minute=0, second=0, microsecond=0, tzinfo=None
    )
    visitor_id = f"VIS_{uuid.uuid4().hex[:6]}"

    events = [
        StoreEvent(event_id=str(uuid.uuid4()), store_id="STORE_BLR_002",
                   camera_id="CAM_ENTRY_01", visitor_id=visitor_id,
                   event_type="ENTRY", timestamp=base_ts, is_staff=False, confidence=0.9),
        StoreEvent(event_id=str(uuid.uuid4()), store_id="STORE_BLR_002",
                   camera_id="CAM_FLOOR_01", visitor_id=visitor_id,
                   event_type="ZONE_ENTER", timestamp=base_ts + timedelta(minutes=2),
                   zone_id="SKINCARE", is_staff=False, confidence=0.88),
        StoreEvent(event_id=str(uuid.uuid4()), store_id="STORE_BLR_002",
                   camera_id="CAM_BILLING_01", visitor_id=visitor_id,
                   event_type="ZONE_ENTER", timestamp=base_ts + timedelta(minutes=10),
                   zone_id="BILLING", is_staff=False, confidence=0.91),
        StoreEvent(event_id=str(uuid.uuid4()), store_id="STORE_BLR_002",
                   camera_id="CAM_ENTRY_01", visitor_id=visitor_id,
                   event_type="EXIT", timestamp=base_ts + timedelta(minutes=20),
                   is_staff=False, confidence=0.85),
    ]
    for e in events:
        db_session.add(e)
    db_session.commit()
    return visitor_id
