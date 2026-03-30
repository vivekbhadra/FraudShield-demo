import logging
from app.cache.redis_client import is_merchant_blacklisted

logger = logging.getLogger(__name__)

# Score high enough to guarantee a BLOCK verdict on its own
RISK_POINTS = 100.0


async def evaluate(merchant_id: str) -> float:
    """
    Returns 100.0 (auto-block score) if the merchant is on the
    blacklist Redis set. This is a hard rule — a single blacklisted
    merchant match overrides all other scores and guarantees a BLOCK.

    The blacklist is loaded from PostgreSQL into Redis on service startup
    and can be updated at runtime without restarting the service.
    """
    blacklisted = await is_merchant_blacklisted(merchant_id)

    if blacklisted:
        logger.warning(
            f"BLACKLIST rule triggered: merchant={merchant_id} is blacklisted. "
            f"Auto-blocking transaction."
        )
        return RISK_POINTS

    return 0.0
