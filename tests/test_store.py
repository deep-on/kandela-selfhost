"""Tests for MemoryStore core functionality."""

import tempfile
from pathlib import Path

import pytest

from memory_mcp.constants import MemoryType
from memory_mcp.db.store import MemoryStore


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    """Create a MemoryStore with a temp directory."""
    return MemoryStore(db_path=str(tmp_path / "test_db"))


class TestStore:
    def test_store_and_search(self, store: MemoryStore) -> None:
        doc_id = store.store(
            project="test_proj",
            content="VHDL RTL for Zynq FPGA uses AXI4-Stream interface",
            memory_type=MemoryType.FACT,
            tags=["vhdl", "fpga"],
        )
        assert doc_id.startswith("test_proj_")

        results = store.search(query="FPGA interface design", project="test_proj")
        assert len(results) > 0
        assert "AXI4-Stream" in results[0]["content"]

    def test_store_multiple_types(self, store: MemoryStore) -> None:
        store.store("proj", "We chose 900MHz for BMS", MemoryType.DECISION)
        store.store("proj", "User prefers snake_case", MemoryType.FACT)
        store.store("proj", "Worked on RF sim today", MemoryType.SUMMARY)

        # Filter by type
        decisions = store.search("frequency band", project="proj", memory_type=MemoryType.DECISION)
        assert len(decisions) > 0
        assert decisions[0]["metadata"]["type"] == "decision"

    def test_cross_project_search(self, store: MemoryStore) -> None:
        store.store("proj_a", "OpenEMS simulation of battery pack RF propagation")
        store.store("proj_b", "802.11ah Wi-Fi HaLow modem firmware")

        results = store.search(
            query="RF wireless communication",
            cross_project=True,
        )
        assert len(results) >= 2

    def test_delete(self, store: MemoryStore) -> None:
        doc_id = store.store("proj", "temporary note")
        assert store.delete("proj", doc_id) is True

        results = store.search("temporary note", project="proj")
        assert len(results) == 0

    def test_list_projects(self, store: MemoryStore) -> None:
        store.store("alpha", "content a")
        store.store("beta", "content b")

        projects = store.list_projects()
        assert "alpha" in projects
        assert "beta" in projects

    def test_project_stats(self, store: MemoryStore) -> None:
        store.store("stats_proj", "fact 1", MemoryType.FACT)
        store.store("stats_proj", "fact 2", MemoryType.FACT)
        store.store("stats_proj", "decision 1", MemoryType.DECISION)

        stats = store.project_stats("stats_proj")
        assert stats["total_memories"] == 3
        assert stats["by_type"]["fact"] == 2
        assert stats["by_type"]["decision"] == 1

    def test_global_stats(self, store: MemoryStore) -> None:
        store.store("proj_x", "content x")
        store.store("proj_y", "content y")

        stats = store.global_stats()
        assert stats["total_projects"] == 2
        assert stats["total_memories"] == 2

    def test_empty_search(self, store: MemoryStore) -> None:
        results = store.search("anything", project="nonexistent")
        assert results == []

    def test_search_no_project_no_cross(self, store: MemoryStore) -> None:
        results = store.search("anything")
        assert results == []

    def test_get_recent_returns_newest_first(self, store: MemoryStore) -> None:
        """get_recent should return memories sorted by creation time, newest first."""
        import time

        store.store("proj", "첫 번째 기억", MemoryType.FACT)
        time.sleep(0.05)
        store.store("proj", "두 번째 기억", MemoryType.DECISION)
        time.sleep(0.05)
        store.store("proj", "세 번째 기억 (최신)", MemoryType.SNIPPET)

        results = store.get_recent("proj", n_results=3)
        assert len(results) == 3
        assert "세 번째" in results[0]["content"]
        assert "첫 번째" in results[2]["content"]

    def test_get_recent_filters_by_type(self, store: MemoryStore) -> None:
        """get_recent should filter by memory type."""
        store.store("proj", "fact 1", MemoryType.FACT)
        store.store("proj", "decision 1", MemoryType.DECISION)
        store.store("proj", "fact 2", MemoryType.FACT)

        results = store.get_recent("proj", memory_type=MemoryType.FACT, n_results=10)
        assert len(results) == 2
        assert all(r["metadata"]["type"] == "fact" for r in results)

    def test_get_recent_empty_project(self, store: MemoryStore) -> None:
        """get_recent on empty project should return empty list."""
        results = store.get_recent("nonexistent", n_results=5)
        assert results == []

    def test_migrate_embeddings(self, store: MemoryStore) -> None:
        store.store("proj", "Python 3.11 사용", MemoryType.FACT)
        store.store("proj", "Docker 배포 결정", MemoryType.DECISION)

        result = store.migrate_embeddings()
        assert result["projects_migrated"] >= 1
        assert result["documents_migrated"] == 2
        assert result["errors"] == 0

        # Search still works after migration
        results = store.search("파이썬 버전", project="proj")
        assert len(results) > 0

    def test_migrate_empty_db(self, store: MemoryStore) -> None:
        result = store.migrate_embeddings()
        assert result["projects_migrated"] == 0
        assert result["documents_migrated"] == 0


class TestStorageInfo:
    """프로젝트별 저장 용량 정보 테스트."""

    def test_empty_project_storage(self, store: MemoryStore) -> None:
        """빈 프로젝트의 저장 용량은 0이어야 한다."""
        info = store.get_project_storage_info("nonexistent")
        assert info["memory_count"] == 0
        assert info["content_bytes"] == 0
        assert info["metadata_bytes"] == 0
        assert info["embedding_bytes"] == 0
        assert info["total_estimated_bytes"] == 0
        assert info["embedding_dim"] > 0

    def test_project_storage_with_data(self, store: MemoryStore) -> None:
        """데이터가 있는 프로젝트의 저장 용량은 양수여야 한다."""
        store.store("proj", "Hello World content", MemoryType.FACT)
        info = store.get_project_storage_info("proj")
        assert info["memory_count"] == 1
        assert info["content_bytes"] > 0
        assert info["metadata_bytes"] > 0
        assert info["embedding_bytes"] > 0
        assert info["total_estimated_bytes"] == (
            info["content_bytes"] + info["metadata_bytes"] + info["embedding_bytes"]
        )

    def test_storage_content_bytes_proportional(self, store: MemoryStore) -> None:
        """콘텐츠 크기는 저장된 텍스트 길이에 비례해야 한다."""
        store.store("proj", "short", MemoryType.FACT)
        info_short = store.get_project_storage_info("proj")

        store.store("proj", "A" * 1000, MemoryType.FACT)
        info_long = store.get_project_storage_info("proj")

        assert info_long["content_bytes"] > info_short["content_bytes"]
        assert info_long["memory_count"] == 2

    def test_storage_embedding_bytes_calculation(self, store: MemoryStore) -> None:
        """임베딩 크기 계산: count * dim * 4 (float32)."""
        store.store("proj", "test", MemoryType.FACT)
        store.store("proj", "test2", MemoryType.FACT)
        info = store.get_project_storage_info("proj")
        expected = 2 * info["embedding_dim"] * 4
        assert info["embedding_bytes"] == expected

    def test_get_all_storage_info(self, store: MemoryStore) -> None:
        """전체 저장 용량 정보 조회."""
        store.store("alpha", "content a", MemoryType.FACT)
        store.store("beta", "content b", MemoryType.DECISION)
        info = store.get_all_storage_info()

        assert "alpha" in info["projects"]
        assert "beta" in info["projects"]
        assert info["totals"]["memory_count"] == 2
        assert info["totals"]["content_bytes"] > 0
        assert info["totals"]["embedding_bytes"] > 0
        assert info["disk_usage_bytes"] >= 0

    def test_get_all_storage_info_empty(self, store: MemoryStore) -> None:
        """빈 DB의 전체 저장 용량."""
        info = store.get_all_storage_info()
        assert info["totals"]["memory_count"] == 0
        assert info["projects"] == {}
        assert info["disk_usage_bytes"] >= 0

    def test_disk_usage_positive(self, store: MemoryStore) -> None:
        """데이터 저장 후 디스크 사용량은 양수여야 한다."""
        store.store("proj", "some content for disk", MemoryType.FACT)
        info = store.get_all_storage_info()
        assert info["disk_usage_bytes"] > 0

    def test_unicode_content_bytes(self, store: MemoryStore) -> None:
        """유니코드 콘텐츠의 바이트 크기가 정확해야 한다."""
        content = "한글 테스트 콘텐츠"
        store.store("proj", content, MemoryType.FACT)
        info = store.get_project_storage_info("proj")
        expected_bytes = len(content.encode("utf-8"))
        assert info["content_bytes"] == expected_bytes

    def test_embedding_dim_attribute(self, store: MemoryStore) -> None:
        """MemoryStore._embedding_dim이 올바르게 설정되어야 한다."""
        assert store._embedding_dim > 0
        # paraphrase-multilingual-MiniLM-L12-v2 has 384 dims
        assert store._embedding_dim == 384


class TestTokenStats:
    """Token usage estimation tests."""

    def test_empty_project_token_stats(self, store: MemoryStore) -> None:
        """빈 프로젝트의 토큰 통계는 모두 0이어야 한다."""
        stats = store.get_project_token_stats("empty_proj")
        assert stats["memory_count"] == 0
        assert stats["estimated_tokens_stored"] == 0
        assert stats["total_recalls"] == 0
        assert stats["total_searches"] == 0
        assert stats["estimated_benefit_tokens"] == 0
        assert stats["guide_tokens_per_session"] == 300
        # V2 benchmark fields
        assert stats["overhead_tokens"] == 0
        assert stats["cost_usd"] == 0.0
        assert stats["benefit_usd"] == 0.0
        assert stats["net_saving_usd"] == 0.0

    def test_token_stats_with_data(self, store: MemoryStore) -> None:
        """데이터가 있으면 토큰 추정치가 양수여야 한다."""
        store.store("proj", "This is test content for token estimation", MemoryType.FACT)
        store.store("proj", "Another piece of important content", MemoryType.DECISION)
        stats = store.get_project_token_stats("proj")
        assert stats["memory_count"] == 2
        assert stats["total_content_chars"] > 0
        assert stats["estimated_tokens_stored"] > 0
        assert stats["avg_content_chars"] > 0

    def test_token_stats_proportional(self, store: MemoryStore) -> None:
        """더 많은 콘텐츠를 가진 프로젝트는 더 높은 토큰 수를 가져야 한다."""
        store.store("small", "short", MemoryType.FACT)
        store.store("large", "this is a much longer content string for testing token estimation accuracy", MemoryType.FACT)
        small = store.get_project_token_stats("small")
        large = store.get_project_token_stats("large")
        assert large["estimated_tokens_stored"] > small["estimated_tokens_stored"]

    def test_get_all_token_stats(self, store: MemoryStore) -> None:
        """전체 토큰 통계가 프로젝트별 합산과 일치해야 한다."""
        store.store("alpha", "content alpha", MemoryType.FACT)
        store.store("beta", "content beta longer", MemoryType.SNIPPET)
        all_stats = store.get_all_token_stats()
        assert "projects" in all_stats
        assert "totals" in all_stats
        assert "session_cost" in all_stats
        assert "alpha" in all_stats["projects"]
        assert "beta" in all_stats["projects"]
        assert all_stats["totals"]["memory_count"] == 2
        assert all_stats["session_cost"]["guide_tokens"] == 300
        assert all_stats["session_cost"]["tool_desc_tokens"] == 889
        # V2 benchmark session cost fields
        assert all_stats["session_cost"]["auto_recall_tokens"] == 260
        assert all_stats["session_cost"]["context_search_tokens"] == 120
        assert all_stats["session_cost"]["session_summary_tokens"] == 170
        assert all_stats["session_cost"]["estimated_overhead_per_session"] == 850
        assert all_stats["session_cost"]["estimated_total"] == 1739
        # V2 benchmark total fields
        assert "overhead_tokens" in all_stats["totals"]
        assert "cost_usd" in all_stats["totals"]
        assert "benefit_usd" in all_stats["totals"]
        assert "net_saving_usd" in all_stats["totals"]

    def test_get_all_token_stats_empty(self, store: MemoryStore) -> None:
        """빈 상태에서의 전체 통계."""
        all_stats = store.get_all_token_stats()
        assert all_stats["totals"]["memory_count"] == 0
        assert all_stats["totals"]["estimated_tokens_stored"] == 0

    def test_usage_counters_reflected(self, store: MemoryStore) -> None:
        """recall/search 카운터가 토큰 통계에 반영되어야 한다."""
        doc_id = store.store("proj", "content for usage test", MemoryType.FACT)
        # Simulate usage
        store.update_usage_counters("proj", [doc_id], "recall_count")
        store.update_usage_counters("proj", [doc_id], "recall_count")
        store.update_usage_counters("proj", [doc_id], "search_count")
        stats = store.get_project_token_stats("proj")
        assert stats["total_recalls"] == 2
        assert stats["total_searches"] == 1
        assert stats["estimated_benefit_tokens"] > 0
        # V2: overhead = 2 recalls * 730 + 1 search * 120 = 1580
        assert stats["overhead_tokens"] == 2 * 730 + 1 * 120
        assert stats["cost_usd"] > 0
        assert stats["benefit_usd"] > 0
        assert stats["net_saving_usd"] > 0  # benefit > cost for 2 recalls


class TestGetAllWorkspacePaths:
    def test_empty_store(self, store: MemoryStore) -> None:
        """프로젝트가 없으면 빈 딕셔너리를 반환한다."""
        assert store.get_all_workspace_paths() == {}

    def test_single_workspace(self, store: MemoryStore) -> None:
        """워크스페이스 경로 메모리가 있으면 매핑을 반환한다."""
        store.store(
            "proj_a",
            "워크스페이스 경로: /home/user/proj-a",
            MemoryType.FACT,
            tags=["workspace", "path"],
            importance=9.0,
        )
        result = store.get_all_workspace_paths()
        assert result == {"proj_a": "/home/user/proj-a"}

    def test_multiple_projects(self, store: MemoryStore) -> None:
        """여러 프로젝트의 워크스페이스 경로를 모두 반환한다."""
        store.store(
            "proj_a", "워크스페이스 경로: /path/a",
            MemoryType.FACT, tags=["workspace", "path"],
        )
        store.store(
            "proj_b", "워크스페이스 경로: /path/b",
            MemoryType.FACT, tags=["workspace", "path"],
        )
        result = store.get_all_workspace_paths()
        assert result == {"proj_a": "/path/a", "proj_b": "/path/b"}

    def test_missing_path_tag_ignored(self, store: MemoryStore) -> None:
        """workspace 태그만 있고 path 태그가 없으면 무시된다."""
        store.store(
            "proj", "워크스페이스 경로: /some/path",
            MemoryType.FACT, tags=["workspace"],
        )
        assert store.get_all_workspace_paths() == {}

    def test_malformed_content_ignored(self, store: MemoryStore) -> None:
        """경로 형식이 아닌 내용은 무시된다."""
        store.store(
            "proj", "random content without path",
            MemoryType.FACT, tags=["workspace", "path"],
        )
        assert store.get_all_workspace_paths() == {}

    def test_projects_without_workspace_omitted(self, store: MemoryStore) -> None:
        """워크스페이스가 없는 프로젝트는 결과에서 제외된다."""
        store.store("proj_a", "일반 메모리", MemoryType.FACT)
        store.store(
            "proj_b", "워크스페이스 경로: /path/b",
            MemoryType.FACT, tags=["workspace", "path"],
        )
        result = store.get_all_workspace_paths()
        assert "proj_a" not in result
        assert result == {"proj_b": "/path/b"}
