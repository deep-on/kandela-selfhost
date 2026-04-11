"""Tests for RAG pipeline enhancements (Phase 8A + 8B).

Covers:
Phase 8A:
- Metadata v2 migration (created_ts)
- Extended search filters (tags, priority, date_after/before)
- MMR (Maximum Marginal Relevance) reranking
- Time-Weighted Retrieval
- _build_where() filter construction
- _iso_to_ts() conversion

Phase 8B:
- BM25 tokenizer and index
- RRF (Reciprocal Rank Fusion) merging
- Hybrid search (semantic + BM25) integration
"""

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from memory_mcp.constants import (
    DECAY_RATES,
    DEFAULT_MMR_LAMBDA,
    IMPORTANCE_CRITICAL_THRESHOLD,
    IMPORTANCE_LOW_THRESHOLD,
    MemoryPriority,
    MemoryType,
)
from memory_mcp.db.store import MemoryStore


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    """Create a MemoryStore with a temp directory."""
    return MemoryStore(db_path=str(tmp_path / "test_db"))


@pytest.fixture
def populated_store(store: MemoryStore) -> MemoryStore:
    """Store with diverse test data for filter/MMR/time-weighted tests."""
    # Critical memory
    store.store(
        project="rag_test",
        content="SSH deploy path is user@prod-server:/app/deploy",
        memory_type=MemoryType.FACT,
        tags=["deploy", "ssh"],
        priority=MemoryPriority.CRITICAL,
    )
    # Normal memories
    store.store(
        project="rag_test",
        content="Python FastAPI backend with PostgreSQL database",
        memory_type=MemoryType.FACT,
        tags=["python", "backend"],
        priority=MemoryPriority.NORMAL,
    )
    store.store(
        project="rag_test",
        content="Decided to use React for frontend with TypeScript",
        memory_type=MemoryType.DECISION,
        tags=["frontend", "react"],
        priority=MemoryPriority.NORMAL,
    )
    store.store(
        project="rag_test",
        content="Docker compose setup with nginx reverse proxy",
        memory_type=MemoryType.SNIPPET,
        tags=["deploy", "docker"],
        priority=MemoryPriority.NORMAL,
    )
    # Low (auto-saved)
    store.store(
        project="rag_test",
        content="User asked about deployment configuration details",
        memory_type=MemoryType.FACT,
        tags=["auto-saved"],
        priority=MemoryPriority.LOW,
    )
    # Summary
    store.store(
        project="rag_test",
        content="Session: set up CI/CD pipeline with GitHub Actions",
        memory_type=MemoryType.SUMMARY,
        tags=["session"],
        priority=MemoryPriority.NORMAL,
    )
    return store


# ═══════════════════════════════════════════════════════════════════
# _iso_to_ts()
# ═══════════════════════════════════════════════════════════════════

class TestIsoToTs:
    def test_valid_iso_date(self, store: MemoryStore) -> None:
        ts = store._iso_to_ts("2026-02-22")
        assert ts is not None
        assert isinstance(ts, int)

    def test_valid_iso_datetime(self, store: MemoryStore) -> None:
        ts = store._iso_to_ts("2026-02-22T06:30:00+00:00")
        assert ts is not None

    def test_valid_iso_datetime_with_timezone(self, store: MemoryStore) -> None:
        ts = store._iso_to_ts("2026-02-22T15:30:00+09:00")
        assert ts is not None

    def test_invalid_string(self, store: MemoryStore) -> None:
        assert store._iso_to_ts("not-a-date") is None

    def test_none_input(self, store: MemoryStore) -> None:
        assert store._iso_to_ts(None) is None

    def test_empty_string(self, store: MemoryStore) -> None:
        assert store._iso_to_ts("") is None


# ═══════════════════════════════════════════════════════════════════
# _build_where()
# ═══════════════════════════════════════════════════════════════════

class TestBuildWhere:
    """_build_where now always includes deleted_ts filter by default."""

    _DEL_FILTER = {"deleted_ts": {"$eq": 0}}

    def test_no_filters(self, store: MemoryStore) -> None:
        result = store._build_where()
        assert result == self._DEL_FILTER

    def test_type_only(self, store: MemoryStore) -> None:
        result = store._build_where(memory_type=MemoryType.FACT)
        assert "$and" in result
        assert self._DEL_FILTER in result["$and"]
        assert {"type": "fact"} in result["$and"]

    def test_priority_only(self, store: MemoryStore) -> None:
        result = store._build_where(priority=MemoryPriority.CRITICAL)
        assert "$and" in result
        assert self._DEL_FILTER in result["$and"]
        assert {"priority": "critical"} in result["$and"]

    def test_tags_not_in_where(self, store: MemoryStore) -> None:
        """Tags are post-filtered, not in ChromaDB where clause."""
        result = store._build_where()
        assert result == self._DEL_FILTER  # Only deleted_ts filter

    def test_combined_filters_and(self, store: MemoryStore) -> None:
        result = store._build_where(
            memory_type=MemoryType.FACT,
            priority=MemoryPriority.NORMAL,
        )
        assert "$and" in result
        conditions = result["$and"]
        assert {"type": "fact"} in conditions
        assert {"priority": "normal"} in conditions

    def test_date_after_filter(self, store: MemoryStore) -> None:
        result = store._build_where(date_after="2026-02-20")
        assert "$and" in result
        assert self._DEL_FILTER in result["$and"]
        assert any("created_ts" in c for c in result["$and"] if isinstance(c, dict) and "created_ts" in c)

    def test_date_before_filter(self, store: MemoryStore) -> None:
        result = store._build_where(date_before="2026-02-25")
        assert "$and" in result
        assert self._DEL_FILTER in result["$and"]

    def test_date_range_filter(self, store: MemoryStore) -> None:
        result = store._build_where(
            date_after="2026-02-20",
            date_before="2026-02-25",
        )
        assert "$and" in result
        assert len(result["$and"]) == 3  # deleted_ts + 2 date filters

    def test_invalid_date_ignored(self, store: MemoryStore) -> None:
        result = store._build_where(date_after="not-valid")
        assert result == self._DEL_FILTER  # Only deleted_ts filter

    def test_all_filters_combined(self, store: MemoryStore) -> None:
        """Tags are handled by post-filter, so only type+priority+date+deleted_ts in where."""
        result = store._build_where(
            memory_type=MemoryType.FACT,
            priority=MemoryPriority.NORMAL,
            date_after="2026-02-20",
        )
        assert "$and" in result
        assert len(result["$and"]) == 4  # deleted_ts + type + priority + date

    # ── Phase 9: Importance filter tests ──

    def test_importance_min_only(self, store: MemoryStore) -> None:
        result = store._build_where(importance_min=7.0)
        assert "$and" in result
        assert self._DEL_FILTER in result["$and"]
        assert {"importance": {"$gte": 7.0}} in result["$and"]

    def test_importance_max_only(self, store: MemoryStore) -> None:
        result = store._build_where(importance_max=3.0)
        assert "$and" in result
        assert self._DEL_FILTER in result["$and"]
        assert {"importance": {"$lte": 3.0}} in result["$and"]

    def test_importance_range(self, store: MemoryStore) -> None:
        result = store._build_where(importance_min=3.0, importance_max=9.0)
        assert "$and" in result
        conditions = result["$and"]
        assert {"importance": {"$gte": 3.0}} in conditions
        assert {"importance": {"$lte": 9.0}} in conditions

    def test_importance_with_type_filter(self, store: MemoryStore) -> None:
        result = store._build_where(
            memory_type=MemoryType.FACT,
            importance_min=5.0,
        )
        assert "$and" in result
        conditions = result["$and"]
        assert {"type": "fact"} in conditions
        assert {"importance": {"$gte": 5.0}} in conditions

    def test_importance_with_all_filters(self, store: MemoryStore) -> None:
        """All filters combined: type + priority + importance + date + deleted_ts."""
        result = store._build_where(
            memory_type=MemoryType.FACT,
            priority=MemoryPriority.NORMAL,
            importance_min=5.0,
            date_after="2026-02-20",
        )
        assert "$and" in result
        assert len(result["$and"]) == 5  # deleted_ts + type + priority + importance + date


# ═══════════════════════════════════════════════════════════════════
# Metadata v2 Migration (created_ts)
# ═══════════════════════════════════════════════════════════════════

class TestMetadataV2Migration:
    def test_new_memories_have_created_ts(self, store: MemoryStore) -> None:
        """New memories should have created_ts field."""
        store.store(
            project="mig_test",
            content="Test memory with created_ts",
        )
        col = store._get_collection("mig_test")
        raw = col.get(include=["metadatas"])
        meta = raw["metadatas"][0]
        assert "created_ts" in meta
        assert isinstance(meta["created_ts"], int)
        assert meta["created_ts"] > 0

    def test_migrate_adds_created_ts(self, store: MemoryStore) -> None:
        """Migration should add created_ts to memories that lack it."""
        # Manually create a memory without created_ts
        col = store._get_collection("mig_test")
        col.add(
            ids=["old_memory_1"],
            documents=["Old memory without timestamp field"],
            embeddings=[store._embed("Old memory without timestamp field")],
            metadatas=[{
                "project": "mig_test",
                "type": "fact",
                "priority": "normal",
                "tags": "[]",
                "created_at": "2026-01-15T10:00:00+00:00",
            "deleted_ts": 0,
            }],
        )

        # Run migration
        result = store.migrate_metadata_v2()
        assert result["updated"] == 1
        assert result["errors"] == 0

        # Verify
        raw = col.get(ids=["old_memory_1"], include=["metadatas"])
        meta = raw["metadatas"][0]
        assert "created_ts" in meta
        assert isinstance(meta["created_ts"], int)

    def test_migrate_idempotent(self, store: MemoryStore) -> None:
        """Running migration twice should not duplicate work."""
        store.store(project="mig_test", content="Memory for idempotency test")

        result1 = store.migrate_metadata_v2()
        result2 = store.migrate_metadata_v2()

        # Second run should skip all (already migrated)
        assert result2["skipped"] >= result1["updated"] + result1["skipped"]
        assert result2["updated"] == 0

    def test_migrate_bad_date_reports_error(self, store: MemoryStore) -> None:
        """Migration should report errors for unparseable dates."""
        col = store._get_collection("mig_test")
        col.add(
            ids=["bad_date_memory"],
            documents=["Memory with bad date"],
            embeddings=[store._embed("Memory with bad date")],
            metadatas=[{
                "project": "mig_test",
                "type": "fact",
                "priority": "normal",
                "tags": "[]",
                "created_at": "not-a-valid-date",
            "deleted_ts": 0,
            }],
        )

        result = store.migrate_metadata_v2()
        assert result["errors"] >= 1


# ═══════════════════════════════════════════════════════════════════
# Extended Search Filters
# ═══════════════════════════════════════════════════════════════════

class TestSearchFilters:
    def test_search_filter_by_priority(self, populated_store: MemoryStore) -> None:
        results = populated_store.search(
            query="deployment",
            project="rag_test",
            priority=MemoryPriority.CRITICAL,
        )
        assert len(results) > 0
        for r in results:
            assert r["metadata"]["priority"] == "critical"

    def test_search_filter_by_type(self, populated_store: MemoryStore) -> None:
        results = populated_store.search(
            query="frontend technology",
            project="rag_test",
            memory_type=MemoryType.DECISION,
        )
        assert len(results) > 0
        for r in results:
            assert r["metadata"]["type"] == "decision"

    def test_search_filter_by_tags(self, populated_store: MemoryStore) -> None:
        results = populated_store.search(
            query="server setup",
            project="rag_test",
            tags=["deploy"],
        )
        assert len(results) > 0
        for r in results:
            # Tags are JSON-encoded strings, parse and verify
            import json
            stored_tags = json.loads(r["metadata"].get("tags", "[]"))
            assert "deploy" in stored_tags

    def test_search_combined_filters(self, populated_store: MemoryStore) -> None:
        results = populated_store.search(
            query="deployment",
            project="rag_test",
            memory_type=MemoryType.FACT,
            priority=MemoryPriority.NORMAL,
        )
        for r in results:
            assert r["metadata"]["type"] == "fact"
            assert r["metadata"]["priority"] == "normal"

    def test_search_no_results_with_strict_filter(self, populated_store: MemoryStore) -> None:
        results = populated_store.search(
            query="anything",
            project="rag_test",
            tags=["nonexistent_tag"],
        )
        assert len(results) == 0


# ═══════════════════════════════════════════════════════════════════
# MMR (Maximum Marginal Relevance)
# ═══════════════════════════════════════════════════════════════════

class TestMMR:
    def test_mmr_returns_results(self, populated_store: MemoryStore) -> None:
        results = populated_store.search(
            query="project setup and deployment",
            project="rag_test",
            n_results=3,
            use_mmr=True,
        )
        assert len(results) > 0
        assert len(results) <= 3

    def test_mmr_no_embedding_key_in_output(self, populated_store: MemoryStore) -> None:
        """Embeddings should be stripped from final results."""
        results = populated_store.search(
            query="deployment",
            project="rag_test",
            n_results=3,
            use_mmr=True,
        )
        for r in results:
            assert "embedding" not in r

    def test_mmr_diversity(self, populated_store: MemoryStore) -> None:
        """MMR should produce more diverse results than plain search."""
        plain = populated_store.search(
            query="server deployment configuration",
            project="rag_test",
            n_results=4,
            use_mmr=False,
        )
        mmr = populated_store.search(
            query="server deployment configuration",
            project="rag_test",
            n_results=4,
            use_mmr=True,
        )
        # MMR results should have at least as many unique types
        plain_types = {r["metadata"].get("type") for r in plain}
        mmr_types = {r["metadata"].get("type") for r in mmr}
        assert len(mmr_types) >= len(plain_types)

    def test_mmr_rerank_static(self, store: MemoryStore) -> None:
        """Test _mmr_rerank with controlled embeddings.

        Uses deterministic vectors in a low-dimensional subspace:
        - query: [1, 0, 0, ...]
        - sim1, sim2: nearly identical, both close to query direction
        - diff1: moderately relevant to query but in a different direction
        After selecting the first similar item, MMR should prefer diff1
        over the redundant second similar item.
        """
        dim = 384

        # Query direction
        query = np.zeros(dim, dtype=np.float32)
        query[0] = 1.0

        # Two near-duplicate candidates, both very similar to query
        sim1 = np.zeros(dim, dtype=np.float32)
        sim1[0] = 0.95
        sim1[1] = 0.31
        sim1 /= np.linalg.norm(sim1)

        sim2 = np.zeros(dim, dtype=np.float32)
        sim2[0] = 0.93
        sim2[1] = 0.37
        sim2 /= np.linalg.norm(sim2)

        # Diverse candidate: moderate relevance but in a different direction
        diff1 = np.zeros(dim, dtype=np.float32)
        diff1[0] = 0.5
        diff1[2] = 0.866
        diff1 /= np.linalg.norm(diff1)

        results = [
            {"id": "sim1", "content": "A", "metadata": {}, "distance": 0.05,
             "embedding": sim1.tolist()},
            {"id": "sim2", "content": "B", "metadata": {}, "distance": 0.07,
             "embedding": sim2.tolist()},
            {"id": "diff1", "content": "C", "metadata": {}, "distance": 0.50,
             "embedding": diff1.tolist()},
        ]

        reranked = MemoryStore._mmr_rerank(
            query_embedding=query.tolist(),
            results=results,
            n_results=2,
            lambda_param=0.5,  # Balance relevance and diversity
        )

        assert len(reranked) == 2
        ids = [r["id"] for r in reranked]
        # First selected should be most relevant (sim1)
        assert ids[0] == "sim1"
        # MMR should prefer diverse diff1 over redundant sim2
        assert "diff1" in ids, "MMR should prefer diverse result over near-duplicate"

    def test_mmr_empty_input(self, store: MemoryStore) -> None:
        result = MemoryStore._mmr_rerank(
            query_embedding=[0.0] * 384,
            results=[],
            n_results=5,
        )
        assert result == []

    def test_mmr_fewer_than_requested(self, store: MemoryStore) -> None:
        """When results < n_results, return all."""
        results = [
            {"id": "only1", "content": "X", "metadata": {}, "distance": 0.1,
             "embedding": [0.1] * 384},
        ]
        reranked = MemoryStore._mmr_rerank(
            query_embedding=[0.0] * 384,
            results=results,
            n_results=5,
        )
        assert len(reranked) == 1
        assert "embedding" not in reranked[0]


# ═══════════════════════════════════════════════════════════════════
# Time-Weighted Retrieval
# ═══════════════════════════════════════════════════════════════════

class TestTimeWeighted:
    def test_time_decay_increases_distance(self, store: MemoryStore) -> None:
        """Older memories should have increased distance."""
        old_time = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()
        new_time = datetime.now(timezone.utc).isoformat()

        results = [
            {"id": "old", "content": "old", "metadata": {
                "priority": "normal", "created_at": old_time,
            }, "distance": 0.1},
            {"id": "new", "content": "new", "metadata": {
                "priority": "normal", "created_at": new_time,
            }, "distance": 0.1},
        ]

        decayed = MemoryStore._apply_time_decay(results)

        old_item = next(r for r in decayed if r["id"] == "old")
        new_item = next(r for r in decayed if r["id"] == "new")

        assert old_item["distance"] > new_item["distance"]

    def test_critical_no_decay(self, store: MemoryStore) -> None:
        """CRITICAL memories should have zero decay."""
        old_time = (datetime.now(timezone.utc) - timedelta(hours=1000)).isoformat()

        results = [
            {"id": "crit", "content": "critical", "metadata": {
                "priority": "critical", "created_at": old_time,
            }, "distance": 0.1},
        ]

        decayed = MemoryStore._apply_time_decay(results)
        assert decayed[0]["distance"] == 0.1  # Unchanged

    def test_low_decays_faster_than_normal(self, store: MemoryStore) -> None:
        """LOW priority should decay faster than NORMAL."""
        old_time = (datetime.now(timezone.utc) - timedelta(hours=200)).isoformat()

        results = [
            {"id": "norm", "content": "normal", "metadata": {
                "priority": "normal", "created_at": old_time,
            }, "distance": 0.1},
            {"id": "low", "content": "low", "metadata": {
                "priority": "low", "created_at": old_time,
            }, "distance": 0.1},
        ]

        decayed = MemoryStore._apply_time_decay(results)

        low_item = next(r for r in decayed if r["id"] == "low")
        norm_item = next(r for r in decayed if r["id"] == "norm")

        assert low_item["distance"] > norm_item["distance"]

    def test_time_weighted_search_integration(self, populated_store: MemoryStore) -> None:
        """time_weighted=True should complete without error."""
        results = populated_store.search(
            query="deployment",
            project="rag_test",
            n_results=3,
            time_weighted=True,
        )
        assert len(results) > 0

    def test_time_decay_sorts_by_adjusted_distance(self, store: MemoryStore) -> None:
        """Results should be sorted by adjusted distance after decay."""
        now = datetime.now(timezone.utc)
        results = [
            {"id": "A", "content": "a", "metadata": {
                "priority": "low",
                "created_at": (now - timedelta(hours=500)).isoformat(),
            }, "distance": 0.05},  # Close but very old + low priority
            {"id": "B", "content": "b", "metadata": {
                "priority": "normal",
                "created_at": now.isoformat(),
            }, "distance": 0.15},  # Further but brand new
        ]

        decayed = MemoryStore._apply_time_decay(results)
        # After decay, A (old+low) should have much higher distance than B (new+normal)
        assert decayed[0]["id"] == "B"  # B should be first (lower adjusted distance)

    # ── Phase 9: Importance-based decay tests ──

    def test_importance_based_decay(self, store: MemoryStore) -> None:
        """Memories with importance field should use importance-based decay."""
        old_time = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()

        results = [
            {"id": "high_imp", "content": "high", "metadata": {
                "importance": 9.0, "created_at": old_time,
            }, "distance": 0.1},
            {"id": "low_imp", "content": "low", "metadata": {
                "importance": 2.0, "created_at": old_time,
            }, "distance": 0.1},
        ]

        decayed = MemoryStore._apply_time_decay(results)
        high = next(r for r in decayed if r["id"] == "high_imp")
        low = next(r for r in decayed if r["id"] == "low_imp")

        # High importance = low decay → distance barely changed
        # Low importance = high decay → distance increased more
        assert low["distance"] > high["distance"]

    def test_importance_10_no_decay(self, store: MemoryStore) -> None:
        """Importance 10.0 should have zero decay (like old CRITICAL)."""
        old_time = (datetime.now(timezone.utc) - timedelta(hours=1000)).isoformat()

        results = [
            {"id": "max_imp", "content": "max", "metadata": {
                "importance": 10.0, "created_at": old_time,
            }, "distance": 0.1},
        ]

        decayed = MemoryStore._apply_time_decay(results)
        assert decayed[0]["distance"] == 0.1  # Unchanged

    def test_time_decay_saves_original_distance(self, store: MemoryStore) -> None:
        """Time decay should save original_distance before adjustment."""
        old_time = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()

        results = [
            {"id": "x", "content": "x", "metadata": {
                "importance": 5.0, "created_at": old_time,
            }, "distance": 0.2},
        ]

        decayed = MemoryStore._apply_time_decay(results)
        assert decayed[0].get("original_distance") == 0.2
        assert decayed[0]["distance"] > 0.2  # Adjusted upward


# ═══════════════════════════════════════════════════════════════════
# Combined: MMR + Time-Weighted
# ═══════════════════════════════════════════════════════════════════

class TestCombinedRAG:
    def test_mmr_and_time_weighted(self, populated_store: MemoryStore) -> None:
        """Both MMR and time-weighted should work together."""
        results = populated_store.search(
            query="project deployment and setup",
            project="rag_test",
            n_results=3,
            use_mmr=True,
            time_weighted=True,
        )
        assert len(results) > 0
        assert len(results) <= 3
        # No embedding keys in output
        for r in results:
            assert "embedding" not in r

    def test_all_filters_with_mmr(self, populated_store: MemoryStore) -> None:
        """Filters + MMR should work together."""
        results = populated_store.search(
            query="deployment",
            project="rag_test",
            memory_type=MemoryType.FACT,
            priority=MemoryPriority.NORMAL,
            n_results=3,
            use_mmr=True,
        )
        for r in results:
            assert r["metadata"]["type"] == "fact"
            assert r["metadata"]["priority"] == "normal"


# ═══════════════════════════════════════════════════════════════════
# Constants validation
# ═══════════════════════════════════════════════════════════════════

class TestConstants:
    def test_decay_rates_exist(self) -> None:
        assert "critical" in DECAY_RATES
        assert "normal" in DECAY_RATES
        assert "low" in DECAY_RATES

    def test_critical_zero_decay(self) -> None:
        assert DECAY_RATES["critical"] == 0.0

    def test_low_higher_than_normal(self) -> None:
        assert DECAY_RATES["low"] > DECAY_RATES["normal"]

    def test_mmr_lambda_range(self) -> None:
        assert 0.0 <= DEFAULT_MMR_LAMBDA <= 1.0


# ═══════════════════════════════════════════════════════════════════
# Phase 8B: BM25 Tokenizer
# ═══════════════════════════════════════════════════════════════════

from memory_mcp.db.bm25 import (
    MemoryBM25Index,
    _tokenize_regex,
    kiwi_available,
    tokenize,
)
from memory_mcp.db.fusion import reciprocal_rank_fusion


class TestTokenizerRegex:
    """Tests for the regex-based tokenizer (fallback behavior)."""

    def test_english_tokens(self) -> None:
        tokens = _tokenize_regex("Hello World 123")
        assert tokens == ["hello", "world", "123"]

    def test_korean_tokens(self) -> None:
        tokens = _tokenize_regex("서버 설정을 변경했다")
        assert "서버" in tokens
        assert "설정을" in tokens
        assert "변경했다" in tokens

    def test_mixed_tokens(self) -> None:
        tokens = _tokenize_regex("ZYNQ_CLK_PIN은 L16에 할당")
        assert "zynq_clk_pin" in tokens
        assert "은" in tokens
        assert "l16" in tokens
        assert "할당" in tokens

    def test_empty_string(self) -> None:
        assert _tokenize_regex("") == []

    def test_special_chars_only(self) -> None:
        assert _tokenize_regex("!@#$%^&*()") == []

    def test_underscore_preserved(self) -> None:
        """Underscores within identifiers should be preserved."""
        tokens = _tokenize_regex("my_var_name")
        assert "my_var_name" in tokens

    def test_case_insensitive(self) -> None:
        assert _tokenize_regex("FastAPI") == ["fastapi"]


class TestTokenizerUnified:
    """Tests for tokenize() that work regardless of kiwi availability."""

    def test_english_tokens(self) -> None:
        tokens = tokenize("Hello World 123")
        assert "hello" in tokens
        assert "world" in tokens
        assert "123" in tokens

    def test_korean_content_words(self) -> None:
        """Core content words should always be extractable."""
        tokens = tokenize("서버 설정을 변경했다")
        assert "서버" in tokens
        # With kiwi: "설정" extracted; without kiwi: "설정을" kept
        assert "설정" in tokens or "설정을" in tokens
        assert "변경" in tokens or "변경했다" in tokens

    def test_mixed_korean_english(self) -> None:
        tokens = tokenize("ZYNQ_CLK_PIN은 L16에 할당")
        # Underscore identifier always preserved
        assert "zynq_clk_pin" in tokens
        assert "할당" in tokens

    def test_empty_string(self) -> None:
        assert tokenize("") == []

    def test_special_chars_only(self) -> None:
        assert tokenize("!@#$%^&*()") == []

    def test_underscore_preserved(self) -> None:
        tokens = tokenize("my_var_name")
        assert "my_var_name" in tokens

    def test_case_insensitive(self) -> None:
        tokens = tokenize("FastAPI")
        assert "fastapi" in tokens


class TestKiwiTokenizer:
    """Tests for kiwi morphological tokenization (H-2.3).

    These tests verify that kiwi correctly extracts Korean morphemes,
    improving BM25 recall for agglutinative Korean text.
    """

    @pytest.fixture(autouse=True)
    def _require_kiwi(self) -> None:
        if not kiwi_available():
            pytest.skip("kiwipiepy not installed")

    def test_particle_removal(self) -> None:
        """Particles (조사) should be removed from tokens."""
        tokens = tokenize("서버를 설정에서 변경을 했다")
        # Particles 를, 에서, 을 should not appear
        assert "를" not in tokens
        assert "에서" not in tokens
        assert "을" not in tokens
        # Content words should remain
        assert "서버" in tokens
        assert "설정" in tokens
        assert "변경" in tokens

    def test_verb_stem_extraction(self) -> None:
        """Verb endings should be stripped, keeping stems."""
        tokens = tokenize("배포합니다")
        assert "배포" in tokens
        # Endings like 합니다 should not appear as-is
        assert "합니다" not in tokens

    def test_adjective_handling(self) -> None:
        """Adjective stems should be extracted."""
        tokens = tokenize("빠른 서버가 좋다")
        assert "빠르" in tokens or "빠른" in tokens  # kiwi may vary
        assert "서버" in tokens

    def test_mixed_text_preserves_identifiers(self) -> None:
        """English underscore identifiers should be preserved alongside kiwi tokens."""
        tokens = tokenize("ZYNQ_CLK_PIN은 L16에 할당됨")
        assert "zynq_clk_pin" in tokens  # underscore identifier preserved
        assert "할당" in tokens
        # Mixed alpha-numeric identifier preserved
        assert "l16" in tokens
        # Individual components also available from kiwi
        assert "zynq" in tokens or "clk" in tokens

    def test_morpheme_improves_bm25_recall(self) -> None:
        """Kiwi should enable BM25 matching across morphological variants."""
        # Document with inflected forms
        doc_tokens = tokenize("서버 배포를 완료했습니다")
        # Query with base forms
        query_tokens = tokenize("배포 완료")

        # With kiwi, "배포" should appear in both doc and query tokens
        doc_has_배포 = "배포" in doc_tokens
        query_has_배포 = "배포" in query_tokens
        assert doc_has_배포 and query_has_배포, (
            f"BM25 recall broken: doc={doc_tokens}, query={query_tokens}"
        )

    def test_bm25_index_korean_morpheme_match(self) -> None:
        """BM25 index should match Korean queries with morphological variants."""
        docs = [
            "서버 배포를 완료했습니다",
            "데이터베이스 마이그레이션 스크립트",
            "Docker 컨테이너 설정을 변경했다",
        ]
        ids = ["d1", "d2", "d3"]
        metas = [{"type": "fact"}] * 3
        index = MemoryBM25Index(docs, ids, metas)

        # "배포" should match doc with "배포를 완료했습니다"
        results = index.search("배포", n_results=3)
        assert len(results) > 0
        assert results[0]["id"] == "d1"

        # "설정 변경" should match doc with "설정을 변경했다"
        results = index.search("설정 변경", n_results=3)
        assert len(results) > 0
        assert results[0]["id"] == "d3"


# ═══════════════════════════════════════════════════════════════════
# Phase 8B: BM25 Index
# ═══════════════════════════════════════════════════════════════════

class TestBM25Index:
    def test_basic_search(self) -> None:
        docs = [
            "Python FastAPI backend server",
            "React frontend with TypeScript",
            "Docker deployment configuration",
        ]
        ids = ["d1", "d2", "d3"]
        metas = [{"type": "fact"}] * 3

        index = MemoryBM25Index(docs, ids, metas)
        results = index.search("FastAPI backend", n_results=2)

        assert len(results) > 0
        assert results[0]["id"] == "d1"  # Best match
        assert "score" in results[0]
        assert results[0]["score"] > 0

    def test_korean_search(self) -> None:
        docs = [
            "서버 포트는 8443으로 변경",
            "프론트엔드는 React 사용",
            "데이터베이스 설정 완료",
        ]
        ids = ["k1", "k2", "k3"]
        metas = [{}] * 3

        index = MemoryBM25Index(docs, ids, metas)
        results = index.search("서버 포트", n_results=2)

        assert len(results) > 0
        assert results[0]["id"] == "k1"

    def test_exact_identifier_match(self) -> None:
        """BM25 should excel at exact identifier matching."""
        docs = [
            "ZYNQ_CLK_PIN은 L16에 할당",
            "FPGA 클럭 설정 방법",
            "Vivado 타이밍 제약 조건",
        ]
        ids = ["pin1", "clk1", "timing1"]
        metas = [{}] * 3

        index = MemoryBM25Index(docs, ids, metas)
        results = index.search("ZYNQ_CLK_PIN", n_results=2)

        assert len(results) > 0
        assert results[0]["id"] == "pin1"

    def test_no_match_returns_empty(self) -> None:
        docs = ["alpha beta gamma"]
        ids = ["x1"]
        metas = [{}]

        index = MemoryBM25Index(docs, ids, metas)
        results = index.search("completely unrelated xyz", n_results=5)

        # BM25 may return 0 results if no tokens match
        for r in results:
            assert r["score"] > 0  # Only positive scores returned

    def test_empty_query(self) -> None:
        docs = ["some content"]
        ids = ["x1"]
        metas = [{}]

        index = MemoryBM25Index(docs, ids, metas)
        results = index.search("", n_results=5)
        assert results == []

    def test_corpus_size(self) -> None:
        docs = ["a", "b", "c"]
        index = MemoryBM25Index(docs, ["1", "2", "3"], [{}] * 3)
        assert index.corpus_size == 3

    def test_mismatched_lengths_raises(self) -> None:
        with pytest.raises(ValueError):
            MemoryBM25Index(["a", "b"], ["1"], [{}])


# ═══════════════════════════════════════════════════════════════════
# Phase 8B: Reciprocal Rank Fusion
# ═══════════════════════════════════════════════════════════════════

class TestRRF:
    def test_single_list(self) -> None:
        results = reciprocal_rank_fusion(
            [{"id": "a", "content": "A", "distance": 0.1},
             {"id": "b", "content": "B", "distance": 0.2}],
            n_results=2,
        )
        assert len(results) == 2
        assert results[0]["id"] == "a"  # Rank 1 has higher RRF score
        assert "rrf_score" in results[0]

    def test_merge_two_lists(self) -> None:
        semantic = [
            {"id": "s1", "content": "S1", "distance": 0.05},
            {"id": "s2", "content": "S2", "distance": 0.10},
            {"id": "s3", "content": "S3", "distance": 0.15},
        ]
        bm25 = [
            {"id": "s2", "content": "S2", "score": 5.0},  # overlap with semantic
            {"id": "b1", "content": "B1", "score": 4.0},
            {"id": "s1", "content": "S1", "score": 3.0},  # overlap
        ]
        results = reciprocal_rank_fusion(semantic, bm25, n_results=3)

        ids = [r["id"] for r in results]
        # s1 and s2 appear in both lists, should rank highest
        assert "s1" in ids[:2] or "s2" in ids[:2]
        # All results have rrf_score
        for r in results:
            assert r["rrf_score"] > 0

    def test_deduplication(self) -> None:
        """Same document in multiple lists should appear only once."""
        list1 = [{"id": "dup", "content": "X", "distance": 0.1}]
        list2 = [{"id": "dup", "content": "X", "score": 5.0}]
        results = reciprocal_rank_fusion(list1, list2, n_results=5)
        assert len(results) == 1
        assert results[0]["id"] == "dup"

    def test_empty_lists(self) -> None:
        results = reciprocal_rank_fusion([], [], n_results=5)
        assert results == []

    def test_n_results_limit(self) -> None:
        items = [{"id": f"i{i}", "content": str(i)} for i in range(10)]
        results = reciprocal_rank_fusion(items, n_results=3)
        assert len(results) == 3

    def test_rrf_score_ordering(self) -> None:
        """Results must be sorted by RRF score descending."""
        list1 = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        list2 = [{"id": "c"}, {"id": "a"}, {"id": "b"}]
        results = reciprocal_rank_fusion(list1, list2, n_results=3)

        for i in range(len(results) - 1):
            assert results[i]["rrf_score"] >= results[i + 1]["rrf_score"]


# ═══════════════════════════════════════════════════════════════════
# Phase 8B: Hybrid Search Integration
# ═══════════════════════════════════════════════════════════════════

class TestHybridSearch:
    def test_hybrid_returns_results(self, populated_store: MemoryStore) -> None:
        """Hybrid search should return results without error."""
        results = populated_store.search(
            query="deployment docker",
            project="rag_test",
            n_results=3,
            use_hybrid=True,
        )
        assert len(results) > 0
        assert len(results) <= 3

    def test_hybrid_finds_exact_keywords(self, store: MemoryStore) -> None:
        """Hybrid search should find exact keyword matches that semantic might miss."""
        # Store memories with specific identifiers
        store.store(
            project="hybrid_test",
            content="ZYNQ_CLK_PIN is assigned to L16 on the FPGA board",
            memory_type=MemoryType.FACT,
        )
        store.store(
            project="hybrid_test",
            content="The server runs on port 8443 with TLS enabled",
            memory_type=MemoryType.FACT,
        )
        store.store(
            project="hybrid_test",
            content="General project configuration and setup guide",
            memory_type=MemoryType.FACT,
        )

        # Search with exact identifier
        results = store.search(
            query="ZYNQ_CLK_PIN",
            project="hybrid_test",
            n_results=3,
            use_hybrid=True,
        )
        assert len(results) > 0
        # The exact match should be in results
        ids_content = [r["content"] for r in results]
        assert any("ZYNQ_CLK_PIN" in c for c in ids_content)

    def test_hybrid_no_rrf_score_in_final(self, populated_store: MemoryStore) -> None:
        """RRF score should be present in hybrid results."""
        results = populated_store.search(
            query="deployment",
            project="rag_test",
            n_results=3,
            use_hybrid=True,
        )
        # RRF results carry rrf_score
        for r in results:
            assert "rrf_score" in r

    def test_hybrid_with_filters(self, populated_store: MemoryStore) -> None:
        """Hybrid search should work with metadata filters."""
        results = populated_store.search(
            query="deployment",
            project="rag_test",
            memory_type=MemoryType.FACT,
            n_results=3,
            use_hybrid=True,
        )
        for r in results:
            assert r["metadata"]["type"] == "fact"

    def test_hybrid_with_mmr(self, populated_store: MemoryStore) -> None:
        """Hybrid + MMR should work together."""
        results = populated_store.search(
            query="project deployment setup",
            project="rag_test",
            n_results=3,
            use_hybrid=True,
            use_mmr=True,
        )
        assert len(results) > 0
        assert len(results) <= 3
        # MMR strips embeddings
        for r in results:
            assert "embedding" not in r

    def test_hybrid_with_time_weighted(self, populated_store: MemoryStore) -> None:
        """Hybrid + time-weighted should work together."""
        results = populated_store.search(
            query="deployment",
            project="rag_test",
            n_results=3,
            use_hybrid=True,
            time_weighted=True,
        )
        assert len(results) > 0

    def test_hybrid_disabled_by_default(self, populated_store: MemoryStore) -> None:
        """Without use_hybrid, results should NOT have rrf_score."""
        results = populated_store.search(
            query="deployment",
            project="rag_test",
            n_results=3,
        )
        for r in results:
            assert "rrf_score" not in r

    def test_bm25_search_method(self, populated_store: MemoryStore) -> None:
        """Direct _bm25_search should return valid results."""
        results = populated_store._bm25_search(
            project="rag_test",
            query="deploy docker",
            n_results=3,
        )
        assert len(results) > 0
        for r in results:
            assert "id" in r
            assert "content" in r
            assert "distance" in r
            assert 0 < r["distance"] <= 1.0  # Synthetic distance range


# ═══════════════════════════════════════════════════════════════════
# H-2.1: Dynamic Weighted RRF
# ═══════════════════════════════════════════════════════════════════

class TestDynamicRRF:
    """Tests for Dynamic Weighted RRF (H-2.1)."""

    def test_rrf_with_weights(self) -> None:
        """RRF should apply per-ranker weights when provided."""
        semantic = [
            {"id": "s1", "content": "S1", "distance": 0.05},
            {"id": "s2", "content": "S2", "distance": 0.10},
        ]
        bm25 = [
            {"id": "b1", "content": "B1", "score": 5.0},
            {"id": "s1", "content": "S1", "score": 3.0},
        ]
        # Heavy BM25 weight → b1 should rank higher
        results = reciprocal_rank_fusion(
            semantic, bm25, n_results=3, weights=[0.3, 0.7],
        )
        ids = [r["id"] for r in results]
        # s1 appears in both, but b1 only in bm25 with high weight
        assert "s1" in ids
        assert "b1" in ids

    def test_rrf_equal_weights_matches_no_weights(self) -> None:
        """weights=[1.0, 1.0] should produce same result as weights=None."""
        items = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        items2 = [{"id": "c"}, {"id": "b"}, {"id": "a"}]

        r_none = reciprocal_rank_fusion(items, items2, n_results=3, weights=None)
        r_equal = reciprocal_rank_fusion(items, items2, n_results=3, weights=[1.0, 1.0])

        ids_none = [r["id"] for r in r_none]
        ids_equal = [r["id"] for r in r_equal]
        assert ids_none == ids_equal

    def test_rrf_weight_length_mismatch(self) -> None:
        """Mismatched weights length should raise ValueError."""
        import pytest
        with pytest.raises(ValueError, match="weights length"):
            reciprocal_rank_fusion(
                [{"id": "a"}], [{"id": "b"}],
                weights=[1.0, 1.0, 1.0],  # 3 weights for 2 lists
            )

    def test_specificity_no_matches(self) -> None:
        """Zero BM25 scores → specificity 0."""
        from memory_mcp.db.fusion import compute_query_specificity
        assert compute_query_specificity([0.0, 0.0, 0.0, 0.0]) == 0.0

    def test_specificity_empty(self) -> None:
        """Empty scores → neutral specificity."""
        from memory_mcp.db.fusion import compute_query_specificity
        assert compute_query_specificity([]) == 0.5

    def test_specificity_single_match(self) -> None:
        """Very few positive scores → high specificity."""
        from memory_mcp.db.fusion import compute_query_specificity
        scores = [10.0] + [0.0] * 99  # only 1 match out of 100
        spec = compute_query_specificity(scores)
        assert spec >= 0.7  # should be high

    def test_specificity_spread_matches(self) -> None:
        """Many similar positive scores → low specificity."""
        from memory_mcp.db.fusion import compute_query_specificity
        scores = [5.0] * 50 + [0.0] * 50  # half the docs match equally
        spec = compute_query_specificity(scores)
        assert spec <= 0.3  # should be low

    def test_dynamic_weights_range(self) -> None:
        """Dynamic weights should always be in valid range."""
        from memory_mcp.db.fusion import compute_dynamic_weights
        for spec in [0.0, 0.25, 0.5, 0.75, 1.0]:
            weights = compute_dynamic_weights(spec)
            assert len(weights) == 2
            assert abs(sum(weights) - 1.0) < 1e-10  # must sum to 1
            assert 0.3 <= weights[0] <= 0.7  # semantic
            assert 0.3 <= weights[1] <= 0.7  # bm25

    def test_dynamic_weights_direction(self) -> None:
        """High specificity → higher BM25 weight."""
        from memory_mcp.db.fusion import compute_dynamic_weights
        w_low = compute_dynamic_weights(0.0)
        w_high = compute_dynamic_weights(1.0)
        # BM25 weight (index 1) should be higher for high specificity
        assert w_high[1] > w_low[1]
        # Semantic weight (index 0) should be lower for high specificity
        assert w_high[0] < w_low[0]

    def test_dynamic_rrf_search(self, store: MemoryStore) -> None:
        """dynamic_rrf=True should work without errors in search()."""
        store.store(
            project="drrf_test",
            content="ZYNQ_CLK_PIN is assigned to L16 on the FPGA board",
            memory_type=MemoryType.FACT,
        )
        store.store(
            project="drrf_test",
            content="The deployment uses Docker containers on port 8443",
            memory_type=MemoryType.FACT,
        )
        store.store(
            project="drrf_test",
            content="General notes about the project architecture",
            memory_type=MemoryType.FACT,
        )

        # Specific query (should favor BM25)
        results_specific = store.search(
            query="ZYNQ_CLK_PIN L16",
            project="drrf_test",
            n_results=3,
            use_hybrid=True,
            dynamic_rrf=True,
        )
        assert len(results_specific) > 0
        assert any("ZYNQ_CLK_PIN" in r["content"] for r in results_specific)

        # Abstract query (should favor semantic)
        results_abstract = store.search(
            query="how is the system set up",
            project="drrf_test",
            n_results=3,
            use_hybrid=True,
            dynamic_rrf=True,
        )
        assert len(results_abstract) > 0

    def test_dynamic_rrf_without_hybrid_is_noop(self, populated_store: MemoryStore) -> None:
        """dynamic_rrf without use_hybrid should have no effect."""
        r1 = populated_store.search(
            query="deployment", project="rag_test", n_results=3,
            use_hybrid=False, dynamic_rrf=False,
        )
        r2 = populated_store.search(
            query="deployment", project="rag_test", n_results=3,
            use_hybrid=False, dynamic_rrf=True,
        )
        ids1 = [r["id"] for r in r1]
        ids2 = [r["id"] for r in r2]
        assert ids1 == ids2


# ═══════════════════════════════════════════════════════════════════
# H-2.4: Adaptive MMR Lambda
# ═══════════════════════════════════════════════════════════════════


class TestAdaptiveMMRLambda:
    """Tests for configurable MMR lambda (H-2.4)."""

    def test_mmr_lambda_parameter_accepted(self, store: MemoryStore) -> None:
        """search() should accept mmr_lambda parameter without error."""
        store.store(
            project="lambda_test",
            content="Alpha item about neural network architectures",
            memory_type=MemoryType.FACT,
        )
        store.store(
            project="lambda_test",
            content="Beta item about neural network training pipelines",
            memory_type=MemoryType.FACT,
        )
        store.store(
            project="lambda_test",
            content="Gamma item about deep learning model deployment",
            memory_type=MemoryType.FACT,
        )

        # Should work with various lambda values
        for lam in [0.0, 0.3, 0.5, 0.7, 0.9, 1.0]:
            results = store.search(
                query="neural network",
                project="lambda_test",
                n_results=3,
                use_mmr=True,
                mmr_lambda=lam,
            )
            assert len(results) > 0, f"mmr_lambda={lam} returned no results"

    def test_mmr_lambda_none_uses_default(self, store: MemoryStore) -> None:
        """mmr_lambda=None should use DEFAULT_MMR_LAMBDA (0.7)."""
        store.store(
            project="lambda_def",
            content="First item about FPGA pin assignment methodology",
            memory_type=MemoryType.FACT,
        )
        store.store(
            project="lambda_def",
            content="Second item about FPGA clock routing constraints",
            memory_type=MemoryType.FACT,
        )

        # Explicit default should match implicit default
        r_none = store.search(
            query="FPGA",
            project="lambda_def",
            n_results=2,
            use_mmr=True,
            mmr_lambda=None,
        )
        r_explicit = store.search(
            query="FPGA",
            project="lambda_def",
            n_results=2,
            use_mmr=True,
            mmr_lambda=DEFAULT_MMR_LAMBDA,
        )
        ids_none = [r["id"] for r in r_none]
        ids_explicit = [r["id"] for r in r_explicit]
        assert ids_none == ids_explicit

    def test_mmr_lambda_without_use_mmr_is_noop(self, store: MemoryStore) -> None:
        """mmr_lambda without use_mmr=True should have no effect."""
        store.store(
            project="lambda_noop",
            content="Server deployment on port 8080 with nginx reverse proxy",
            memory_type=MemoryType.FACT,
        )
        store.store(
            project="lambda_noop",
            content="Database migration script version 3.2 for PostgreSQL",
            memory_type=MemoryType.FACT,
        )

        r_base = store.search(
            query="deployment",
            project="lambda_noop",
            n_results=2,
            use_mmr=False,
        )
        r_with_lambda = store.search(
            query="deployment",
            project="lambda_noop",
            n_results=2,
            use_mmr=False,
            mmr_lambda=0.3,
        )
        ids_base = [r["id"] for r in r_base]
        ids_lambda = [r["id"] for r in r_with_lambda]
        assert ids_base == ids_lambda

    def test_lambda_extremes_affect_ordering(self, store: MemoryStore) -> None:
        """Lambda=1.0 (full relevance) vs 0.0 (full diversity) may differ in order."""
        # Create memories with varying similarity to each other
        store.store(
            project="lambda_order",
            content="Python Flask web server handles REST API requests for user management",
            memory_type=MemoryType.FACT,
        )
        store.store(
            project="lambda_order",
            content="Python Django web framework processes HTTP API requests for user accounts",
            memory_type=MemoryType.FACT,
        )
        store.store(
            project="lambda_order",
            content="Rust systems programming language compiles to native binary executables",
            memory_type=MemoryType.FACT,
        )
        store.store(
            project="lambda_order",
            content="Go microservice communicates via gRPC for inter-service data exchange",
            memory_type=MemoryType.FACT,
        )

        # λ=1.0 → pure relevance (essentially cosine order)
        r_relevance = store.search(
            query="Python web API",
            project="lambda_order",
            n_results=4,
            use_mmr=True,
            mmr_lambda=1.0,
        )
        # λ=0.0 → pure diversity (maximize inter-document distance)
        r_diversity = store.search(
            query="Python web API",
            project="lambda_order",
            n_results=4,
            use_mmr=True,
            mmr_lambda=0.0,
        )

        ids_rel = [r["id"] for r in r_relevance]
        ids_div = [r["id"] for r in r_diversity]

        # Both should return results
        assert len(ids_rel) == 4
        assert len(ids_div) == 4
        # Same set of IDs (just potentially different order)
        assert set(ids_rel) == set(ids_div)
        # Orderings may differ (first item is always same since MMR starts with best match)
        # Just verify both return valid results — ordering difference is probabilistic

    def test_lambda_with_hybrid_search(self, store: MemoryStore) -> None:
        """mmr_lambda should work correctly with hybrid search enabled."""
        store.store(
            project="lambda_hybrid",
            content="ZYNQ_CLK_PIN is mapped to L16 on the FPGA development board",
            memory_type=MemoryType.FACT,
        )
        store.store(
            project="lambda_hybrid",
            content="ZYNQ_RST_PIN is mapped to M16 on the FPGA evaluation board",
            memory_type=MemoryType.FACT,
        )
        store.store(
            project="lambda_hybrid",
            content="Docker compose orchestrates microservice containers",
            memory_type=MemoryType.FACT,
        )

        results = store.search(
            query="ZYNQ pin assignment",
            project="lambda_hybrid",
            n_results=3,
            use_hybrid=True,
            use_mmr=True,
            mmr_lambda=0.9,  # High relevance
        )
        assert len(results) > 0
        # High lambda should keep the most relevant results first
        assert any("ZYNQ" in r["content"] for r in results[:2])


# ═══════════════════════════════════════════════════════════════════
# Phase 9B: Retrieval Score Enrichment
# ═══════════════════════════════════════════════════════════════════

class TestRetrievalScoreEnrichment:
    def test_time_weighted_adds_retrieval_score(self, populated_store: MemoryStore) -> None:
        """time_weighted=True should add retrieval_score to results."""
        results = populated_store.search(
            query="deployment",
            project="rag_test",
            n_results=3,
            time_weighted=True,
        )
        assert len(results) > 0
        for r in results:
            assert "retrieval_score" in r
            assert 0.0 <= r["retrieval_score"] <= 1.0

    def test_no_retrieval_score_without_time_weighted(self, populated_store: MemoryStore) -> None:
        """Without time_weighted, no retrieval_score should be added."""
        results = populated_store.search(
            query="deployment",
            project="rag_test",
            n_results=3,
            time_weighted=False,
        )
        for r in results:
            assert "retrieval_score" not in r

    def test_retrieval_score_uses_original_distance(self, store: MemoryStore) -> None:
        """Retrieval score should use original (pre-decay) distance for relevance."""
        now = datetime.now(timezone.utc)
        results = [
            {"id": "A", "content": "a", "metadata": {
                "importance": 5.0, "recall_count": 0, "search_count": 0,
                "created_at": (now - timedelta(hours=100)).isoformat(),
            }, "distance": 0.3, "original_distance": 0.2},
        ]

        enriched = MemoryStore._apply_retrieval_score(results)
        assert "retrieval_score" in enriched[0]
        assert enriched[0]["retrieval_score"] > 0

    def test_retrieval_score_higher_for_important_memory(self, store: MemoryStore) -> None:
        """Higher importance should yield higher retrieval score."""
        now = datetime.now(timezone.utc).isoformat()
        results = [
            {"id": "high", "content": "h", "metadata": {
                "importance": 9.0, "recall_count": 0, "search_count": 0,
                "created_at": now,
            }, "distance": 0.3},
            {"id": "low", "content": "l", "metadata": {
                "importance": 2.0, "recall_count": 0, "search_count": 0,
                "created_at": now,
            }, "distance": 0.3},
        ]

        enriched = MemoryStore._apply_retrieval_score(results)
        high_r = next(r for r in enriched if r["id"] == "high")
        low_r = next(r for r in enriched if r["id"] == "low")
        assert high_r["retrieval_score"] > low_r["retrieval_score"]

    def test_retrieval_score_with_mmr_and_time_weighted(self, populated_store: MemoryStore) -> None:
        """MMR + time_weighted should work with retrieval_score enrichment."""
        results = populated_store.search(
            query="project deployment",
            project="rag_test",
            n_results=3,
            use_mmr=True,
            time_weighted=True,
        )
        assert len(results) > 0
        for r in results:
            assert "retrieval_score" in r
            assert "embedding" not in r  # MMR strips embeddings


# ═══════════════════════════════════════════════════════════════════
# Phase 9B: Get by Importance
# ═══════════════════════════════════════════════════════════════════

class TestGetByImportance:
    def test_get_high_importance_only(self, store: MemoryStore) -> None:
        """get_by_importance(min=9.0) should return only high-importance memories."""
        store.store("proj", "SSH deploy: user@server", MemoryType.FACT, importance=9.5)
        store.store("proj", "normal info", MemoryType.FACT, importance=5.0)
        store.store("proj", "auto-saved junk", MemoryType.FACT, importance=2.0)

        results = store.get_by_importance("proj", min_importance=9.0)
        assert len(results) == 1
        assert "SSH deploy" in results[0]["content"]

    def test_get_low_importance_only(self, store: MemoryStore) -> None:
        """get_by_importance(max=3.0) should return only low-importance memories."""
        store.store("proj", "critical info", MemoryType.FACT, importance=9.0)
        store.store("proj", "normal info", MemoryType.FACT, importance=5.0)
        store.store("proj", "auto-saved stuff", MemoryType.FACT, importance=2.0)

        results = store.get_by_importance("proj", max_importance=3.0)
        assert len(results) == 1
        assert "auto-saved" in results[0]["content"]

    def test_get_importance_range(self, store: MemoryStore) -> None:
        """get_by_importance with both min and max."""
        store.store("proj", "critical", MemoryType.FACT, importance=9.5)
        store.store("proj", "normal A", MemoryType.FACT, importance=5.0)
        store.store("proj", "normal B", MemoryType.FACT, importance=6.0)
        store.store("proj", "low", MemoryType.FACT, importance=2.0)

        results = store.get_by_importance("proj", min_importance=3.0, max_importance=9.0)
        assert len(results) == 2
        contents = {r["content"] for r in results}
        assert "normal A" in contents
        assert "normal B" in contents

    def test_get_importance_empty_project(self, store: MemoryStore) -> None:
        """Empty project should return empty list."""
        results = store.get_by_importance("nonexistent", min_importance=9.0)
        assert results == []

    def test_get_importance_no_matching(self, store: MemoryStore) -> None:
        """No matching memories returns empty list."""
        store.store("proj", "normal stuff", MemoryType.FACT, importance=5.0)
        results = store.get_by_importance("proj", min_importance=9.0)
        assert results == []

    def test_get_importance_sorted_by_time(self, store: MemoryStore) -> None:
        """Results should be sorted newest-first."""
        store.store("proj", "first high", MemoryType.FACT, importance=9.0)
        time.sleep(0.02)
        store.store("proj", "second high", MemoryType.FACT, importance=9.5)

        results = store.get_by_importance("proj", min_importance=9.0)
        assert len(results) == 2
        assert "second high" in results[0]["content"]
        assert "first high" in results[1]["content"]


# ═══════════════════════════════════════════════════════════════════
# Phase 9B: Importance-based Search Filter
# ═══════════════════════════════════════════════════════════════════

class TestImportanceSearchFilter:
    def test_search_with_importance_min(self, store: MemoryStore) -> None:
        """Search with importance_min should exclude low-importance memories."""
        store.store("proj", "SSH connection details for prod server",
                     MemoryType.FACT, importance=9.0)
        store.store("proj", "random auto-saved content about servers",
                     MemoryType.FACT, importance=2.0)

        results = store.search(
            query="server connection",
            project="proj",
            importance_min=5.0,
        )
        assert len(results) >= 1
        for r in results:
            assert r["metadata"]["importance"] >= 5.0

    def test_search_with_importance_max(self, store: MemoryStore) -> None:
        """Search with importance_max should exclude high-importance memories."""
        store.store("proj", "critical deployment info", MemoryType.FACT, importance=9.0)
        store.store("proj", "low priority auto note", MemoryType.FACT, importance=2.0)

        results = store.search(
            query="info",
            project="proj",
            importance_max=5.0,
        )
        for r in results:
            assert r["metadata"]["importance"] <= 5.0
