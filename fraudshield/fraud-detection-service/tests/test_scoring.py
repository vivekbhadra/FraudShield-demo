import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.schemas.fraud import TransactionEvent, RuleBreakdown
from app.scoring import engine
from app.scoring.rules import amount, velocity, new_merchant, off_hours, blacklist


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_event(**kwargs) -> TransactionEvent:
    defaults = {
        "transaction_id": "txn-001",
        "user_id": "user-abc",
        "merchant_id": "merchant-xyz",
        "amount": 1000.0,
        "currency": "INR",
        "created_at": datetime(2024, 6, 15, 14, 0, 0, tzinfo=timezone.utc),
    }
    return TransactionEvent(**{**defaults, **kwargs})


# ── Amount Rule Tests ─────────────────────────────────────────────────────────

class TestAmountRule:
    @pytest.mark.asyncio
    @patch("app.scoring.rules.amount.get_user_avg_spend", new_callable=AsyncMock)
    async def test_triggers_when_amount_exceeds_3x_average(self, mock_avg):
        mock_avg.return_value = 1000.0
        score = await amount.evaluate("user-abc", 4000.0)
        assert score == 35.0

    @pytest.mark.asyncio
    @patch("app.scoring.rules.amount.get_user_avg_spend", new_callable=AsyncMock)
    async def test_no_trigger_within_normal_range(self, mock_avg):
        mock_avg.return_value = 1000.0
        score = await amount.evaluate("user-abc", 2500.0)
        assert score == 0.0

    @pytest.mark.asyncio
    @patch("app.scoring.rules.amount.get_user_avg_spend", new_callable=AsyncMock)
    async def test_no_trigger_with_no_history(self, mock_avg):
        mock_avg.return_value = None
        score = await amount.evaluate("user-abc", 99999.0)
        assert score == 0.0

    @pytest.mark.asyncio
    @patch("app.scoring.rules.amount.get_user_avg_spend", new_callable=AsyncMock)
    async def test_no_trigger_below_minimum_flaggable_amount(self, mock_avg):
        mock_avg.return_value = 100.0
        # 400 is 4x average but below the 500 floor
        score = await amount.evaluate("user-abc", 400.0)
        assert score == 0.0


# ── Velocity Rule Tests ───────────────────────────────────────────────────────

class TestVelocityRule:
    @pytest.mark.asyncio
    @patch("app.scoring.rules.velocity.get_velocity", new_callable=AsyncMock)
    async def test_triggers_above_limit(self, mock_velocity):
        mock_velocity.return_value = 6
        score = await velocity.evaluate("user-abc")
        assert score == 40.0

    @pytest.mark.asyncio
    @patch("app.scoring.rules.velocity.get_velocity", new_callable=AsyncMock)
    async def test_no_trigger_at_limit(self, mock_velocity):
        mock_velocity.return_value = 5
        score = await velocity.evaluate("user-abc")
        assert score == 0.0

    @pytest.mark.asyncio
    @patch("app.scoring.rules.velocity.get_velocity", new_callable=AsyncMock)
    async def test_no_trigger_below_limit(self, mock_velocity):
        mock_velocity.return_value = 2
        score = await velocity.evaluate("user-abc")
        assert score == 0.0


# ── New Merchant Rule Tests ───────────────────────────────────────────────────

class TestNewMerchantRule:
    @pytest.mark.asyncio
    @patch("app.scoring.rules.new_merchant.has_visited_merchant", new_callable=AsyncMock)
    async def test_triggers_for_new_merchant(self, mock_visited):
        mock_visited.return_value = False
        score = await new_merchant.evaluate("user-abc", "new-merchant")
        assert score == 15.0

    @pytest.mark.asyncio
    @patch("app.scoring.rules.new_merchant.has_visited_merchant", new_callable=AsyncMock)
    async def test_no_trigger_for_known_merchant(self, mock_visited):
        mock_visited.return_value = True
        score = await new_merchant.evaluate("user-abc", "known-merchant")
        assert score == 0.0


# ── Off-Hours Rule Tests ──────────────────────────────────────────────────────

class TestOffHoursRule:
    @pytest.mark.asyncio
    async def test_triggers_at_3am_utc(self):
        ts = datetime(2024, 6, 15, 3, 0, 0, tzinfo=timezone.utc)
        score = await off_hours.evaluate(ts)
        assert score == 10.0

    @pytest.mark.asyncio
    async def test_no_trigger_at_noon(self):
        ts = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        score = await off_hours.evaluate(ts)
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_boundary_at_1am(self):
        ts = datetime(2024, 6, 15, 1, 0, 0, tzinfo=timezone.utc)
        score = await off_hours.evaluate(ts)
        assert score == 10.0

    @pytest.mark.asyncio
    async def test_no_trigger_at_5am_boundary(self):
        ts = datetime(2024, 6, 15, 5, 0, 0, tzinfo=timezone.utc)
        score = await off_hours.evaluate(ts)
        assert score == 0.0


# ── Blacklist Rule Tests ──────────────────────────────────────────────────────

class TestBlacklistRule:
    @pytest.mark.asyncio
    @patch("app.scoring.rules.blacklist.is_merchant_blacklisted", new_callable=AsyncMock)
    async def test_triggers_for_blacklisted_merchant(self, mock_bl):
        mock_bl.return_value = True
        score = await blacklist.evaluate("evil-merchant")
        assert score == 100.0

    @pytest.mark.asyncio
    @patch("app.scoring.rules.blacklist.is_merchant_blacklisted", new_callable=AsyncMock)
    async def test_no_trigger_for_clean_merchant(self, mock_bl):
        mock_bl.return_value = False
        score = await blacklist.evaluate("clean-merchant")
        assert score == 0.0


# ── Verdict Threshold Tests ───────────────────────────────────────────────────

class TestVerdictThresholds:
    def test_pass_verdict_below_30(self):
        assert engine._determine_verdict(0.0) == "PASS"
        assert engine._determine_verdict(29.9) == "PASS"

    def test_review_verdict_between_30_and_70(self):
        assert engine._determine_verdict(30.0) == "REVIEW"
        assert engine._determine_verdict(50.0) == "REVIEW"
        assert engine._determine_verdict(69.9) == "REVIEW"

    def test_block_verdict_at_70_and_above(self):
        assert engine._determine_verdict(70.0) == "BLOCK"
        assert engine._determine_verdict(100.0) == "BLOCK"


# ── Integration: Full Scoring Engine ─────────────────────────────────────────

class TestScoringEngine:
    @pytest.mark.asyncio
    @patch("app.scoring.engine.blacklist.evaluate", new_callable=AsyncMock, return_value=0.0)
    @patch("app.scoring.engine.amount.evaluate", new_callable=AsyncMock, return_value=0.0)
    @patch("app.scoring.engine.velocity.evaluate", new_callable=AsyncMock, return_value=0.0)
    @patch("app.scoring.engine.new_merchant.evaluate", new_callable=AsyncMock, return_value=0.0)
    @patch("app.scoring.engine.off_hours.evaluate", new_callable=AsyncMock, return_value=0.0)
    @patch("app.scoring.engine.increment_velocity", new_callable=AsyncMock)
    @patch("app.scoring.engine.update_user_avg_spend", new_callable=AsyncMock)
    @patch("app.scoring.engine.record_merchant_visit", new_callable=AsyncMock)
    async def test_clean_transaction_gets_pass(self, *mocks):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        event = make_event()
        result = await engine.score_transaction(event, mock_db)
        assert result.verdict == "PASS"
        assert result.total_score == 0.0

    @pytest.mark.asyncio
    @patch("app.scoring.engine.blacklist.evaluate", new_callable=AsyncMock, return_value=100.0)
    @patch("app.scoring.engine.increment_velocity", new_callable=AsyncMock)
    @patch("app.scoring.engine.update_user_avg_spend", new_callable=AsyncMock)
    @patch("app.scoring.engine.record_merchant_visit", new_callable=AsyncMock)
    async def test_blacklisted_merchant_short_circuits_to_block(self, *mocks):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        event = make_event(merchant_id="evil-merchant")
        result = await engine.score_transaction(event, mock_db)
        assert result.verdict == "BLOCK"
        assert result.total_score == 100.0

    @pytest.mark.asyncio
    @patch("app.scoring.engine.blacklist.evaluate", new_callable=AsyncMock, return_value=0.0)
    @patch("app.scoring.engine.amount.evaluate", new_callable=AsyncMock, return_value=35.0)
    @patch("app.scoring.engine.velocity.evaluate", new_callable=AsyncMock, return_value=40.0)
    @patch("app.scoring.engine.new_merchant.evaluate", new_callable=AsyncMock, return_value=0.0)
    @patch("app.scoring.engine.off_hours.evaluate", new_callable=AsyncMock, return_value=0.0)
    @patch("app.scoring.engine.increment_velocity", new_callable=AsyncMock)
    @patch("app.scoring.engine.update_user_avg_spend", new_callable=AsyncMock)
    @patch("app.scoring.engine.record_merchant_visit", new_callable=AsyncMock)
    async def test_high_amount_plus_velocity_triggers_block(self, *mocks):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        event = make_event(amount=5000.0)
        result = await engine.score_transaction(event, mock_db)
        assert result.verdict == "BLOCK"
        assert result.total_score == 75.0
