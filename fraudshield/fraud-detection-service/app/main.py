import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db.session import engine, Base, SessionLocal
from app.cache.redis_client import init_redis, close_redis, seed_blacklist
from app.kafka.consumer import start_consumer, stop_consumer
from app.models.fraud import MerchantBlacklist

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Fraud Detection Service...")

    # Create DB tables
    Base.metadata.create_all(bind=engine)

    # Warm up Redis connection pool
    await init_redis()

    # ── Seed blacklist: Postgres → Redis ──────────────────────────────────
    # Runs on every pod start so Redis always reflects the DB truth,
    # even after a crash, OOM kill, or rolling restart wipes the cache.
    try:
        db = SessionLocal()
        rows = (
            db.query(MerchantBlacklist.merchant_id)
            .filter(MerchantBlacklist.is_active == True)
            .all()
        )
        db.close()
        merchant_ids = [row.merchant_id for row in rows]
        await seed_blacklist(merchant_ids)
        logger.info(f"Blacklist sync complete: {len(merchant_ids)} active merchants.")
    except Exception as e:
        logger.error(f"Blacklist seed failed on startup: {e}", exc_info=True)
    # ─────────────────────────────────────────────────────────────────────

    # Start Kafka consumer loop
    await start_consumer()

    yield

    logger.info("Shutting down Fraud Detection Service...")
    await stop_consumer()
    await close_redis()


app = FastAPI(
    title="FraudShield — Fraud Detection Service",
    description=(
        "Consumes TransactionInitiated events from Kafka, runs a "
        "multi-rule weighted scoring engine backed by Redis, and publishes "
        "FraudVerdict events (PASS / REVIEW / BLOCK) back to Kafka."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health", tags=["Health"])
async def health_check():
    return {"service": "fraud-detection-service", "status": "healthy"}


@app.get("/health/deep", tags=["Health"])
async def deep_health_check():
    """
    Checks connectivity to Redis and the DB — useful for
    Kubernetes readiness probes that need more than a 200 OK.
    """
    from app.cache.redis_client import get_redis
    from app.db.session import SessionLocal

    checks = {}

    # Redis ping
    try:
        redis = await get_redis()
        await redis.ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"

    # DB ping
    try:
        db = SessionLocal()
        db.execute("SELECT 1")
        db.close()
        checks["postgres"] = "ok"
    except Exception as e:
        checks["postgres"] = f"error: {e}"

    all_ok = all(v == "ok" for v in checks.values())
    return {
        "service": "fraud-detection-service",
        "status": "healthy" if all_ok else "degraded",
        **checks,
    }