"""CLI entry point: python -m connectors.kafka --config connector.yaml"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from connectors.base import (
    ContexConfig,
    load_config,
    resolve_batch_size,
    resolve_flush_interval,
    resolve_secret_scanner,
    run_stream_connector,
)

from .source import KafkaSource


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m connectors.kafka",
        description="Stream Kafka topics into a Contex project.",
    )
    parser.add_argument(
        "--config",
        default="connector.yaml",
        metavar="PATH",
        help="Path to connector.yaml (default: connector.yaml)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )
    return parser


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        stream=sys.stderr,
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )


async def _main(config_path: str) -> None:
    logger = logging.getLogger(__name__)
    # As PID 1 in a container, an unhandled SIGTERM is ignored. Cancelling lets
    # the consumer leave its group instead of waiting out the session timeout.
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, asyncio.current_task().cancel)
    config = load_config(config_path)
    contex_config = ContexConfig.from_dict(config)
    source = KafkaSource(config)

    def _progress(published: int) -> None:
        logger.info("published %d items", published)

    logger.info(
        "starting Kafka connector — cluster=%s topics=%s project=%s",
        source.cluster, ",".join(source.topics), contex_config.project_id,
    )
    async with source:
        await run_stream_connector(
            contex_config,
            source.events(),
            source.ack,
            origin=f"kafka:{source.cluster}",
            batch_size=resolve_batch_size(config),
            flush_interval=resolve_flush_interval(config),
            secret_scanner=resolve_secret_scanner(config),
            progress=_progress,
        )


def main() -> None:
    args = _build_arg_parser().parse_args()
    _configure_logging(args.log_level)
    try:
        asyncio.run(_main(args.config))
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass


if __name__ == "__main__":
    main()
