"""Unit tests for KafkaSource config and offset commits."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from aiokafka import TopicPartition
from aiokafka.errors import CommitFailedError

from connectors.kafka.source import KafkaSource


def _config(**source):
    base = {"cluster": "main", "bootstrap_servers": "localhost:9092", "topics": ["orders"]}
    return {"source": {**base, **source}}


def test_cluster_is_required():
    with pytest.raises(ValueError, match="source.cluster"):
        KafkaSource(_config(cluster=""))


def test_topics_are_required():
    with pytest.raises(ValueError, match="source.topics"):
        KafkaSource(_config(topics=[]))


def test_topics_accept_a_comma_separated_string():
    assert KafkaSource(_config(topics="orders, customers"))._topics == ["orders", "customers"]


def test_group_id_defaults_from_cluster():
    assert KafkaSource(_config())._group_id == "contex-main"


def test_unkeyed_must_be_publish_or_skip():
    with pytest.raises(ValueError, match="unkeyed"):
        KafkaSource(_config(unkeyed="maybe"))


def test_auto_offset_reset_must_be_earliest_or_latest():
    with pytest.raises(ValueError, match="auto_offset_reset"):
        KafkaSource(_config(auto_offset_reset="middle"))


def test_ack_commits_next_offset_per_partition():
    source = KafkaSource(_config())
    source._consumer = AsyncMock()
    p0, p1 = TopicPartition("orders", 0), TopicPartition("orders", 1)
    asyncio.run(source.ack([(p0, 4), (p1, 9), (p0, 5)]))
    source._consumer.commit.assert_awaited_once_with({p0: 6, p1: 10})


def test_ack_survives_commit_failure():
    source = KafkaSource(_config())
    source._consumer = AsyncMock()
    source._consumer.commit.side_effect = CommitFailedError("rebalanced")
    asyncio.run(source.ack([(TopicPartition("orders", 0), 1)]))
