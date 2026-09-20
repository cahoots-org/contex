"""CLI entry point: python -m connectors.postgres --config connector.yaml"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from connectors.base import ContexConfig, load_config, resolve_batch_size, run_connector

from .reader import read_tables


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m connectors.postgres",
        description="Bulk-load Postgres tables into a Contex project.",
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


def _read_events(config: dict):
    source = config.get("source") or {}
    dsn = source.get("dsn")
    if not dsn:
        raise ValueError("source.dsn is required")

    tables_cfg = config.get("tables") or {}
    columns_cfg = config.get("columns") or {}
    key_columns = config.get("key_columns") or {}
    include_binary = bool(config.get("include_binary", False))

    return read_tables(
        dsn=dsn,
        tbl_include=tables_cfg.get("include") or None,
        tbl_exclude=tables_cfg.get("exclude") or None,
        col_include=columns_cfg.get("include") or None,
        col_exclude=columns_cfg.get("exclude") or None,
        key_columns=key_columns,
        include_binary=include_binary,
    )


async def _main(config_path: str) -> None:
    logger = logging.getLogger(__name__)
    config = load_config(config_path)
    contex_config = ContexConfig.from_dict(config)
    batch_size = resolve_batch_size(config)

    events = _read_events(config)

    def _progress(published: int) -> None:
        logger.info("published %d rows so far …", published)

    logger.info("starting Postgres connector — project=%s batch_size=%d", contex_config.project_id, batch_size)
    stats = await run_connector(contex_config, events, batch_size=batch_size, progress=_progress)
    logger.info("done — published %d rows in %d batch(es)", stats.published, stats.batches)


def main() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()
    _configure_logging(args.log_level)
    asyncio.run(_main(args.config))


if __name__ == "__main__":
    main()
