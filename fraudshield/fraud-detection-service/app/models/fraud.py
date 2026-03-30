import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Float, DateTime, Boolean, Integer
from sqlalchemy.dialects.postgresql import UUID

from app.db.session import Base


class FraudScore(Base):
    """
    Persisted record of every scoring decision made.
    Provides an audit trail and feeds future ML model training.
    """
    __tablename__ = "fraud_scores"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transaction_id = Column(String, nullable=False, unique=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    merchant_id = Column(String, nullable=False)
    amount = Column(Float, nullable=False)

    # Individual rule scores — stored for explainability
    score_high_amount = Column(Float, default=0.0)
    score_velocity = Column(Float, default=0.0)
    score_new_merchant = Column(Float, default=0.0)
    score_off_hours = Column(Float, default=0.0)
    score_blacklist = Column(Float, default=0.0)

    total_score = Column(Float, nullable=False)
    verdict = Column(String, nullable=False)  # PASS | REVIEW | BLOCK

    scored_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self):
        return (
            f"<FraudScore txn={self.transaction_id} "
            f"score={self.total_score} verdict={self.verdict}>"
        )


class MerchantBlacklist(Base):
    """
    Manually curated list of merchants known to be associated
    with fraudulent activity. Loaded into Redis on startup.
    """
    __tablename__ = "merchant_blacklist"

    id = Column(Integer, primary_key=True, autoincrement=True)
    merchant_id = Column(String, nullable=False, unique=True, index=True)
    reason = Column(String, nullable=True)
    added_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    is_active = Column(Boolean, default=True, nullable=False)


class UserSpendProfile(Base):
    """
    Rolling 30-day spend statistics per user, updated after
    every scored transaction. Used by the high-amount rule.
    """
    __tablename__ = "user_spend_profiles"

    user_id = Column(String, primary_key=True, index=True)
    avg_transaction_amount = Column(Float, default=0.0)
    transaction_count = Column(Integer, default=0)
    last_updated = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
