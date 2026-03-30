import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Transactions between 01:00 and 05:00 UTC are considered off-hours
OFF_HOURS_START = 1   # 1am UTC
OFF_HOURS_END = 5     # 5am UTC
RISK_POINTS = 10.0


async def evaluate(created_at: datetime) -> float:
    """
    Returns RISK_POINTS if the transaction was initiated during
    the off-hours window (01:00–05:00 UTC).

    This is a low-weight signal — legitimate night-owl users exist.
    It's meant to compound with other rules (e.g. high amount + off-hours
    = much more suspicious than either alone).
    """
    # Normalise to UTC
    utc_time = created_at.astimezone(timezone.utc)
    hour = utc_time.hour

    if OFF_HOURS_START <= hour < OFF_HOURS_END:
        logger.info(
            f"Off-hours rule triggered: transaction at {utc_time.strftime('%H:%M')} UTC"
        )
        return RISK_POINTS

    return 0.0
