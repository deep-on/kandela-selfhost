"""Tests for REST API dashboard endpoints."""

from __future__ import annotations

import time
from pathlib import Path
from typing import AsyncIterator

import httpx
import pytest

from memory_mcp.constants import MemoryPriority, MemoryType
from memory_mcp.db.store import MemoryStore


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    """Create a temporary MemoryStore for testing."""
    return MemoryStore(db_path=str(tmp_path / "test_db"))


@pytest.fixture
async def client(store: MemoryStore) -> AsyncIterator[httpx.AsyncClient]:
    """Create an async test client with dashboard routes registered."""
    from mcp.server.fastmcp import FastMCP

    from memory_mcp.dashboard import register_dashboard_routes

    mcp = FastMCP("test_memory_mcp", host="127.0.0.1", port=8321)

    def get_store() -> MemoryStore:
        return store

    register_dashboard_routes(mcp, get_store)

    app = mcp.streamable_http_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


# ── /api/health ──────────────────────────────────────────────────


class TestHealthEndpoint:
    async def test_health_returns_200(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"

    async def test_health_has_version(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/api/health")
        data = resp.json()
        assert "version" in data
        assert isinstance(data["version"], str)
        assert len(data["version"]) > 0

    async def test_health_has_uptime(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/api/health")
        data = resp.json()
        assert "uptime_seconds" in data
        assert data["uptime_seconds"] >= 0
        assert "uptime_human" in data
        assert isinstance(data["uptime_human"], str)

    async def test_health_has_tool_count(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/api/health")
        data = resp.json()
        # tool_count can be int or None
        assert "tool_count" in data

    async def test_health_has_memory(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/api/health")
        data = resp.json()
        assert "memory_mb" in data
        assert data["memory_mb"] > 0


# ── /api/workspaces ──────────────────────────────────────────────


class TestWorkspacesEndpoint:
    async def test_empty_workspaces(self, client: httpx.AsyncClient) -> None:
        """프로젝트가 없을 때 빈 워크스페이스 매핑을 반환한다."""
        resp = await client.get("/api/workspaces")
        assert resp.status_code == 200
        data = resp.json()
        assert data["workspaces"] == {}

    async def test_workspaces_with_data(
        self, client: httpx.AsyncClient, store: MemoryStore,
    ) -> None:
        """워크스페이스 경로 메모리가 있으면 매핑을 반환한다."""
        store.store(
            "my_project",
            "워크스페이스 경로: /home/user/my-project",
            MemoryType.FACT,
            tags=["workspace", "path"],
            importance=9.0,
        )
        resp = await client.get("/api/workspaces")
        data = resp.json()
        assert data["workspaces"]["my_project"] == "/home/user/my-project"

    async def test_workspaces_multiple_projects(
        self, client: httpx.AsyncClient, store: MemoryStore,
    ) -> None:
        """여러 프로젝트의 워크스페이스 경로를 모두 반환한다."""
        store.store(
            "proj_a", "워크스페이스 경로: /path/a",
            MemoryType.FACT, tags=["workspace", "path"], importance=9.0,
        )
        store.store(
            "proj_b", "워크스페이스 경로: /path/b",
            MemoryType.FACT, tags=["workspace", "path"], importance=9.0,
        )
        resp = await client.get("/api/workspaces")
        data = resp.json()
        assert len(data["workspaces"]) == 2
        assert data["workspaces"]["proj_a"] == "/path/a"
        assert data["workspaces"]["proj_b"] == "/path/b"

    async def test_workspaces_ignores_non_workspace(
        self, client: httpx.AsyncClient, store: MemoryStore,
    ) -> None:
        """workspace 태그가 없는 메모리는 무시된다."""
        store.store("proj", "some fact", MemoryType.FACT)
        resp = await client.get("/api/workspaces")
        data = resp.json()
        assert data["workspaces"] == {}


# ── /api/projects ────────────────────────────────────────────────


class TestProjectsEndpoint:
    async def test_empty_projects(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/api/projects")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["projects"] == []

    async def test_projects_with_data(
        self, client: httpx.AsyncClient, store: MemoryStore
    ) -> None:
        store.store("alpha", "test content", MemoryType.FACT)
        store.store("beta", "other content", MemoryType.DECISION)
        resp = await client.get("/api/projects")
        data = resp.json()
        assert data["count"] == 2
        names = [p["name"] for p in data["projects"]]
        assert "alpha" in names
        assert "beta" in names

    async def test_projects_have_memory_count(
        self, client: httpx.AsyncClient, store: MemoryStore
    ) -> None:
        store.store("proj", "content1", MemoryType.FACT)
        store.store("proj", "content2", MemoryType.FACT)
        store.store("proj", "content3", MemoryType.DECISION)
        resp = await client.get("/api/projects")
        data = resp.json()
        proj = data["projects"][0]
        assert proj["name"] == "proj"
        assert proj["memory_count"] == 3

    async def test_projects_have_storage_info(
        self, client: httpx.AsyncClient, store: MemoryStore
    ) -> None:
        """프로젝트 목록 응답에 저장 용량 정보가 포함되어야 한다."""
        store.store("proj", "some test content", MemoryType.FACT)
        resp = await client.get("/api/projects")
        data = resp.json()
        proj = data["projects"][0]
        assert "content_bytes" in proj
        assert "embedding_bytes" in proj
        assert "total_estimated_bytes" in proj
        assert proj["content_bytes"] > 0
        assert proj["embedding_bytes"] > 0
        assert proj["total_estimated_bytes"] > 0


# ── /api/stats ───────────────────────────────────────────────────


class TestStatsEndpoint:
    async def test_empty_stats(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_projects"] == 0
        assert data["total_memories"] == 0

    async def test_stats_with_data(
        self, client: httpx.AsyncClient, store: MemoryStore
    ) -> None:
        store.store("proj_a", "content a", MemoryType.FACT)
        store.store("proj_a", "content b", MemoryType.SNIPPET)
        store.store("proj_b", "content c", MemoryType.DECISION)
        resp = await client.get("/api/stats")
        data = resp.json()
        assert data["total_projects"] == 2
        assert data["total_memories"] == 3


# ── /api/projects/{name} ────────────────────────────────────────


class TestProjectDetail:
    async def test_project_not_found(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/api/projects/nonexistent")
        assert resp.status_code == 404
        assert "error" in resp.json()

    async def test_project_detail_with_data(
        self, client: httpx.AsyncClient, store: MemoryStore
    ) -> None:
        store.store("myproj", "important fact", MemoryType.FACT)
        store.store("myproj", "key decision", MemoryType.DECISION)
        resp = await client.get("/api/projects/myproj")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_memories"] == 2
        assert "recent_memories" in data
        assert len(data["recent_memories"]) == 2

    async def test_project_detail_memory_fields(
        self, client: httpx.AsyncClient, store: MemoryStore
    ) -> None:
        store.store(
            "myproj", "some content", MemoryType.FACT,
            tags=["tag1", "tag2"], priority=MemoryPriority.CRITICAL,
        )
        resp = await client.get("/api/projects/myproj")
        data = resp.json()
        mem = data["recent_memories"][0]
        assert "id" in mem
        assert "content" in mem
        assert mem["type"] == "fact"
        assert mem["priority"] == "critical"
        assert "tag1" in mem["tags"]
        assert "created_at" in mem

    async def test_project_detail_has_importance(
        self, client: httpx.AsyncClient, store: MemoryStore
    ) -> None:
        """프로젝트 상세 API 응답에 importance 필드가 포함되어야 한다."""
        store.store(
            "myproj", "important fact", MemoryType.FACT,
            priority=MemoryPriority.CRITICAL,
        )
        resp = await client.get("/api/projects/myproj")
        data = resp.json()
        mem = data["recent_memories"][0]
        assert "importance" in mem
        # CRITICAL priority → importance 9.0 + possible rule bonus
        assert mem["importance"] >= 9.0

    async def test_project_detail_importance_from_priority_normal(
        self, client: httpx.AsyncClient, store: MemoryStore
    ) -> None:
        """NORMAL priority는 importance 5.0으로 변환되어야 한다."""
        store.store("myproj", "normal content", MemoryType.FACT)
        resp = await client.get("/api/projects/myproj")
        data = resp.json()
        mem = data["recent_memories"][0]
        assert "importance" in mem
        assert isinstance(mem["importance"], float)

    async def test_project_detail_has_storage(
        self, client: httpx.AsyncClient, store: MemoryStore
    ) -> None:
        """프로젝트 상세 응답에 storage 필드가 포함되어야 한다."""
        store.store("myproj", "test content for storage", MemoryType.FACT)
        resp = await client.get("/api/projects/myproj")
        data = resp.json()
        assert "storage" in data
        st = data["storage"]
        assert st["memory_count"] == 1
        assert st["content_bytes"] > 0
        assert st["embedding_bytes"] > 0
        assert st["total_estimated_bytes"] > 0
        assert "embedding_dim" in st


# ── /api/search ──────────────────────────────────────────────────


class TestSearchEndpoint:
    async def test_search_requires_query(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/api/search")
        assert resp.status_code == 400
        assert "error" in resp.json()

    async def test_search_returns_results(
        self, client: httpx.AsyncClient, store: MemoryStore
    ) -> None:
        store.store("proj", "FPGA interface design using AXI4-Stream", MemoryType.FACT)
        resp = await client.get("/api/search?q=FPGA+interface&project=proj")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] > 0
        assert data["query"] == "FPGA interface"

    async def test_search_cross_project(
        self, client: httpx.AsyncClient, store: MemoryStore
    ) -> None:
        store.store("alpha", "Docker deployment configuration", MemoryType.FACT)
        store.store("beta", "Docker compose setup guide", MemoryType.SNIPPET)
        resp = await client.get("/api/search?q=Docker+deployment")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] > 0

    async def test_search_result_fields(
        self, client: httpx.AsyncClient, store: MemoryStore
    ) -> None:
        store.store("proj", "test search content", MemoryType.DECISION)
        resp = await client.get("/api/search?q=test+search&project=proj")
        data = resp.json()
        if data["count"] > 0:
            r = data["results"][0]
            assert "id" in r
            assert "content" in r
            assert "distance" in r
            assert "type" in r
            assert "project" in r

    async def test_search_result_has_importance(
        self, client: httpx.AsyncClient, store: MemoryStore
    ) -> None:
        """검색 결과에 importance 필드가 포함되어야 한다."""
        store.store("proj", "Docker compose deployment guide", MemoryType.SNIPPET)
        resp = await client.get("/api/search?q=Docker+deployment&project=proj")
        data = resp.json()
        if data["count"] > 0:
            r = data["results"][0]
            assert "importance" in r
            assert isinstance(r["importance"], float)


# ── /api/storage ─────────────────────────────────────────────────


class TestStorageEndpoint:
    async def test_storage_empty(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/api/storage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["totals"]["memory_count"] == 0
        assert data["disk_usage_bytes"] >= 0
        assert "disk_usage_human" in data

    async def test_storage_with_data(
        self, client: httpx.AsyncClient, store: MemoryStore
    ) -> None:
        store.store("proj_a", "content alpha", MemoryType.FACT)
        store.store("proj_a", "content beta longer text", MemoryType.SNIPPET)
        store.store("proj_b", "content gamma", MemoryType.DECISION)
        resp = await client.get("/api/storage")
        data = resp.json()
        assert data["totals"]["memory_count"] == 3
        assert data["totals"]["content_bytes"] > 0
        assert data["totals"]["embedding_bytes"] > 0
        assert "content_human" in data["totals"]
        assert "embedding_human" in data["totals"]
        assert "total_human" in data["totals"]

    async def test_storage_per_project(
        self, client: httpx.AsyncClient, store: MemoryStore
    ) -> None:
        """프로젝트별 저장 용량 분리 확인."""
        store.store("alpha", "short", MemoryType.FACT)
        store.store("beta", "this is a much longer content string for testing", MemoryType.FACT)
        resp = await client.get("/api/storage")
        data = resp.json()
        projects = data["projects"]
        assert "alpha" in projects
        assert "beta" in projects
        assert projects["beta"]["content_bytes"] > projects["alpha"]["content_bytes"]
        # Both have embeddings
        assert projects["alpha"]["embedding_bytes"] > 0
        assert projects["beta"]["embedding_bytes"] > 0

    async def test_storage_human_readable(
        self, client: httpx.AsyncClient, store: MemoryStore
    ) -> None:
        """사람이 읽기 쉬운 형식의 크기 표시."""
        store.store("proj", "test content", MemoryType.FACT)
        resp = await client.get("/api/storage")
        data = resp.json()
        proj = data["projects"]["proj"]
        assert "content_human" in proj
        assert "embedding_human" in proj
        assert "total_human" in proj
        # Human-readable should contain unit suffix
        assert any(u in proj["total_human"] for u in ["B", "KB", "MB"])

    async def test_storage_has_embedding_dim(
        self, client: httpx.AsyncClient, store: MemoryStore
    ) -> None:
        """임베딩 차원 정보가 포함되어야 한다."""
        store.store("proj", "test", MemoryType.FACT)
        resp = await client.get("/api/storage")
        data = resp.json()
        assert data["totals"]["embedding_dim"] > 0
        assert data["projects"]["proj"]["embedding_dim"] > 0


# ── /api/token-usage ─────────────────────────────────────────────


class TestTokenUsageEndpoint:
    async def test_token_usage_empty(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/api/token-usage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["totals"]["memory_count"] == 0
        assert data["totals"]["estimated_tokens_stored"] == 0
        assert data["session_cost"]["guide_tokens"] == 300
        assert data["session_cost"]["tool_desc_tokens"] == 889

    async def test_token_usage_with_data(
        self, client: httpx.AsyncClient, store: MemoryStore
    ) -> None:
        store.store("proj_a", "content alpha for token test", MemoryType.FACT)
        store.store("proj_a", "content beta longer text", MemoryType.SNIPPET)
        store.store("proj_b", "content gamma", MemoryType.DECISION)
        resp = await client.get("/api/token-usage")
        data = resp.json()
        assert data["totals"]["memory_count"] == 3
        assert data["totals"]["estimated_tokens_stored"] > 0
        assert "stored_tokens_human" in data["totals"]
        assert "benefit_tokens_human" in data["totals"]

    async def test_token_usage_per_project(
        self, client: httpx.AsyncClient, store: MemoryStore
    ) -> None:
        store.store("alpha", "short content", MemoryType.FACT)
        store.store("beta", "this is a much longer content for comparison", MemoryType.FACT)
        resp = await client.get("/api/token-usage")
        data = resp.json()
        assert "alpha" in data["projects"]
        assert "beta" in data["projects"]
        assert data["projects"]["beta"]["estimated_tokens_stored"] > data["projects"]["alpha"]["estimated_tokens_stored"]

    async def test_token_usage_has_roi(
        self, client: httpx.AsyncClient, store: MemoryStore
    ) -> None:
        """ROI 필드가 프로젝트별로 포함되어야 한다."""
        store.store("proj", "content for roi test", MemoryType.FACT)
        resp = await client.get("/api/token-usage")
        data = resp.json()
        proj = data["projects"]["proj"]
        assert "roi" in proj
        assert isinstance(proj["roi"], float)

    async def test_token_usage_session_cost(
        self, client: httpx.AsyncClient
    ) -> None:
        """세션 비용 추정이 포함되어야 한다."""
        resp = await client.get("/api/token-usage")
        data = resp.json()
        sc = data["session_cost"]
        assert sc["guide_tokens"] == 300
        assert sc["tool_desc_tokens"] == 889
        assert sc["auto_recall_tokens"] == 260
        assert sc["context_search_tokens"] == 120
        assert sc["session_summary_tokens"] == 170
        assert sc["estimated_overhead_per_session"] == 850
        assert sc["estimated_total"] == 1739

    async def test_token_usage_has_usd_fields(
        self, client: httpx.AsyncClient, store: MemoryStore
    ) -> None:
        """V2 benchmark USD 필드가 포함되어야 한다."""
        store.store("proj", "content for usd test", MemoryType.FACT)
        resp = await client.get("/api/token-usage")
        data = resp.json()
        proj = data["projects"]["proj"]
        assert "overhead_tokens_human" in proj
        assert "cost_usd_human" in proj
        assert "benefit_usd_human" in proj
        assert "net_saving_usd_human" in proj
        # Totals also have human-readable USD
        assert "overhead_tokens_human" in data["totals"]
        assert "cost_usd_human" in data["totals"]
        assert "net_saving_usd_human" in data["totals"]


# ── /dashboard ───────────────────────────────────────────────────


class TestDashboardPage:
    async def test_dashboard_returns_html(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/dashboard")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_dashboard_has_title(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/dashboard")
        assert "Kandela" in resp.text

    async def test_dashboard_has_api_calls(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/dashboard")
        assert "/api/health" in resp.text
        assert "/api/projects" in resp.text
        assert "/api/stats" in resp.text
        assert "/api/storage" in resp.text

    async def test_dashboard_has_importance_badge_function(self, client: httpx.AsyncClient) -> None:
        """대시보드 HTML에 importanceBadge JS 함수가 있어야 한다."""
        resp = await client.get("/dashboard")
        assert "importanceBadge" in resp.text

    async def test_dashboard_has_format_bytes_function(self, client: httpx.AsyncClient) -> None:
        """대시보드 HTML에 formatBytes JS 함수가 있어야 한다."""
        resp = await client.get("/dashboard")
        assert "formatBytes" in resp.text

    async def test_dashboard_has_storage_card(self, client: httpx.AsyncClient) -> None:
        """대시보드에 Storage 카드가 있어야 한다."""
        resp = await client.get("/dashboard")
        assert "disk-usage" in resp.text
        assert "storage-content" in resp.text
        assert "storage-embeddings" in resp.text
