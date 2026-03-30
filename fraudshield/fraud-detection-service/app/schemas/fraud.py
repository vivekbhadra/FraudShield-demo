from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class TransactionEvent(BaseModel):
    """
    Shape of the TransactionInitiated Kafka event
    published by the Transaction Service.
    """
    transaction_id: str
    user_id: str
    merchant_id: str
    amount: float = Field(..., gt=0)
    currency: str
    created_at: datetime


class RuleBreakdown(BaseModel):
    """
    Per-rule score contribution — returned alongside the verdict
    for explainability. Useful for debugging and audit logs.
    """
    high_amount: float = 0.0
    velocity: float = 0.0
    new_merchant: float = 0.0
    off_hours: float = 0.0
    blacklist: float = 0.0


class ScoringResult(BaseModel):
    transaction_id: str
    user_id: str
    total_score: float
    verdict: str  # PASS | REVIEW | BLOCK
    breakdown: RuleBreakdown
    scored_at: datetime


class FraudVerdictEvent(BaseModel):
    """
    Shape of the FraudVerdict Kafka event published back
    to the transactions.verdict topic.
    """
    transaction_id: str
    user_id: str
    fraud_score: float
    verdict: str
    breakdown: dict
    scored_at: str  # ISO 8601
