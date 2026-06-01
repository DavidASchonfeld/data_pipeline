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


def _offset_and_metadata(offset: int):
    """Build an OffsetAndMetadata across kafka-python versions (newer releases added a leader_epoch field)."""
    from kafka import OffsetAndMetadata  # deferred: only needed when anchoring an offset
    try:
        return OffsetAndMetadata(offset, "", -1)  # newer kafka-python requires (offset, metadata, leader_epoch)
    except TypeError:
        return OffsetAndMetadata(offset, "")  # older kafka-python only takes (offset, metadata)


def ensure_group_bookmark(topic: str, group_id: str) -> None:
    """Guarantee this consumer group has a committed offset (a "bookmark") before it reads.

    A brand-new group — e.g. after a fresh provision or spot replacement wipes Kafka and the Airflow
    metadata DB — has no committed offset. With auto_offset_reset="latest" the consumer would then seek
    *past* every message (the producer publishes before triggering the consumer), read nothing, commit
    nothing, and the empty group gets pruned — so the pipeline can never bootstrap on its own and stalls
    silently. Here the consumer plants its own bookmark: if no committed offset exists yet, it anchors one
    just before the newest message already in the topic, so the very message that triggered this run is
    the one it reads. This is a no-op once a bookmark exists, so it is safe to call on every run.
    """
    from kafka import TopicPartition  # deferred: only needed on the consume path
    import time  # deferred: brief retry while topic metadata propagates after a Kafka redeploy

    bootstrap: str = Variable.get("KAFKA_BOOTSTRAP_SERVERS")  # same broker address the consumer uses
    # Manual-assignment probe (not subscribe) so we can inspect and commit offsets without joining the group.
    probe = KafkaConsumer(bootstrap_servers=bootstrap, group_id=group_id, enable_auto_commit=False)
    try:
        parts = None
        for _ in range(10):  # the topic may not be registered yet in the seconds after a Kafka redeploy
            parts = probe.partitions_for_topic(topic)
            if parts:
                break
            time.sleep(1)
        if not parts:
            return  # topic has no partitions yet — nothing to anchor; this run simply reads 0

        tps = [TopicPartition(topic, p) for p in parts]
        probe.assign(tps)  # assign so we can read committed offsets and commit new ones
        if all(probe.committed(tp) is not None for tp in tps):
            return  # a bookmark already exists for every partition — leave it untouched

        # Fresh group: anchor each partition just before its newest message (clamped to the topic start),
        # so the triggering message is read now rather than skipped, while avoiding the old corrupt
        # messages that can sit near offset 0.
        beginning = probe.beginning_offsets(tps)
        end = probe.end_offsets(tps)
        probe.commit({tp: _offset_and_metadata(max(beginning[tp], end[tp] - 1)) for tp in tps})
    finally:
        probe.close()  # always release the broker connection
