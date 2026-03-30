import logging
from app.cache.redis_client import get_velocity

logger = logging.getLogger(__name__)

# More than this many transactions in 10 minutes triggers the rule
VELOCITY_LIMIT = 5
RISK_POINTS = 40.0


async def evaluate(user_id: str) -> float:
    """
    Returns RISK_POINTS if the user has exceeded VELOCITY_LIMIT
    transactions within the current 10-minute sliding window.

    The counter is maintained in Redis with a TTL — no cron job needed.
    Note: the counter is incremented by the scoring engine BEFORE
    calling this rule, so the count already includes the current transaction.
    """
    count = await get_velocity(user_id)

    if count > VELOCITY_LIMIT:
        logger.info(
            f"Velocity rule triggered: user={user_id}, "
            f"count={count} in 10-min window (limit={VELOCITY_LIMIT})"
        )
        return RISK_POINTS

    return 0.0
