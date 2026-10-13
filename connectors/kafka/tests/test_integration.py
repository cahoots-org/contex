"""Integration tests against a live Kafka-compatible broker (Redpanda in CI).

Skipped unless ``KAFKA_TEST_BOOTSTRAP`` names a reachable broker. Locally:

    docker run -d --name redpanda -p 9092:9092 redpandadata/redpanda:v24.3.6 \
      redpanda start --mode dev-container --kafka-addr 0.0.0.0:9092 \
      --advertise-kafka-addr localhost:9092
    KAFKA_TEST_BOOTSTRAP=localhost:9092 pytest connectors/kafka -v
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from aiokafka import AIOKafkaProducer, TopicPartition
from aiokafka.admin import AIOKafkaAdminClient, NewTopic

from connectors.base import stream
from connectors.kafka.source import KafkaSource

BOOTSTRAP = os.environ.get("KAFKA_TEST_BOOTSTRAP")
pytestmark = pytest.mark.skipif(not BOOTSTRAP, reason="KAFKA_TEST_BOOTSTRAP not set")


class Recorder:
    def __init__(self):
        self.published: list[str] = []
        self.deleted: list[str] = []

    async def publish_batch(self, items):
        self.published += [item["data_key"] for item in items]
        return len(items)

    async def delete_batch(self, keys):
        self.deleted += keys
        return len(keys)


async def _topic() -> str:
    name = f"contex-test-{uuid.uuid4().hex[:8]}"
    admin = AIOKafkaAdminClient(bootstrap_servers=BOOTSTRAP)
    await admin.start()
    try:
        await admin.create_topics([NewTopic(name, num_partitions=1, replication_factor=1)])
    finally:
        await admin.close()
    return name


async def _produce(topic, messages):
    producer = AIOKafkaProducer(bootstrap_servers=BOOTSTRAP)
    await producer.start()
    try:
        for key, value in messages:
            await producer.send_and_wait(topic, key=key, value=value)
    finally:
        await producer.stop()


async def _stream_until(config, recorder, done, timeout=30.0):
    async with KafkaSource(config) as source:
        task = asyncio.create_task(
            stream(source.events(), recorder, source.ack, flush_interval=0.1)
        )
        try:
            async with asyncio.timeout(timeout):
                while not done():
                    await asyncio.sleep(0.1)
            await asyncio.sleep(0.3)  # let the timer flush ack what was published
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        committed = await source._consumer.committed(TopicPartition(config["source"]["topics"][0], 0))
    return committed


@pytest.mark.asyncio
async def test_streams_upserts_deletes_and_commits():
    topic = await _topic()
    await _produce(topic, [(b"a", b'{"v": 1}'), (b"b", b"text"), (b"a", None), (None, b"log line")])
    config = {"source": {"cluster": "t", "bootstrap_servers": BOOTSTRAP, "topics": [topic],
                         "group_id": f"g-{topic}"}}
    recorder = Recorder()
    committed = await _stream_until(config, recorder, lambda: len(recorder.published) == 3)
    assert recorder.published == [f"t:{topic}/a", f"t:{topic}/b", f"t:{topic}/0/3"]
    assert recorder.deleted == [f"t:{topic}/a"]
    assert committed == 4


@pytest.mark.asyncio
async def test_restart_resumes_after_the_last_commit():
    topic = await _topic()
    await _produce(topic, [(b"a", b"one")])
    config = {"source": {"cluster": "t", "bootstrap_servers": BOOTSTRAP, "topics": [topic],
                         "group_id": f"g-{topic}"}}
    first = Recorder()
    await _stream_until(config, first, lambda: first.published)
    await _produce(topic, [(b"b", b"two")])
    second = Recorder()
    await _stream_until(config, second, lambda: second.published)
    assert second.published == [f"t:{topic}/b"]
