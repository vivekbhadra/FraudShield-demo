import logging
from app.cache.redis_client import get_user_avg_spend

logger = logging.getLogger(__name__)

# Transaction must exceed 3x the user's 30-day average to trigger
MULTIPLIER_THRESHOLD = 3.0
RISK_POINTS = 35.0

# Absolute floor — amounts below this are never flagged for being "high"
MINIMUM_FLAGGABLE_AMOUNT = 500.0


async def evaluate(user_id: str, amount: float) -> float:
    """
    Returns RISK_POINTS if the transaction amount is more than
    MULTIPLIER_THRESHOLD times the user's rolling 30-day average.

    Returns 0.0 if:
    - The user has no spend history (benefit of the doubt on first transaction)
    - The amount is below the minimum flaggable floor
    - The amount is within normal range
    """
    if amount < MINIMUM_FLAGGABLE_AMOUNT:
        return 0.0

    avg = await get_user_avg_spend(user_id)

    if avg is None:
        # No history — cannot determine if anomalous
        logger.debug(f"No spend history for user {user_id}, skipping high-amount rule.")
        return 0.0

    if avg == 0.0:
        return 0.0

    ratio = amount / avg
    if ratio > MULTIPLIER_THRESHOLD:
        logger.info(
            f"High amount rule triggered: user={user_id}, "
            f"amount={amount}, avg={avg:.2f}, ratio={ratio:.2f}x"
        )
        return RISK_POINTS

    return 0.0
