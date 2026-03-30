import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Float, DateTime, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
import enum

from app.db.session import Base


class TransactionStatus(str, enum.Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FLAGGED = "FLAGGED"
    BLOCKED = "BLOCKED"


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(String, nullable=False, index=True)
    merchant_id = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String(3), nullable=False, default="INR")
    status = Column(
        SAEnum(TransactionStatus),
        nullable=False,
        default=TransactionStatus.PENDING,
    )
    fraud_score = Column(Float, nullable=True)
    fraud_verdict = Column(String, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self):
        return (
            f"<Transaction id={self.id} user={self.user_id} "
            f"amount={self.amount} status={self.status}>"
        )
