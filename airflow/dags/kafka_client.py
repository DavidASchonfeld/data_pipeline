"""Shared Kafka client factories — single source of truth for producer/consumer config."""
import json  # needed for JSON serialization/deserialization of Kafka message payloads
from kafka import KafkaProducer, KafkaConsumer  # kafka-python, installed via _PIP_ADDITIONAL_REQUIREMENTS
from airflow.sdk import Variable  # Airflow Variable for runtime config lookup


def make_producer() -> KafkaProducer:
    """Return a configured KafkaProducer; resolves broker address at call time."""
    bootstrap: str = Variable.get("KAFKA_BOOTSTRAP_SERVERS")  # fetch broker address from Airflow Variable at runtime
    return KafkaProducer(
        bootstrap_servers=bootstrap,  # point producer at the cluster
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),  # serialize dicts to JSON bytes before sending
        max_block_ms=15000,  # fail fast if broker unreachable during send/flush
    )


def make_consumer(topic: str, group_id: str) -> KafkaConsumer:
    """Return a KafkaConsumer subscribed to topic with manual commit; resolves broker at call time."""
    bootstrap: str = Variable.get("KAFKA_BOOTSTRAP_SERVERS")  # fetch broker address from Airflow Variable at runtime
    return KafkaConsumer(
        topic,  # subscribe to the given topic
        bootstrap_servers=bootstrap,  # point consumer at the cluster
        group_id=group_id,  # consumer group for offset tracking per pipeline
        auto_offset_reset="latest",  # start from newest message if no prior offset exists
        enable_auto_commit=False,  # manual commit: we control when to advance the bookmark
        consumer_timeout_ms=30000,  # stop polling after 30s — message should arrive quickly after trigger
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),  # deserialize JSON bytes back to dicts
    )
