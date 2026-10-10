"""The seam every connector emits: a stream of ChangeEvents."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ChangeEvent:
    """A single change a connector's reader produces.

    ``op`` is ``"upsert"`` or ``"delete"``. The runner maps an upsert to a
    ``contex_publish_batch`` item (``key`` -> ``data_key``, ``payload`` ->
    ``data``, ``data_format`` -> ``data_format``) and sends a delete's ``key``
    to ``contex_delete``; its payload is ignored. ``published_at`` (ISO-8601)
    is when the source last changed the item, so time windows don't treat a
    backfill as fresh.
    """

    op: str
    key: str
    payload: object
    source_meta: dict = field(default_factory=dict)
    data_format: str = "json"
    published_at: str | None = None

    def to_item(self) -> dict:
        """Render this event as a contex_publish_batch item."""
        item = {
            "data_key": self.key,
            "data": self.payload,
            "data_format": self.data_format,
        }
        if self.published_at:
            item["published_at"] = self.published_at
        return item
