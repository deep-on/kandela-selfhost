"""Tests for duplicate detection at store time."""

from pathlib import Path

import pytest

from memory_mcp.constants import DUPLICATE_DISTANCE_THRESHOLD, IMPORTANCE_MAX
from memory_mcp.db.store import MemoryStore


@pytest.fixture()
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(db_path=str(tmp_path / "test_db"))


class TestCheckDuplicate:
    """Test check_duplicate() method."""

    def test_exact_duplicate_detected(self, store: MemoryStore) -> None:
        """동일 텍스트 저장 시 중복 감지."""
        store.store("proj", "SSH 접속: ssh testuser@test-server")
        match, embedding = store.check_duplicate(
            "proj", "SSH 접속: ssh testuser@test-server",
        )
        assert match is not None
        assert match["distance"] < 0.05  # near-zero for exact match
        assert "testuser" in match["content"]
        assert len(embedding) > 0

    def test_paraphrase_detected(self, store: MemoryStore) -> None:
        """유사 표현(paraphrase) 중복 감지."""
        store.store("proj", "배포 시 Docker Compose를 사용해서 서비스를 시작한다")
        match, _ = store.check_duplicate(
            "proj", "Docker Compose로 배포하여 서비스 시작",
        )
        # Paraphrases should have low distance with multilingual model
        assert match is not None
        assert match["distance"] < DUPLICATE_DISTANCE_THRESHOLD

    def test_different_content_passes(self, store: MemoryStore) -> None:
        """다른 내용은 중복이 아님."""
        store.store("proj", "Python 3.11 이상이 필요합니다")
        match, embedding = store.check_duplicate(
            "proj", "오늘 날씨가 맑고 화창합니다",
        )
        assert match is None
        assert len(embedding) > 0

    def test_empty_project_returns_none(self, store: MemoryStore) -> None:
        """빈 프로젝트에서는 중복 없음."""
        match, embedding = store.check_duplicate(
            "empty_proj", "아무 내용",
        )
        assert match is None
        assert len(embedding) > 0

    def test_cross_project_isolation(self, store: MemoryStore) -> None:
        """다른 프로젝트 간 중복 체크 격리."""
        store.store("proj_a", "중요한 설정값 정보")
        match, _ = store.check_duplicate(
            "proj_b", "중요한 설정값 정보",
        )
        assert match is None  # 다른 프로젝트이므로 중복 아님

    def test_returns_embedding_for_reuse(self, store: MemoryStore) -> None:
        """embedding이 항상 반환되어 store()에서 재사용 가능."""
        _, embedding = store.check_duplicate("proj", "테스트 컨텐츠")
        assert isinstance(embedding, list)
        assert len(embedding) == 384  # MiniLM embedding dimension

    def test_custom_threshold(self, store: MemoryStore) -> None:
        """사용자 정의 threshold 적용."""
        store.store("proj", "메모리 관련 설정")
        # Very strict threshold — even slightly different text should be flagged
        match_strict, _ = store.check_duplicate(
            "proj", "메모리 관련 설정", threshold=0.5,
        )
        assert match_strict is not None

    def test_duplicate_match_contains_metadata(self, store: MemoryStore) -> None:
        """중복 결과에 metadata 포함."""
        store.store("proj", "포트 번호는 8321입니다", tags=["config"])
        match, _ = store.check_duplicate("proj", "포트 번호는 8321입니다")
        assert match is not None
        assert "metadata" in match
        assert match["metadata"]["project"] == "proj"


class TestStoreWithDuplicateCheck:
    """Test store() with _embedding parameter."""

    def test_precomputed_embedding_used(self, store: MemoryStore) -> None:
        """pre-computed embedding이 store()에 전달되면 재사용."""
        _, embedding = store.check_duplicate("proj", "테스트 메모리")
        # Store with pre-computed embedding
        doc_id = store.store("proj", "테스트 메모리", _embedding=embedding)
        assert doc_id is not None

        # Verify it's searchable
        results = store.search("테스트 메모리", project="proj")
        assert len(results) >= 1
        assert results[0]["content"] == "테스트 메모리"

    def test_store_without_embedding_still_works(self, store: MemoryStore) -> None:
        """_embedding=None이면 자체 임베딩 수행."""
        doc_id = store.store("proj", "자체 임베딩 테스트")
        assert doc_id is not None
        results = store.search("자체 임베딩 테스트", project="proj")
        assert len(results) >= 1

    def test_force_store_creates_both(self, store: MemoryStore) -> None:
        """force_store 시나리오: 동일 내용 두 개 저장 가능."""
        id1 = store.store("proj", "같은 내용의 메모리")
        id2 = store.store("proj", "같은 내용의 메모리")
        assert id1 != id2

        col = store._get_collection("proj")
        assert col.count() == 2
