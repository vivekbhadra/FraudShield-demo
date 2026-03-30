import logging
from app.cache.redis_client import has_visited_merchant

logger = logging.getLogger(__name__)

RISK_POINTS = 15.0


async def evaluate(user_id: str, merchant_id: str) -> float:
    """
    Returns RISK_POINTS if this is the first time the user has
    transacted with this merchant.

    First-time merchant visits are a known fraud signal — card-testing
    attacks often target new merchants. The score is low (15 pts) since
    it's expected behaviour for legitimate new purchases too; it's
    designed to stack with other rules rather than trigger alone.
    """
    visited = await has_visited_merchant(user_id, merchant_id)

    if not visited:
        logger.info(
            f"New merchant rule triggered: user={user_id}, "
            f"merchant={merchant_id} (first visit)"
        )
        return RISK_POINTS

    return 0.0
