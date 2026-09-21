"""CLI entrypoint: ``python -m connectors.atlassian --config connector.yaml``.

Bulk-loads Jira issues and/or Confluence pages into a Contex project. Pass
``--dry-run N`` to read from the source and print the first N events per
resource without publishing (no Contex instance required).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from connectors.base import ContexConfig, load_config, resolve_batch_size, run_connector

from .client import AtlassianClient
from .readers import read_confluence_pages, read_jira_issues


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m connectors.atlassian",
        description="Bulk-load Jira issues and Confluence pages into a Contex project.",
    )
    p.add_argument("--config", default="connector.yaml", help="Path to connector.yaml")
    p.add_argument(
        "--dry-run",
        type=int,
        metavar="N",
        default=0,
        help="Read and print the first N events per resource; do not publish.",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return p


def _reader_for(resource: str, client: AtlassianClient, source: dict):
    """Build the async event generator for one resource, or None if unknown."""
    site_url = source.get("site_url", "")
    if resource == "jira":
        jira = source.get("jira") or {}
        return read_jira_issues(
            client,
            site_url=site_url,
            projects=jira.get("projects"),
            extra_jql=jira.get("jql"),
            since=jira.get("since"),
            include_comments=bool(jira.get("include_comments", True)),
        )
    if resource == "confluence":
        conf = source.get("confluence") or {}
        return read_confluence_pages(
            client,
            site_url=site_url,
            spaces=conf.get("spaces"),
            include_personal=bool(conf.get("include_personal", False)),
            include_comments=bool(conf.get("include_comments", True)),
            since=conf.get("since"),
        )
    logging.warning("unknown resource %r — skipping", resource)
    return None


async def _dry_run(client: AtlassianClient, source: dict, resources: list[str], limit: int) -> None:
    for resource in resources:
        events = _reader_for(resource, client, source)
        if events is None:
            continue
        logging.info("dry-run %s (first %d):", resource, limit)
        shown = 0
        async for event in events:
            preview = event.payload
            if isinstance(preview, dict):
                preview = {k: preview[k] for k in list(preview)[:4] if k in preview}
            print(f"  {event.key}\n    {json.dumps(preview, default=str)[:280]}")
            shown += 1
            if shown >= limit:
                break


async def _run(config_path: str, dry_run: int) -> None:
    config = load_config(config_path)
    source = config.get("source") or {}
    resources = source.get("resources") or ["jira", "confluence"]

    client = AtlassianClient(
        source.get("site_url", ""),
        source.get("email", ""),
        source.get("token", ""),
    )
    async with client:
        if dry_run:
            await _dry_run(client, source, resources, dry_run)
            return

        contex_cfg = ContexConfig.from_dict(config)
        batch_size = resolve_batch_size(config)
        total = 0
        for resource in resources:
            events = _reader_for(resource, client, source)
            if events is None:
                continue
            logging.info("starting %s", resource)

            def _progress(n: int, _r: str = resource) -> None:
                logging.info("  %s: %d published", _r, n)

            stats = await run_connector(
                contex_cfg, events, batch_size=batch_size, progress=_progress
            )
            total += stats.published
            logging.info("finished %s: %d items in %d batches", resource, stats.published, stats.batches)
        logging.info("done — total published: %d", total)


def main() -> None:
    args = _build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        asyncio.run(_run(args.config, args.dry_run))
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001 - top-level CLI guard
        logging.error("connector failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
