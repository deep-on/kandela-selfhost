"""Tests for auto_recall — verifies that recent memories are always returned."""

import time
import tempfile
from pathlib import Path

import pytest

from memory_mcp.constants import MemoryType
from memory_mcp.db.store import MemoryStore


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(db_path=str(tmp_path / "test_db"))


def _simulate_session(store: MemoryStore, project: str, session_num: int) -> None:
    """Simulate a work session that stores facts, decisions, and a summary."""
    store.store(project, f"세션{session_num}: Python 3.11 환경 확인", MemoryType.FACT)
    time.sleep(0.02)
    store.store(project, f"세션{session_num}: FastAPI 대신 FastMCP를 사용하기로 결정", MemoryType.DECISION)
    time.sleep(0.02)
    store.store(project, f"세션{session_num}: docker compose up -d", MemoryType.SNIPPET)
    time.sleep(0.02)
    store.store(
        project,
        f"세션{session_num} 요약: 서버 배포 완료, 테스트 통과, 다음은 hooks 구현",
        MemoryType.SUMMARY,
    )
    time.sleep(0.02)


class TestAutoRecall:
    """Test the auto_recall logic independently (without MCP server layer)."""

    def test_recall_returns_recent_summaries_by_time(self, store: MemoryStore) -> None:
        """가장 최근 세션 요약이 시간순으로 반환되어야 한다."""
        project = "test_recall"
        # 세션 1 (오래된)
        _simulate_session(store, project, 1)
        time.sleep(0.05)
        # 세션 2 (최신)
        _simulate_session(store, project, 2)

        summaries = store.get_recent(project, memory_type=MemoryType.SUMMARY, n_results=3)
        assert len(summaries) == 2
        # 최신 세션이 첫 번째
        assert "세션2" in summaries[0]["content"]
        assert "세션1" in summaries[1]["content"]

    def test_recall_returns_all_types_in_recent(self, store: MemoryStore) -> None:
        """get_recent은 모든 타입(fact, decision, snippet, summary)을 반환해야 한다."""
        project = "test_all_types"
        _simulate_session(store, project, 1)

        recent = store.get_recent(project, n_results=10)
        types_found = {r["metadata"]["type"] for r in recent}
        assert types_found == {"fact", "decision", "snippet", "summary"}

    def test_recall_most_recent_session_not_lost(self, store: MemoryStore) -> None:
        """직전 세션(세션3)의 기억이 반드시 반환되어야 한다 — 핵심 버그 수정 테스트."""
        project = "test_recent_not_lost"
        _simulate_session(store, project, 1)
        time.sleep(0.05)
        _simulate_session(store, project, 2)
        time.sleep(0.05)
        # 세션 3 (가장 최신) — 이것이 누락되는 것이 버그였음
        store.store(project, "세션3: hooks 자동화 구현 완료", MemoryType.FACT)
        time.sleep(0.02)
        store.store(project, "세션3: Stop hook으로 자동 저장 트리거 채택", MemoryType.DECISION)
        time.sleep(0.02)
        store.store(
            project,
            "세션3 요약: hooks 구현 및 배포 완료. Phase 3 마무리.",
            MemoryType.SUMMARY,
        )

        # 최근 기억 조회 — 세션 3 기억이 반드시 포함되어야 함
        recent = store.get_recent(project, n_results=5)
        contents = " ".join(r["content"] for r in recent)
        assert "세션3" in contents, f"세션3 기억이 누락됨! 반환된 내용: {contents}"

        # 최근 요약 조회 — 세션 3 요약이 첫 번째여야 함
        summaries = store.get_recent(project, memory_type=MemoryType.SUMMARY, n_results=3)
        assert "세션3" in summaries[0]["content"]

    def test_recall_semantic_search_complements_recent(self, store: MemoryStore) -> None:
        """시맨틱 검색은 시간순과 다른 결과를 보완적으로 제공해야 한다."""
        project = "test_semantic"
        # 오래된 기억이지만 특정 주제
        store.store(project, "AXI4-Stream 인터페이스 사용 결정", MemoryType.DECISION, tags=["fpga"])
        time.sleep(0.05)
        # 최신 기억 (다른 주제)
        store.store(project, "Docker compose 배포 완료", MemoryType.FACT)
        store.store(project, "pytest 14개 통과", MemoryType.FACT)

        # 시맨틱 검색: "FPGA 인터페이스" → 오래된 기억이 더 관련성 높음
        semantic = store.search(query="FPGA 인터페이스 설계", project=project, n_results=3)
        assert any("AXI4" in r["content"] for r in semantic)

        # 시간순: 최신이 먼저
        recent = store.get_recent(project, n_results=3)
        assert "pytest" in recent[0]["content"] or "Docker" in recent[0]["content"]

    def test_recall_empty_project(self, store: MemoryStore) -> None:
        """빈 프로젝트에서는 빈 결과가 반환되어야 한다."""
        assert store.get_recent("empty_project") == []
        assert store.search("anything", project="empty_project") == []

    def test_recall_deduplication_logic(self, store: MemoryStore) -> None:
        """auto_recall에서 시간순 + 시맨틱이 합쳐질 때 중복이 없어야 한다."""
        project = "test_dedup"
        store.store(project, "ChromaDB 사용 결정", MemoryType.DECISION)

        # 같은 항목이 시간순과 시맨틱 양쪽에서 나올 수 있음
        recent = store.get_recent(project, n_results=5)
        semantic = store.search("ChromaDB", project=project, n_results=5)

        # 양쪽에 같은 ID가 있어야 함 (동일 항목)
        recent_ids = {r["id"] for r in recent}
        semantic_ids = {r["id"] for r in semantic}
        assert recent_ids & semantic_ids  # 교집합이 있어야 함

        # 합칠 때 중복 제거 시뮬레이션
        seen: set[str] = set()
        merged: list[dict] = []
        for item in recent + semantic:
            if item["id"] not in seen:
                seen.add(item["id"])
                merged.append(item)
        assert len(merged) == 1  # 1개만 있으므로 중복 제거 후 1개
