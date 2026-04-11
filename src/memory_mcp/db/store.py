"""Core memory store backed by ChromaDB and sentence-transformers."""

from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import threading
from collections import OrderedDict

import chromadb
import numpy as np
from sentence_transformers import SentenceTransformer

from memory_mcp.constants import (
    COLLECTION_PREFIX,
    DECAY_RATES,
    DEFAULT_DB_PATH,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_MMR_FETCH_K,
    DEFAULT_MMR_LAMBDA,
    DEFAULT_N_RESULTS,
    DUPLICATE_DISTANCE_THRESHOLD,
    IMPORTANCE_DEFAULT,
    IMPORTANCE_MAX,
    IMPORTANCE_MIN,
    MAX_N_RESULTS,
    MemoryPriority,
    MemoryType,
)
from memory_mcp.db.bm25 import MemoryBM25Index
from memory_mcp.db.fusion import (
    compute_dynamic_weights,
    compute_query_specificity,
    reciprocal_rank_fusion,
)

logger = logging.getLogger(__name__)

# ── Module-level caches (shared across all MemoryStore instances) ──────

_EMBED_CACHE_MAX = 512
_embed_cache: OrderedDict[str, list[float]] = OrderedDict()
_embed_cache_lock = threading.Lock()
_embed_semaphore = threading.Semaphore(3)  # limit concurrent encode() calls

# ── Pre-loaded embedding model (shared singleton) ──────
_preloaded_embedder: SentenceTransformer | None = None


def preload_embedding_model(model_name: str) -> SentenceTransformer:
    """Pre-load embedding model into module-level cache.

    Call this before lifespan to avoid blocking the event loop during
    MemoryStore initialization. Subsequent MemoryStore() calls will
    use the cached model automatically.
    """
    global _preloaded_embedder
    if _preloaded_embedder is None:
        logger.info("Pre-loading embedding model: %s", model_name)
        _preloaded_embedder = SentenceTransformer(model_name)
        logger.info("Embedding model pre-loaded (dim=%d)", _preloaded_embedder.get_sentence_embedding_dimension() or 384)
    return _preloaded_embedder


def _cached_embed(embedder: SentenceTransformer, text: str) -> list[float]:
    """Embed text with LRU cache (module-level, shared across instances).

    Embedding is deterministic — same input always produces same output.
    Only search queries benefit from caching; store() content is 1-shot.
    """
    with _embed_cache_lock:
        if text in _embed_cache:
            _embed_cache.move_to_end(text)
            return _embed_cache[text]

    # Cache miss — compute (outside lock to avoid blocking)
    with _embed_semaphore:
        result = embedder.encode(text, normalize_embeddings=True).tolist()

    with _embed_cache_lock:
        _embed_cache[text] = result
        if len(_embed_cache) > _EMBED_CACHE_MAX:
            _embed_cache.popitem(last=False)  # evict oldest

    return result


class MemoryStore:
    """ChromaDB-backed memory store with semantic search.

    Each project gets its own ChromaDB collection. Memories are stored with
    metadata (type, tags, timestamp) and can be searched semantically or
    filtered by metadata.
    """

    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        embedder: SentenceTransformer | None = None,
    ) -> None:
        # 1. Resolve and validate path
        db_path_obj = Path(db_path).expanduser().resolve()
        try:
            db_path_obj.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            raise RuntimeError(
                f"Permission denied: cannot create '{db_path_obj}'\n"
                f"  Try: KANDELA_DB_PATH=~/other-path kandela"
            )
        except OSError as e:
            raise RuntimeError(
                f"Cannot create data directory '{db_path_obj}': {e}\n"
                f"  Check that the path is valid and the disk has space."
            ) from e

        self._db_path = str(db_path_obj)

        # 2. Initialize ChromaDB with clear error messages
        try:
            self._chroma = chromadb.PersistentClient(path=self._db_path)
        except Exception as e:
            raise RuntimeError(
                f"ChromaDB failed to initialize at '{self._db_path}': {e}\n"
                f"  If DB is corrupted, try removing: rm -rf {self._db_path}"
            ) from e

        # 3. Load embedding model (or reuse shared/preloaded instance)
        if embedder is not None:
            # Reuse a shared SentenceTransformer (multi-user mode saves ~450MB per user)
            self._embedder = embedder
            logger.info("Reusing shared embedding model for db=%s", self._db_path)
        elif _preloaded_embedder is not None:
            # Use module-level preloaded model (avoids blocking event loop)
            self._embedder = _preloaded_embedder
            logger.info("Using preloaded embedding model for db=%s", self._db_path)
        else:
            # Load fresh model (may download ~449MB on first run)
            logger.info("Loading embedding model: %s", embedding_model)
            try:
                self._embedder = SentenceTransformer(embedding_model)
            except Exception as e:
                hf_cache = os.environ.get("HF_HOME", "~/.cache/huggingface")
                raise RuntimeError(
                    f"Failed to load embedding model '{embedding_model}': {e}\n"
                    f"  First run downloads ~449MB model. Check network connection.\n"
                    f"  Model cache: {hf_cache}"
                ) from e

        self._embedding_dim = self._embedder.get_sentence_embedding_dimension() or 384

        # BM25 index cache: project -> (version, MemoryBM25Index)
        # Invalidated on store/delete/update via _invalidate_bm25()
        self._bm25_cache: dict[str, tuple[int, MemoryBM25Index]] = {}
        self._bm25_versions: dict[str, int] = {}  # project -> write counter
        self._bm25_lock = threading.Lock()

        logger.info("MemoryStore ready (db=%s, dim=%d)", self._db_path, self._embedding_dim)

    def _collection_name(self, project: str) -> str:
        """Sanitize and prefix collection name."""
        safe = project.lower().replace(" ", "_").replace("-", "_")
        return f"{COLLECTION_PREFIX}{safe}"

    def _list_collection_names(self) -> list[str]:
        """List collection names (compatible with ChromaDB 0.5 and 0.6+).

        In 0.5, list_collections() returns Collection objects with .name.
        In 0.6+, list_collections() returns strings directly.
        """
        result = []
        for item in self._chroma.list_collections():
            if isinstance(item, str):
                result.append(item)
            else:
                result.append(item.name)
        return result

    def _get_collection(self, project: str) -> chromadb.Collection:
        return self._chroma.get_or_create_collection(
            name=self._collection_name(project),
            metadata={"hnsw:space": "cosine"},
        )

    def close(self) -> None:
        """Release resources held by this store.

        ChromaDB PersistentClient has no explicit close(), so we clear
        caches and release references for GC.
        """
        with self._bm25_lock:
            self._bm25_cache.clear()
            self._bm25_versions.clear()
        # ChromaDB: clear system cache if available, then release reference
        if hasattr(self._chroma, 'clear_system_cache'):
            try:
                self._chroma.clear_system_cache()
            except Exception:
                pass
        logger.info("MemoryStore closed (db=%s)", self._db_path)

    # ── Project Visibility (single-user file-based) ──────────

    def get_project_searchable(self, project: str) -> bool:
        """Single-user: file-based searchable setting. Default True."""
        settings_file = Path(self._db_path) / "project_settings.json"
        if not settings_file.exists():
            return True
        try:
            settings = json.loads(settings_file.read_text())
            return settings.get(project, True)
        except Exception:
            return True

    def set_project_searchable(self, project: str, searchable: bool) -> None:
        """Single-user: set searchable flag for a project."""
        settings_file = Path(self._db_path) / "project_settings.json"
        settings: dict[str, bool] = {}
        if settings_file.exists():
            try:
                settings = json.loads(settings_file.read_text())
            except Exception:
                pass
        settings[project] = searchable
        settings_file.write_text(json.dumps(settings, indent=2))

    def bulk_set_searchable(self, changes: dict[str, bool]) -> dict[str, bool]:
        """Set searchable for multiple projects in 1 file I/O.

        Returns:
            Previous state dict {project: previous_searchable}.
        """
        settings_file = Path(self._db_path) / "project_settings.json"
        settings: dict[str, bool] = {}
        if settings_file.exists():
            try:
                settings = json.loads(settings_file.read_text())
            except Exception:
                pass

        previous: dict[str, bool] = {}
        for proj, val in changes.items():
            previous[proj] = settings.get(proj, True)
            settings[proj] = val

        settings_file.write_text(json.dumps(settings, indent=2))
        return previous

    def _invalidate_bm25(self, project: str) -> None:
        """Invalidate BM25 cache for a project (call on any write)."""
        with self._bm25_lock:
            self._bm25_versions[project] = self._bm25_versions.get(project, 0) + 1
            self._bm25_cache.pop(project, None)

    def _get_bm25_index(
        self,
        project: str,
        where_filter: dict[str, Any] | None = None,
    ) -> MemoryBM25Index | None:
        """Get or build BM25 index for a project (cached).

        Cache is keyed by (project, version). Invalidated by writes.
        When where_filter is used, cache is bypassed (filtered subset).
        """
        # Filtered queries bypass cache (subset of docs)
        if where_filter:
            return self._build_bm25_index(project, where_filter)

        version = self._bm25_versions.get(project, 0)
        with self._bm25_lock:
            cached = self._bm25_cache.get(project)
            if cached and cached[0] == version:
                return cached[1]

        # Build outside lock
        index = self._build_bm25_index(project, where_filter=None)
        if index is not None:
            with self._bm25_lock:
                self._bm25_cache[project] = (version, index)
        return index

    def _build_bm25_index(
        self,
        project: str,
        where_filter: dict[str, Any] | None,
    ) -> MemoryBM25Index | None:
        """Build a fresh BM25 index from ChromaDB documents."""
        col = self._get_collection(project)
        if col.count() == 0:
            return None

        get_kwargs: dict[str, Any] = {"include": ["documents", "metadatas"]}
        if where_filter:
            get_kwargs["where"] = where_filter

        try:
            raw = col.get(**get_kwargs)
        except Exception:
            logger.exception("BM25 get() failed on project %s", project)
            return None

        if not raw["documents"]:
            return None

        try:
            return MemoryBM25Index(
                raw["documents"],
                raw["ids"],
                raw["metadatas"] or [{} for _ in raw["documents"]],
            )
        except Exception:
            logger.exception("BM25 index build failed on project %s", project)
            return None

    def _embed(self, text: str) -> list[float]:
        return _cached_embed(self._embedder, text)

    def _make_id(self, project: str) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        return f"{project}_{ts}"

    # ── Store ────────────────────────────────────────────────────────

    def store(
        self,
        project: str,
        content: str,
        memory_type: MemoryType = MemoryType.FACT,
        tags: list[str] | None = None,
        priority: MemoryPriority = MemoryPriority.NORMAL,
        importance: float | None = None,
        linked_projects: list[str] | None = None,
        session_id: str | None = None,
        _embedding: list[float] | None = None,
    ) -> str:
        """Store a memory and return its ID.

        Args:
            project: Project identifier.
            content: Memory content text.
            memory_type: Memory classification type.
            tags: Optional tags for filtering.
            priority: DEPRECATED — use importance instead.
            importance: Importance score (1.0-10.0). If None, derived from priority.
            linked_projects: Other projects this memory is also relevant to.
            session_id: Identifier for the session that created this memory.
                        Used for multi-session concurrency tracking.
            _embedding: Pre-computed embedding (internal use, avoids double-embed
                        when called after check_duplicate).
        """
        from memory_mcp.constants import IMPORTANCE_DEFAULT, PRIORITY_TO_IMPORTANCE
        from memory_mcp.importance.rules import (
            apply_rule_bonus,
            infer_infrastructure_tags,
        )

        col = self._get_collection(project)
        doc_id = self._make_id(project)
        embedding = _embedding if _embedding is not None else self._embed(content)

        # Auto-tag infrastructure content (Session Continuity)
        resolved_tags = infer_infrastructure_tags(content, tags or [])

        # Resolve importance: explicit importance wins, then priority conversion
        if importance is not None:
            resolved_importance = importance
        else:
            resolved_importance = PRIORITY_TO_IMPORTANCE.get(
                priority.value, IMPORTANCE_DEFAULT,
            )

        # Apply server-side rule bonus
        final_importance = apply_rule_bonus(
            content, resolved_tags, resolved_importance,
        )

        now = datetime.now(timezone.utc)
        metadata: dict[str, Any] = {
            "project": project,
            "type": memory_type.value,
            "importance": float(final_importance),
            "priority": priority.value,  # backward compat
            "tags": json.dumps(resolved_tags),
            "created_at": now.isoformat(),
            "created_ts": int(now.timestamp()),
            "linked_projects": json.dumps(linked_projects or []),
            "recall_count": 0,
            "search_count": 0,
            "deleted_ts": 0,  # soft-delete support (0 = not deleted)
        }
        if session_id:
            metadata["session_id"] = session_id

        col.add(
            documents=[content],
            embeddings=[embedding],
            metadatas=[metadata],
            ids=[doc_id],
        )
        self._invalidate_bm25(project)
        logger.info(
            "Stored memory %s in project %s (importance=%.1f)",
            doc_id, project, final_importance,
        )
        return doc_id

    # ── Duplicate Detection ──────────────────────────────────────────

    def check_duplicate(
        self,
        project: str,
        content: str,
        threshold: float | None = None,
    ) -> tuple[dict[str, Any] | None, list[float]]:
        """Check if a near-duplicate memory exists in the project.

        Embeds content, queries ChromaDB for the nearest neighbor,
        and checks if its cosine distance is below threshold.

        Always returns the computed embedding so callers can pass it
        to store() without re-embedding.

        Args:
            project: Project identifier.
            content: Content to check against existing memories.
            threshold: Cosine distance threshold
                       (default: DUPLICATE_DISTANCE_THRESHOLD).

        Returns:
            Tuple of (match_or_none, embedding).
            match is a dict with id, content, distance, metadata
            if a near-duplicate is found; None otherwise.
        """
        if threshold is None:
            threshold = DUPLICATE_DISTANCE_THRESHOLD

        embedding = self._embed(content)
        col = self._get_collection(project)

        if self._active_count(project) == 0:
            return None, embedding

        results = self._query_collection(
            col, embedding, where_filter={"deleted_ts": {"$eq": 0}}, n_results=1,
        )

        if not results:
            return None, embedding

        nearest = results[0]
        if nearest["distance"] < threshold:
            logger.info(
                "Duplicate detected in project %s (distance=%.4f, id=%s)",
                project, nearest["distance"], nearest["id"],
            )
            return nearest, embedding

        return None, embedding

    # ── Update ────────────────────────────────────────────────────────

    def update(
        self,
        project: str,
        memory_id: str,
        content: str | None = None,
        memory_type: MemoryType | None = None,
        importance: float | None = None,
        tags: list[str] | None = None,
        linked_projects: list[str] | None = None,
    ) -> dict[str, Any]:
        """Update an existing memory's content, type, importance, or tags.

        If content changes, re-embeds automatically and re-applies
        importance rules against the new content.
        Preserves original created_at, recall_count, search_count.
        Adds/updates updated_at timestamp.

        Args:
            project: Project identifier.
            memory_id: ID of the memory to update.
            content: New content text (triggers re-embedding).
            memory_type: New memory type.
            importance: New importance score.
            tags: New tags list (replaces existing). None=no change, []=clear.
            linked_projects: New linked projects list. None=no change, []=clear.

        Returns:
            Dict with id, updated_fields, importance.

        Raises:
            ValueError: If memory_id not found in the project.
        """
        from memory_mcp.importance.rules import apply_rule_bonus

        col = self._get_collection(project)

        # Fetch existing memory
        try:
            raw = col.get(
                ids=[memory_id],
                include=["documents", "metadatas"],
            )
        except Exception as e:
            raise ValueError(f"Failed to fetch memory '{memory_id}': {e}") from e

        if not raw["ids"]:
            raise ValueError(
                f"Memory '{memory_id}' not found in project '{project}'"
            )

        existing_content = raw["documents"][0]
        existing_meta = dict(raw["metadatas"][0])

        # Build update payload
        new_embedding = None
        updated_fields: list[str] = []
        new_meta = dict(existing_meta)  # start from existing

        # Content update → re-embed
        if content is not None:
            new_embedding = self._embed(content)
            updated_fields.append("content")

        # Memory type update
        if memory_type is not None:
            new_meta["type"] = memory_type.value
            updated_fields.append("memory_type")

        # Tags update
        if tags is not None:
            new_meta["tags"] = json.dumps(tags)
            updated_fields.append("tags")

        # Linked projects update
        if linked_projects is not None:
            new_meta["linked_projects"] = json.dumps(linked_projects)
            updated_fields.append("linked_projects")

        # Resolve tags for rule evaluation
        resolved_tags = (
            tags if tags is not None
            else json.loads(existing_meta.get("tags", "[]"))
        )
        effective_content = content if content is not None else existing_content

        # Importance update + rule re-application
        if importance is not None:
            final_importance = apply_rule_bonus(
                effective_content, resolved_tags, importance,
            )
            new_meta["importance"] = float(final_importance)
            updated_fields.append("importance")
        elif content is not None or tags is not None:
            # Content or tags changed: re-apply rules on existing base importance
            base_importance = float(
                existing_meta.get("importance", IMPORTANCE_DEFAULT)
            )
            final_importance = apply_rule_bonus(
                effective_content, resolved_tags, base_importance,
            )
            new_meta["importance"] = float(final_importance)

        # Timestamp
        now = datetime.now(timezone.utc)
        new_meta["updated_at"] = now.isoformat()

        # Build ChromaDB update kwargs
        update_kwargs: dict[str, Any] = {
            "ids": [memory_id],
            "metadatas": [new_meta],
        }
        if content is not None:
            update_kwargs["documents"] = [content]
        if new_embedding is not None:
            update_kwargs["embeddings"] = [new_embedding]

        try:
            col.update(**update_kwargs)
        except Exception as e:
            raise RuntimeError(
                f"ChromaDB update failed for '{memory_id}': {e}"
            ) from e

        # Invalidate BM25 cache if content or tags changed
        if "content" in updated_fields or "tags" in updated_fields:
            self._invalidate_bm25(project)

        logger.info(
            "Updated memory %s in project %s (fields: %s)",
            memory_id, project, ", ".join(updated_fields),
        )

        return {
            "id": memory_id,
            "updated_fields": updated_fields,
            "importance": new_meta["importance"],
        }

    # ── Search ───────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        project: str | None = None,
        memory_type: MemoryType | None = None,
        n_results: int = DEFAULT_N_RESULTS,
        cross_project: bool = False,
        *,
        tags: list[str] | None = None,
        priority: MemoryPriority | None = None,
        importance_min: float | None = None,
        importance_max: float | None = None,
        date_after: str | None = None,
        date_before: str | None = None,
        use_mmr: bool = False,
        time_weighted: bool = False,
        use_hybrid: bool = False,
        dynamic_rrf: bool = False,
        mmr_lambda: float | None = None,
    ) -> list[dict[str, Any]]:
        """Semantic search across memories with optional RAG enhancements.

        Args:
            query: Search query text.
            project: Project to search in (ignored if cross_project=True).
            memory_type: Filter by memory type.
            n_results: Max results to return.
            cross_project: If True, search all projects.
            tags: Filter by tags (OR matching).
            priority: Filter by priority level.
            importance_min: Minimum importance score (inclusive).
            importance_max: Maximum importance score (inclusive).
            date_after: ISO date string — only memories created after this.
            date_before: ISO date string — only memories created before this.
            use_mmr: Use MMR reranking for diverse results.
            time_weighted: Apply time-decay to distances.
            use_hybrid: Use hybrid search (semantic + BM25 via RRF).
            dynamic_rrf: When True with use_hybrid, adjust BM25/semantic weights
                based on query specificity. Specific queries favor BM25,
                abstract queries favor semantic. (H-2.1)
            mmr_lambda: MMR relevance/diversity trade-off (0=full diversity,
                1=full relevance). None uses DEFAULT_MMR_LAMBDA (0.7). (H-2.4)

        Returns:
            List of dicts with keys: id, content, metadata, distance.
        """
        n_results = min(n_results, MAX_N_RESULTS)
        embedding = self._embed(query)
        where_filter = self._build_where(
            memory_type=memory_type,
            priority=priority,
            importance_min=importance_min,
            importance_max=importance_max,
            date_after=date_after,
            date_before=date_before,
        )

        # MMR needs more candidates; tag post-filter also needs extra
        extra_factor = DEFAULT_MMR_FETCH_K if use_mmr else 1
        if tags:
            extra_factor = max(extra_factor, 3)  # fetch more to compensate for post-filter
        fetch_n = n_results * extra_factor
        include_embeddings = use_mmr

        if cross_project:
            results = self._search_all_collections(
                embedding, where_filter, fetch_n,
                include_embeddings=include_embeddings,
            )
        elif not project:
            return []
        else:
            col = self._get_collection(project)
            results = self._query_collection(
                col, embedding, where_filter, fetch_n,
                include_embeddings=include_embeddings,
            )

        # Hybrid search: merge semantic results with BM25 via RRF
        if use_hybrid and project and not cross_project:
            bm25_results = self._bm25_search(
                project=project,
                query=query,
                n_results=fetch_n,
                where_filter=where_filter,
            )

            # Dynamic RRF: compute per-query weights from BM25 score distribution
            rrf_weights: list[float] | None = None
            if dynamic_rrf:
                rrf_weights = self._compute_rrf_weights(project, query, where_filter)

            results = reciprocal_rank_fusion(
                results, bm25_results,
                n_results=fetch_n,
                weights=rrf_weights,
            )

        # Post-filter by tags (ChromaDB $contains not supported on metadata)
        if tags:
            results = self._filter_by_tags(results, tags)

        # Post-processing pipeline
        if time_weighted:
            results = self._apply_time_decay(results)

        if use_mmr:
            effective_lambda = mmr_lambda if mmr_lambda is not None else DEFAULT_MMR_LAMBDA
            results = self._mmr_rerank(
                query_embedding=embedding,
                results=results,
                n_results=n_results,
                lambda_param=effective_lambda,
            )
        else:
            results = results[:n_results]

        # Enrich with composite retrieval score (Phase 9)
        if time_weighted:
            results = self._apply_retrieval_score(results)

        return results

    def _build_where(
        self,
        memory_type: MemoryType | None = None,
        priority: MemoryPriority | None = None,
        importance_min: float | None = None,
        importance_max: float | None = None,
        date_after: str | None = None,
        date_before: str | None = None,
        include_deleted: bool = False,
    ) -> dict[str, Any] | None:
        """Build ChromaDB where filter, combining multiple conditions with $and.

        Note: tags filtering is done via post-filter (_filter_by_tags)
        because ChromaDB metadata does not support $contains on strings.
        """
        conditions: list[dict[str, Any]] = []

        # Exclude soft-deleted memories by default
        if not include_deleted:
            conditions.append({"deleted_ts": {"$eq": 0}})

        if memory_type is not None:
            conditions.append({"type": memory_type.value})

        if priority is not None:
            conditions.append({"priority": priority.value})

        if importance_min is not None:
            conditions.append({"importance": {"$gte": float(importance_min)}})

        if importance_max is not None:
            conditions.append({"importance": {"$lte": float(importance_max)}})

        if date_after:
            ts = self._iso_to_ts(date_after)
            if ts is not None:
                conditions.append({"created_ts": {"$gt": ts}})

        if date_before:
            ts = self._iso_to_ts(date_before)
            if ts is not None:
                conditions.append({"created_ts": {"$lt": ts}})

        if not conditions:
            return None
        if len(conditions) == 1:
            return conditions[0]
        return {"$and": conditions}

    @staticmethod
    def _iso_to_ts(iso_date: str) -> int | None:
        """Convert ISO date string to UNIX timestamp."""
        try:
            dt = datetime.fromisoformat(iso_date)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _filter_by_tags(
        results: list[dict[str, Any]],
        tags: list[str],
    ) -> list[dict[str, Any]]:
        """Post-filter results by tags (OR matching).

        Tags are stored as JSON-encoded strings in metadata.
        Returns results where ANY of the requested tags is present.
        """
        filtered = []
        for r in results:
            raw_tags = r.get("metadata", {}).get("tags", "[]")
            try:
                stored_tags = json.loads(raw_tags) if isinstance(raw_tags, str) else raw_tags
            except (json.JSONDecodeError, TypeError):
                stored_tags = []
            if any(t in stored_tags for t in tags):
                filtered.append(r)
        return filtered

    # ── BM25 Search ────────────────────────────────────────────────

    def _bm25_search(
        self,
        project: str,
        query: str,
        n_results: int,
        where_filter: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Keyword search using cached BM25 index.

        Uses _get_bm25_index() for cache-friendly index retrieval.
        Results include a synthetic 'distance' derived from BM25 score
        for compatibility with the rest of the pipeline.
        """
        index = self._get_bm25_index(project, where_filter)
        if index is None:
            return []

        try:
            bm25_results = index.search(query, n_results=n_results)
        except Exception:
            logger.exception("BM25 search failed on project %s", project)
            return []

        # Convert BM25 results to standard format (add synthetic distance)
        results: list[dict[str, Any]] = []
        for r in bm25_results:
            # Convert BM25 score to pseudo-distance: higher score = lower distance
            # Use 1/(1+score) to map score∈[0,∞) to distance∈(0,1]
            distance = 1.0 / (1.0 + r["score"]) if r["score"] > 0 else 1.0
            results.append({
                "id": r["id"],
                "content": r["content"],
                "metadata": r["metadata"],
                "distance": distance,
            })

        return results

    def _compute_rrf_weights(
        self,
        project: str,
        query: str,
        where_filter: dict[str, Any] | None = None,
    ) -> list[float] | None:
        """Compute dynamic RRF weights based on query specificity (H-2.1).

        Uses shared _get_bm25_index() to avoid duplicate index builds.

        Returns:
            [semantic_weight, bm25_weight] or None if computation fails.
        """
        try:
            index = self._get_bm25_index(project, where_filter)
            if index is None:
                return None

            bm25_scores = index.get_raw_scores(query)

            if not bm25_scores:
                return None

            specificity = compute_query_specificity(bm25_scores)
            weights = compute_dynamic_weights(specificity)

            logger.debug(
                "Dynamic RRF: specificity=%.3f → sem=%.2f, bm25=%.2f",
                specificity, weights[0], weights[1],
            )
            return weights

        except Exception:
            logger.debug("Dynamic RRF weight computation failed, using equal weights")
            return None

    def _search_all_collections(
        self,
        embedding: list[float],
        where_filter: dict[str, Any] | None,
        n_results: int,
        *,
        include_embeddings: bool = False,
    ) -> list[dict[str, Any]]:
        all_results: list[dict[str, Any]] = []
        for col_info in self._list_collection_names():
            if not col_info.startswith(COLLECTION_PREFIX):
                continue
            col = self._chroma.get_collection(col_info)
            per_col = min(n_results, 3)  # 프로젝트당 최대 3개
            results = self._query_collection(
                col, embedding, where_filter, per_col,
                include_embeddings=include_embeddings,
            )
            all_results.extend(results)

        # Sort by distance (lower = more relevant)
        all_results.sort(key=lambda r: r["distance"])
        return all_results[:n_results]

    def get_linked_memories(
        self,
        target_project: str,
        n_results: int = 10,
    ) -> list[dict[str, Any]]:
        """Get memories from other projects that are linked to target_project.

        Scans all collections for memories whose ``linked_projects`` metadata
        contains *target_project*.  Results are sorted by importance (highest
        first) and limited to *n_results*.
        """
        target_col_name = self._collection_name(target_project)
        all_linked: list[dict[str, Any]] = []

        for col_info in self._list_collection_names():
            if not col_info.startswith(COLLECTION_PREFIX):
                continue
            # Skip the target project's own collection
            if col_info == target_col_name:
                continue
            col = self._chroma.get_collection(col_info)
            if col.count() == 0:
                continue

            results = col.get(where={"deleted_ts": {"$eq": 0}}, include=["documents", "metadatas"])
            for i, meta in enumerate(results["metadatas"]):
                linked_raw = meta.get("linked_projects", "[]")
                try:
                    linked = json.loads(linked_raw) if isinstance(linked_raw, str) else []
                except (json.JSONDecodeError, TypeError):
                    linked = []
                if target_project in linked:
                    all_linked.append({
                        "id": results["ids"][i],
                        "content": results["documents"][i],
                        "metadata": meta,
                    })

        # Sort by importance (higher first)
        all_linked.sort(
            key=lambda x: float(x["metadata"].get("importance", 5.0)),
            reverse=True,
        )
        return all_linked[:n_results]

    def discover_cross_project_relevant(
        self,
        source_project: str,
        query: str,
        exclude_projects: set[str] | None = None,
        n_results: int | None = None,
        max_projects: int | None = None,
        distance_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        """Find relevant memories from other projects via semantic search.

        Used by auto_recall to surface cross-project knowledge.
        Skips source project, _global, and any projects in *exclude_projects*.

        Args:
            source_project: Current project (excluded from search).
            query: Semantic search query (typically the auto_recall context).
            exclude_projects: Additional projects to skip (already loaded).
            n_results: Max total results to return.
            max_projects: Max number of other projects to scan.
            distance_threshold: Max cosine distance to include.

        Returns:
            List of relevant memory dicts, sorted by distance (ascending).
        """
        from memory_mcp.constants import (
            CROSS_DISCOVERY_DISTANCE_THRESHOLD,
            CROSS_DISCOVERY_MAX_PROJECTS,
            CROSS_DISCOVERY_MAX_RESULTS,
            GLOBAL_PROJECT_NAME,
        )

        if not query:
            return []

        if n_results is None:
            n_results = CROSS_DISCOVERY_MAX_RESULTS
        if max_projects is None:
            max_projects = CROSS_DISCOVERY_MAX_PROJECTS
        if distance_threshold is None:
            distance_threshold = CROSS_DISCOVERY_DISTANCE_THRESHOLD

        embedding = self._embed(query)
        source_col_name = self._collection_name(source_project)
        global_col_name = self._collection_name(GLOBAL_PROJECT_NAME)

        exclude_col_names = {source_col_name, global_col_name}
        if exclude_projects:
            for p in exclude_projects:
                exclude_col_names.add(self._collection_name(p))

        all_results: list[dict[str, Any]] = []
        scanned = 0

        for col_info in self._list_collection_names():
            if scanned >= max_projects:
                break
            if not col_info.startswith(COLLECTION_PREFIX):
                continue
            if col_info in exclude_col_names:
                continue

            # Check searchable flag (cross-project visibility control)
            proj_name = col_info[len(COLLECTION_PREFIX):]
            if not self.get_project_searchable(proj_name):
                continue

            col = self._chroma.get_collection(col_info)
            if col.count() == 0:
                continue
            scanned += 1

            results = self._query_collection(
                col, embedding, where_filter=None, n_results=2,
            )
            for r in results:
                if r["distance"] < distance_threshold:
                    all_results.append(r)

        all_results.sort(key=lambda r: r["distance"])
        return all_results[:n_results]

    def detect_cross_project_pattern(
        self,
        source_project: str,
        embedding: list[float],
        threshold: float | None = None,
        min_matches: int | None = None,
        max_scan: int | None = None,
    ) -> list[dict[str, Any]]:
        """Check if similar content exists in multiple other projects.

        Uses a pre-computed embedding (from duplicate check) to query
        other project collections. If similar content is found in
        *min_matches* or more distinct projects, returns the matches.

        Args:
            source_project: Project the memory is being stored in.
            embedding: Pre-computed embedding for the stored content.
            threshold: Max cosine distance to consider "similar".
            min_matches: Min number of other projects with similar content.
            max_scan: Max collections to scan.

        Returns:
            List of matches with project/content/distance, or empty list.
        """
        from memory_mcp.constants import (
            CROSS_PROJECT_MAX_SCAN,
            CROSS_PROJECT_MIN_MATCHES,
            CROSS_PROJECT_SIMILARITY_THRESHOLD,
            GLOBAL_PROJECT_NAME,
        )

        if threshold is None:
            threshold = CROSS_PROJECT_SIMILARITY_THRESHOLD
        if min_matches is None:
            min_matches = CROSS_PROJECT_MIN_MATCHES
        if max_scan is None:
            max_scan = CROSS_PROJECT_MAX_SCAN

        source_col_name = self._collection_name(source_project)
        global_col_name = self._collection_name(GLOBAL_PROJECT_NAME)
        matches: list[dict[str, Any]] = []
        scanned = 0

        for col_info in self._list_collection_names():
            if scanned >= max_scan:
                break
            if not col_info.startswith(COLLECTION_PREFIX):
                continue
            if col_info == source_col_name:
                continue
            if col_info == global_col_name:
                continue

            col = self._chroma.get_collection(col_info)
            if col.count() == 0:
                continue
            scanned += 1

            results = self._query_collection(
                col, embedding, where_filter=None, n_results=1,
            )
            if results and results[0]["distance"] < threshold:
                project_name = col_info[len(COLLECTION_PREFIX):]
                matches.append({
                    "project": project_name,
                    "content": results[0]["content"],
                    "distance": results[0]["distance"],
                })

        if len(matches) >= min_matches:
            return matches
        return []

    def _query_collection(
        self,
        col: chromadb.Collection,
        embedding: list[float],
        where_filter: dict[str, Any] | None,
        n_results: int,
        *,
        include_embeddings: bool = False,
    ) -> list[dict[str, Any]]:
        if col.count() == 0:
            return []

        # n_results cannot exceed collection count
        actual_n = min(n_results, col.count())

        include_fields = ["documents", "metadatas", "distances"]
        if include_embeddings:
            include_fields.append("embeddings")

        kwargs: dict[str, Any] = {
            "query_embeddings": [embedding],
            "n_results": actual_n,
            "include": include_fields,
        }
        if where_filter:
            kwargs["where"] = where_filter

        try:
            raw = col.query(**kwargs)
        except Exception:
            logger.exception("Query failed on collection %s", col.name)
            return []

        results: list[dict[str, Any]] = []
        if not raw["documents"] or not raw["documents"][0]:
            return results

        for i, doc in enumerate(raw["documents"][0]):
            item: dict[str, Any] = {
                "id": raw["ids"][0][i],
                "content": doc,
                "metadata": raw["metadatas"][0][i] if raw["metadatas"] else {},
                "distance": raw["distances"][0][i] if raw["distances"] else 0.0,
            }
            if (
                include_embeddings
                and raw.get("embeddings") is not None
                and len(raw["embeddings"]) > 0
                and len(raw["embeddings"][0]) > i
            ):
                emb = raw["embeddings"][0][i]
                # Convert numpy array to list if needed
                item["embedding"] = emb.tolist() if hasattr(emb, "tolist") else list(emb)
            results.append(item)
        return results

    # ── MMR (Maximum Marginal Relevance) ──────────────────────────

    @staticmethod
    def _mmr_rerank(
        query_embedding: list[float],
        results: list[dict[str, Any]],
        n_results: int,
        lambda_param: float = DEFAULT_MMR_LAMBDA,
    ) -> list[dict[str, Any]]:
        """Re-rank results using Maximum Marginal Relevance.

        Balances relevance to query with diversity among selected results.
        lambda_param: 0=full diversity, 1=full relevance (default 0.7).
        """
        if not results or n_results <= 0:
            return []
        if len(results) <= n_results:
            # Strip embeddings from output
            for r in results:
                r.pop("embedding", None)
            return results

        # Build embedding matrix
        candidates_with_emb = [r for r in results if "embedding" in r]
        if not candidates_with_emb:
            # No embeddings available — fall back to top-N by distance
            return results[:n_results]

        q_emb = np.array(query_embedding, dtype=np.float32)
        emb_matrix = np.array(
            [r["embedding"] for r in candidates_with_emb], dtype=np.float32,
        )

        # Cosine similarity to query (embeddings are already normalized)
        query_sim = emb_matrix @ q_emb

        selected_indices: list[int] = []
        remaining = set(range(len(candidates_with_emb)))

        for _ in range(min(n_results, len(candidates_with_emb))):
            best_idx = -1
            best_score = -math.inf

            for idx in remaining:
                relevance = float(query_sim[idx])

                # Max similarity to already selected
                if selected_indices:
                    sel_embs = emb_matrix[selected_indices]
                    sim_to_selected = float(np.max(emb_matrix[idx] @ sel_embs.T))
                else:
                    sim_to_selected = 0.0

                mmr_score = lambda_param * relevance - (1 - lambda_param) * sim_to_selected
                if mmr_score > best_score:
                    best_score = mmr_score
                    best_idx = idx

            if best_idx == -1:
                break
            selected_indices.append(best_idx)
            remaining.discard(best_idx)

        # Return selected results (strip embeddings)
        reranked = [candidates_with_emb[i] for i in selected_indices]
        for r in reranked:
            r.pop("embedding", None)
        return reranked

    # ── Time-Weighted Retrieval ───────────────────────────────────

    @staticmethod
    def _apply_time_decay(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Apply time-decay weighting to search distances.

        formula: adjusted_distance = distance * (1 + decay_rate) ^ hours_passed

        ACT-R inspired enhancements (Phase 10):
        - Uses *effective* importance (stored + usage bonus) for decay rate,
          so frequently accessed memories decay slower (rehearsal effect).
        - Uses max(created_at, last_accessed_at) as time reference,
          so recently accessed memories are treated as "fresh".
        """
        from memory_mcp.importance.scorer import (
            compute_effective_importance,
            importance_to_decay_rate,
        )

        now = datetime.now(timezone.utc)

        for r in results:
            meta = r.get("metadata", {})

            # Phase 10: effective importance (with usage bonus) for decay rate
            importance = meta.get("importance")
            if isinstance(importance, (int, float)):
                recall_count = meta.get("recall_count", 0)
                search_count = meta.get("search_count", 0)
                effective_imp = compute_effective_importance(
                    float(importance),
                    int(recall_count) if isinstance(recall_count, int) else 0,
                    int(search_count) if isinstance(search_count, int) else 0,
                )
                decay_rate = importance_to_decay_rate(effective_imp)
            else:
                priority = meta.get("priority", "normal")
                decay_rate = DECAY_RATES.get(priority, DECAY_RATES["normal"])

            if decay_rate == 0.0:
                continue  # No decay for high-importance

            # Phase 10: use max(created_at, last_accessed_at) as time reference
            # Recently accessed memories are treated as "fresh" (ACT-R rehearsal)
            created_at = meta.get("created_at", "")
            last_accessed_at = meta.get("last_accessed_at", "")

            ref_dt = None
            for ts_str in (last_accessed_at, created_at):
                if ts_str:
                    try:
                        dt = datetime.fromisoformat(ts_str)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        if ref_dt is None or dt > ref_dt:
                            ref_dt = dt
                    except (ValueError, TypeError):
                        continue

            hours = max(0.0, (now - ref_dt).total_seconds() / 3600) if ref_dt else 0.0

            # Apply decay: increase distance for older items
            original_distance = r.get("distance", 0.0)
            if original_distance >= 0:
                r["original_distance"] = original_distance
                r["distance"] = original_distance * (1 + decay_rate) ** hours

        # Re-sort by adjusted distance
        results.sort(key=lambda r: r["distance"])
        return results

    @staticmethod
    def _apply_retrieval_score(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Enrich results with composite retrieval score (Phase 9).

        Computes retrieval_score = α·relevance + β·importance + γ·recency
        and adds it to each result. Does NOT re-sort; ordering is preserved
        from the previous pipeline stage (time_decay, MMR, etc.).

        Uses original_distance (pre time-decay) for relevance if available.
        """
        from memory_mcp.constants import PRIORITY_TO_IMPORTANCE
        from memory_mcp.importance.scorer import (
            compute_effective_importance,
            compute_retrieval_score,
            importance_to_decay_rate,
        )

        now = datetime.now(timezone.utc)

        for r in results:
            meta = r.get("metadata", {})

            # 1. Relevance (from cosine distance)
            distance = r.get("original_distance", r.get("distance", 0.0))
            if distance < 0:
                relevance = 1.0  # Non-search results (e.g., get_recent)
            else:
                relevance = max(0.0, 1.0 - distance)

            # 2. Effective importance
            importance = meta.get("importance")
            if not isinstance(importance, (int, float)):
                priority_str = meta.get("priority", "normal")
                importance = PRIORITY_TO_IMPORTANCE.get(priority_str, IMPORTANCE_DEFAULT)

            recall_count = meta.get("recall_count", 0)
            search_count = meta.get("search_count", 0)
            effective_imp = compute_effective_importance(
                float(importance),
                int(recall_count) if isinstance(recall_count, int) else 0,
                int(search_count) if isinstance(search_count, int) else 0,
            )
            normalized_importance = (effective_imp - IMPORTANCE_MIN) / (
                IMPORTANCE_MAX - IMPORTANCE_MIN
            )

            # 3. Recency — use max(created_at, last_accessed_at) like _apply_time_decay
            # Recently accessed memories are treated as "fresh" (ACT-R rehearsal)
            created_at = meta.get("created_at", "")
            last_accessed_at = meta.get("last_accessed_at", "")

            ref_dt = None
            for ts_str in (last_accessed_at, created_at):
                if ts_str:
                    try:
                        dt = datetime.fromisoformat(ts_str)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        if ref_dt is None or dt > ref_dt:
                            ref_dt = dt
                    except (ValueError, TypeError):
                        continue

            hours = max(0.0, (now - ref_dt).total_seconds() / 3600) if ref_dt else 0.0

            decay_rate = importance_to_decay_rate(effective_imp)
            recency = math.exp(-decay_rate * hours)

            # 4. Composite score
            r["retrieval_score"] = compute_retrieval_score(
                relevance, normalized_importance, recency,
            )

        return results

    # ── Get Recent (time-based) ─────────────────────────────────────

    def get_recent(
        self,
        project: str,
        memory_type: MemoryType | None = None,
        n_results: int = 5,
    ) -> list[dict[str, Any]]:
        """Get the most recent memories sorted by creation time (newest first).

        Unlike search(), this does NOT use semantic similarity — it simply
        returns the N most recently created memories.
        """
        col = self._get_collection(project)
        if col.count() == 0:
            return []

        kwargs: dict[str, Any] = {"include": ["documents", "metadatas"]}
        if memory_type:
            kwargs["where"] = {"$and": [{"type": memory_type.value}, {"deleted_ts": {"$eq": 0}}]}
        else:
            kwargs["where"] = {"deleted_ts": {"$eq": 0}}

        try:
            raw = col.get(**kwargs)
        except Exception:
            logger.exception("get_recent failed on project %s", project)
            return []

        results: list[dict[str, Any]] = []
        for i, doc_id in enumerate(raw["ids"]):
            meta = raw["metadatas"][i] if raw["metadatas"] else {}
            results.append({
                "id": doc_id,
                "content": raw["documents"][i] if raw["documents"] else "",
                "metadata": meta,
                "distance": -1.0,  # Not applicable for time-based retrieval
            })

        # Sort by created_at descending (newest first)
        results.sort(
            key=lambda r: r["metadata"].get("created_at", ""),
            reverse=True,
        )
        return results[:n_results]

    def get_recent_by_other_sessions(
        self,
        project: str,
        current_session_id: str,
        n_results: int = 5,
    ) -> list[dict[str, Any]]:
        """Get recent memories created by OTHER sessions.

        Returns memories that have a session_id different from
        ``current_session_id``, sorted by creation time (newest first).
        Useful for notifying users about concurrent changes.
        """
        col = self._get_collection(project)
        if col.count() == 0:
            return []

        # Always use Python-side filtering because ChromaDB's $ne operator
        # also returns documents that lack the session_id field entirely.
        try:
            raw = col.get(where={"deleted_ts": {"$eq": 0}}, include=["documents", "metadatas"])
        except Exception:
            logger.exception("get_recent_by_other_sessions failed on project %s", project)
            return []

        filtered_ids = []
        filtered_docs = []
        filtered_metas = []
        for i, doc_id in enumerate(raw["ids"]):
            meta = raw["metadatas"][i] if raw["metadatas"] else {}
            sid = meta.get("session_id", "")
            if sid and sid != current_session_id:
                filtered_ids.append(doc_id)
                filtered_docs.append(raw["documents"][i] if raw["documents"] else "")
                filtered_metas.append(meta)
        raw = {"ids": filtered_ids, "documents": filtered_docs, "metadatas": filtered_metas}

        results: list[dict[str, Any]] = []
        for i, doc_id in enumerate(raw["ids"]):
            meta = raw["metadatas"][i] if raw["metadatas"] else {}
            results.append({
                "id": doc_id,
                "content": raw["documents"][i] if raw["documents"] else "",
                "metadata": meta,
                "distance": -1.0,
            })

        results.sort(
            key=lambda r: r["metadata"].get("created_at", ""),
            reverse=True,
        )
        return results[:n_results]

    # ── Get by Priority ──────────────────────────────────────────────

    def get_by_priority(
        self,
        project: str,
        priority: MemoryPriority,
        n_results: int = 50,
    ) -> list[dict[str, Any]]:
        """Get all memories with a specific priority level.

        Returns memories sorted by creation time (newest first).
        Useful for loading all CRITICAL memories at session start.
        """
        col = self._get_collection(project)
        if col.count() == 0:
            return []

        try:
            raw = col.get(
                where={"$and": [{"priority": priority.value}, {"deleted_ts": {"$eq": 0}}]},
                include=["documents", "metadatas"],
            )
        except Exception:
            logger.exception("get_by_priority failed on project %s", project)
            return []

        results: list[dict[str, Any]] = []
        for i, doc_id in enumerate(raw["ids"]):
            meta = raw["metadatas"][i] if raw["metadatas"] else {}
            results.append({
                "id": doc_id,
                "content": raw["documents"][i] if raw["documents"] else "",
                "metadata": meta,
                "distance": -1.0,  # Not applicable for priority-based retrieval
            })

        # Sort by created_at descending (newest first)
        results.sort(
            key=lambda r: r["metadata"].get("created_at", ""),
            reverse=True,
        )
        return results[:n_results]

    # ── Get by Importance ────────────────────────────────────────────

    def get_by_importance(
        self,
        project: str,
        min_importance: float | None = None,
        max_importance: float | None = None,
        n_results: int = 50,
    ) -> list[dict[str, Any]]:
        """Get memories filtered by importance score range.

        Returns memories sorted by creation time (newest first).
        Useful for loading high-importance memories at session start.

        Args:
            project: Project identifier.
            min_importance: Minimum importance (inclusive). None=no lower bound.
            max_importance: Maximum importance (inclusive). None=no upper bound.
            n_results: Maximum number of results.
        """
        col = self._get_collection(project)
        if col.count() == 0:
            return []

        where = self._build_where(
            importance_min=min_importance,
            importance_max=max_importance,
        )

        kwargs: dict[str, Any] = {"include": ["documents", "metadatas"]}
        if where:
            kwargs["where"] = where

        try:
            raw = col.get(**kwargs)
        except Exception:
            logger.exception("get_by_importance failed on project %s", project)
            return []

        results: list[dict[str, Any]] = []
        for i, doc_id in enumerate(raw["ids"]):
            meta = raw["metadatas"][i] if raw["metadatas"] else {}
            results.append({
                "id": doc_id,
                "content": raw["documents"][i] if raw["documents"] else "",
                "metadata": meta,
                "distance": -1.0,  # Not applicable for importance-based retrieval
            })

        # Sort by created_at descending (newest first)
        results.sort(
            key=lambda r: r["metadata"].get("created_at", ""),
            reverse=True,
        )
        return results[:n_results]

    def get_by_tags(
        self,
        project: str,
        tags: list[str],
        n_results: int = 20,
    ) -> list[dict[str, Any]]:
        """Get memories matching ANY of the given tags (OR matching).

        Returns results sorted by importance (descending), then creation
        time (newest first).  Used for forced infrastructure memory
        inclusion in session continuity checks.
        """
        if not self.project_exists(project):
            return []
        col = self._get_collection(project)
        if col.count() == 0:
            return []

        try:
            raw = col.get(where={"deleted_ts": {"$eq": 0}}, include=["documents", "metadatas"])
        except Exception:
            logger.exception("get_by_tags failed on project %s", project)
            return []

        results: list[dict[str, Any]] = []
        for i, doc_id in enumerate(raw["ids"]):
            meta = raw["metadatas"][i] if raw["metadatas"] else {}
            results.append({
                "id": doc_id,
                "content": raw["documents"][i] if raw["documents"] else "",
                "metadata": meta,
                "distance": -1.0,
            })

        # Post-filter by tags
        results = self._filter_by_tags(results, tags)

        # Sort by importance desc, then created_at desc
        def _sort_key(r: dict[str, Any]) -> tuple[float, str]:
            imp = r["metadata"].get("importance", 5.0)
            if not isinstance(imp, (int, float)):
                imp = 5.0
            created = r["metadata"].get("created_at", "")
            return (float(imp), created)

        results.sort(key=_sort_key, reverse=True)
        return results[:n_results]

    # ── Delete ───────────────────────────────────────────────────────

    def get_by_id(self, project: str, memory_id: str) -> dict[str, Any] | None:
        """Get a single memory by ID. Returns dict or None if not found."""
        col = self._get_collection(project)
        try:
            raw = col.get(ids=[memory_id], include=["documents", "metadatas"])
            if raw["ids"]:
                return {
                    "id": raw["ids"][0],
                    "content": raw["documents"][0] if raw["documents"] else "",
                    "metadata": raw["metadatas"][0] if raw["metadatas"] else {},
                }
        except Exception:
            pass
        return None

    def delete(self, project: str, memory_id: str) -> bool:
        """Soft-delete a memory by setting deleted_ts. Returns True if done."""
        col = self._get_collection(project)
        try:
            existing = col.get(ids=[memory_id], include=["metadatas"])
            if not existing["ids"]:
                return False
            meta = existing["metadatas"][0]
            now_ts = int(datetime.now(timezone.utc).timestamp())
            col.update(ids=[memory_id], metadatas=[{**meta, "deleted_ts": now_ts}])
            self._invalidate_bm25(project)
            logger.info("Soft-deleted memory %s from project %s", memory_id, project)
            return True
        except Exception:
            logger.exception("Soft-delete failed for %s", memory_id)
            return False

    def trim_trash(self, max_items: int = 300) -> int:
        """Remove oldest trash items if total exceeds max_items (FIFO)."""
        all_trash = self.list_trash(project=None, limit=max_items + 500)
        if len(all_trash) <= max_items:
            return 0
        # Sort by deleted_ts ascending (oldest first), purge excess
        all_trash.sort(key=lambda x: x["deleted_ts"])
        excess = all_trash[:len(all_trash) - max_items]
        purged = 0
        for item in excess:
            if self.purge_memory(item["project"], item["id"]):
                purged += 1
        if purged > 0:
            logger.info("Trash trimmed: %d oldest items purged (max=%d)", purged, max_items)
        return purged

    def restore_memory(self, project: str, memory_id: str) -> bool:
        """Restore a soft-deleted memory. Returns True if restored."""
        col = self._get_collection(project)
        try:
            existing = col.get(ids=[memory_id], include=["metadatas"])
            if not existing["ids"]:
                return False
            meta = existing["metadatas"][0]
            col.update(ids=[memory_id], metadatas=[{**meta, "deleted_ts": 0}])
            self._invalidate_bm25(project)
            logger.info("Restored memory %s in project %s", memory_id, project)
            return True
        except Exception:
            logger.exception("Restore failed for %s", memory_id)
            return False

    def purge_memory(self, project: str, memory_id: str) -> bool:
        """Permanently delete a memory from ChromaDB. Returns True if done."""
        col = self._get_collection(project)
        try:
            col.delete(ids=[memory_id])
            self._invalidate_bm25(project)
            logger.info("Purged memory %s from project %s", memory_id, project)
            return True
        except Exception:
            logger.exception("Purge failed for %s", memory_id)
            return False

    def list_trash(self, project: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """List soft-deleted memories, optionally filtered by project."""
        results: list[dict[str, Any]] = []
        projects = [project] if project else self.list_projects()
        for proj in projects:
            col = self._get_collection(proj)
            data = col.get(
                where={"deleted_ts": {"$gt": 0}},
                include=["metadatas", "documents"],
            )
            for i, mid in enumerate(data["ids"]):
                meta = data["metadatas"][i] if data["metadatas"] else {}
                doc = data["documents"][i] if data["documents"] else ""
                results.append({
                    "id": mid,
                    "project": proj,
                    "content": doc[:200] if doc else "",
                    "deleted_ts": meta.get("deleted_ts", 0),
                    "type": meta.get("type", ""),
                    "importance": meta.get("importance", 0),
                })
        results.sort(key=lambda x: x["deleted_ts"], reverse=True)
        return results[:limit]

    def purge_expired_trash(self, retention_days: int) -> dict[str, int]:
        """Permanently delete memories whose deleted_ts is older than retention."""
        from datetime import timedelta
        cutoff_ts = int((datetime.now(timezone.utc) - timedelta(days=retention_days)).timestamp())
        purged = 0
        for proj in self.list_projects():
            col = self._get_collection(proj)
            data = col.get(
                where={"$and": [{"deleted_ts": {"$gt": 0}}, {"deleted_ts": {"$lt": cutoff_ts}}]},
                include=[],
            )
            if data["ids"]:
                col.delete(ids=data["ids"])
                purged += len(data["ids"])
                self._invalidate_bm25(proj)
        return {"purged_count": purged}

    def _active_count(self, project: str) -> int:
        """Count non-deleted memories in a project."""
        col = self._get_collection(project)
        data = col.get(where={"deleted_ts": {"$eq": 0}}, include=[])
        return len(data["ids"])

    # ── Usage Tracking ────────────────────────────────────────────────

    def update_usage_counters(
        self,
        project: str,
        memory_ids: list[str],
        counter: str,
    ) -> int:
        """Increment a usage counter for specific memories.

        Args:
            project: Project identifier.
            memory_ids: List of memory IDs to update.
            counter: Counter field name ("recall_count" or "search_count").

        Returns:
            Number of successfully updated memories.
        """
        if not memory_ids or counter not in ("recall_count", "search_count"):
            return 0

        col = self._get_collection(project)
        updated = 0

        # Batch fetch all metadata
        try:
            raw = col.get(ids=memory_ids, include=["metadatas"])
        except Exception:
            logger.exception("update_usage_counters fetch failed for project %s", project)
            return 0

        now = datetime.now(timezone.utc)

        for i, doc_id in enumerate(raw["ids"]):
            meta = raw["metadatas"][i] if raw["metadatas"] else {}
            new_meta = dict(meta)
            current_val = meta.get(counter, 0)
            new_meta[counter] = (int(current_val) if isinstance(current_val, int) else 0) + 1
            new_meta["last_accessed_at"] = now.isoformat()

            try:
                col.update(ids=[doc_id], metadatas=[new_meta])
                updated += 1
            except Exception:
                logger.exception("update_usage_counters failed for %s", doc_id)

        if updated > 0:
            logger.debug(
                "Updated %s for %d/%d memories in %s",
                counter, updated, len(memory_ids), project,
            )
        return updated

    # ── Project Brief (Lazy Retrieval) ────────────────────────────────

    def get_project_brief(self, project: str) -> dict[str, Any]:
        """Generate a minimal project brief for lazy recall.

        Only 2 ChromaDB queries: metadata-only for counts + latest summary.
        Returns a compact dict suitable for ~100-200 token output.

        Returns:
            Dict with:
            - memory_count: total memories
            - critical_count: memories with importance >= 9.0
            - type_counts: dict of memory_type -> count
            - topic_keywords: top tags (excluding 'auto-saved')
            - last_session_date: date of most recent summary (or None)
            - last_summary_snippet: first N chars of most recent summary
            - unreviewed_count: memories with 'unreviewed' tag
            - pending_task_count: memories with 'task' AND 'pending' tags
        """
        from memory_mcp.constants import (
            BRIEF_SUMMARY_SNIPPET_LEN,
            IMPORTANCE_CRITICAL_THRESHOLD,
        )

        col = self._get_collection(project)
        count = col.count()

        if count == 0:
            return {
                "memory_count": 0,
                "critical_count": 0,
                "type_counts": {},
                "topic_keywords": [],
                "last_session_date": None,
                "last_summary_snippet": None,
                "unreviewed_count": 0,
                "pending_task_count": 0,
            }

        # Query 1: all metadata (no documents/embeddings — lightweight)
        all_meta = col.get(where={"deleted_ts": {"$eq": 0}}, include=["metadatas"])
        metas = all_meta.get("metadatas") or []

        critical_count = 0
        unreviewed_count = 0
        pending_task_count = 0
        type_counts: dict[str, int] = {}
        tag_freq: dict[str, int] = {}

        for meta in metas:
            if not meta:
                continue
            # Count by type
            mtype = meta.get("type", "fact")
            type_counts[mtype] = type_counts.get(mtype, 0) + 1

            # Count critical
            imp = meta.get("importance")
            if isinstance(imp, (int, float)) and float(imp) >= IMPORTANCE_CRITICAL_THRESHOLD:
                critical_count += 1

            # Count tags
            raw_tags = meta.get("tags", "[]")
            if isinstance(raw_tags, str):
                try:
                    raw_tags = json.loads(raw_tags)
                except (json.JSONDecodeError, TypeError):
                    raw_tags = []
            if isinstance(raw_tags, list):
                if "unreviewed" in raw_tags:
                    unreviewed_count += 1
                if "task" in raw_tags and "pending" in raw_tags:
                    pending_task_count += 1
                for tag in raw_tags:
                    if tag and tag != "auto-saved":
                        tag_freq[tag] = tag_freq.get(tag, 0) + 1

        # Top tags by frequency
        top_tags = sorted(tag_freq.items(), key=lambda x: x[1], reverse=True)[:8]
        topic_keywords = [tag for tag, _ in top_tags]

        # Query 2: most recent summary
        last_session_date = None
        last_summary_snippet = None

        try:
            summaries = self.get_recent(
                project,
                memory_type=MemoryType.SUMMARY,
                n_results=1,
            )
            if summaries:
                latest = summaries[0]
                last_session_date = latest.get("metadata", {}).get("created_at", "")[:10]
                content = latest.get("content", "")
                if len(content) > BRIEF_SUMMARY_SNIPPET_LEN:
                    last_summary_snippet = content[:BRIEF_SUMMARY_SNIPPET_LEN] + "..."
                else:
                    last_summary_snippet = content
        except Exception:
            logger.debug("get_project_brief: failed to fetch latest summary for %s", project)

        return {
            "memory_count": count,
            "critical_count": critical_count,
            "type_counts": type_counts,
            "topic_keywords": topic_keywords,
            "last_session_date": last_session_date,
            "last_summary_snippet": last_summary_snippet,
            "unreviewed_count": unreviewed_count,
            "pending_task_count": pending_task_count,
        }

    def get_by_tag(
        self,
        project: str,
        tag: str,
        n_results: int = 50,
    ) -> list[dict[str, Any]]:
        """Return memories that contain a specific tag (sorted by created_ts desc).

        Args:
            project: Project identifier.
            tag: Tag to filter by (exact match within tags list).
            n_results: Maximum results to return.

        Returns:
            List of memory dicts with id, content, and metadata.
        """
        col = self._get_collection(project)
        if col.count() == 0:
            return []

        all_data = col.get(where={"deleted_ts": {"$eq": 0}}, include=["documents", "metadatas"])
        results: list[dict[str, Any]] = []

        for i, meta in enumerate(all_data.get("metadatas") or []):
            if not meta:
                continue
            raw_tags = meta.get("tags", "[]")
            if isinstance(raw_tags, str):
                try:
                    parsed_tags = json.loads(raw_tags)
                except (json.JSONDecodeError, TypeError):
                    parsed_tags = []
            else:
                parsed_tags = raw_tags if isinstance(raw_tags, list) else []

            if tag in parsed_tags:
                results.append({
                    "id": all_data["ids"][i],
                    "content": all_data["documents"][i],
                    "metadata": meta,
                })

        results.sort(
            key=lambda r: r["metadata"].get("created_ts", 0),
            reverse=True,
        )
        return results[:n_results]

    # ── Workspace Paths ──────────────────────────────────────────────

    def get_all_workspace_paths(self) -> dict[str, str]:
        """Return project_id → workspace_path mapping for all projects.

        Searches each project for memories tagged ``['workspace', 'path']``
        and parses the content format ``워크스페이스 경로: /absolute/path``.

        Returns:
            Dict mapping project IDs to their workspace absolute paths.
            Projects without workspace paths are omitted.
        """
        result: dict[str, str] = {}
        for project in self.list_projects():
            memories = self.get_by_tag(project, "workspace", n_results=5)
            for mem in memories:
                raw_tags = mem.get("metadata", {}).get("tags", "[]")
                if isinstance(raw_tags, str):
                    try:
                        tags = json.loads(raw_tags)
                    except (json.JSONDecodeError, TypeError):
                        tags = []
                else:
                    tags = raw_tags if isinstance(raw_tags, list) else []

                if "path" not in tags:
                    continue

                content = mem.get("content", "")
                if "워크스페이스 경로:" in content:
                    path = content.split("워크스페이스 경로:", 1)[1].strip()
                    if path:
                        result[project] = path
                        break  # one workspace per project
        return result

    # ── Storage Info ─────────────────────────────────────────────────

    def get_project_storage_info(self, project: str) -> dict[str, Any]:
        """Get storage size metrics for a single project.

        Returns:
            Dict with raw data sizes:
            - memory_count: number of memories
            - content_bytes: total raw text size (UTF-8)
            - metadata_bytes: estimated metadata JSON size
            - embedding_bytes: estimated embedding storage (count * dim * 4)
            - total_estimated_bytes: sum of above
            - embedding_dim: embedding vector dimension
        """
        col = self._get_collection(project)
        count = col.count()

        if count == 0:
            return {
                "memory_count": 0,
                "content_bytes": 0,
                "metadata_bytes": 0,
                "embedding_bytes": 0,
                "total_estimated_bytes": 0,
                "embedding_dim": self._embedding_dim,
            }

        all_data = col.get(where={"deleted_ts": {"$eq": 0}}, include=["documents", "metadatas"])
        docs = all_data["documents"] or []
        metas = all_data["metadatas"] or []

        content_bytes = sum(len(d.encode("utf-8")) for d in docs if d)
        metadata_bytes = sum(len(json.dumps(m).encode("utf-8")) for m in metas if m)
        embedding_bytes = count * self._embedding_dim * 4  # float32

        return {
            "memory_count": count,
            "content_bytes": content_bytes,
            "metadata_bytes": metadata_bytes,
            "embedding_bytes": embedding_bytes,
            "total_estimated_bytes": content_bytes + metadata_bytes + embedding_bytes,
            "embedding_dim": self._embedding_dim,
        }

    def get_all_storage_info(self) -> dict[str, Any]:
        """Get storage info for all projects plus disk usage.

        Returns:
            Dict with per-project breakdown and totals:
            - projects: dict of project_name -> storage info
            - totals: aggregated storage across all projects
            - disk_usage_bytes: actual disk usage of db_path directory
        """
        projects = self.list_projects()
        project_storage: dict[str, Any] = {}
        totals = {
            "memory_count": 0,
            "content_bytes": 0,
            "metadata_bytes": 0,
            "embedding_bytes": 0,
            "total_estimated_bytes": 0,
        }

        for p in projects:
            info = self.get_project_storage_info(p)
            project_storage[p] = info
            for key in totals:
                totals[key] += info.get(key, 0)

        totals["embedding_dim"] = self._embedding_dim

        # Measure actual disk usage
        disk_bytes = self._get_disk_usage()

        return {
            "projects": project_storage,
            "totals": totals,
            "disk_usage_bytes": disk_bytes,
        }

    def _get_disk_usage(self) -> int:
        """Calculate total disk usage of the ChromaDB data directory."""
        db_dir = Path(self._db_path)
        if not db_dir.exists():
            return 0
        total = 0
        for f in db_dir.rglob("*"):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
        return total

    # ── Token & Usage Stats ──────────────────────────────────────────

    def get_project_token_stats(self, project: str) -> dict[str, Any]:
        """Get token usage estimates and kandela usage statistics.

        Token estimation: mixed Korean/English text ≈ 2 chars per token.
        Overhead & benefit numbers based on V2 benchmark measured data:
        - Memory overhead per session: ~1,396 tokens
        - Token saving per session: ~19,560 tokens (28.8% reduction)
        - Cost saving per session: ~$0.185

        Returns:
            Dict with token/cost estimates and usage counters.
        """
        # ── V2 benchmark measured constants ──────────────────────
        CHARS_PER_TOKEN = 2           # Korean-heavy mixed text (conservative)
        AUTO_RECALL_TOKENS = 260      # brief mode auto_recall response
        GUIDE_TOKENS = 300            # CLAUDE.md guide section
        CONTEXT_SEARCH_TOKENS = 120   # per search call (3 results)
        SESSION_SUMMARY_TOKENS = 170  # end-of-session summary store
        OVERHEAD_BASE_PER_SESSION = AUTO_RECALL_TOKENS + GUIDE_TOKENS + SESSION_SUMMARY_TOKENS  # 730
        # Benefit: V2 benchmark measured 19,560 tok/session saving
        # Conservative: use 60% of measured value for estimation
        BENEFIT_PER_SESSION = 11_700  # conservative session saving
        # API pricing: Sonnet $3/M input + $15/M output
        # Overhead is input tokens, benefit is mixed (~$9/M weighted avg)
        OVERHEAD_PRICE_PER_M = 3.0    # overhead = input tokens
        BENEFIT_PRICE_PER_M = 9.0     # saving = mixed input+output

        col = self._get_collection(project)
        count = col.count()

        zero = {
            "memory_count": 0,
            "total_content_chars": 0,
            "estimated_tokens_stored": 0,
            "total_recalls": 0,
            "total_searches": 0,
            "avg_content_chars": 0,
            "guide_tokens_per_session": GUIDE_TOKENS,
            "estimated_recall_tokens": 0,
            "estimated_benefit_tokens": 0,
            "overhead_tokens": 0,
            "cost_usd": 0.0,
            "benefit_usd": 0.0,
            "net_saving_usd": 0.0,
        }
        if count == 0:
            return zero

        all_data = col.get(where={"deleted_ts": {"$eq": 0}}, include=["documents", "metadatas"])
        docs = all_data["documents"] or []
        metas = all_data["metadatas"] or []

        total_chars = sum(len(d) for d in docs if d)
        total_recalls = sum(
            int(m.get("recall_count", 0)) for m in metas if m
        )
        total_searches = sum(
            int(m.get("search_count", 0)) for m in metas if m
        )

        avg_chars = total_chars / count if count else 0
        estimated_tokens_stored = total_chars // CHARS_PER_TOKEN

        # Overhead: base per session (recall) + per search call
        overhead_tokens = (
            total_recalls * OVERHEAD_BASE_PER_SESSION
            + total_searches * CONTEXT_SEARCH_TOKENS
        )

        # Benefit: each recall session saves ~11.7K tokens (conservative)
        estimated_benefit_tokens = total_recalls * BENEFIT_PER_SESSION

        # Estimate recall tokens per session
        avg_items_per_recall = min(count, 10)
        estimated_recall_tokens = int(avg_items_per_recall * avg_chars // CHARS_PER_TOKEN)

        # USD cost/benefit
        cost_usd = overhead_tokens / 1_000_000 * OVERHEAD_PRICE_PER_M
        benefit_usd = estimated_benefit_tokens / 1_000_000 * BENEFIT_PRICE_PER_M
        net_saving_usd = benefit_usd - cost_usd

        return {
            "memory_count": count,
            "total_content_chars": total_chars,
            "estimated_tokens_stored": estimated_tokens_stored,
            "total_recalls": total_recalls,
            "total_searches": total_searches,
            "avg_content_chars": int(avg_chars),
            "guide_tokens_per_session": GUIDE_TOKENS,
            "estimated_recall_tokens": estimated_recall_tokens,
            "estimated_benefit_tokens": estimated_benefit_tokens,
            "overhead_tokens": overhead_tokens,
            "cost_usd": round(cost_usd, 4),
            "benefit_usd": round(benefit_usd, 4),
            "net_saving_usd": round(net_saving_usd, 4),
            # Formula breakdown for transparency
            "overhead_breakdown": {
                "auto_recall": {"per_unit": AUTO_RECALL_TOKENS, "count": total_recalls,
                                "total": AUTO_RECALL_TOKENS * total_recalls},
                "guide_section": {"per_unit": GUIDE_TOKENS, "count": total_recalls,
                                  "total": GUIDE_TOKENS * total_recalls},
                "session_summary": {"per_unit": SESSION_SUMMARY_TOKENS, "count": total_recalls,
                                    "total": SESSION_SUMMARY_TOKENS * total_recalls},
                "context_search": {"per_unit": CONTEXT_SEARCH_TOKENS, "count": total_searches,
                                   "total": CONTEXT_SEARCH_TOKENS * total_searches},
            },
            "benefit_basis": {
                "v2_measured_per_session": 19_560,
                "conservative_factor": 0.6,
                "applied_per_session": BENEFIT_PER_SESSION,
                "session_count": total_recalls,
                "total_tokens": estimated_benefit_tokens,
            },
            "pricing": {
                "overhead_per_m": OVERHEAD_PRICE_PER_M,
                "benefit_per_m": BENEFIT_PRICE_PER_M,
            },
        }

    def get_all_token_stats(self) -> dict[str, Any]:
        """Get token usage stats for all projects.

        Returns:
            Dict with per-project stats, totals, and session cost breakdown.
        """
        projects = self.list_projects()
        project_stats: dict[str, Any] = {}
        totals = {
            "memory_count": 0,
            "total_content_chars": 0,
            "estimated_tokens_stored": 0,
            "total_recalls": 0,
            "total_searches": 0,
            "estimated_benefit_tokens": 0,
            "overhead_tokens": 0,
            "cost_usd": 0.0,
            "benefit_usd": 0.0,
            "net_saving_usd": 0.0,
        }

        for p in projects:
            info = self.get_project_token_stats(p)
            project_stats[p] = info
            for key in totals:
                totals[key] += info.get(key, 0)

        # Round USD totals
        for k in ("cost_usd", "benefit_usd", "net_saving_usd"):
            totals[k] = round(totals[k], 4)

        # Per-session cost breakdown (V2 benchmark measured)
        session_cost = {
            "auto_recall_tokens": 260,         # brief mode recall
            "guide_tokens": 300,               # CLAUDE.md guide section
            "context_search_tokens": 120,      # per search call
            "session_summary_tokens": 170,     # end-of-session store
            "tool_desc_tokens": 889,           # MCP protocol tool descriptions
            "estimated_overhead_per_session": 850,  # recall+guide+summary (excl. searches)
            "estimated_total": 1739,           # overhead + tool descs
        }

        return {
            "projects": project_stats,
            "totals": totals,
            "session_cost": session_cost,
        }

    # ── Project management ───────────────────────────────────────────

    def project_exists(self, project: str) -> bool:
        """Check if a project collection exists."""
        col_name = self._collection_name(project)
        return any(
            c.name == col_name for c in self._chroma.list_collections()
        )

    def list_projects(self) -> list[str]:
        """List all registered project names."""
        projects = []
        for col_info in self._list_collection_names():
            if col_info.startswith(COLLECTION_PREFIX):
                projects.append(col_info[len(COLLECTION_PREFIX):])
        return sorted(projects)

    def list_projects_with_stats(self) -> list[dict[str, Any]]:
        """List all projects with their memory counts and last changed time.

        Returns:
            Sorted list of dicts with 'name', 'memory_count', and
            'last_changed' (ISO timestamp or None) keys.
        """
        results: list[dict[str, Any]] = []
        for col_info in self._list_collection_names():
            if not col_info.startswith(COLLECTION_PREFIX):
                continue
            name = col_info[len(COLLECTION_PREFIX):]
            col = self._chroma.get_collection(col_info)
            count = col.count()
            last_changed: str | None = None
            if count > 0:
                try:
                    all_meta = col.get(where={"deleted_ts": {"$eq": 0}}, include=["metadatas"])
                    for meta in all_meta.get("metadatas") or []:
                        ts = meta.get("updated_at") or meta.get("created_at", "")
                        if ts and (last_changed is None or ts > last_changed):
                            last_changed = ts
                except Exception:
                    pass
            results.append({
                "name": name,
                "memory_count": count,
                "last_changed": last_changed,
            })
        results.sort(key=lambda r: r["name"])
        return results

    def get_activity_heatmap(
        self, start_date: str, end_date: str, tz_offset_hours: int = 9,
    ) -> dict[str, Any]:
        """Aggregate memory counts per project per day for heatmap rendering.

        Args:
            start_date: ISO date string (inclusive), e.g. '2026-03-09'.
            end_date: ISO date string (inclusive), e.g. '2026-04-07'.
            tz_offset_hours: Local timezone offset (default KST=9).

        Returns:
            Dict with 'projects' list, each containing 'name', 'last_activity',
            'total_memories', and 'cells' (date+count pairs).
        """
        from datetime import date as _date

        local_tz = timezone(timedelta(hours=tz_offset_hours))
        # Convert date strings to unix timestamps for ChromaDB range filter
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=local_tz)
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, tzinfo=local_tz,
        )
        start_ts = int(start_dt.timestamp())
        end_ts = int(end_dt.timestamp())

        projects = []
        for col_name in self._list_collection_names():
            if not col_name.startswith(COLLECTION_PREFIX):
                continue
            name = col_name[len(COLLECTION_PREFIX):]
            col = self._chroma.get_collection(col_name)

            # Range-filtered active memories (heatmap cells)
            try:
                raw = col.get(
                    where={"$and": [
                        {"deleted_ts": {"$eq": 0}},
                        {"created_ts": {"$gte": start_ts}},
                        {"created_ts": {"$lte": end_ts}},
                    ]},
                    include=["metadatas"],
                )
            except Exception:
                raw = {"metadatas": []}
            metas = raw.get("metadatas") or []

            # Group by date
            date_counts: dict[str, int] = {}
            for meta in metas:
                ts = meta.get("created_ts", 0)
                if ts == 0:
                    continue
                dt = datetime.fromtimestamp(ts, tz=local_tz)
                ds = dt.strftime("%Y-%m-%d")
                date_counts[ds] = date_counts.get(ds, 0) + 1

            # Total active memories (range-independent)
            try:
                total_active = len(
                    col.get(where={"deleted_ts": {"$eq": 0}}, include=[])["ids"]
                )
            except Exception:
                total_active = 0

            # Last activity date (range-independent)
            last_activity = None
            if total_active > 0:
                try:
                    all_raw = col.get(
                        where={"deleted_ts": {"$eq": 0}}, include=["metadatas"],
                    )
                    max_ts = max(
                        (m.get("created_ts", 0) for m in (all_raw.get("metadatas") or [])),
                        default=0,
                    )
                    if max_ts > 0:
                        last_activity = datetime.fromtimestamp(
                            max_ts, tz=local_tz,
                        ).strftime("%Y-%m-%d")
                except Exception:
                    pass

            # Build cells for every date in range (including 0-count days)
            cells = []
            d = _date.fromisoformat(start_date)
            end_d = _date.fromisoformat(end_date)
            while d <= end_d:
                ds = d.isoformat()
                cells.append({"date": ds, "count": date_counts.get(ds, 0)})
                d += timedelta(days=1)

            projects.append({
                "name": name,
                "last_activity": last_activity,
                "total_memories": total_active,
                "cells": cells,
            })

        # Sort by last activity (most recent first)
        projects.sort(key=lambda p: p["last_activity"] or "", reverse=True)
        return {"projects": projects}

    def rename_project(self, old_name: str, new_name: str) -> dict[str, Any]:
        """Rename a project by copying all data to a new collection.

        ChromaDB doesn't support renaming collections natively, so this:
        1. Creates new collection
        2. Copies all documents, embeddings, metadatas (updating project field)
        3. Verifies count matches
        4. Deletes old collection

        Returns:
            Dict with old_name, new_name, memories_moved.

        Raises:
            ValueError: If old project doesn't exist, new name already exists,
                        or names are the same.
        """
        if old_name == new_name:
            raise ValueError(f"Old and new names are the same: '{old_name}'")

        old_col_name = self._collection_name(old_name)
        new_col_name = self._collection_name(new_name)

        if old_col_name == new_col_name:
            raise ValueError(
                f"'{old_name}' and '{new_name}' resolve to the same collection name: {old_col_name}"
            )

        if not self.project_exists(old_name):
            raise ValueError(f"Project '{old_name}' does not exist")

        if self.project_exists(new_name):
            raise ValueError(f"Project '{new_name}' already exists")

        # Get old collection and all its data
        old_col = self._chroma.get_collection(old_col_name)
        count = old_col.count()

        if count == 0:
            # Empty project — just create new and delete old
            self._chroma.get_or_create_collection(
                name=new_col_name, metadata={"hnsw:space": "cosine"}
            )
            self._chroma.delete_collection(old_col_name)
            logger.info("Renamed empty project %s -> %s", old_name, new_name)
            return {"old_name": old_name, "new_name": new_name, "memories_moved": 0}

        # Get all data from old collection
        all_data = old_col.get(include=["documents", "embeddings", "metadatas"])

        # Create new collection
        new_col = self._chroma.get_or_create_collection(
            name=new_col_name, metadata={"hnsw:space": "cosine"}
        )

        # Update project field in metadata
        updated_metadatas = []
        for meta in all_data["metadatas"] or []:
            new_meta = dict(meta)
            new_meta["project"] = new_name
            updated_metadatas.append(new_meta)

        # Batch add to new collection
        new_col.add(
            ids=all_data["ids"],
            documents=all_data["documents"],
            embeddings=all_data["embeddings"],
            metadatas=updated_metadatas,
        )

        # Verify count matches
        if new_col.count() != count:
            logger.error(
                "Rename count mismatch: old=%d, new=%d", count, new_col.count()
            )
            # Don't delete old collection if counts don't match
            raise RuntimeError(
                f"Count mismatch after copy: expected {count}, got {new_col.count()}. "
                f"Both collections exist — manual cleanup needed."
            )

        # Delete old collection
        self._chroma.delete_collection(old_col_name)
        logger.info("Renamed project %s -> %s (%d memories)", old_name, new_name, count)
        return {"old_name": old_name, "new_name": new_name, "memories_moved": count}

    def delete_project(self, project: str) -> dict[str, Any]:
        """Delete an entire project collection and all its memories.

        Returns:
            Dict with project name and count of deleted memories.

        Raises:
            ValueError: If the project doesn't exist.
        """
        if not self.project_exists(project):
            raise ValueError(f"Project '{project}' does not exist")

        col = self._get_collection(project)
        count = col.count()
        self._chroma.delete_collection(self._collection_name(project))
        self._invalidate_bm25(project)
        logger.info("Deleted project %s (%d memories)", project, count)
        return {"project": project, "memories_deleted": count}

    def project_stats(self, project: str) -> dict[str, Any]:
        """Get stats for a project's memory collection."""
        col = self._get_collection(project)
        total = col.count()

        type_counts: dict[str, int] = {}
        if total > 0:
            all_meta = col.get(include=["metadatas"])
            for meta in all_meta["metadatas"] or []:
                mtype = meta.get("type", "unknown")
                type_counts[mtype] = type_counts.get(mtype, 0) + 1

        return {
            "project": project,
            "total_memories": total,
            "by_type": type_counts,
        }

    def global_stats(self) -> dict[str, Any]:
        """Get stats across all projects."""
        projects = self.list_projects()
        project_stats = {p: self.project_stats(p) for p in projects}
        total = sum(s["total_memories"] for s in project_stats.values())
        return {
            "total_projects": len(projects),
            "total_memories": total,
            "projects": project_stats,
        }

    # ── Migration ─────────────────────────────────────────────────────

    def migrate_metadata_v2(self) -> dict[str, Any]:
        """Add created_ts (UNIX timestamp) to all memories that lack it.

        Idempotent — skips memories that already have created_ts.
        Needed for date range filters ($gt/$lt) which require numeric fields.

        Returns:
            Dict with migration stats: projects, updated, skipped, errors.
        """
        projects = self.list_projects()
        total_updated = 0
        total_skipped = 0
        total_errors = 0

        for project in projects:
            col = self._get_collection(project)
            if col.count() == 0:
                continue

            all_data = col.get(include=["metadatas"])
            ids = all_data["ids"]
            metas = all_data["metadatas"] or []

            for i, meta in enumerate(metas):
                if meta.get("created_ts") is not None:
                    total_skipped += 1
                    continue

                # Derive created_ts from created_at ISO string
                created_at = meta.get("created_at", "")
                ts = self._iso_to_ts(created_at)
                if ts is None:
                    total_errors += 1
                    logger.warning(
                        "Cannot parse created_at for %s: %s", ids[i], created_at
                    )
                    continue

                new_meta = dict(meta)
                new_meta["created_ts"] = ts

                try:
                    col.update(ids=[ids[i]], metadatas=[new_meta])
                    total_updated += 1
                except Exception:
                    logger.exception("Migration failed for %s", ids[i])
                    total_errors += 1

            if total_updated > 0:
                logger.info(
                    "Migrated %d memories in project %s", total_updated, project
                )

        return {
            "projects_scanned": len(projects),
            "updated": total_updated,
            "skipped": total_skipped,
            "errors": total_errors,
        }

    def migrate_metadata_v3(self) -> dict[str, Any]:
        """Migrate priority-only memories to importance-based system.

        For each memory:
        - If 'importance' field exists as a valid float → skip
        - Otherwise, convert 'priority' string to importance float:
            critical → 9.0, normal → 5.0, low → 2.0
        - Also initializes recall_count, search_count to 0 if missing.

        Idempotent — safe to run multiple times.

        Returns:
            Dict with migration stats: projects_scanned, updated, skipped, errors.
        """
        from memory_mcp.constants import IMPORTANCE_DEFAULT, PRIORITY_TO_IMPORTANCE

        projects = self.list_projects()
        total_updated = 0
        total_skipped = 0
        total_errors = 0

        for project in projects:
            col = self._get_collection(project)
            if col.count() == 0:
                continue

            all_data = col.get(include=["metadatas"])
            ids = all_data["ids"]
            metas = all_data["metadatas"] or []

            for i, meta in enumerate(metas):
                # Check if already fully migrated
                if (
                    isinstance(meta.get("importance"), (int, float))
                    and isinstance(meta.get("recall_count"), int)
                    and isinstance(meta.get("search_count"), int)
                ):
                    total_skipped += 1
                    continue

                new_meta = dict(meta)
                needs_update = False

                # Convert priority → importance
                if not isinstance(meta.get("importance"), (int, float)):
                    priority_str = meta.get("priority", "normal")
                    new_meta["importance"] = PRIORITY_TO_IMPORTANCE.get(
                        priority_str, IMPORTANCE_DEFAULT,
                    )
                    needs_update = True

                # Initialize usage counters
                if not isinstance(meta.get("recall_count"), int):
                    new_meta["recall_count"] = 0
                    needs_update = True
                if not isinstance(meta.get("search_count"), int):
                    new_meta["search_count"] = 0
                    needs_update = True

                if not needs_update:
                    total_skipped += 1
                    continue

                try:
                    col.update(ids=[ids[i]], metadatas=[new_meta])
                    total_updated += 1
                except Exception:
                    logger.exception("v3 migration failed for %s", ids[i])
                    total_errors += 1

        logger.info(
            "v3 migration: %d projects, %d updated, %d skipped, %d errors",
            len(projects), total_updated, total_skipped, total_errors,
        )
        return {
            "projects_scanned": len(projects),
            "updated": total_updated,
            "skipped": total_skipped,
            "errors": total_errors,
        }

    def migrate_metadata_v4_trash(self) -> dict[str, Any]:
        """Add deleted_ts=0 to all existing memories for soft-delete support.

        ChromaDB cannot filter on absent metadata fields, so every memory
        must have an explicit ``deleted_ts`` value.  New memories get
        ``deleted_ts=0`` at store time (handled in ``store()``).

        Uses batch update per project for performance.
        Idempotent — skips memories that already have ``deleted_ts``.

        Returns:
            Dict with migration stats.
        """
        projects = self.list_projects()
        total_updated = 0
        total_skipped = 0

        for project in projects:
            col = self._get_collection(project)
            if col.count() == 0:
                continue

            all_data = col.get(include=["metadatas"])
            ids = all_data["ids"]
            metas = all_data["metadatas"] or []

            batch_ids: list[str] = []
            batch_metas: list[dict[str, Any]] = []

            for i, meta in enumerate(metas):
                if "deleted_ts" in meta:
                    total_skipped += 1
                    continue
                batch_ids.append(ids[i])
                batch_metas.append({**meta, "deleted_ts": 0})

            if batch_ids:
                try:
                    col.update(ids=batch_ids, metadatas=batch_metas)
                    total_updated += len(batch_ids)
                except Exception:
                    logger.exception(
                        "v4 trash migration batch failed for project %s",
                        project,
                    )

        logger.info(
            "v4 trash migration: %d projects, %d updated, %d skipped",
            len(projects), total_updated, total_skipped,
        )
        return {
            "projects_scanned": len(projects),
            "updated": total_updated,
            "skipped": total_skipped,
        }

    def migrate_embeddings(self) -> dict[str, Any]:
        """Re-embed all documents using the current embedding model.

        Use this after changing the embedding model to update all stored
        embeddings. Documents and metadata are preserved.

        Returns:
            Dict with migration stats: projects migrated, total documents, errors.
        """
        projects = self.list_projects()
        total_docs = 0
        total_errors = 0

        for project in projects:
            col = self._get_collection(project)
            count = col.count()
            if count == 0:
                continue

            all_data = col.get(include=["documents", "metadatas"])
            ids = all_data["ids"]
            docs = all_data["documents"] or []
            metas = all_data["metadatas"] or []

            for i, doc in enumerate(docs):
                try:
                    new_embedding = self._embed(doc)
                    col.update(
                        ids=[ids[i]],
                        embeddings=[new_embedding],
                    )
                    total_docs += 1
                except Exception:
                    logger.exception("Migration failed for %s in %s", ids[i], project)
                    total_errors += 1

            logger.info("Migrated %d documents in project %s", len(docs), project)

        return {
            "projects_migrated": len(projects),
            "documents_migrated": total_docs,
            "errors": total_errors,
        }
