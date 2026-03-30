import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db.session import engine, Base
from app.cache.redis_client import init_redis, close_redis
from app.kafka.consumer import start_consumer, stop_consumer

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
    return {"service": "fraud-detection-service", "status": "healthy" if all_ok else "degraded", **checks}
