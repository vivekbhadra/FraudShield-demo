import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.transaction import (
    TransactionCreate,
    TransactionResponse,
    PaginatedTransactions,
)
from app.services import transaction_svc

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post(
    "/",
    response_model=TransactionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Initiate a new payment transaction",
    description=(
        "Accepts a payment request, persists it with PENDING status, and "
        "asynchronously triggers fraud scoring via Kafka. Returns 202 immediately."
    ),
)
def initiate_transaction(payload: TransactionCreate, db: Session = Depends(get_db)):
    txn = transaction_svc.create_transaction(db, payload)
    return txn


@router.get(
    "/{transaction_id}",
    response_model=TransactionResponse,
    summary="Get transaction by ID",
)
def get_transaction(transaction_id: UUID, db: Session = Depends(get_db)):
    txn = transaction_svc.get_transaction(db, transaction_id)
    if not txn:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transaction {transaction_id} not found.",
        )
    return txn


@router.get(
    "/user/{user_id}",
    response_model=PaginatedTransactions,
    summary="List transactions for a specific user",
)
def get_user_transactions(
    user_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    items, total = transaction_svc.get_transactions_by_user(db, user_id, page, page_size)
    return PaginatedTransactions(
        total=total,
        page=page,
        page_size=page_size,
        items=items,
    )
