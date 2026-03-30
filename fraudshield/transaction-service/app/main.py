import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db.session import engine, Base
from app.routers import transactions
from app.kafka.consumer import start_consumer, stop_consumer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create DB tables, kick off Kafka consumer
    logger.info("Starting Transaction Service...")
    Base.metadata.create_all(bind=engine)
    await start_consumer()
    yield
    # Shutdown: gracefully stop Kafka consumer
    logger.info("Shutting down Transaction Service...")
    await stop_consumer()


app = FastAPI(
    title="FraudShield — Transaction Service",
    description=(
        "Handles payment ingestion, persists transactions, publishes "
        "TransactionInitiated events to Kafka, and listens for FraudVerdict "
        "events to update transaction status."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(transactions.router, prefix="/transactions", tags=["Transactions"])


@app.get("/health", tags=["Health"])
def health_check():
    return {"service": "transaction-service", "status": "healthy"}
