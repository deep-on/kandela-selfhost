"""Tests for multi-session concurrency tracking (Phase 1 & Phase 2)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from memory_mcp.constants import MemoryType
from memory_mcp.db.store import MemoryStore


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    """Create a MemoryStore with a temp directory."""
    return MemoryStore(db_path=str(tmp_path / "test_db"))


class TestSessionIdStorage:
    """Phase 1: session_id is stored in metadata."""

    def test_store_with_session_id(self, store: MemoryStore) -> None:
        """Memory stored with session_id should have it in metadata."""
        doc_id = store.store(
            project="proj",
            content="Decision made in session A",
            memory_type=MemoryType.DECISION,
            session_id="session-A-123",
        )
        results = store.search(query="decision", project="proj")
        assert len(results) >= 1
        assert results[0]["metadata"]["session_id"] == "session-A-123"

    def test_store_without_session_id(self, store: MemoryStore) -> None:
        """Memory stored without session_id should not have it in metadata."""
        store.store(
            project="proj",
            content="Fact without session tracking",
        )
        results = store.search(query="fact", project="proj")
        assert len(results) >= 1
        # session_id should not be in metadata at all
        assert "session_id" not in results[0]["metadata"]

    def test_multiple_sessions_same_project(self, store: MemoryStore) -> None:
        """Different sessions can store memories in the same project."""
        store.store(
            project="shared",
            content="Session A stored: API endpoint design using REST",
            session_id="session-A",
        )
        store.store(
            project="shared",
            content="Session B stored: database schema migration plan",
            session_id="session-B",
        )

        results = store.search(query="API REST endpoint", project="shared")
        assert len(results) >= 1
        assert results[0]["metadata"]["session_id"] == "session-A"

        results = store.search(query="database schema migration", project="shared")
        assert len(results) >= 1
        assert results[0]["metadata"]["session_id"] == "session-B"


class TestCrossSessionDetection:
    """Phase 2: detect changes from other sessions."""

    def test_get_recent_by_other_sessions(self, store: MemoryStore) -> None:
        """Should return memories from other sessions, not the current one."""
        store.store(
            project="proj",
            content="My own memory from current session",
            session_id="current-session",
        )
        store.store(
            project="proj",
            content="Memory from another concurrent session about deployment",
            session_id="other-session",
        )

        others = store.get_recent_by_other_sessions(
            project="proj",
            current_session_id="current-session",
        )
        assert len(others) == 1
        assert "another concurrent session" in others[0]["content"]
        assert others[0]["metadata"]["session_id"] == "other-session"

    def test_get_recent_by_other_sessions_empty(self, store: MemoryStore) -> None:
        """No results when only the current session has memories."""
        store.store(
            project="proj",
            content="Only current session memory here",
            session_id="my-session",
        )
        others = store.get_recent_by_other_sessions(
            project="proj",
            current_session_id="my-session",
        )
        assert len(others) == 0

    def test_get_recent_by_other_sessions_no_session_id(self, store: MemoryStore) -> None:
        """Memories without session_id should not appear in cross-session results."""
        store.store(
            project="proj",
            content="Old memory without session tracking",
        )
        store.store(
            project="proj",
            content="Memory with session ID from other session",
            session_id="other-session",
        )

        others = store.get_recent_by_other_sessions(
            project="proj",
            current_session_id="my-session",
        )
        # Should only return the one with a different session_id
        assert len(others) == 1
        assert others[0]["metadata"]["session_id"] == "other-session"

    def test_get_recent_by_other_sessions_multiple(self, store: MemoryStore) -> None:
        """Multiple other sessions should all be returned."""
        store.store(project="proj", content="From session A: fix bug #1", session_id="session-A")
        store.store(project="proj", content="From session B: add feature #2", session_id="session-B")
        store.store(project="proj", content="From session C (me): my work", session_id="session-C")

        others = store.get_recent_by_other_sessions(
            project="proj",
            current_session_id="session-C",
        )
        assert len(others) == 2
        session_ids = {m["metadata"]["session_id"] for m in others}
        assert session_ids == {"session-A", "session-B"}

    def test_get_recent_by_other_sessions_n_results(self, store: MemoryStore) -> None:
        """n_results should limit the number returned."""
        for i in range(10):
            store.store(
                project="proj",
                content=f"Memory {i} from other session",
                session_id="other-session",
            )
        store.store(project="proj", content="My memory", session_id="my-session")

        others = store.get_recent_by_other_sessions(
            project="proj",
            current_session_id="my-session",
            n_results=3,
        )
        assert len(others) == 3


class TestSessionSummaryTagging:
    """Session summaries should carry session_id."""

    def test_summary_with_session_id(self, store: MemoryStore) -> None:
        """Session summary stored with session_id."""
        store.store(
            project="proj",
            content="Session summary: completed API refactoring and deployment",
            memory_type=MemoryType.SUMMARY,
            session_id="summary-session",
        )
        recent = store.get_recent(project="proj", memory_type=MemoryType.SUMMARY)
        assert len(recent) >= 1
        assert recent[0]["metadata"]["session_id"] == "summary-session"


class TestBackwardCompatibility:
    """Existing memories without session_id should work fine."""

    def test_search_mixed_session_ids(self, store: MemoryStore) -> None:
        """Memories with and without session_id coexist."""
        store.store(project="proj", content="Old memory without session")
        store.store(project="proj", content="New memory with session", session_id="new-session")

        results = store.search(query="memory", project="proj")
        assert len(results) == 2

    def test_get_recent_mixed(self, store: MemoryStore) -> None:
        """get_recent works with mixed session_id presence."""
        store.store(project="proj", content="Old fact")
        store.store(project="proj", content="New fact", session_id="s1")

        recent = store.get_recent(project="proj")
        assert len(recent) == 2
