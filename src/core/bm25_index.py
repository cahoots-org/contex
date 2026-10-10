"""Index-time BM25 settings for the pg_search index on embeddings.

The tokenizer, k1 and b are baked into the index, so changing them means
rebuilding it. The settings an index was built with are kept in its comment;
at boot, the index is rebuilt only when the configured settings differ.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

from sqlalchemy import text

INDEX = "embeddings_bm25"
# Tokenizers that need no arguments beyond k1/b.
TOKENIZERS = frozenset({"unicode_words", "simple", "whitespace", "source_code", "icu"})
_TEXT_FIELDS = ("description", "data_original")
# Arbitrary constant so concurrent replicas don't rebuild at the same time.
_LOCK_KEY = 0x626D3235


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number, got {raw!r}")


@dataclass(frozen=True)
class Bm25Settings:
    tokenizer: str = "unicode_words"
    k1: float = 1.2
    b: float = 0.75

    def __post_init__(self) -> None:
        if self.tokenizer not in TOKENIZERS:
            raise ValueError(f"BM25_TOKENIZER must be one of {sorted(TOKENIZERS)}, got {self.tokenizer!r}")
        if not 0 <= self.k1 <= 100:
            raise ValueError(f"BM25_K1 must be between 0 and 100, got {self.k1}")
        if not 0 <= self.b <= 1:
            raise ValueError(f"BM25_B must be between 0 and 1, got {self.b}")

    @classmethod
    def from_env(cls) -> "Bm25Settings":
        return cls(
            tokenizer=(os.getenv("BM25_TOKENIZER") or "").strip() or cls.tokenizer,
            k1=_env_float("BM25_K1", cls.k1),
            b=_env_float("BM25_B", cls.b),
        )

    def create_sql(self) -> str:
        # Safe to inline: every value was validated above.
        fields = ", ".join(
            f"({field}::pdb.{self.tokenizer}('k1={self.k1}', 'b={self.b}'))" for field in _TEXT_FIELDS
        )
        return f"CREATE INDEX {INDEX} ON embeddings USING bm25 (id, {fields}, project_id) WITH (key_field = 'id')"


async def ensure_bm25_index(db, settings: Bm25Settings) -> bool:
    """Rebuild the BM25 index if it was built with other settings; True if rebuilt.

    An index with no recorded settings predates them and was built with the defaults.
    """
    async with db.session() as session:
        await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_KEY})
        comment = await session.scalar(text(f"SELECT obj_description('{INDEX}'::regclass, 'pg_class')"))
        built = Bm25Settings(**json.loads(comment)) if comment else Bm25Settings()
        if built == settings:
            return False
        # ponytail: blocking rebuild, writes wait for it; build concurrently if corpora get large.
        await session.execute(text(f"DROP INDEX {INDEX}"))
        await session.execute(text(settings.create_sql()))
        await session.execute(
            text(f"COMMENT ON INDEX {INDEX} IS {_quote(json.dumps(asdict(settings)))}")
        )
    return True


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
