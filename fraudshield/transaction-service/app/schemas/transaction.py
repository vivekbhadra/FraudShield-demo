from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class TransactionCreate(BaseModel):
    user_id: str = Field(..., min_length=1, description="ID of the user initiating payment")
    merchant_id: str = Field(..., min_length=1, description="Target merchant identifier")
    amount: float = Field(..., gt=0, description="Transaction amount — must be positive")
    currency: str = Field(default="INR", min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def currency_uppercase(cls, v: str) -> str:
        return v.upper()


class TransactionResponse(BaseModel):
    id: UUID
    user_id: str
    merchant_id: str
    amount: float
    currency: str
    status: str
    fraud_score: Optional[float] = None
    fraud_verdict: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TransactionStatusUpdate(BaseModel):
    """Internal schema — used when Fraud Detection verdict arrives via Kafka."""
    transaction_id: UUID
    fraud_score: float
    fraud_verdict: str  # PASS | REVIEW | BLOCK


class PaginatedTransactions(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[TransactionResponse]
