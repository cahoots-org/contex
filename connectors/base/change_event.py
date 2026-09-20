"""The seam every connector emits: a stream of ChangeEvents."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ChangeEvent:
    """A single change a connector's reader produces.

    v1 connectors only ever emit ``op="upsert"``; the shape leaves room for
    ``"delete"`` once CDC-style connectors exist. The runner maps each event to
    a ``contex_publish_batch`` item — ``key`` -> ``data_key``, ``payload`` ->
    ``data``, ``data_format`` -> ``data_format``.
    """

    op: str
    key: str
    payload: object
    source_meta: dict = field(default_factory=dict)
    data_format: str = "json"

    def to_item(self) -> dict:
        """Render this event as a contex_publish_batch item."""
        return {
            "data_key": self.key,
            "data": self.payload,
            "data_format": self.data_format,
        }
