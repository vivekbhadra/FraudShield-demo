import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.models.transaction import Transaction, TransactionStatus
from app.schemas.transaction import TransactionCreate, TransactionStatusUpdate
from app.services.transaction_svc import (
    create_transaction,
    get_transaction,
    apply_fraud_verdict,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_db():
    db = MagicMock()
    return db


@pytest.fixture
def sample_payload():
    return TransactionCreate(
        user_id="user_abc",
        merchant_id="merchant_xyz",
        amount=4500.0,
        currency="INR",
    )


@pytest.fixture
def sample_transaction():
    txn = Transaction(
        id=uuid.uuid4(),
        user_id="user_abc",
        merchant_id="merchant_xyz",
        amount=4500.0,
        currency="INR",
        status=TransactionStatus.PENDING,
    )
    return txn


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestCreateTransaction:
    @patch("app.services.transaction_svc.publish_transaction_initiated")
    def test_creates_with_pending_status(self, mock_publish, mock_db, sample_payload, sample_transaction):
        mock_db.refresh.side_effect = lambda obj: None
        mock_db.add.return_value = None
        mock_db.commit.return_value = None

        # Simulate that after commit/refresh the object has an id
        with patch("app.services.transaction_svc.Transaction", return_value=sample_transaction):
            result = create_transaction(mock_db, sample_payload)

        assert result.status == TransactionStatus.PENDING
        assert result.user_id == "user_abc"
        mock_publish.assert_called_once()

    @patch("app.services.transaction_svc.publish_transaction_initiated")
    def test_publishes_kafka_event(self, mock_publish, mock_db, sample_payload, sample_transaction):
        with patch("app.services.transaction_svc.Transaction", return_value=sample_transaction):
            create_transaction(mock_db, sample_payload)

        mock_publish.assert_called_once_with(sample_transaction)


class TestGetTransaction:
    def test_returns_transaction_when_found(self, mock_db, sample_transaction):
        mock_db.query.return_value.filter.return_value.first.return_value = sample_transaction
        result = get_transaction(mock_db, sample_transaction.id)
        assert result == sample_transaction

    def test_returns_none_when_not_found(self, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None
        result = get_transaction(mock_db, uuid.uuid4())
        assert result is None


class TestApplyFraudVerdict:
    def test_pass_verdict_sets_completed(self, mock_db, sample_transaction):
        mock_db.query.return_value.filter.return_value.first.return_value = sample_transaction
        update = TransactionStatusUpdate(
            transaction_id=sample_transaction.id,
            fraud_score=12.0,
            fraud_verdict="PASS",
        )
        result = apply_fraud_verdict(mock_db, update)
        assert result.status == TransactionStatus.COMPLETED
        assert result.fraud_score == 12.0

    def test_block_verdict_sets_blocked(self, mock_db, sample_transaction):
        mock_db.query.return_value.filter.return_value.first.return_value = sample_transaction
        update = TransactionStatusUpdate(
            transaction_id=sample_transaction.id,
            fraud_score=85.0,
            fraud_verdict="BLOCK",
        )
        result = apply_fraud_verdict(mock_db, update)
        assert result.status == TransactionStatus.BLOCKED

    def test_review_verdict_sets_flagged(self, mock_db, sample_transaction):
        mock_db.query.return_value.filter.return_value.first.return_value = sample_transaction
        update = TransactionStatusUpdate(
            transaction_id=sample_transaction.id,
            fraud_score=45.0,
            fraud_verdict="REVIEW",
        )
        result = apply_fraud_verdict(mock_db, update)
        assert result.status == TransactionStatus.FLAGGED

    def test_returns_none_for_unknown_transaction(self, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None
        update = TransactionStatusUpdate(
            transaction_id=uuid.uuid4(),
            fraud_score=20.0,
            fraud_verdict="PASS",
        )
        result = apply_fraud_verdict(mock_db, update)
        assert result is None
