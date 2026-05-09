import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.schemas.fraud import TransactionEvent, ScoringResult, RuleBreakdown
from app.models.fraud import FraudScore, UserSpendProfile
from app.cache.redis_client import (
    increment_velocity,
    update_user_avg_spend,
    record_merchant_visit,
)
from app.scoring.rules import amount, velocity, new_merchant, off_hours, blacklist

logger = logging.getLogger(__name__)

# Verdict thresholds — tuned to minimise false positives
THRESHOLD_PASS = 30.0    # below 30  → PASS
THRESHOLD_REVIEW = 70.0  # 30–69     → REVIEW
                         # 70+       → BLOCK


def _determine_verdict(score: float) -> str:
    if score >= THRESHOLD_REVIEW:
        return "BLOCK"
    if score >= THRESHOLD_PASS:
        return "REVIEW"
    return "PASS"


async def score_transaction(event: TransactionEvent, db: Session) -> ScoringResult:
    """
    Central scoring orchestrator. Runs all rules concurrently where possible,
    aggregates scores, determines verdict, persists the result, and updates
    the user's rolling spend profile.

    Design note: rules are intentionally independent — no rule depends on
    the output of another. This makes them easy to add, remove, or retune
    without side effects.
    """
    logger.info(
        f"Scoring transaction {event.transaction_id} | "
        f"user={event.user_id} | amount={event.amount} {event.currency}"
    )

    # ── Step 1: Increment velocity counter BEFORE evaluating rules ────────────
    # This ensures the current transaction is counted in the velocity window
    await increment_velocity(event.user_id)

    # ── Step 2: Run all rules ─────────────────────────────────────────────────
    # Rules are awaited individually rather than gathered so that a failure
    # in one rule degrades gracefully without cancelling others.

    score_blacklist = 0.0
    score_high_amount = 0.0
    score_velocity_val = 0.0
    score_new_merchant = 0.0
    score_off_hours = 0.0

    try:
        score_blacklist = await blacklist.evaluate(event.merchant_id)
        logger.warning(f"DEBUG_BLACKLIST merchant={event.merchant_id} "
                        f"score_blacklist={score_blacklist}")
    except Exception as e:
        logger.error(f"Blacklist rule failed: {e}", exc_info=True)

    # Short-circuit: if blacklisted, no need to run remaining rules
    if score_blacklist >= 100.0:
        total_score = score_blacklist
        breakdown = RuleBreakdown(blacklist=score_blacklist)
        verdict = "BLOCK"
        logger.warning(
            f"Transaction {event.transaction_id} auto-blocked — blacklisted merchant."
        )
    else:
        try:
            score_high_amount = await amount.evaluate(event.user_id, event.amount)
        except Exception as e:
            logger.error(f"Amount rule failed: {e}", exc_info=True)

        try:
            score_velocity_val = await velocity.evaluate(event.user_id)
        except Exception as e:
            logger.error(f"Velocity rule failed: {e}", exc_info=True)

        try:
            score_new_merchant = await new_merchant.evaluate(event.user_id, event.merchant_id)
        except Exception as e:
            logger.error(f"New merchant rule failed: {e}", exc_info=True)

        try:
            score_off_hours = await off_hours.evaluate(event.created_at)
        except Exception as e:
            logger.error(f"Off-hours rule failed: {e}", exc_info=True)

        total_score = (
            score_high_amount
            + score_velocity_val
            + score_new_merchant
            + score_off_hours
        )

        breakdown = RuleBreakdown(
            high_amount=score_high_amount,
            velocity=score_velocity_val,
            new_merchant=score_new_merchant,
            off_hours=score_off_hours,
            blacklist=score_blacklist,
        )
        verdict = _determine_verdict(total_score)

    scored_at = datetime.now(timezone.utc)

    logger.info(
        f"Scored transaction {event.transaction_id}: "
        f"total={total_score} verdict={verdict} | "
        f"breakdown={breakdown.model_dump()}"
    )

    # ── Step 3: Persist scoring record ────────────────────────────────────────
    try:
        fraud_record = FraudScore(
            transaction_id=event.transaction_id,
            user_id=event.user_id,
            merchant_id=event.merchant_id,
            amount=event.amount,
            score_high_amount=score_high_amount,
            score_velocity=score_velocity_val,
            score_new_merchant=score_new_merchant,
            score_off_hours=score_off_hours,
            score_blacklist=score_blacklist,
            total_score=total_score,
            verdict=verdict,
            scored_at=scored_at,
        )
        db.add(fraud_record)

        # Update or create user spend profile
        profile = db.query(UserSpendProfile).filter(
            UserSpendProfile.user_id == event.user_id
        ).first()

        if profile is None:
            profile = UserSpendProfile(
                user_id=event.user_id,
                avg_transaction_amount=event.amount,
                transaction_count=1,
            )
            db.add(profile)
        else:
            profile.transaction_count += 1
            profile.avg_transaction_amount = (
                profile.avg_transaction_amount
                + (event.amount - profile.avg_transaction_amount)
                / profile.transaction_count
            )

        db.commit()

        # Update Redis avg spend cache after DB commit succeeds
        await update_user_avg_spend(
            event.user_id, event.amount, profile.transaction_count
        )
        # Record merchant visit
        await record_merchant_visit(event.user_id, event.merchant_id)

    except Exception as e:
        logger.error(f"Failed to persist fraud score: {e}", exc_info=True)
        db.rollback()
        logger.warning(
            f"DEBUG_FINAL txn={event.transaction_id} "
            f"score={total_score} verdict={verdict} "
            f"breakdown={breakdown.model_dump()}"
        )
    return ScoringResult(
        transaction_id=event.transaction_id,
        user_id=event.user_id,
        total_score=total_score,
        verdict=verdict,
        breakdown=breakdown,
        scored_at=scored_at,
    )
