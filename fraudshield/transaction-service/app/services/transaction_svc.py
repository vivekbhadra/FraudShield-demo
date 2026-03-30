import logging
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.transaction import Transaction, TransactionStatus
from app.schemas.transaction import TransactionCreate, TransactionStatusUpdate
from app.kafka.producer import publish_transaction_initiated

logger = logging.getLogger(__name__)


def create_transaction(db: Session, payload: TransactionCreate) -> Transaction:
    """
    Persist a new transaction with PENDING status and publish a
    TransactionInitiated event to Kafka. Returns immediately — fraud
    scoring happens asynchronously downstream.
    """
    txn = Transaction(
        user_id=payload.user_id,
        merchant_id=payload.merchant_id,
        amount=payload.amount,
        currency=payload.currency,
        status=TransactionStatus.PENDING,
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)

    logger.info(f"Transaction {txn.id} created for user {txn.user_id}, amount={txn.amount}")

    # Fire-and-forget event publish — non-blocking
    publish_transaction_initiated(txn)

    return txn


def get_transaction(db: Session, transaction_id: UUID) -> Transaction | None:
    return db.query(Transaction).filter(Transaction.id == transaction_id).first()


def get_transactions_by_user(
    db: Session, user_id: str, page: int = 1, page_size: int = 20
) -> tuple[list[Transaction], int]:
    query = db.query(Transaction).filter(Transaction.user_id == user_id)
    total = query.count()
    items = (
        query.order_by(Transaction.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return items, total


def apply_fraud_verdict(db: Session, update: TransactionStatusUpdate) -> Transaction | None:
    """
    Called by the Kafka consumer when a FraudVerdict event arrives.
    Maps the fraud verdict to the appropriate TransactionStatus.
    """
    txn = db.query(Transaction).filter(Transaction.id == update.transaction_id).first()
    if not txn:
        logger.warning(f"Received verdict for unknown transaction {update.transaction_id}")
        return None

    verdict_to_status = {
        "PASS": TransactionStatus.COMPLETED,
        "REVIEW": TransactionStatus.FLAGGED,
        "BLOCK": TransactionStatus.BLOCKED,
    }

    txn.fraud_score = update.fraud_score
    txn.fraud_verdict = update.fraud_verdict
    txn.status = verdict_to_status.get(update.fraud_verdict, TransactionStatus.FLAGGED)

    db.commit()
    db.refresh(txn)

    logger.info(
        f"Transaction {txn.id} updated: verdict={txn.fraud_verdict}, "
        f"score={txn.fraud_score}, status={txn.status}"
    )
    return txn
