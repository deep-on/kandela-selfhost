"""Tests for brief mode auto_recall — Lazy Retrieval algorithm."""

import time
from pathlib import Path

import pytest

from memory_mcp.constants import MemoryType
from memory_mcp.db.store import MemoryStore


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(db_path=str(tmp_path / "test_db"))


class TestGetProjectBrief:
    """Test store.get_project_brief() — the data source for brief mode."""

    def test_empty_project(self, store: MemoryStore) -> None:
        """빈 프로젝트에 대해 0 카운트 반환."""
        brief = store.get_project_brief("nonexistent_project")
        assert brief["memory_count"] == 0
        assert brief["critical_count"] == 0
        assert brief["type_counts"] == {}
        assert brief["topic_keywords"] == []
        assert brief["last_session_date"] is None
        assert brief["last_summary_snippet"] is None

    def test_basic_counts(self, store: MemoryStore) -> None:
        """기본 카운트 (전체, critical, 타입별)."""
        project = "test_brief"
        store.store(project, "Python 3.11 환경", MemoryType.FACT, importance=5.0)
        store.store(project, "Docker 배포 경로", MemoryType.FACT, importance=9.5)
        store.store(project, "FastMCP 채택 결정", MemoryType.DECISION, importance=7.0)
        store.store(project, "세션 요약: 배포 완료", MemoryType.SUMMARY)

        brief = store.get_project_brief(project)
        assert brief["memory_count"] == 4
        assert brief["critical_count"] == 1  # only importance >= 9.0
        assert brief["type_counts"]["fact"] == 2
        assert brief["type_counts"]["decision"] == 1
        assert brief["type_counts"]["summary"] == 1

    def test_last_session_info(self, store: MemoryStore) -> None:
        """최근 세션 요약 날짜와 스니펫."""
        project = "test_session"
        store.store(
            project,
            "세션 요약: API 인증 구현 완료, 테스트 통과, 다음 단계는 대시보드",
            MemoryType.SUMMARY,
        )
        time.sleep(0.02)

        brief = store.get_project_brief(project)
        assert brief["last_session_date"] is not None
        assert brief["last_summary_snippet"] is not None
        assert len(brief["last_summary_snippet"]) <= 120

    def test_topic_keywords_from_tags(self, store: MemoryStore) -> None:
        """태그에서 토픽 키워드 추출."""
        project = "test_topics"
        store.store(project, "Docker 설정", MemoryType.FACT, tags=["docker", "deployment"])
        store.store(project, "Auth 구현", MemoryType.FACT, tags=["auth", "docker"])
        store.store(project, "테스트 통과", MemoryType.FACT, tags=["testing"])

        brief = store.get_project_brief(project)
        # docker appears in 2 items, should be in topics
        assert "docker" in brief["topic_keywords"]

    def test_auto_saved_excluded_from_tags(self, store: MemoryStore) -> None:
        """auto-saved 태그는 토픽 키워드에서 제외."""
        project = "test_autosaved"
        store.store(
            project, "자동 저장 내용", MemoryType.FACT,
            tags=["auto-saved"], importance=2.0,
        )
        store.store(project, "수동 저장", MemoryType.FACT, tags=["docker"])

        brief = store.get_project_brief(project)
        assert "auto-saved" not in brief["topic_keywords"]


class TestBriefModeOutput:
    """Test the brief mode output characteristics."""

    def test_brief_output_compact(self, store: MemoryStore) -> None:
        """Brief 모드 출력은 compact해야 함 (~100-200 토큰)."""
        from memory_mcp.utils.formatting import (
            format_compact_result,
            format_project_brief,
        )

        project = "test_compact"
        # Populate with typical data
        for i in range(10):
            store.store(project, f"일반 기억 {i}", MemoryType.FACT, importance=5.0)
        store.store(project, "Critical: 배포 경로 /opt/app", MemoryType.FACT, importance=9.5)
        store.store(project, "Critical: SSH root@server", MemoryType.FACT, importance=9.0)
        store.store(project, "세션 요약: 환경 설정 및 배포 완료", MemoryType.SUMMARY)

        brief = store.get_project_brief(project)
        crits = store.get_by_importance(project, min_importance=9.0, n_results=20)

        output = format_project_brief(brief, project, critical_memories=crits)

        # Output should be much shorter than full recall
        # Rough estimate: 200 tokens ≈ ~800 chars for Korean/mixed text
        assert len(output) < 2000, f"Brief output too long: {len(output)} chars"

    def test_model_mode_field(self) -> None:
        """AutoRecallInput에 mode 필드가 있어야 함."""
        from memory_mcp.tools.models import AutoRecallInput

        inp = AutoRecallInput(project="test", mode="brief")
        assert inp.mode == "brief"

        inp_full = AutoRecallInput(project="test", mode="full")
        assert inp_full.mode == "full"

        inp_default = AutoRecallInput(project="test")
        assert inp_default.mode is None

    def test_context_search_input_model(self) -> None:
        """ContextSearchInput 모델 검증."""
        from memory_mcp.tools.models import ContextSearchInput

        inp = ContextSearchInput(query="docker", project="test")
        assert inp.query == "docker"
        assert inp.project == "test"
        assert inp.n_results == 3  # default
        assert inp.include_content is True  # default
        assert inp.cross_project is False  # default

    def test_context_search_input_max_n(self) -> None:
        """ContextSearchInput n_results 최대값 검증."""
        from memory_mcp.tools.models import ContextSearchInput

        inp = ContextSearchInput(query="test", project="p", n_results=10)
        assert inp.n_results == 10

        with pytest.raises(Exception):  # validation error for > 10
            ContextSearchInput(query="test", project="p", n_results=11)
