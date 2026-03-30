import asyncio
import json
import logging
import os

from confluent_kafka import Consumer, KafkaError, KafkaException

from app.db.session import SessionLocal
from app.schemas.transaction import TransactionStatusUpdate
from app.services.transaction_svc import apply_fraud_verdict

logger = logging.getLogger(__name__)

KAFKA_BROKER = os.getenv("KAFKA_BROKER", "localhost:9092")
TOPIC_FRAUD_VERDICT = os.getenv("TOPIC_FRAUD_VERDICT", "fraud.verdict")
CONSUMER_GROUP = "transaction-service-group"

_consumer_task: asyncio.Task | None = None
_running = False


def _build_consumer() -> Consumer:
    return Consumer({
        "bootstrap.servers": KAFKA_BROKER,
        "group.id": CONSUMER_GROUP,
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
        "session.timeout.ms": 10000,
        "topic.metadata.refresh.interval.ms": 5000,
    })


async def _consume_loop():
    global _running

    while _running:
        consumer = None
        try:
            consumer = _build_consumer()
            consumer.subscribe([TOPIC_FRAUD_VERDICT])
            logger.info(f"Subscribed to '{TOPIC_FRAUD_VERDICT}'")

            while _running:
                msg = consumer.poll(timeout=1.0)

                if msg is None:
                    await asyncio.sleep(0.05)
                    continue

                if msg.error():
                    code = msg.error().code()

                    if code == KafkaError._PARTITION_EOF:
                        continue

                    # Topic not yet created — Kafka auto-create takes a moment
                    if code == KafkaError.UNKNOWN_TOPIC_OR_PART:
                        logger.warning("Topic not ready yet, waiting 3s...")
                        await asyncio.sleep(3)
                        continue

                    logger.error(f"Kafka error: {msg.error()}")
                    continue

                try:
                    data = json.loads(msg.value().decode("utf-8"))
                    logger.info(f"Received FraudVerdict: txn={data.get('transaction_id')}")

                    update = TransactionStatusUpdate(
                        transaction_id=data["transaction_id"],
                        fraud_score=data["fraud_score"],
                        fraud_verdict=data["verdict"],
                    )

                    db = SessionLocal()
                    try:
                        apply_fraud_verdict(db, update)
                    finally:
                        db.close()

                    consumer.commit(asynchronous=False)

                except Exception as exc:
                    logger.error(f"Error processing message: {exc}", exc_info=True)

        except KafkaException as exc:
            logger.error(f"Kafka connection error: {exc}. Retrying in 5s...")
            await asyncio.sleep(5)

        except asyncio.CancelledError:
            logger.info("Consumer task cancelled.")
            break

        except Exception as exc:
            logger.error(f"Unexpected error: {exc}. Retrying in 5s...", exc_info=True)
            await asyncio.sleep(5)

        finally:
            if consumer:
                try:
                    consumer.close()
                except Exception:
                    pass


async def start_consumer():
    global _running, _consumer_task
    _running = True
    _consumer_task = asyncio.create_task(_consume_loop())
    logger.info("Transaction Service Kafka consumer started.")


async def stop_consumer():
    global _running, _consumer_task
    _running = False
    if _consumer_task:
        _consumer_task.cancel()
        try:
            await _consumer_task
        except asyncio.CancelledError:
            pass
