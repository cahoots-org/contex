"""Consume Kafka topics as a stream of ChangeEvents and commit what Contex accepted."""
from __future__ import annotations

import logging

from aiokafka import AIOKafkaConsumer, TopicPartition
from aiokafka.errors import KafkaError
from aiokafka.helpers import create_ssl_context

from .mapping import message_to_event

log = logging.getLogger(__name__)


class KafkaSource:
    """A consumer-group member whose offsets advance only through :meth:`ack`."""

    def __init__(self, config: dict):
        source = config.get("source") or {}
        self.cluster = source.get("cluster") or ""
        if not self.cluster:
            raise ValueError("source.cluster is required: it names this cluster in data keys")
        self._bootstrap = source.get("bootstrap_servers") or ""
        if not self._bootstrap:
            raise ValueError("source.bootstrap_servers is required")
        topics = source.get("topics") or []
        if isinstance(topics, str):
            topics = topics.split(",")
        self._topics = [t.strip() for t in topics if t.strip()]
        if not self._topics:
            raise ValueError("source.topics is required")
        self._group_id = source.get("group_id") or f"contex-{self.cluster}"
        self._unkeyed = source.get("unkeyed") or "publish"
        if self._unkeyed not in ("publish", "skip"):
            raise ValueError(f"source.unkeyed must be publish or skip, got {self._unkeyed!r}")
        self._offset_reset = source.get("auto_offset_reset") or "earliest"
        if self._offset_reset not in ("earliest", "latest"):
            raise ValueError(
                f"source.auto_offset_reset must be earliest or latest, got {self._offset_reset!r}"
            )
        self._security_protocol = source.get("security_protocol") or "PLAINTEXT"
        self._sasl_mechanism = source.get("sasl_mechanism") or "PLAIN"
        self._sasl_username = source.get("sasl_username") or None
        self._sasl_password = source.get("sasl_password") or None
        self._consumer: AIOKafkaConsumer | None = None

    async def __aenter__(self) -> "KafkaSource":
        ssl_context = create_ssl_context() if self._security_protocol.endswith("SSL") else None
        self._consumer = AIOKafkaConsumer(
            *self._topics,
            bootstrap_servers=self._bootstrap,
            group_id=self._group_id,
            enable_auto_commit=False,
            auto_offset_reset=self._offset_reset,
            security_protocol=self._security_protocol,
            sasl_mechanism=self._sasl_mechanism,
            sasl_plain_username=self._sasl_username,
            sasl_plain_password=self._sasl_password,
            ssl_context=ssl_context,
        )
        try:
            await self._consumer.start()
        except BaseException:
            await self._consumer.stop()
            self._consumer = None
            raise
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._consumer is not None:
            await self._consumer.stop()
        self._consumer = None

    async def events(self):
        """Yield ``(event or None, (TopicPartition, offset))`` for every message."""
        async for msg in self._consumer:
            position = (TopicPartition(msg.topic, msg.partition), msg.offset)
            yield message_to_event(self.cluster, msg, self._unkeyed), position

    async def ack(self, positions: list) -> None:
        """Commit past the highest acked offset on each partition.

        A failed commit (usually a rebalance) is logged, not raised: the
        partition's next owner redelivers from the last commit, and
        redelivered upserts and deletes are harmless.
        """
        offsets: dict[TopicPartition, int] = {}
        for partition, offset in positions:
            offsets[partition] = max(offsets.get(partition, 0), offset + 1)
        try:
            await self._consumer.commit(offsets)
        except KafkaError as exc:
            log.warning("offset commit failed, these messages may be redelivered: %s", exc)
