import json
import logging
import os

from confluent_kafka import Producer

from app.schemas.fraud import ScoringResult

logger = logging.getLogger(__name__)

KAFKA_BROKER = os.getenv("KAFKA_BROKER", "localhost:9092")
TOPIC_FRAUD_VERDICT = os.getenv("TOPIC_FRAUD_VERDICT", "fraud.verdict")

_producer = None


def _get_producer() -> Producer:
    global _producer
    if _producer is None:
        _producer = Producer({
            "bootstrap.servers": KAFKA_BROKER,
            "client.id": "fraud-detection-service-producer",
            "acks": "all",
            "retries": 5,
            "retry.backoff.ms": 300,
        })
    return _producer


def _delivery_report(err, msg):
    if err:
        logger.error(f"Kafka delivery failed: topic={msg.topic()}, error={err}")
    else:
        logger.debug(
            f"FraudVerdict delivered: topic={msg.topic()} "
            f"partition=[{msg.partition()}] offset={msg.offset()}"
        )


def publish_fraud_verdict(result: ScoringResult) -> None:
    """
    Publishes a FraudVerdict event to the fraud.verdict Kafka topic.
    Uses transaction_id as the message key for partition affinity —
    the Transaction Service consumer will receive verdicts for the
    same transaction on the same partition in order.
    """
    producer = _get_producer()

    payload = {
        "transaction_id": result.transaction_id,
        "user_id": result.user_id,
        "fraud_score": result.total_score,
        "verdict": result.verdict,
        "breakdown": result.breakdown.model_dump(),
        "scored_at": result.scored_at.isoformat(),
    }

    try:
        producer.produce(
            topic=TOPIC_FRAUD_VERDICT,
            key=result.transaction_id,
            value=json.dumps(payload),
            callback=_delivery_report,
        )
        producer.poll(0)
        logger.info(
            f"Published FraudVerdict: txn={result.transaction_id} "
            f"verdict={result.verdict} score={result.total_score}"
        )
    except Exception as exc:
        logger.error(f"Failed to publish FraudVerdict: {exc}", exc_info=True)
