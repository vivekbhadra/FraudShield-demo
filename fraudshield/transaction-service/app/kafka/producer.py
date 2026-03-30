import json
import logging
import os
from datetime import timezone

from confluent_kafka import Producer

logger = logging.getLogger(__name__)

KAFKA_BROKER = os.getenv("KAFKA_BROKER", "localhost:9092")
TOPIC_TRANSACTION_INITIATED = os.getenv("TOPIC_TRANSACTION_INITIATED", "transactions.initiated")

_producer: Producer | None = None


def _get_producer() -> Producer:
    global _producer
    if _producer is None:
        _producer = Producer(
            {
                "bootstrap.servers": KAFKA_BROKER,
                "client.id": "transaction-service-producer",
                # Ensure messages are not lost on broker leader failover
                "acks": "all",
                "retries": 5,
                "retry.backoff.ms": 300,
            }
        )
    return _producer


def _delivery_report(err, msg):
    if err:
        logger.error(f"Kafka delivery failed for topic={msg.topic()}: {err}")
    else:
        logger.debug(f"Delivered to {msg.topic()} [{msg.partition()}] offset={msg.offset()}")


def publish_transaction_initiated(transaction) -> None:
    """
    Serialise the transaction and publish it to the transactions.initiated topic.
    Uses the transaction ID as the message key so that all events for the same
    transaction land on the same partition (ordering guarantee).
    """
    producer = _get_producer()

    payload = {
        "transaction_id": str(transaction.id),
        "user_id": transaction.user_id,
        "merchant_id": transaction.merchant_id,
        "amount": transaction.amount,
        "currency": transaction.currency,
        "created_at": transaction.created_at.astimezone(timezone.utc).isoformat(),
    }

    try:
        producer.produce(
            topic=TOPIC_TRANSACTION_INITIATED,
            key=str(transaction.id),
            value=json.dumps(payload),
            callback=_delivery_report,
        )
        # poll(0) triggers delivery callbacks without blocking
        producer.poll(0)
        logger.info(f"Published TransactionInitiated event for {transaction.id}")
    except Exception as exc:
        logger.error(f"Failed to publish TransactionInitiated event: {exc}", exc_info=True)
