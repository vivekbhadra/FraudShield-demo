import asyncio
import json
import logging
import os

from confluent_kafka import Consumer, KafkaError, KafkaException

from app.db.session import SessionLocal
from app.schemas.fraud import TransactionEvent
from app.scoring.engine import score_transaction
from app.kafka.producer import publish_fraud_verdict

logger = logging.getLogger(__name__)

KAFKA_BROKER = os.getenv("KAFKA_BROKER", "localhost:9092")
TOPIC_TRANSACTION_INITIATED = os.getenv("TOPIC_TRANSACTION_INITIATED", "transactions.initiated")
CONSUMER_GROUP = "fraud-detection-service-group"

_consumer_task: asyncio.Task | None = None
_running = False


def _build_consumer() -> Consumer:
    return Consumer({
        "bootstrap.servers": KAFKA_BROKER,
        "group.id": CONSUMER_GROUP,
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
        "session.timeout.ms": 10000,
        "max.poll.interval.ms": 300000,
        "topic.metadata.refresh.interval.ms": 5000,
    })


async def _process_message(data: dict) -> None:
    event = TransactionEvent(**data)
    db = SessionLocal()
    try:
        result = await score_transaction(event, db)
        publish_fraud_verdict(result)
    finally:
        db.close()


async def _consume_loop():
    global _running

    while _running:
        consumer = None
        try:
            consumer = _build_consumer()
            consumer.subscribe([TOPIC_TRANSACTION_INITIATED])
            logger.info(f"Subscribed to '{TOPIC_TRANSACTION_INITIATED}'")

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
                    logger.info(f"Received TransactionInitiated: txn={data.get('transaction_id')}")

                    await _process_message(data)
                    consumer.commit(asynchronous=False)

                except Exception as exc:
                    logger.error(f"Error processing message: {exc}", exc_info=True)
                    # No commit — message will be reprocessed on restart

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
    logger.info("Fraud Detection Kafka consumer started.")


async def stop_consumer():
    global _running, _consumer_task
    _running = False
    if _consumer_task:
        _consumer_task.cancel()
        try:
            await _consumer_task
        except asyncio.CancelledError:
            pass
