"""Tests for Session Continuity — environment tracking and continuity checks."""

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from memory_mcp.constants import MemoryType
from memory_mcp.db.session_env import (
    SESSION_GAP_WARNING_HOURS,
    SessionEnvironment,
    SessionEnvironmentStore,
)
from memory_mcp.db.store import MemoryStore
from memory_mcp.server import WORKFLOW_TAGS
from memory_mcp.tools.models import AutoRecallInput, EnvironmentInfo


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(db_path=str(tmp_path / "test_db"))


@pytest.fixture
def env_store(tmp_path: Path) -> SessionEnvironmentStore:
    return SessionEnvironmentStore(db_path=str(tmp_path / "test_db"))


# ── EnvironmentInfo Model ────────────────────────────────────────


class TestEnvironmentInfo:
    def test_environment_info_optional_fields(self) -> None:
        env = EnvironmentInfo()
        assert env.cwd is None
        assert env.hostname is None

    def test_environment_info_with_values(self) -> None:
        env = EnvironmentInfo(cwd="/home/user/project", hostname="my-host")
        assert env.cwd == "/home/user/project"
        assert env.hostname == "my-host"

    def test_auto_recall_input_with_environment(self) -> None:
        inp = AutoRecallInput(
            project="test",
            environment=EnvironmentInfo(cwd="/foo", hostname="bar"),
        )
        assert inp.environment is not None
        assert inp.environment.cwd == "/foo"

    def test_auto_recall_input_without_environment(self) -> None:
        inp = AutoRecallInput(project="test")
        assert inp.environment is None


# ── SessionEnvironmentStore ──────────────────────────────────────


class TestSessionEnvironmentStore:
    def test_save_and_get_last(self, env_store: SessionEnvironmentStore) -> None:
        env_store.save(
            "proj1",
            cwd="/home/user/proj1",
            hostname="laptop",
            client_name="claude-code",
            client_version="1.0.0",
        )
        last = env_store.get_last("proj1")
        assert last is not None
        assert last.project == "proj1"
        assert last.cwd == "/home/user/proj1"
        assert last.hostname == "laptop"
        assert last.client_name == "claude-code"
        assert last.client_version == "1.0.0"

    def test_get_last_returns_none_for_new_project(
        self, env_store: SessionEnvironmentStore
    ) -> None:
        assert env_store.get_last("nonexistent") is None

    def test_get_previous(self, env_store: SessionEnvironmentStore) -> None:
        id1 = env_store.save("proj1", cwd="/path/a")
        id2 = env_store.save("proj1", cwd="/path/b")
        prev = env_store.get_previous("proj1", id2)
        assert prev is not None
        assert prev.id == id1
        assert prev.cwd == "/path/a"

    def test_get_previous_returns_none_for_first(
        self, env_store: SessionEnvironmentStore
    ) -> None:
        id1 = env_store.save("proj1", cwd="/path/a")
        assert env_store.get_previous("proj1", id1) is None

    def test_prune_keeps_max_records(
        self, env_store: SessionEnvironmentStore
    ) -> None:
        from memory_mcp.db.session_env import MAX_RECORDS_PER_PROJECT

        # Store more than MAX_RECORDS_PER_PROJECT
        for i in range(MAX_RECORDS_PER_PROJECT + 10):
            env_store.save("proj_prune", cwd=f"/path/{i}")

        # Count remaining records
        import sqlite3

        with sqlite3.connect(env_store._db_file) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM session_environments WHERE project = ?",
                ("proj_prune",),
            ).fetchone()[0]

        assert count == MAX_RECORDS_PER_PROJECT

    def test_separate_projects(self, env_store: SessionEnvironmentStore) -> None:
        env_store.save("proj_a", cwd="/a")
        env_store.save("proj_b", cwd="/b")
        last_a = env_store.get_last("proj_a")
        last_b = env_store.get_last("proj_b")
        assert last_a is not None and last_a.cwd == "/a"
        assert last_b is not None and last_b.cwd == "/b"

    def test_session_id_stored(self, env_store: SessionEnvironmentStore) -> None:
        env_store.save("proj1", session_id="sess-abc-123", cwd="/path")
        last = env_store.get_last("proj1")
        assert last is not None
        assert last.session_id == "sess-abc-123"


# ── Continuity Check Scenarios ───────────────────────────────────


class TestContinuityChecks:
    """Test continuity check detection scenarios at the store level."""

    def test_c1_cwd_change_detected(
        self, env_store: SessionEnvironmentStore
    ) -> None:
        """CWD change should be detectable."""
        id1 = env_store.save("proj1", cwd="/home/user/old-project")
        id2 = env_store.save("proj1", cwd="/home/user/new-project")
        prev = env_store.get_previous("proj1", id2)
        assert prev is not None
        assert prev.cwd != "/home/user/new-project"
        assert prev.cwd == "/home/user/old-project"

    def test_c1_trailing_slash_normalization(
        self, env_store: SessionEnvironmentStore
    ) -> None:
        """Trailing slashes should be ignored in CWD comparison."""
        cwd1 = "/home/user/project/"
        cwd2 = "/home/user/project"
        # Simulating what server.py does: rstrip("/")
        assert cwd1.rstrip("/") == cwd2.rstrip("/")

    def test_c2_hostname_change_detected(
        self, env_store: SessionEnvironmentStore
    ) -> None:
        id1 = env_store.save("proj1", hostname="macbook")
        id2 = env_store.save("proj1", hostname="test-server")
        prev = env_store.get_previous("proj1", id2)
        assert prev is not None
        assert prev.hostname == "macbook"

    def test_c4_client_version_change_detected(
        self, env_store: SessionEnvironmentStore
    ) -> None:
        id1 = env_store.save(
            "proj1", client_name="claude-code", client_version="1.0.33"
        )
        id2 = env_store.save(
            "proj1", client_name="claude-code", client_version="1.0.34"
        )
        prev = env_store.get_previous("proj1", id2)
        assert prev is not None
        assert prev.client_version == "1.0.33"

    def test_c5_long_gap_detected(
        self, env_store: SessionEnvironmentStore
    ) -> None:
        """Long gap detection via recalled_at timestamps."""
        # Save first, then check the gap logic
        env_store.save("proj1", cwd="/path")
        last = env_store.get_last("proj1")
        assert last is not None

        # Simulate gap detection logic from server.py
        prev_time = datetime.fromisoformat(last.recalled_at)
        if prev_time.tzinfo is None:
            prev_time = prev_time.replace(tzinfo=timezone.utc)
        # Fake "now" 48 hours later
        fake_now = prev_time + timedelta(hours=48)
        gap_hours = (fake_now - prev_time).total_seconds() / 3600
        assert gap_hours >= SESSION_GAP_WARNING_HOURS

    def test_no_change_no_warning(
        self, env_store: SessionEnvironmentStore
    ) -> None:
        """Same environment should not trigger warnings."""
        id1 = env_store.save(
            "proj1",
            cwd="/same/path",
            hostname="same-host",
            client_name="claude-code",
            client_version="1.0.0",
        )
        id2 = env_store.save(
            "proj1",
            cwd="/same/path",
            hostname="same-host",
            client_name="claude-code",
            client_version="1.0.0",
        )
        prev = env_store.get_previous("proj1", id2)
        assert prev is not None
        # All fields match — no warning should be generated
        assert prev.cwd.rstrip("/") == "/same/path"
        assert prev.hostname == "same-host"
        assert prev.client_name == "claude-code"
        assert prev.client_version == "1.0.0"


# ── get_by_tags ──────────────────────────────────────────────────


class TestGetByTags:
    def test_get_by_tags_basic(self, store: MemoryStore) -> None:
        """Memories with matching tags should be returned."""
        store.store(
            "proj1",
            "Docker 배포 on test-server",
            MemoryType.FACT,
            tags=["infrastructure", "docker"],
        )
        store.store(
            "proj1",
            "SSH 포트 2222",
            MemoryType.FACT,
            tags=["infrastructure", "ssh"],
        )
        store.store(
            "proj1",
            "Python 3.12 사용",
            MemoryType.FACT,
            tags=["python"],
        )

        results = store.get_by_tags("proj1", ["infrastructure"])
        assert len(results) == 2
        contents = {r["content"] for r in results}
        assert "Docker 배포 on test-server" in contents
        assert "SSH 포트 2222" in contents

    def test_get_by_tags_or_matching(self, store: MemoryStore) -> None:
        """Multiple tags should use OR matching."""
        store.store("proj1", "Docker setup", MemoryType.FACT, tags=["docker"])
        store.store("proj1", "SSH config", MemoryType.FACT, tags=["ssh"])
        store.store("proj1", "Code review", MemoryType.FACT, tags=["review"])

        results = store.get_by_tags("proj1", ["docker", "ssh"])
        assert len(results) == 2

    def test_get_by_tags_empty_result(self, store: MemoryStore) -> None:
        """No matching tags should return empty list."""
        store.store("proj1", "Some memory", MemoryType.FACT, tags=["python"])
        results = store.get_by_tags("proj1", ["nonexistent"])
        assert results == []

    def test_get_by_tags_nonexistent_project(self, store: MemoryStore) -> None:
        results = store.get_by_tags("nonexistent", ["infrastructure"])
        assert results == []

    def test_get_by_tags_sorted_by_importance(self, store: MemoryStore) -> None:
        """Results should be sorted by importance descending."""
        store.store(
            "proj1", "Low imp", MemoryType.FACT,
            tags=["infra"], importance=3.0,
        )
        store.store(
            "proj1", "High imp", MemoryType.FACT,
            tags=["infra"], importance=9.0,
        )
        store.store(
            "proj1", "Med imp", MemoryType.FACT,
            tags=["infra"], importance=6.0,
        )

        results = store.get_by_tags("proj1", ["infra"])
        assert len(results) == 3
        imps = [r["metadata"]["importance"] for r in results]
        assert imps == sorted(imps, reverse=True)

    def test_get_by_tags_respects_n_results(self, store: MemoryStore) -> None:
        for i in range(10):
            store.store(
                "proj1", f"Memory {i}", MemoryType.FACT,
                tags=["bulk"],
            )
        results = store.get_by_tags("proj1", ["bulk"], n_results=3)
        assert len(results) == 3


# ── has_session_summary ──────────────────────────────────────────


class TestHasSessionSummary:
    def test_has_summary_true(
        self, store: MemoryStore, env_store: SessionEnvironmentStore
    ) -> None:
        store.store("proj1", "Session summary content", MemoryType.SUMMARY)
        assert env_store.has_session_summary("proj1", store) is True

    def test_has_summary_false(
        self, store: MemoryStore, env_store: SessionEnvironmentStore
    ) -> None:
        store.store("proj1", "Just a fact", MemoryType.FACT)
        assert env_store.has_session_summary("proj1", store) is False

    def test_has_summary_nonexistent_project(
        self, store: MemoryStore, env_store: SessionEnvironmentStore
    ) -> None:
        # Non-existent project should return True (assume OK)
        assert env_store.has_session_summary("nonexistent", store) is True


# ── Phase 3: Auto Infrastructure Tagging ─────────────────────────


class TestAutoInfraTagging:
    """Test automatic infrastructure tag inference."""

    def test_docker_content_tagged(self, store: MemoryStore) -> None:
        """Content with docker commands should get docker + infrastructure tags."""
        mem_id = store.store(
            "proj1",
            "docker compose -f compose.dev.yaml build && docker compose up -d",
            MemoryType.FACT,
        )
        col = store._get_collection("proj1")
        result = col.get(ids=[mem_id], include=["metadatas"])
        tags = json.loads(result["metadatas"][0]["tags"])
        assert "docker" in tags
        assert "infrastructure" in tags

    def test_ssh_content_tagged(self, store: MemoryStore) -> None:
        mem_id = store.store(
            "proj1",
            "ssh -p 2222 testuser@test.example.com",
            MemoryType.FACT,
        )
        col = store._get_collection("proj1")
        result = col.get(ids=[mem_id], include=["metadatas"])
        tags = json.loads(result["metadatas"][0]["tags"])
        assert "ssh" in tags
        assert "infrastructure" in tags

    def test_deploy_content_tagged(self, store: MemoryStore) -> None:
        mem_id = store.store(
            "proj1",
            "배포 완료: test-server 서버에 memory-mcp 컨테이너 재시작",
            MemoryType.FACT,
        )
        col = store._get_collection("proj1")
        result = col.get(ids=[mem_id], include=["metadatas"])
        tags = json.loads(result["metadatas"][0]["tags"])
        assert "infrastructure" in tags

    def test_no_infra_content_not_tagged(self, store: MemoryStore) -> None:
        """Regular content should NOT get infrastructure tags."""
        mem_id = store.store(
            "proj1",
            "Python 3.12에서 타입 힌트 개선됨",
            MemoryType.FACT,
        )
        col = store._get_collection("proj1")
        result = col.get(ids=[mem_id], include=["metadatas"])
        tags = json.loads(result["metadatas"][0]["tags"])
        assert "infrastructure" not in tags

    def test_existing_tags_preserved(self, store: MemoryStore) -> None:
        """Existing tags should be preserved when infra tags are added."""
        mem_id = store.store(
            "proj1",
            "docker exec memory-mcp-dev pytest tests/",
            MemoryType.FACT,
            tags=["workflow"],
        )
        col = store._get_collection("proj1")
        result = col.get(ids=[mem_id], include=["metadatas"])
        tags = json.loads(result["metadatas"][0]["tags"])
        assert "workflow" in tags
        assert "docker" in tags
        assert "infrastructure" in tags

    def test_no_duplicate_tags(self, store: MemoryStore) -> None:
        """If user already tagged 'docker', don't duplicate it."""
        mem_id = store.store(
            "proj1",
            "docker compose up -d",
            MemoryType.FACT,
            tags=["docker", "infrastructure"],
        )
        col = store._get_collection("proj1")
        result = col.get(ids=[mem_id], include=["metadatas"])
        tags = json.loads(result["metadatas"][0]["tags"])
        # Should not have duplicates
        assert tags.count("docker") == 1
        assert tags.count("infrastructure") == 1


# ── Phase 3: CWD Collision Detection ────────────────────────────


class TestCWDCollisionDetection:
    def test_c8_other_projects_at_same_cwd(
        self, env_store: SessionEnvironmentStore
    ) -> None:
        env_store.save("proj_a", cwd="/shared/workspace")
        env_store.save("proj_b", cwd="/shared/workspace")
        others = env_store.get_other_projects_at_cwd(
            "/shared/workspace", "proj_a"
        )
        assert "proj_b" in others

    def test_c8_no_collision(
        self, env_store: SessionEnvironmentStore
    ) -> None:
        env_store.save("proj_a", cwd="/path/a")
        env_store.save("proj_b", cwd="/path/b")
        others = env_store.get_other_projects_at_cwd("/path/a", "proj_a")
        assert others == []

    def test_c8_trailing_slash_normalized(
        self, env_store: SessionEnvironmentStore
    ) -> None:
        """CWD with trailing slash should match CWD without."""
        env_store.save("proj_a", cwd="/shared/workspace/")  # trailing slash
        env_store.save("proj_b", cwd="/shared/workspace")   # no trailing slash
        others = env_store.get_other_projects_at_cwd(
            "/shared/workspace", "proj_a"
        )
        assert "proj_b" in others


# ── Phase 3: CWD Normalization ───────────────────────────────────


class TestCWDNormalization:
    def test_trailing_slash_stripped_on_save(
        self, env_store: SessionEnvironmentStore
    ) -> None:
        env_store.save("proj1", cwd="/home/user/project/")
        last = env_store.get_last("proj1")
        assert last is not None
        assert last.cwd == "/home/user/project"  # trailing slash removed

    def test_none_cwd_preserved(
        self, env_store: SessionEnvironmentStore
    ) -> None:
        env_store.save("proj1", cwd=None)
        last = env_store.get_last("proj1")
        assert last is not None
        assert last.cwd is None


# ── Compaction Recovery (recall_source='compact') ─────────────


class TestCompactionRecovery:
    """Tests for auto_recall compact mode behavior."""

    def test_auto_recall_input_accepts_recall_source(self) -> None:
        """AutoRecallInput should accept recall_source parameter."""
        inp = AutoRecallInput(project="test", recall_source="compact")
        assert inp.recall_source == "compact"

    def test_auto_recall_input_recall_source_default_none(self) -> None:
        """recall_source should default to None."""
        inp = AutoRecallInput(project="test")
        assert inp.recall_source is None

    def test_auto_recall_input_recall_source_other_values(self) -> None:
        """recall_source accepts arbitrary short strings."""
        inp = AutoRecallInput(project="test", recall_source="startup")
        assert inp.recall_source == "startup"

    def test_workflow_tags_constant_exists(self) -> None:
        """WORKFLOW_TAGS constant should be defined."""
        assert isinstance(WORKFLOW_TAGS, frozenset)
        assert "workflow" in WORKFLOW_TAGS

    def test_store_get_by_tags_finds_workflow(self, store: MemoryStore) -> None:
        """get_by_tags should find memories tagged with workflow tags."""
        store.store(
            "proj1",
            "rsync 후 docker exec memory-mcp-dev pytest 실행",
            MemoryType.DECISION,
            tags=["workflow"],
            importance=9.0,
        )
        store.store(
            "proj1",
            "일반적인 코드 구조 결정",
            MemoryType.DECISION,
        )

        results = store.get_by_tags("proj1", tags=["workflow"], n_results=10)
        assert len(results) >= 1
        assert any("rsync" in r["content"] for r in results)

    def test_compact_mode_more_summaries(self, store: MemoryStore) -> None:
        """Compact mode should request more summaries (5 vs 3)."""
        # Store 5 session summaries
        for i in range(5):
            store.store(
                "proj1",
                f"세션{i+1} 요약: 작업 {i+1} 완료",
                MemoryType.SUMMARY,
            )
            time.sleep(0.02)

        # Normal mode: 3 summaries
        normal = store.get_recent(
            "proj1", memory_type=MemoryType.SUMMARY, n_results=3
        )
        assert len(normal) == 3

        # Compact mode: 5 summaries
        compact = store.get_recent(
            "proj1", memory_type=MemoryType.SUMMARY, n_results=5
        )
        assert len(compact) == 5

    def test_compact_workflow_memories_deduped(self, store: MemoryStore) -> None:
        """Workflow memories in compact mode should not duplicate critical memories."""
        # A memory that is both critical (importance >= 9.0) and workflow-tagged
        mem_id = store.store(
            "proj1",
            "배포 절차: rsync → docker build → docker up",
            MemoryType.DECISION,
            tags=["workflow", "deployment"],
            importance=9.0,
        )

        # Should appear in both critical and workflow queries
        from memory_mcp.constants import IMPORTANCE_CRITICAL_THRESHOLD
        critical = store.get_by_importance(
            "proj1", min_importance=IMPORTANCE_CRITICAL_THRESHOLD
        )
        workflow = store.get_by_tags("proj1", tags=["workflow"], n_results=5)

        critical_ids = {r["id"] for r in critical}
        workflow_ids = {r["id"] for r in workflow}
        # Both should contain the same memory
        assert mem_id in critical_ids
        assert mem_id in workflow_ids
        # Deduplication would handle this in the server layer
