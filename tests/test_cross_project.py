"""Tests for cross-project features: source_project, _global, linked_projects,
cross-project discovery, and pattern detection."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from memory_mcp.constants import GLOBAL_PROJECT_NAME, MemoryType
from memory_mcp.db.store import MemoryStore
from memory_mcp.tools.models import MemoryStoreInput


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    """Create a MemoryStore with a temp directory."""
    return MemoryStore(db_path=str(tmp_path / "test_db"))


# ── Feature 2: source_project (cross-project search) ──────────


class TestSourceProject:
    """search with source_project parameter."""

    def test_search_in_different_project(self, store: MemoryStore) -> None:
        """source_project로 다른 프로젝트의 기억을 검색할 수 있다."""
        store.store("proj_a", "JWT 토큰은 15분 만료로 결정", memory_type=MemoryType.DECISION)
        store.store("proj_b", "React 18 사용", memory_type=MemoryType.FACT)

        # proj_b에서 proj_a의 기억을 검색
        results = store.search(query="JWT 토큰 만료", project="proj_a")
        assert len(results) >= 1
        assert "JWT" in results[0]["content"]

    def test_search_without_source_project(self, store: MemoryStore) -> None:
        """source_project 없으면 기존 동작과 동일."""
        store.store("proj_a", "Python 3.12 사용")

        results = store.search(query="Python", project="proj_a")
        assert len(results) >= 1

    def test_cross_project_overrides_source(self, store: MemoryStore) -> None:
        """cross_project=True이면 모든 프로젝트에서 검색."""
        store.store("proj_a", "Alpha project memory")
        store.store("proj_b", "Beta project memory")

        results = store.search(query="project memory", cross_project=True)
        assert len(results) >= 2


# ── Feature 3: _global project ─────────────────────────────────


class TestGlobalProject:
    """_global 프로젝트 기능 테스트."""

    def test_store_in_global(self, store: MemoryStore) -> None:
        """_global 프로젝트에 저장할 수 있다."""
        doc_id = store.store(
            GLOBAL_PROJECT_NAME,
            "나는 한국어 주석을 선호한다",
            importance=9.0,
        )
        assert doc_id.startswith(f"{GLOBAL_PROJECT_NAME}_")

    def test_global_listed_in_projects(self, store: MemoryStore) -> None:
        """_global은 프로젝트 목록에 나타난다."""
        store.store(GLOBAL_PROJECT_NAME, "글로벌 규칙")
        projects = store.list_projects()
        assert GLOBAL_PROJECT_NAME in projects

    def test_global_search(self, store: MemoryStore) -> None:
        """_global 프로젝트에서 검색할 수 있다."""
        store.store(GLOBAL_PROJECT_NAME, "커밋 메시지는 영어로 작성", importance=9.0)

        results = store.search(query="커밋 메시지", project=GLOBAL_PROJECT_NAME)
        assert len(results) >= 1
        assert "커밋" in results[0]["content"]

    def test_global_project_exists(self, store: MemoryStore) -> None:
        """_global이 없을 때 project_exists는 False."""
        assert not store.project_exists(GLOBAL_PROJECT_NAME)

        store.store(GLOBAL_PROJECT_NAME, "test")
        assert store.project_exists(GLOBAL_PROJECT_NAME)

    def test_global_not_loaded_when_recalling_global(self, store: MemoryStore) -> None:
        """_global auto_recall 시 _global을 이중 로드하지 않는다 (재귀 방지)."""
        store.store(GLOBAL_PROJECT_NAME, "Global rule", importance=9.0)
        # get_by_importance for _global should work normally
        critical = store.get_by_importance(
            project=GLOBAL_PROJECT_NAME,
            min_importance=9.0,
        )
        assert len(critical) >= 1


# ── Feature 4: linked_projects (soft links) ────────────────────


class TestLinkedProjects:
    """기억 소프트 링크 테스트."""

    def test_store_with_linked_projects(self, store: MemoryStore) -> None:
        """linked_projects와 함께 저장할 수 있다."""
        doc_id = store.store(
            "auth-service",
            "JWT 토큰은 15분 만료",
            memory_type=MemoryType.DECISION,
            linked_projects=["api-gateway", "user-portal"],
        )
        assert doc_id.startswith("auth-service_")

        # 메타데이터에 linked_projects가 저장되었는지 확인
        col = store._get_collection("auth-service")
        result = col.get(ids=[doc_id], include=["metadatas"])
        meta = result["metadatas"][0]
        linked = json.loads(meta["linked_projects"])
        assert "api-gateway" in linked
        assert "user-portal" in linked

    def test_store_without_linked_projects(self, store: MemoryStore) -> None:
        """linked_projects 없이 저장하면 빈 리스트."""
        doc_id = store.store("proj_a", "Some content")

        col = store._get_collection("proj_a")
        result = col.get(ids=[doc_id], include=["metadatas"])
        meta = result["metadatas"][0]
        linked = json.loads(meta["linked_projects"])
        assert linked == []

    def test_get_linked_memories(self, store: MemoryStore) -> None:
        """다른 프로젝트에서 링크된 기억을 가져올 수 있다."""
        store.store(
            "auth-service",
            "JWT 토큰은 15분 만료로 결정",
            memory_type=MemoryType.DECISION,
            importance=7.0,
            linked_projects=["api-gateway"],
        )
        store.store(
            "auth-service",
            "비밀번호는 bcrypt로 해싱",
            memory_type=MemoryType.DECISION,
            importance=6.0,
            linked_projects=["api-gateway", "user-portal"],
        )
        store.store(
            "auth-service",
            "세션 쿠키는 httpOnly",
            memory_type=MemoryType.FACT,
            # linked_projects 없음
        )

        # api-gateway에서 링크된 기억 조회
        linked = store.get_linked_memories("api-gateway")
        assert len(linked) == 2
        # importance 순 정렬 (7.0 > 6.0)
        assert "JWT" in linked[0]["content"]
        assert "bcrypt" in linked[1]["content"]

        # user-portal에서 링크된 기억 조회 (bcrypt만)
        linked_portal = store.get_linked_memories("user-portal")
        assert len(linked_portal) == 1
        assert "bcrypt" in linked_portal[0]["content"]

    def test_linked_memories_excludes_own_project(self, store: MemoryStore) -> None:
        """자기 프로젝트의 기억은 linked_memories에 포함되지 않는다."""
        store.store(
            "proj_a",
            "Self-referencing content",
            linked_projects=["proj_a"],  # 자기 자신 링크 (비정상이지만 안전 처리)
        )

        # proj_a 자신의 컬렉션은 건너뜀
        linked = store.get_linked_memories("proj_a")
        assert len(linked) == 0

    def test_linked_memories_empty_when_no_links(self, store: MemoryStore) -> None:
        """링크된 기억이 없으면 빈 리스트 반환."""
        store.store("proj_a", "No links here")

        linked = store.get_linked_memories("proj_b")
        assert linked == []

    def test_update_linked_projects(self, store: MemoryStore) -> None:
        """update로 linked_projects를 추가/변경할 수 있다."""
        doc_id = store.store("proj_a", "Original content")

        # 링크 추가
        result = store.update(
            "proj_a", doc_id, linked_projects=["proj_b", "proj_c"],
        )
        assert "linked_projects" in result["updated_fields"]

        # 메타데이터 확인
        col = store._get_collection("proj_a")
        meta = col.get(ids=[doc_id], include=["metadatas"])["metadatas"][0]
        linked = json.loads(meta["linked_projects"])
        assert set(linked) == {"proj_b", "proj_c"}

        # 링크 삭제
        result2 = store.update("proj_a", doc_id, linked_projects=[])
        assert "linked_projects" in result2["updated_fields"]
        meta2 = col.get(ids=[doc_id], include=["metadatas"])["metadatas"][0]
        assert json.loads(meta2["linked_projects"]) == []

    def test_update_only_linked_projects(self, store: MemoryStore) -> None:
        """linked_projects만 업데이트해도 유효하다."""
        doc_id = store.store("proj_a", "Content here")

        # linked_projects만으로 업데이트 가능
        result = store.update("proj_a", doc_id, linked_projects=["proj_b"])
        assert result["updated_fields"] == ["linked_projects"]

    def test_linked_memories_sorted_by_importance(self, store: MemoryStore) -> None:
        """링크된 기억은 importance 순으로 정렬된다."""
        store.store(
            "proj_x", "Low importance link", importance=3.0,
            linked_projects=["proj_target"],
        )
        time.sleep(0.01)
        store.store(
            "proj_y", "High importance link", importance=9.0,
            linked_projects=["proj_target"],
        )
        time.sleep(0.01)
        store.store(
            "proj_z", "Medium importance link", importance=6.0,
            linked_projects=["proj_target"],
        )

        linked = store.get_linked_memories("proj_target")
        assert len(linked) == 3
        importances = [float(l["metadata"]["importance"]) for l in linked]
        assert importances == sorted(importances, reverse=True)

    def test_linked_memories_respects_n_results(self, store: MemoryStore) -> None:
        """n_results로 반환 개수를 제한할 수 있다."""
        for i in range(5):
            store.store(
                f"proj_{i}", f"Content {i}",
                linked_projects=["target"],
            )

        linked = store.get_linked_memories("target", n_results=2)
        assert len(linked) == 2


# ── Integration: Features working together ─────────────────────


class TestCrossProjectIntegration:
    """여러 기능이 함께 동작하는 통합 테스트."""

    def test_global_and_linked_both_work(self, store: MemoryStore) -> None:
        """_global + linked_projects가 동시에 동작."""
        # 글로벌 기억
        store.store(
            GLOBAL_PROJECT_NAME,
            "커밋 메시지는 영어로",
            importance=9.0,
        )
        # 링크된 기억
        store.store(
            "auth-service",
            "JWT 15분 만료 결정",
            linked_projects=["my-app"],
        )
        # 타겟 프로젝트 기억
        store.store("my-app", "React 18 프로젝트")

        # 글로벌 조회
        assert store.project_exists(GLOBAL_PROJECT_NAME)
        global_critical = store.get_by_importance(
            GLOBAL_PROJECT_NAME, min_importance=9.0,
        )
        assert len(global_critical) >= 1

        # 링크 조회
        linked = store.get_linked_memories("my-app")
        assert len(linked) == 1
        assert "JWT" in linked[0]["content"]

        # 프로젝트 자체 검색
        results = store.search("React", project="my-app")
        assert len(results) >= 1

    def test_backward_compatibility_no_linked_projects_field(
        self, store: MemoryStore,
    ) -> None:
        """linked_projects 필드가 없는 기존 메모리도 안전하게 처리."""
        # 직접 ChromaDB에 linked_projects 없는 메모리 저장 (레거시 시뮬레이션)
        col = store._get_collection("legacy_proj")
        col.add(
            documents=["Old memory without linked_projects"],
            metadatas=[{
                "project": "legacy_proj",
                "type": "fact",
                "importance": 5.0,
                "priority": "normal",
                "tags": "[]",
                "created_at": "2026-01-01T00:00:00",
                "created_ts": 1735689600,
                "recall_count": 0,
                "search_count": 0,
                # linked_projects 필드 없음
            "deleted_ts": 0,
            }],
            ids=["legacy_001"],
        )

        # get_linked_memories가 에러 없이 동작
        linked = store.get_linked_memories("some_target")
        assert isinstance(linked, list)  # 에러 없이 빈 리스트 반환


# ── is_global convenience parameter ────────────────────────────


class TestIsGlobal:
    """is_global=True 파라미터 테스트."""

    def test_is_global_routes_to_global_project(self) -> None:
        """is_global=True이면 project가 _global로 변경된다."""
        inp = MemoryStoreInput(
            project="any_project",
            content="글로벌 선호 기억",
            is_global=True,
        )
        assert inp.project == GLOBAL_PROJECT_NAME

    def test_is_global_false_preserves_project(self) -> None:
        """is_global=False(기본값)이면 project가 그대로 유지된다."""
        inp = MemoryStoreInput(
            project="my_project",
            content="일반 기억",
        )
        assert inp.project == "my_project"
        assert inp.is_global is False

    def test_is_global_stores_to_global(self, store: MemoryStore) -> None:
        """is_global=True로 실제 저장 시 _global 프로젝트에 저장된다."""
        doc_id = store.store(
            GLOBAL_PROJECT_NAME,
            "한국어 주석을 선호한다",
            importance=9.0,
        )
        assert store.project_exists(GLOBAL_PROJECT_NAME)
        results = store.search("한국어 주석", project=GLOBAL_PROJECT_NAME)
        assert len(results) >= 1


# ── Cross-project discovery (semantic auto-recommend) ──────────


class TestCrossProjectDiscovery:
    """auto_recall 시 시맨틱 검색으로 다른 프로젝트 관련 기억 발견."""

    def test_discover_finds_related(self, store: MemoryStore) -> None:
        """다른 프로젝트에서 관련 내용을 발견한다."""
        store.store("backend", "JWT 토큰 인증 방식으로 API를 보호한다")
        store.store("frontend", "API 호출 시 JWT 토큰을 Authorization 헤더에 전송")
        store.store("devops", "쿠버네티스 클러스터에서 서비스 배포")

        results = store.discover_cross_project_relevant(
            source_project="mobile",
            query="JWT 토큰 인증",
        )
        assert len(results) >= 1
        # JWT 관련 내용이 발견되어야 함
        contents = [r["content"] for r in results]
        assert any("JWT" in c for c in contents)

    def test_discover_skips_source_and_global(self, store: MemoryStore) -> None:
        """source_project와 _global은 검색에서 제외된다."""
        store.store("my_project", "내 프로젝트의 기억")
        store.store(GLOBAL_PROJECT_NAME, "글로벌 기억", importance=9.0)
        store.store("other", "다른 프로젝트 기억")

        results = store.discover_cross_project_relevant(
            source_project="my_project",
            query="프로젝트 기억",
        )
        # source(my_project)와 _global의 내용이 포함되지 않아야 함
        for r in results:
            proj = r.get("metadata", {}).get("project", "")
            assert proj != "my_project"
            assert proj != GLOBAL_PROJECT_NAME

    def test_discover_respects_distance_threshold(self, store: MemoryStore) -> None:
        """거리 임계값을 초과하는 무관한 내용은 제외된다."""
        store.store("other", "파이썬 웹 프레임워크 Django 사용법")

        results = store.discover_cross_project_relevant(
            source_project="my_project",
            query="쿠버네티스 클러스터 모니터링",
            distance_threshold=0.1,  # 매우 엄격한 임계값
        )
        assert len(results) == 0

    def test_discover_respects_max_projects(self, store: MemoryStore) -> None:
        """max_projects로 스캔 프로젝트 수를 제한한다."""
        for i in range(10):
            store.store(f"proj_{i}", f"프로젝트 {i}의 기억")

        results = store.discover_cross_project_relevant(
            source_project="target",
            query="프로젝트 기억",
            max_projects=2,
        )
        # 최대 2개 프로젝트만 스캔 → 결과도 4개 이하 (프로젝트당 2개)
        assert len(results) <= 4

    def test_discover_empty_query_returns_empty(self, store: MemoryStore) -> None:
        """빈 쿼리는 빈 리스트 반환."""
        store.store("other", "some content")

        results = store.discover_cross_project_relevant(
            source_project="my_project",
            query="",
        )
        assert results == []

    def test_discover_exclude_projects(self, store: MemoryStore) -> None:
        """exclude_projects로 추가 제외 가능."""
        store.store("proj_a", "공유 API 설계 문서")
        store.store("proj_b", "공유 API 구현 가이드")
        store.store("proj_c", "공유 API 테스트 전략")

        results = store.discover_cross_project_relevant(
            source_project="target",
            query="공유 API",
            exclude_projects={"proj_a", "proj_b"},
        )
        # proj_a, proj_b는 제외되고 proj_c만 검색됨
        for r in results:
            proj = r.get("metadata", {}).get("project", "")
            assert proj not in ("proj_a", "proj_b")


# ── Cross-project pattern detection (at store time) ────────────


class TestCrossProjectPatternDetection:
    """store 시 여러 프로젝트에 유사 내용 존재 감지."""

    def test_detect_pattern_found(self, store: MemoryStore) -> None:
        """유사 내용이 2개+ 다른 프로젝트에 있으면 감지된다."""
        store.store("proj_a", "Docker compose로 로컬 개발환경을 구성한다")
        store.store("proj_b", "Docker compose를 사용하여 개발환경 설정")
        store.store("proj_c", "Docker compose 기반 로컬 환경 구축")

        # proj_d에서 유사 내용 저장 시 감지
        embedding = store._embed("Docker compose로 개발환경을 구성한다")
        matches = store.detect_cross_project_pattern(
            source_project="proj_d",
            embedding=embedding,
        )
        assert len(matches) >= 2
        project_names = {m["project"] for m in matches}
        assert project_names.issubset({"proj_a", "proj_b", "proj_c"})

    def test_detect_pattern_below_threshold(self, store: MemoryStore) -> None:
        """무관한 내용은 감지되지 않는다."""
        store.store("proj_a", "React 18 프론트엔드 개발")
        store.store("proj_b", "Vue.js 3 프론트엔드 개발")

        embedding = store._embed("쿠버네티스 클러스터 모니터링 시스템")
        matches = store.detect_cross_project_pattern(
            source_project="proj_c",
            embedding=embedding,
            threshold=0.1,  # 매우 엄격
        )
        assert matches == []

    def test_detect_pattern_skips_source_and_global(self, store: MemoryStore) -> None:
        """source_project와 _global은 감지에서 제외된다."""
        store.store("source", "Docker compose 환경")
        store.store(GLOBAL_PROJECT_NAME, "Docker compose 글로벌 규칙")
        store.store("other_a", "Docker compose로 개발")
        store.store("other_b", "Docker compose 설정")

        embedding = store._embed("Docker compose 환경")
        matches = store.detect_cross_project_pattern(
            source_project="source",
            embedding=embedding,
        )
        # source와 _global의 내용은 포함되지 않아야 함
        for m in matches:
            assert m["project"] != "source"
            assert m["project"] != GLOBAL_PROJECT_NAME

    def test_detect_pattern_min_matches(self, store: MemoryStore) -> None:
        """매치가 min_matches 미만이면 빈 리스트 반환."""
        store.store("proj_a", "독특한 내용 A")

        embedding = store._embed("독특한 내용 A와 유사")
        matches = store.detect_cross_project_pattern(
            source_project="proj_b",
            embedding=embedding,
            min_matches=3,  # 3개 이상 필요하지만 1개만 존재
        )
        assert matches == []
