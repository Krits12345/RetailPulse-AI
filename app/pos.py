"""
POST /pos/ingest — ingest POS transactions for conversion rate correlation.
Accepts batches of transactions. Idempotent by transaction_id.
"""
from datetime import datetime
from typing import List
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel

from app.database import get_db, POSTransaction
from app.logging_config import get_logger

router = APIRouter()
logger = get_logger(__name__)


class POSTxn(BaseModel):
    transaction_id: str
    store_id: str
    timestamp: datetime
    basket_value_inr: float


class POSTxnBatch(BaseModel):
    transactions: List[POSTxn]


class POSTxnResult(BaseModel):
    accepted: int
    duplicate: int


@router.post("/pos/ingest", response_model=POSTxnResult, status_code=207)
def ingest_pos(batch: POSTxnBatch, db: Session = Depends(get_db)):
    accepted = duplicate = 0
    for txn in batch.transactions:
        try:
            sp = db.begin_nested()
            db.add(POSTransaction(
                transaction_id=txn.transaction_id,
                store_id=txn.store_id,
                timestamp=txn.timestamp.replace(tzinfo=None),
                basket_value_inr=txn.basket_value_inr,
            ))
            db.flush()
            sp.commit()
            accepted += 1
        except IntegrityError:
            sp.rollback()
            duplicate += 1
    db.commit()
    logger.info("pos_ingest_complete", extra={"accepted": accepted, "duplicate": duplicate})
    return POSTxnResult(accepted=accepted, duplicate=duplicate)
