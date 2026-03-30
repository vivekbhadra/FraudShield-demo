import logging
import os
from typing import Optional

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

_redis: Optional[aioredis.Redis] = None


async def init_redis() -> None:
    global _redis
    _redis = aioredis.from_url(
        REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
        max_connections=20,
    )
    await _redis.ping()
    logger.info("Redis connection pool initialised.")


async def close_redis() -> None:
    global _redis
    if _redis:
        await _redis.aclose()
        logger.info("Redis connection pool closed.")


async def get_redis() -> aioredis.Redis:
    if _redis is None:
        raise RuntimeError("Redis not initialised. Call init_redis() first.")
    return _redis


# ── Velocity helpers ──────────────────────────────────────────────────────────

VELOCITY_WINDOW_SECONDS = 600  # 10-minute sliding window
VELOCITY_KEY_PREFIX = "velocity:"


async def increment_velocity(user_id: str) -> int:
    """
    Increments a user's transaction counter within the 10-minute
    sliding window. Returns the updated count.
    Uses Redis INCR + EXPIRE so the key self-destructs after the window.
    """
    redis = await get_redis()
    key = f"{VELOCITY_KEY_PREFIX}{user_id}"
    count = await redis.incr(key)
    if count == 1:
        # First transaction in this window — set expiry
        await redis.expire(key, VELOCITY_WINDOW_SECONDS)
    return count


async def get_velocity(user_id: str) -> int:
    redis = await get_redis()
    key = f"{VELOCITY_KEY_PREFIX}{user_id}"
    val = await redis.get(key)
    return int(val) if val else 0


# ── Blacklist helpers ─────────────────────────────────────────────────────────

BLACKLIST_KEY = "merchant:blacklist"


async def is_merchant_blacklisted(merchant_id: str) -> bool:
    redis = await get_redis()
    return await redis.sismember(BLACKLIST_KEY, merchant_id)


async def add_to_blacklist(merchant_id: str) -> None:
    redis = await get_redis()
    await redis.sadd(BLACKLIST_KEY, merchant_id)


# ── User average spend helpers ────────────────────────────────────────────────

AVG_SPEND_KEY_PREFIX = "user:avg_spend:"


async def get_user_avg_spend(user_id: str) -> Optional[float]:
    redis = await get_redis()
    val = await redis.get(f"{AVG_SPEND_KEY_PREFIX}{user_id}")
    return float(val) if val else None


async def update_user_avg_spend(user_id: str, new_amount: float, count: int) -> None:
    """
    Incremental rolling average update — avoids storing the full
    transaction history in Redis. Formula: avg = avg + (new - avg) / count
    """
    redis = await get_redis()
    key = f"{AVG_SPEND_KEY_PREFIX}{user_id}"
    current = await redis.get(key)
    if current is None:
        new_avg = new_amount
    else:
        current_avg = float(current)
        new_avg = current_avg + (new_amount - current_avg) / max(count, 1)
    # Store with 30-day TTL — profiles expire if user is inactive
    await redis.set(key, str(new_avg), ex=60 * 60 * 24 * 30)


# ── Merchant visit helpers ────────────────────────────────────────────────────

MERCHANT_VISIT_KEY_PREFIX = "user:merchants:"


async def has_visited_merchant(user_id: str, merchant_id: str) -> bool:
    redis = await get_redis()
    key = f"{MERCHANT_VISIT_KEY_PREFIX}{user_id}"
    return await redis.sismember(key, merchant_id)


async def record_merchant_visit(user_id: str, merchant_id: str) -> None:
    redis = await get_redis()
    key = f"{MERCHANT_VISIT_KEY_PREFIX}{user_id}"
    await redis.sadd(key, merchant_id)
    await redis.expire(key, 60 * 60 * 24 * 90)  # 90-day memory
