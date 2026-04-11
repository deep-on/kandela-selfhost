"""Tests for memory_infra_update/get tools and related rules."""

from pathlib import Path

import pytest

from memory_mcp.constants import MemoryType
from memory_mcp.db.store import MemoryStore
from memory_mcp.importance.rules import apply_rule_bonus

PROJECT = "test_project"
INFRA_TAG = "project-infra"


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(db_path=str(tmp_path / "test_db"))


class TestInfraStorage:
    """인프라 문서 저장 구조 테스트."""

    def test_create_infra_document(self, store: MemoryStore) -> None:
        """인프라 문서 최초 생성."""
        content = "## 테스트 실행\n- 위치: docker exec memory-mcp-dev pytest tests/ -v"
        mem_id = store.store(PROJECT, content, MemoryType.FACT,
                             tags=[INFRA_TAG], importance=9.0)

        results = store.get_by_tag(PROJECT, INFRA_TAG)
        assert len(results) == 1
        assert results[0]["content"] == content
        assert results[0]["id"] == mem_id

    def test_update_infra_overwrites(self, store: MemoryStore) -> None:
        """업데이트는 기존 문서를 덮어씀."""
        old = "## 테스트\n- docker exec dev pytest"
        mem_id = store.store(PROJECT, old, MemoryType.FACT,
                             tags=[INFRA_TAG], importance=9.0)

        new = "## 테스트\n- docker exec memory-mcp-dev pytest tests/ -v\n## 벤치마크\n- benchmark_v4만 사용"
        store.update(PROJECT, mem_id, content=new, importance=9.0)

        results = store.get_by_tag(PROJECT, INFRA_TAG)
        assert len(results) == 1
        assert "benchmark_v4" in results[0]["content"]

    def test_only_one_infra_document(self, store: MemoryStore) -> None:
        """프로젝트당 인프라 문서는 하나만."""
        store.store(PROJECT, "인프라 설정 v1", MemoryType.FACT,
                    tags=[INFRA_TAG], importance=9.0)
        results = store.get_by_tag(PROJECT, INFRA_TAG)
        assert len(results) == 1

    def test_infra_importance_is_critical(self, store: MemoryStore) -> None:
        """인프라 문서는 importance 9.0 (critical)."""
        store.store(PROJECT, "테스트 실행 위치", MemoryType.FACT,
                    tags=[INFRA_TAG], importance=9.0)
        results = store.get_by_tag(PROJECT, INFRA_TAG)
        imp = float(results[0]["metadata"].get("importance", 0))
        assert imp >= 9.0

    def test_infra_isolated_per_project(self, store: MemoryStore) -> None:
        """프로젝트별 인프라 문서는 독립."""
        store.store("proj_a", "A 인프라", MemoryType.FACT, tags=[INFRA_TAG], importance=9.0)
        store.store("proj_b", "B 인프라", MemoryType.FACT, tags=[INFRA_TAG], importance=9.0)

        assert "A 인프라" in store.get_by_tag("proj_a", INFRA_TAG)[0]["content"]
        assert "B 인프라" in store.get_by_tag("proj_b", INFRA_TAG)[0]["content"]

    def test_infra_not_mixed_with_progress(self, store: MemoryStore) -> None:
        """project-infra와 project-progress 태그는 독립."""
        store.store(PROJECT, "인프라 설정", MemoryType.FACT, tags=[INFRA_TAG], importance=9.0)
        store.store(PROJECT, "Phase 1 진행 중", MemoryType.FACT,
                    tags=["project-progress"], importance=9.0)

        infra = store.get_by_tag(PROJECT, INFRA_TAG)
        progress = store.get_by_tag(PROJECT, "project-progress")

        assert len(infra) == 1
        assert len(progress) == 1
        assert "인프라" in infra[0]["content"]
        assert "Phase" in progress[0]["content"]


class TestInfraImportanceRules:
    """인프라/테스트 관련 importance 규칙 테스트."""

    def test_test_execution_location_bonus(self) -> None:
        """테스트 실행 위치 패턴 → 높은 importance."""
        content = "pytest는 dev 컨테이너에서만 실행해야 함"
        result = apply_rule_bonus(content, [], 5.0)
        assert result > 7.0  # 패턴 매칭으로 충분히 상승

    def test_benchmark_location_bonus(self) -> None:
        """benchmark_v4 패턴 → 높은 importance."""
        content = "벤치마크 실행: benchmark_v4 디렉토리에서만"
        result = apply_rule_bonus(content, [], 5.0)
        assert result > 7.0

    def test_project_infra_tag_bonus(self) -> None:
        """project-infra 태그 → importance 보너스."""
        content = "테스트 설정"
        base = apply_rule_bonus(content, [], 5.0)
        with_tag = apply_rule_bonus(content, ["project-infra"], 5.0)
        assert with_tag > base

    def test_docker_exec_bonus(self) -> None:
        """docker exec 패턴 → importance 상승."""
        content = "docker exec memory-mcp-dev pytest tests/ -v"
        result = apply_rule_bonus(content, [], 5.0)
        assert result > 5.0

    def test_combined_patterns_reach_critical(self) -> None:
        """테스트 위치 + docker + gotcha 조합 → CRITICAL(9.0) 도달."""
        content = (
            "주의: pytest 테스트 실행 위치는 반드시 dev 컨테이너. "
            "docker exec memory-mcp-dev pytest tests/ -v"
        )
        result = apply_rule_bonus(content, ["gotcha"], 5.0)
        assert result >= 9.0
