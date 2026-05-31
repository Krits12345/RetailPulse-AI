from sqlalchemy import (
    create_engine, Column, String, Float, Boolean,
    Integer, DateTime, Index, text
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from datetime import datetime, timezone
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/store_intelligence.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
    echo=False,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class StoreEvent(Base):
    __tablename__ = "store_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String, unique=True, nullable=False)
    store_id = Column(String, nullable=False)
    camera_id = Column(String, nullable=False)
    visitor_id = Column(String, nullable=False)
    event_type = Column(String, nullable=False)
    timestamp = Column(DateTime, nullable=False)
    zone_id = Column(String, nullable=True)
    dwell_ms = Column(Integer, default=0)
    is_staff = Column(Boolean, default=False)
    confidence = Column(Float, nullable=False)
    queue_depth = Column(Integer, nullable=True)
    sku_zone = Column(String, nullable=True)
    session_seq = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_store_ts", "store_id", "timestamp"),
        Index("ix_visitor", "visitor_id"),
        Index("ix_event_type", "event_type"),
    )


class POSTransaction(Base):
    __tablename__ = "pos_transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    transaction_id = Column(String, unique=True, nullable=False)
    store_id = Column(String, nullable=False)
    timestamp = Column(DateTime, nullable=False)
    basket_value_inr = Column(Float, nullable=False)

    __table_args__ = (Index("ix_pos_store_ts", "store_id", "timestamp"),)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables and load POS data from CSV on first run."""
    Base.metadata.create_all(bind=engine)
    _seed_pos_transactions()


def _seed_pos_transactions():
    """Load pos_transactions.csv into DB (idempotent via UNIQUE constraint)."""
    import csv
    from pathlib import Path
    from datetime import datetime, timezone

    csv_path = Path("data/pos_transactions.csv")
    if not csv_path.exists():
        return

    db = SessionLocal()
    try:
        with open(csv_path) as f:
            for row in csv.DictReader(f):
                existing = db.query(POSTransaction).filter_by(
                    transaction_id=row["transaction_id"]
                ).first()
                if existing:
                    continue
                ts = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
                db.add(POSTransaction(
                    transaction_id=row["transaction_id"],
                    store_id=row["store_id"],
                    timestamp=ts.replace(tzinfo=None),
                    basket_value_inr=float(row["basket_value_inr"]),
                ))
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()
