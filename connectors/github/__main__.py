"""CLI entrypoint: ``python -m connectors.github --config connector.yaml``."""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from connectors.base import ContexConfig, load_config, resolve_batch_size, run_connector

from .client import GitHubClient
from .readers import read_commits, read_files, read_issues, read_pulls


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m connectors.github",
        description="Bulk-load GitHub repos into a Contex project.",
    )
    p.add_argument(
        "--config",
        default="connector.yaml",
        help="Path to connector.yaml (default: connector.yaml)",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return p


async def _run(config_path: str) -> None:
    config = load_config(config_path)
    contex_cfg = ContexConfig.from_dict(config)
    batch_size = resolve_batch_size(config)

    source = config.get("source") or {}
    token: str = source.get("token", "")
    repos: list[str] = source.get("repos") or []
    resources: list[str] = source.get("resources") or ["files", "issues", "pulls"]
    state: str = source.get("state", "all")
    include_binary: bool = bool(config.get("include_binary", False))

    paths_cfg = config.get("paths") or {}
    include_globs: list[str] | None = paths_cfg.get("include") or None
    exclude_globs: list[str] | None = paths_cfg.get("exclude") or None

    commits_cfg = config.get("commits") or {}
    commit_since: str | None = commits_cfg.get("since") or None
    commit_branch: str | None = commits_cfg.get("branch") or None

    total_published = 0

    async with GitHubClient(token) as client:
        for repo_slug in repos:
            if "/" not in repo_slug:
                logging.warning("skipping malformed repo slug %r (expected owner/repo)", repo_slug)
                continue
            owner, repo = repo_slug.split("/", 1)

            for resource in resources:
                if resource == "files":
                    events = read_files(
                        client,
                        owner,
                        repo,
                        include=include_globs,
                        exclude=exclude_globs,
                        include_binary=include_binary,
                    )
                elif resource == "issues":
                    events = read_issues(client, owner, repo, state=state)
                elif resource == "pulls":
                    events = read_pulls(client, owner, repo, state=state)
                elif resource == "commits":
                    events = read_commits(
                        client, owner, repo, since=commit_since, branch=commit_branch
                    )
                else:
                    logging.warning("unknown resource type %r — skipping", resource)
                    continue

                logging.info("starting %s/%s %s", owner, repo, resource)

                def _progress(n: int, _r: str = resource, _slug: str = repo_slug) -> None:
                    logging.info("  %s %s: %d published", _slug, _r, n)

                stats = await run_connector(
                    contex_cfg,
                    events,
                    batch_size=batch_size,
                    progress=_progress,
                )
                total_published += stats.published
                logging.info(
                    "finished %s/%s %s: %d items in %d batches",
                    owner,
                    repo,
                    resource,
                    stats.published,
                    stats.batches,
                )

    logging.info("done — total published: %d", total_published)


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        asyncio.run(_run(args.config))
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as exc:
        logging.error("connector failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
