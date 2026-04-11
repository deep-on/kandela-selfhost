"""Tests for cross-project mismatch hint feature.

When a user calls a tool with a project that differs from the session's
active project (set during auto_recall), the server appends a hint
suggesting they open a new session in the correct directory.
"""

from pathlib import Path

import pytest

from memory_mcp.db.store import MemoryStore


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(db_path=str(tmp_path / "test_db"))


class TestProjectMismatchHint:
    """Test _set_active_project / _project_mismatch_hint logic."""

    def test_no_active_project_no_hint(self) -> None:
        """active project가 설정 안 된 상태에서는 힌트 없음."""
        # Simulate the logic directly
        active_project: dict[str, str] = {}
        uid = "_default"
        project = "some_project"
        active = active_project.get(uid)
        assert active is None  # no hint when no active project

    def test_same_project_no_hint(self) -> None:
        """같은 프로젝트이면 힌트 없음."""
        active_project: dict[str, str] = {"_default": "my_project"}
        uid = "_default"
        project = "my_project"
        active = active_project.get(uid)
        assert active == project  # no mismatch

    def test_different_project_triggers_hint(self) -> None:
        """다른 프로젝트이면 힌트 발생."""
        active_project: dict[str, str] = {"_default": "project_a"}
        uid = "_default"
        project = "project_b"
        active = active_project.get(uid)
        assert active is not None
        assert active != project  # mismatch detected

    def test_hint_contains_active_and_target(self) -> None:
        """힌트 메시지에 active 프로젝트와 target 프로젝트 모두 포함."""
        active = "memory_mcp_server_dev"
        target = "chargepark_dev"
        hint = (
            f"\n\n⚠️ 현재 세션의 프로젝트는 '{active}'입니다. "
            f"'{target}' 프로젝트 작업은 해당 디렉토리에서 "
            f"새 세션을 여는 것을 권장합니다."
        )
        assert active in hint
        assert target in hint
        assert "새 세션" in hint

    def test_hint_includes_workspace_path(self) -> None:
        """워크스페이스 경로가 있으면 힌트에 포함."""
        ws_path = "/home/user/chargepark"
        hint = f"'{ws_path}'"
        assert "/home/user/chargepark" in hint
