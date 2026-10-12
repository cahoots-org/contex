"""Semantic data matching using embeddings for agent context discovery"""

import asyncio
import hashlib
import os
from datetime import datetime
from typing import Any, Collection, Dict, List, Optional, Tuple

from sqlalchemy import Text, all_, any_, delete, func, literal, select, text, update
from sqlalchemy.dialects.postgresql import ARRAY, insert as pg_insert

from src.core.database import DatabaseManager
from src.core.db_models import Embedding, Symbol
from src.core.embedder import OnnxEmbedder, encode_async
from src.core.hybrid_search_service import HybridSearchService
from src.core.limits import positive_int_env
from src.core.lexical_search import PgFtsLexical
from src.core.logging import get_logger
from src.core.node_converter import NodeConverter
from src.core.node_parsers import DocumentRejected
from src.core.relevance import JevReranker
from src.core.vector_search import PgVectorSearch

logger = get_logger(__name__)

_MODEL_CACHE: dict[str, OnnxEmbedder] = {}


def _load_model(model_name: str) -> OnnxEmbedder:
    """Load an embedding model, cached per process by name.

    The model is stateless for inference, so one instance is shared across all
    matchers instead of re-loading the model (and re-checking the HuggingFace Hub) on
    every ContextEngine construction.
    """
    model = _MODEL_CACHE.get(model_name)
    if model is None:
        logger.info("Loading embedding model", model_name=model_name)
        model = OnnxEmbedder(model_name)
        _MODEL_CACHE[model_name] = model
    return model


_TITLE_FIELDS = ("title", "summary", "name")

# Columns a republished node overwrites.
_REWRITTEN_COLUMNS = (
    "data_key", "node_path", "node_type", "description", "data",
    "data_format", "content_hash", "embedding", "published_at",
)


def _document_title(nodes) -> str:
    """The root object's title-like field (a ticket summary, a PR or page title)."""
    root = next((n for n in nodes if n.path == ""), None)
    if root is None or not isinstance(root.content, dict):
        return ""
    return next(
        (root.content[f] for f in _TITLE_FIELDS if isinstance(root.content.get(f), str)), ""
    )


def _embedding_text(data_key: str, node, title: str = "") -> str:
    """Node text to embed and index, prefixed with its source key and document title.

    The prefix gives every chunk its provenance: a query naming the file or path
    matches it, and a child node (a ticket comment) matches its parent's topic.
    """
    header = " ".join(part for part in (data_key, title) if part)
    text = node.get_text_content()
    return f"{header}\n{text}" if header else text


async def _stored_hashes(session, project_id: str, node_keys: List[str]) -> Dict[str, str]:
    """Stored content hash per node_key, for those of node_keys that exist."""
    result = await session.execute(
        select(Embedding.node_key, Embedding.content_hash)
        .where(Embedding.project_id == project_id)
        .where(Embedding.node_key == any_(literal(node_keys, ARRAY(Text))))
    )
    return dict(result.all())


def collapse_by_document(
    candidates: List[Dict[str, Any]], top_k: int, per_document: int
) -> List[Dict[str, Any]]:
    """Collapse ranked node matches into at most ``top_k`` documents.

    Each document appears once, at the rank of its best node, with up to
    ``per_document - 1`` further matched nodes under ``related``, so one
    heavily-matching document can't fill every slot.
    """
    docs: Dict[str, Dict[str, Any]] = {}
    for candidate in candidates:
        doc = docs.get(candidate["document"])
        if doc is None:
            if len(docs) < top_k:
                docs[candidate["document"]] = {**candidate, "related": []}
        elif len(doc["related"]) < per_document - 1:
            doc["related"].append({k: v for k, v in candidate.items() if k != "document"})
    return list(docs.values())


class SemanticDataMatcher:
    """
    Matches agent semantic needs to available project data using embeddings.

    Uses PostgreSQL with pgvector for persistent vector storage and similarity search.

    Key features:
    - PostgreSQL-backed persistent storage (survives restarts)
    - Native vector similarity search via pgvector
    - Optional hybrid search (pgvector + pg_search BM25, fused with RRF)
    - Auto-generates descriptions from data structure
    - Fast embedding-based similarity matching
    - Handles schema evolution gracefully
    """

    def __init__(
        self,
        db: DatabaseManager,
        model_name: str = "thenlper/gte-base",
        similarity_threshold: float = 0.35,
        max_matches: int = 10,
    ):
        """
        Initialize semantic matcher.

        Args:
            db: Database manager instance
            model_name: HuggingFace model with an ONNX export
            similarity_threshold: Minimum similarity to match (0-1)
            max_matches: Maximum matches to return per need
        """
        self.db = db
        self.model_name = model_name
        self.model = _load_model(model_name)
        self.threshold = similarity_threshold
        self.max_matches = max_matches
        self.embedding_dim = self.model.get_sentence_embedding_dimension()
        self.node_converter = NodeConverter()
        self.vector_search = PgVectorSearch(db, self.model)
        # Matched nodes returned per document (its best plus related).
        self.nodes_per_document = positive_int_env("NODES_PER_DOCUMENT", 3)
        # Nodes searched per requested document, so collapsing still fills top_k.
        self.candidate_pool_factor = positive_int_env("CANDIDATE_POOL_FACTOR", 10)
        self.reranker = JevReranker.from_env()

        # Initialize hybrid search if enabled: pgvector (vector) + pg_search BM25 (lexical)
        # (lexical) fused with backend-agnostic RRF. Single database, no extra
        # stateful services.
        self.hybrid_search = None
        if os.getenv("HYBRID_SEARCH_ENABLED", "false").lower() == "true":
            try:
                rrf_k = int(os.getenv("RRF_K", "60"))
                self.hybrid_search = HybridSearchService(
                    vector_search=self.vector_search,
                    lexical_search=PgFtsLexical(db),
                    k=rrf_k,
                )
                logger.info("Hybrid search enabled (pgvector + pg_search BM25, RRF)")
            except Exception as e:
                logger.warning("Failed to initialize hybrid search", error=str(e))
                self.hybrid_search = None

        logger.info(
            "Semantic matcher initialized",
            threshold=similarity_threshold,
            max_matches=max_matches,
        )

    async def initialize_index(self):
        """Fail startup when EMBED_MODEL's dimension doesn't match the embeddings column."""
        async with self.db.session() as session:
            column_dim = await session.scalar(
                text(
                    "SELECT atttypmod FROM pg_attribute "
                    "WHERE attrelid = 'embeddings'::regclass AND attname = 'embedding'"
                )
            )
            if column_dim is not None and column_dim != self.embedding_dim:
                raise RuntimeError(
                    f"EMBED_MODEL {self.model_name!r} produces {self.embedding_dim}-dim "
                    f"vectors but the embeddings column is {column_dim}-dim. Migrate the "
                    f"column to vector({self.embedding_dim}) and re-embed."
                )

    async def register_data_batch(
        self,
        project_id: str,
        items: List[Tuple[str, Any, Optional[str]]],
        published_at: Optional[Dict[str, datetime]] = None,
        origin: Optional[str] = None,
    ) -> List[Optional[str]]:
        """Register many ``(data_key, data, format_hint)`` items with one encode call.

        Encoding is the expensive step and pads each batch to its longest text,
        so pooling every item's changed nodes into one call lets length-sorted
        batching work across the whole set rather than one file at a time.
        ``published_at`` maps a data_key to when its source changed it.
        ``origin`` tags every item, including unchanged ones, with its source stream.
        Returns, per item, None or why it was not registered.
        """
        published_at = published_at or {}
        if origin is not None:
            # Unchanged nodes skip the upsert below, so tag existing rows here.
            await self._tag_origin(project_id, [data_key for data_key, _, _ in items], origin)
        plans, errors = [], [None] * len(items)
        for index, (data_key, data, format_hint) in enumerate(items):
            try:
                plans.append((index, await self._plan_registration(project_id, data_key, data, format_hint)))
            except DocumentRejected as e:
                logger.warning("Rejected data", project_id=project_id, data_key=data_key, error=str(e))
                errors[index] = str(e)
            except Exception:
                logger.exception("Failed to read data", project_id=project_id, data_key=data_key)
                errors[index] = "could not read the document"
        texts = [plan["texts"][i] for _, plan in plans for i in plan["changed"]]
        embeddings = await encode_async(self.model, texts, batch_size=16) if texts else []

        offset = 0
        for index, plan in plans:
            n = len(plan["changed"])
            try:
                await self._write_registration(
                    project_id, plan, embeddings[offset:offset + n], published_at.get(plan["data_key"]), origin
                )
            except Exception:
                logger.exception("Failed to store data", project_id=project_id, data_key=plan["data_key"])
                errors[index] = "could not store the document"
            offset += n
        return errors

    async def _plan_registration(
        self, project_id: str, data_key: str, data: Any, format_hint: Optional[str],
    ) -> Dict[str, Any]:
        """Parse one item and work out which of its nodes need (re-)embedding."""
        # data_key carries the file extension the code parser needs to pick a grammar.
        # Parsing is CPU-bound (PDF, OCR, DOCX), so it runs off the event loop.
        parse_result = await asyncio.to_thread(
            self.node_converter.parse, data, format_hint, data_key=data_key
        )

        if not parse_result.success:
            logger.warning("Failed to parse data", project_id=project_id, data_key=data_key,
                           error=parse_result.error)
            raise DocumentRejected(f"could not read the document as {parse_result.format_name}")

        # Empty content still gets a plan, so publishing it removes the old nodes.
        nodes = parse_result.nodes
        logger.debug(
            "Parsed data into nodes",
            project_id=project_id,
            data_key=data_key,
            node_count=len(nodes),
            format=parse_result.format_name,
        )

        # Joined with a control character, which data_keys can't contain, so one
        # document's key never equals another's key plus a node path.
        node_keys = [f"{data_key}\x1f{node.path}" if node.path else data_key for node in nodes]
        title = _document_title(nodes)
        embedding_texts = [_embedding_text(data_key, node, title) for node in nodes]
        # Skip encode + upsert for nodes whose embedded text and model are
        # unchanged. The hash covers the exact string we encode and index, so it
        # captures both content and the provenance prefix.
        content_hashes = [
            hashlib.sha256(f"{self.model_name}\n{t}".encode("utf-8")).hexdigest()
            for t in embedding_texts
        ]
        async with self.db.session() as session:
            prior_hashes = await _stored_hashes(session, project_id, node_keys)

        changed = [
            i
            for i, (node_key, content_hash) in enumerate(zip(node_keys, content_hashes))
            if prior_hashes.get(node_key) != content_hash
        ]

        return {
            "data_key": data_key,
            "parse_result": parse_result,
            "nodes": nodes,
            "node_keys": node_keys,
            "texts": embedding_texts,
            "hashes": content_hashes,
            "changed": changed,
        }

    async def _write_registration(
        self, project_id: str, plan: Dict[str, Any], embeddings,
        published_at: Optional[datetime] = None, origin: Optional[str] = None,
    ) -> None:
        """Upsert one item's changed nodes, drop nodes it no longer has, and rewrite its symbols."""
        data_key, parse_result = plan["data_key"], plan["parse_result"]
        nodes, node_keys, changed = plan["nodes"], plan["node_keys"], plan["changed"]

        rows = [
            {
                "project_id": project_id,
                "data_key": data_key,
                "node_key": node_keys[i],
                "node_path": nodes[i].path,
                "node_type": nodes[i].node_type.value,
                "description": plan["texts"][i],
                "data": nodes[i].content if isinstance(nodes[i].content, dict) else {"value": nodes[i].content},
                "data_format": parse_result.format_name,
                "content_hash": plan["hashes"][i],
                "embedding": embedding.tolist(),
                "published_at": published_at,
                "origin": origin,
            }
            for i, embedding in zip(changed, embeddings)
        ]
        # An upsert, so concurrent first publishes of one key don't collide on
        # the unique (project_id, node_key) index.
        stmt = pg_insert(Embedding)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Embedding.project_id, Embedding.node_key],
            set_={
                **{column: stmt.excluded[column] for column in _REWRITTEN_COLUMNS},
                "origin": func.coalesce(stmt.excluded.origin, Embedding.origin),
                "updated_at": func.now(),
            },
        )
        async with self.db.session() as session:
            # Serialize writers of one document, and refuse a plan whose skipped
            # nodes another writer has since replaced.
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:project_id || chr(31) || :data_key, 0))"),
                {"project_id": project_id, "data_key": data_key},
            )
            rewritten = set(changed)
            skipped = {k: h for i, (k, h) in enumerate(zip(node_keys, plan["hashes"])) if i not in rewritten}
            if await _stored_hashes(session, project_id, list(skipped)) != skipped:
                raise RuntimeError("document changed while it was being registered")
            if rows:
                await session.execute(stmt, rows)
            removed = await session.execute(
                delete(Embedding)
                .where(Embedding.project_id == project_id)
                .where(Embedding.data_key == data_key)
                .where(Embedding.node_key != all_(literal(node_keys, ARRAY(Text))))
            )
            # Symbols are the defs/refs the parser recorded per node, rewritten
            # whenever the nodes change. Unchanged documents keep theirs as is.
            if rows or removed.rowcount:
                await session.execute(
                    delete(Symbol)
                    .where(Symbol.project_id == project_id)
                    .where(Symbol.data_key == data_key)
                )
                session.add_all([
                    Symbol(project_id=project_id, data_key=data_key, node_key=node_key,
                           name=name, role=role)
                    for node, node_key in zip(nodes, node_keys)
                    for role, names in (("def", node.metadata.get("defs", [])),
                                        ("ref", node.metadata.get("refs", [])))
                    for name in names if len(name) <= 255
                ])

        logger.info(
            "Registered data",
            project_id=project_id,
            data_key=data_key,
            node_count=len(nodes),
            embedded_count=len(changed),
        )

    async def match_agent_needs(
        self,
        project_id: str,
        needs: List[str],
        top_k: Optional[int] = None,
        threshold: Optional[float] = None,
        since: Optional[datetime] = None,
        rerank: bool = False,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Match agent semantic needs to available data.

        Uses hybrid search (pgvector + pg_search BM25, fused with RRF) if enabled,
        otherwise uses pgvector cosine-similarity search.

        Args:
            project_id: Project identifier
            needs: List of semantic needs (natural language)
            top_k: Per-request max matches per need; defaults to the instance value.
            threshold: Per-request min similarity (0-1); defaults to the instance value.
            since: When set, only match data created or updated on or after this
                time (compared against ``COALESCE(published_at, updated_at, created_at)``).
            rerank: Reorder candidates with the opt-in relevance reranker when
                one is configured (adds ``relevance`` to judged matches).

        Returns:
            Dict mapping each need to at most ``top_k`` documents, best first.
            Each is its best-matching node plus other matched nodes from the
            same document (see ``collapse_by_document``), and the document's
            root node data when that root isn't already among them:
            {
                "need description": [
                    {"data_key": "<node key>", "document": "<document key>",
                     "similarity": 0.85, "data": {...}, "description": "...",
                     "related": [{"data_key": ..., "similarity": ..., ...}],
                     "document_data": {...}},
                    ...
                ]
            }
        """
        effective_max = top_k if top_k is not None else self.max_matches
        effective_threshold = (
            threshold if threshold is not None else self.threshold
        )

        pool = effective_max * self.candidate_pool_factor
        matches = {}

        for need in needs:
            logger.debug("Matching need", need=need, project_id=project_id)
            candidates = await self._collect_candidates(
                project_id, need, effective_max, pool, since, effective_threshold
            )
            if rerank and self.reranker:
                candidates = await self.reranker.rerank(need, candidates)
            matches[need] = collapse_by_document(
                candidates, effective_max, self.nodes_per_document
            )
            await self._attach_document_roots(project_id, matches[need])
            logger.debug("Matched need", need=need, count=len(matches[need]))

        return matches

    async def _collect_candidates(
        self, project_id: str, need: str, top_k: int, pool: int,
        since: Optional[datetime], threshold: float,
    ) -> List[Dict[str, Any]]:
        """Ranked candidate nodes spanning up to ``top_k`` documents.

        The pool is counted in nodes but results in documents, so a few large
        documents can fill it on their own. Re-rank with every document seen so
        far excluded until ``top_k`` documents are found or the rankers run dry.
        """
        candidates: List[Dict[str, Any]] = []
        seen: set[str] = set()
        # Terminates: each non-empty round adds only unseen documents.
        while True:
            ranked = await self._rank_nodes(project_id, need, top_k, pool, since, seen)
            exhausted = len(ranked) < pool
            batch = await self._load_candidates(
                project_id, [(key, sim) for key, sim in ranked if sim >= threshold]
            )
            candidates += batch
            seen |= {c["document"] for c in batch}
            if exhausted or not batch or len(seen) >= top_k:
                break
        return candidates

    async def _rank_nodes(
        self, project_id: str, need: str, top_k: int, pool: int,
        since: Optional[datetime], exclude_documents: Collection[str] = (),
    ) -> List[Tuple[str, float]]:
        """Up to ``pool`` (node_key, cosine similarity), best first: hybrid if
        enabled (fused at ``top_k`` depth), else vector."""
        if self.hybrid_search:
            try:
                return await self.hybrid_search.search(
                    project_id=project_id, query=need, top_k=top_k, since=since, pool=pool,
                    exclude_documents=exclude_documents,
                )
            except Exception as e:
                logger.warning("Hybrid search error, falling back to vector search", error=str(e))
        return await self.vector_search.search(
            project_id, need, pool, since=since, exclude_documents=exclude_documents
        )

    async def _load_candidates(
        self, project_id: str, ranked: List[Tuple[str, float]]
    ) -> List[Dict[str, Any]]:
        """Attach each ranked node's stored row, keeping rank order."""
        if not ranked:
            return []
        async with self.db.session() as session:
            result = await session.execute(
                select(Embedding)
                .where(Embedding.project_id == project_id)
                .where(Embedding.node_key.in_([key for key, _ in ranked]))
            )
            rows = {row.node_key: row for row in result.scalars()}
        return [
            {
                "data_key": key,
                "document": rows[key].data_key,
                "similarity": float(similarity),
                "data": rows[key].data,
                "description": rows[key].description,
            }
            for key, similarity in ranked
            if key in rows
        ]

    async def _attach_document_roots(
        self, project_id: str, results: List[Dict[str, Any]]
    ) -> None:
        """Add each document's root node data (a ticket's own fields, a code
        file's summary) to its result unless that root already matched."""
        if not results:
            return
        async with self.db.session() as session:
            rows = await session.execute(
                select(Embedding.node_key, Embedding.data)
                .where(Embedding.project_id == project_id)
                .where(Embedding.node_key.in_([r["document"] for r in results]))
            )
            roots = dict(rows.all())
        for result in results:
            matched = {result["data_key"], *(r["data_key"] for r in result["related"])}
            if result["document"] in roots and result["document"] not in matched:
                result["document_data"] = roots[result["document"]]

    async def _tag_origin(self, project_id: str, data_keys: List[str], origin: str) -> None:
        async with self.db.session() as session:
            await session.execute(
                update(Embedding)
                .where(Embedding.project_id == project_id)
                .where(Embedding.data_key.in_(data_keys))
                .where(Embedding.origin.is_distinct_from(origin))
                .values(origin=origin)
            )

    async def list_keys(
        self, project_id: str, origin: str, after: Optional[str], limit: int,
    ) -> List[str]:
        """An origin's document keys in order, starting after ``after``."""
        stmt = (
            select(Embedding.data_key).distinct()
            .where(Embedding.project_id == project_id)
            .where(Embedding.origin == origin)
            .order_by(Embedding.data_key)
            .limit(limit)
        )
        if after is not None:
            stmt = stmt.where(Embedding.data_key > after)
        async with self.db.session() as session:
            return list((await session.execute(stmt)).scalars())

    async def delete_data_keys(self, project_id: str, data_keys: List[str]) -> List[str]:
        """Remove these documents' nodes and symbols; return the keys that existed."""
        async with self.db.session() as session:
            existing = list((await session.execute(
                select(Embedding.data_key).distinct()
                .where(Embedding.project_id == project_id)
                .where(Embedding.data_key.in_(data_keys))
            )).scalars())
            if existing:
                for model in (Embedding, Symbol):
                    await session.execute(
                        delete(model)
                        .where(model.project_id == project_id)
                        .where(model.data_key.in_(existing))
                    )
        return existing
