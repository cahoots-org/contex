"""CLI entrypoint: python -m connectors.s3 --config connector.yaml"""
from __future__ import annotations

import argparse
import asyncio
import logging

from connectors.base import (
    ContexConfig,
    load_config,
    resolve_batch_size,
    resolve_prune,
    resolve_secret_scanner,
    run_connector,
)

from .reader import read_objects

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bulk-load S3 objects into Contex.")
    parser.add_argument("--config", default="connector.yaml", help="Path to connector.yaml")
    return parser.parse_args()


async def _main(config_path: str) -> None:
    config = load_config(config_path)
    contex_config = ContexConfig.from_dict(config)
    batch_size = resolve_batch_size(config)
    secret_scanner = resolve_secret_scanner(config)

    logger.info(
        "starting S3 import: bucket=%s prefix=%s",
        (config.get("source") or {}).get("bucket", "?"),
        (config.get("source") or {}).get("prefix", ""),
    )

    def progress(n: int) -> None:
        logger.info("published %d items", n)

    source = config.get("source") or {}
    stats = await run_connector(
        contex_config,
        read_objects(config),
        batch_size=batch_size,
        secret_scanner=secret_scanner,
        progress=progress,
        origin=f"s3:{source.get('bucket', '')}/{source.get('prefix', '')}",
        prune=resolve_prune(config),
    )

    logger.info("done: published=%d deleted=%d batches=%d", stats.published, stats.deleted, stats.batches)


def main() -> None:
    args = _parse_args()
    asyncio.run(_main(args.config))


if __name__ == "__main__":
    main()
