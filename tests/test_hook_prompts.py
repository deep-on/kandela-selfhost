"""Tests for server-side hook evaluation logic (hook_prompts.py)."""

import time

import pytest

from memory_mcp.templates.hook_prompts import (
    _DEPLOY_PATTERN,  # noqa: F401
    _DESTRUCTIVE_PATTERN,  # noqa: F401
    _RESTART_PATTERN,  # noqa: F401
    _error_history,
    classify_danger,
    clear_error_signature,
    compute_interval,
    detect_change_intent,
    evaluate_context_monitor,
    evaluate_prompt_guard,
    evaluate_session_start,
    extract_topics,
    match_workspace,
    track_error,
)


class TestClassifyDanger:
    """Test danger command classification."""

    @pytest.mark.parametrize("cmd", [
        "uvicorn app:app --reload",
        "systemctl restart nginx",
        "docker restart my-container",
        "pm2 restart all",
        "nginx -s reload",
        "kill -9 $(pgrep uvicorn)",
        "nohup python run_server.py &",
        "supervisorctl restart worker",
        "service apache2 restart",
    ])
    def test_restart_patterns(self, cmd: str) -> None:
        assert classify_danger(cmd) == "restart"

    @pytest.mark.parametrize("cmd", [
        "docker compose up -d",
        "docker compose -f compose.yaml up -d app",
        "docker compose up -d memory-mcp",
    ])
    def test_no_deps_missing(self, cmd: str) -> None:
        assert classify_danger(cmd) == "no_deps_missing"

    @pytest.mark.parametrize("cmd", [
        "docker compose up -d --no-deps app",
        "docker compose up -d --no-deps memory-mcp",
    ])
    def test_no_deps_present_safe(self, cmd: str) -> None:
        assert classify_danger(cmd) is None

    @pytest.mark.parametrize("cmd", [
        "rm -rf /var/log",
        "rm -rf .",
        "DROP TABLE users",
        "DROP DATABASE mydb",
        "TRUNCATE users",
        "git push origin main --force",
        "git reset --hard HEAD~5",
        "git clean -fd",
        "docker system prune",
        "docker volume rm data",
        "docker volume prune",
        "dd if=/dev/zero of=/dev/sda",
    ])
    def test_destructive_patterns(self, cmd: str) -> None:
        assert classify_danger(cmd) == "destructive"

    @pytest.mark.parametrize("cmd", [
        "kubectl apply -f deploy.yaml",
        "terraform apply",
        "ansible-playbook deploy.yml",
        "docker push myimage:latest",
        "helm install myrelease ./chart",
        "helm upgrade myrelease ./chart",
        "aws ecs deploy",
        "gcloud run deploy",
        "fly deploy",
    ])
    def test_deploy_patterns(self, cmd: str) -> None:
        assert classify_danger(cmd) == "deploy"

    @pytest.mark.parametrize("cmd", [
        "ls -la",
        "git status",
        "pip install requests",
        "python test.py",
        "docker ps",
        "cat /etc/hosts",
        "echo hello",
    ])
    def test_safe_commands(self, cmd: str) -> None:
        assert classify_danger(cmd) is None


class TestComputeInterval:
    """Test adaptive interval calculation."""

    def test_low_usage(self) -> None:
        assert compute_interval(30) == 120

    def test_medium_usage(self) -> None:
        assert compute_interval(55) == 60

    def test_high_usage(self) -> None:
        assert compute_interval(75) == 30

    def test_critical_usage(self) -> None:
        assert compute_interval(90) == 10

    def test_boundary_50(self) -> None:
        assert compute_interval(50) == 60

    def test_boundary_70(self) -> None:
        assert compute_interval(70) == 30

    def test_boundary_85(self) -> None:
        assert compute_interval(85) == 10


class TestErrorTracking:
    """Test error tracking with in-memory state."""

    def setup_method(self) -> None:
        _error_history.clear()

    def test_track_single_error(self) -> None:
        count = track_error("test_proj", "sig1")
        assert count == 1

    def test_track_repeated_errors(self) -> None:
        for _ in range(3):
            count = track_error("test_proj", "sig1")
        assert count == 3

    def test_different_signatures_independent(self) -> None:
        track_error("test_proj", "sig1")
        track_error("test_proj", "sig1")
        count = track_error("test_proj", "sig2")
        assert count == 1

    def test_clear_signature(self) -> None:
        for _ in range(3):
            track_error("test_proj", "sig1")
        clear_error_signature("test_proj", "sig1")
        count = track_error("test_proj", "sig1")
        assert count == 1

    def test_different_projects_independent(self) -> None:
        track_error("proj_a", "sig1")
        track_error("proj_a", "sig1")
        count = track_error("proj_b", "sig1")
        assert count == 1


class TestEvaluateContextMonitor:
    """Test the main evaluation function."""

    def setup_method(self) -> None:
        _error_history.clear()

    def test_safe_command_no_output(self) -> None:
        result = evaluate_context_monitor(
            project="test",
            tool_name="Bash",
            command="ls -la",
            exit_code=0,
        )
        assert result["warn_type"] is None
        assert result["output"] == ""

    def test_restart_detected(self) -> None:
        result = evaluate_context_monitor(
            project="test",
            tool_name="Bash",
            command="docker restart my-container",
        )
        assert result["warn_type"] == "restart"

    def test_no_deps_missing_detected(self) -> None:
        result = evaluate_context_monitor(
            project="test",
            tool_name="Bash",
            command="docker compose up -d",
        )
        assert result["warn_type"] == "no_deps_missing"

    def test_no_deps_present_safe(self) -> None:
        result = evaluate_context_monitor(
            project="test",
            tool_name="Bash",
            command="docker compose up -d --no-deps app",
        )
        assert result["warn_type"] is None

    def test_destructive_detected(self) -> None:
        result = evaluate_context_monitor(
            project="test",
            tool_name="Bash",
            command="rm -rf /tmp/data",
        )
        assert result["warn_type"] == "destructive"

    def test_repeated_failure_output(self) -> None:
        for _ in range(2):
            evaluate_context_monitor(
                project="test",
                tool_name="Bash",
                command="make build",
                exit_code=1,
            )
        result = evaluate_context_monitor(
            project="test",
            tool_name="Bash",
            command="make build",
            exit_code=1,
        )
        assert "반복 실패 감지" in result["output"]
        assert result["err_count"] >= 3

    def test_context_warning_when_high(self) -> None:
        result = evaluate_context_monitor(
            project="test",
            tool_name="Read",
            input_tokens=180000,
            ctx_limit=200000,
            last_check_ts=0,
            interval=0,
            warned=False,
        )
        assert result["warned"] is True
        assert result["next_interval"] == 10

    def test_no_double_warning(self) -> None:
        result = evaluate_context_monitor(
            project="test",
            tool_name="Read",
            input_tokens=180000,
            ctx_limit=200000,
            last_check_ts=0,
            interval=0,
            warned=True,
        )
        # Already warned, should stay warned but not re-trigger
        assert result["warned"] is True

    def test_non_bash_tool_no_danger(self) -> None:
        result = evaluate_context_monitor(
            project="test",
            tool_name="Read",
            command="rm -rf /",
        )
        assert result["warn_type"] is None

    def test_interval_not_elapsed_no_context_check(self) -> None:
        now = time.time()
        result = evaluate_context_monitor(
            project="test",
            tool_name="Read",
            input_tokens=180000,
            last_check_ts=now,
            interval=120,
            warned=False,
        )
        assert result["should_check_context"] is False
        assert result["warned"] is False  # Not checked, so not warned

    def test_adaptive_intervals(self) -> None:  # noqa: E301
        result = evaluate_context_monitor(
            project="test",
            tool_name="Read",
            input_tokens=60000,
            ctx_limit=200000,
            last_check_ts=0,
            interval=0,
        )
        assert result["next_interval"] == 120  # 30% usage


class TestMatchWorkspace:
    """Test workspace matching logic."""

    def test_exact_match(self) -> None:
        workspaces = {"proj_a": "/home/user/proj_a", "proj_b": "/home/user/proj_b"}
        result = match_workspace("/home/user/proj_a", workspaces)
        assert len(result) == 1
        assert result[0][0] == "proj_a"

    def test_child_of_workspace(self) -> None:
        workspaces = {"proj_a": "/home/user/proj_a"}
        result = match_workspace("/home/user/proj_a/src/lib", workspaces)
        assert len(result) == 1
        assert result[0][0] == "proj_a"

    def test_child_longest_prefix(self) -> None:
        workspaces = {
            "parent": "/home/user",
            "child": "/home/user/proj",
        }
        result = match_workspace("/home/user/proj/src", workspaces)
        assert len(result) == 1
        assert result[0][0] == "child"

    def test_parent_single(self) -> None:
        workspaces = {"nested": "/home/user/parent/child"}
        result = match_workspace("/home/user/parent", workspaces)
        assert len(result) == 1
        assert result[0][0] == "nested"

    def test_parent_multiple_ambiguous(self) -> None:
        workspaces = {
            "proj_a": "/home/user/mono/proj_a",
            "proj_b": "/home/user/mono/proj_b",
        }
        result = match_workspace("/home/user/mono", workspaces)
        assert len(result) == 2

    def test_no_match(self) -> None:
        workspaces = {"proj_a": "/home/user/proj_a"}
        result = match_workspace("/opt/other", workspaces)
        assert len(result) == 0

    def test_empty_workspaces(self) -> None:
        result = match_workspace("/home/user", {})
        assert len(result) == 0


class TestEvaluateSessionStart:
    """Test session start evaluation."""

    def test_single_match_prompt(self) -> None:
        result = evaluate_session_start(
            cwd="/home/user/proj",
            hostname="myhost",
            workspaces={"my_proj": "/home/user/proj"},
            server_guide_version=17,
            server_install_version=15,
        )
        assert result["matched"] is True
        assert result["project_id"] == "my_proj"
        assert "my_proj" in result["prompt"]
        assert "auto_recall" in result["prompt"]

    def test_no_match(self) -> None:
        result = evaluate_session_start(
            cwd="/opt/other",
            hostname="myhost",
            workspaces={"my_proj": "/home/user/proj"},
            server_guide_version=17,
            server_install_version=15,
        )
        assert result["matched"] is False
        assert result["project_id"] == ""

    def test_guide_update_hint(self) -> None:
        result = evaluate_session_start(
            cwd="/home/user/proj",
            hostname="myhost",
            workspaces={"my_proj": "/home/user/proj"},
            server_guide_version=18,
            server_install_version=15,
            local_guide_version=16,
        )
        assert len(result["update_hints"]) == 1
        assert "가이드 업데이트" in result["update_hints"][0]

    def test_install_update_hint(self) -> None:
        result = evaluate_session_start(
            cwd="/home/user/proj",
            hostname="myhost",
            workspaces={"my_proj": "/home/user/proj"},
            server_guide_version=17,
            server_install_version=15,
            local_install_version=13,
        )
        assert any("Hook 업데이트" in h for h in result["update_hints"])

    def test_no_update_when_current(self) -> None:
        result = evaluate_session_start(
            cwd="/home/user/proj",
            hostname="myhost",
            workspaces={"my_proj": "/home/user/proj"},
            server_guide_version=17,
            server_install_version=15,
            local_guide_version=17,
            local_install_version=15,
        )
        assert len(result["update_hints"]) == 0

    def test_multi_match_prompt(self) -> None:
        result = evaluate_session_start(
            cwd="/home/user/mono",
            hostname="myhost",
            workspaces={
                "proj_a": "/home/user/mono/a",
                "proj_b": "/home/user/mono/b",
            },
            server_guide_version=17,
            server_install_version=15,
        )
        assert result["matched"] is True
        assert result["project_id"] == ""
        assert len(result["multi_match"]) == 2
        assert "여러 프로젝트" in result["prompt"]


class TestDetectChangeIntent:
    """Test change-intent keyword detection."""

    @pytest.mark.parametrize("prompt", [
        "Let's switch to RabbitMQ for the message queue",
        "Can we change to PostgreSQL?",
        "Replace the current Redis with Kafka",
        "I want to migrate to MongoDB",
        "Should we lower the pool_size to 5?",
        "Let's reduce the timeout",
        "We need to increase the batch size",
        "Remove the legacy API endpoint",
        "Let's delete the old migration files",
        "Can we drop support for Python 3.8?",
        "Disable the rate limiter",
        "Deprecate the v1 API",
        "Upgrade to React 19",
        "Downgrade to Node 18",
        "Let's revert to the old algorithm",
        "We should rollback the schema change",
        "Swap out Express for Fastify",
        "Get rid of the singleton pattern",
        "Stop using SQLAlchemy",
        "Use Celery instead of custom workers",
        "We should no longer use that library",
    ])
    def test_english_change_keywords(self, prompt: str) -> None:
        assert detect_change_intent(prompt) is True

    @pytest.mark.parametrize("prompt", [
        "Redis를 RabbitMQ로 바꾸자",
        "pool_size 변경해주세요",
        "데이터베이스 교체 필요",
        "Kafka로 전환하자",
        "타임아웃을 줄이자",
        "레거시 API 삭제해야 함",
        "v1 API 폐기하자",
        "React 업그레이드하자",
        "롤백 필요",
    ])
    def test_korean_change_keywords(self, prompt: str) -> None:
        assert detect_change_intent(prompt) is True

    @pytest.mark.parametrize("prompt", [
        "How does the Redis connection work?",
        "Show me the pool_size configuration",
        "What is the current architecture?",
        "List all API endpoints",
        "Run the test suite",
        "Create a new service for orders",
        "Add logging to the payment module",
        "Fix the bug in user authentication",
        "Explain the database schema",
        "현재 아키텍처를 설명해주세요",
    ])
    def test_no_change_intent(self, prompt: str) -> None:
        assert detect_change_intent(prompt) is False


class TestExtractTopics:
    """Test topic extraction from prompts."""

    def test_basic_extraction(self) -> None:
        topics = extract_topics("Let's switch to RabbitMQ for the message queue")
        assert "RabbitMQ" in topics
        assert "message" in topics
        assert "queue" in topics

    def test_filters_stop_words(self) -> None:
        topics = extract_topics("Can we change to PostgreSQL for the database?")
        assert "PostgreSQL" in topics
        assert "the" not in topics
        assert "to" not in topics
        assert "we" not in topics

    def test_filters_change_keywords(self) -> None:
        topics = extract_topics("Replace Redis with Kafka")
        # "replace" should be filtered as a change keyword
        lower_topics = [t.lower() for t in topics]
        assert "replace" not in lower_topics
        assert "Redis" in topics or "redis" in [t.lower() for t in topics]
        assert "Kafka" in topics or "kafka" in [t.lower() for t in topics]

    def test_max_topics(self) -> None:
        topics = extract_topics(
            "Switch to RabbitMQ Kafka PostgreSQL MongoDB Elasticsearch Kibana Redis Celery",
            max_topics=3,
        )
        assert len(topics) <= 3

    def test_deduplication(self) -> None:
        topics = extract_topics("Redis Redis redis REDIS configuration")
        redis_count = sum(1 for t in topics if t.lower() == "redis")
        assert redis_count == 1

    def test_korean_topics(self) -> None:
        topics = extract_topics("Redis를 RabbitMQ로 바꾸자")
        # Should have RabbitMQ or Redis (not Korean particles)
        assert any(t in ("Redis", "RabbitMQ") for t in topics)

    def test_code_block_removal(self) -> None:
        topics = extract_topics("Replace ```pool_size=10``` with a dynamic value")
        assert "pool_size=10" not in topics

    def test_url_removal(self) -> None:
        topics = extract_topics("Switch to https://example.com/new-api for the API")
        assert "https://example.com/new-api" not in topics

    def test_empty_prompt(self) -> None:
        topics = extract_topics("")
        assert topics == []

    def test_prefers_longer_words(self) -> None:
        topics = extract_topics("Switch to PostgreSQL or pg")
        if len(topics) >= 2:
            # PostgreSQL should come before pg (longer = more specific)
            pg_idx = next((i for i, t in enumerate(topics) if t.lower() == "postgresql"), None)
            short_idx = next((i for i, t in enumerate(topics) if t.lower() == "pg"), None)
            if pg_idx is not None and short_idx is not None:
                assert pg_idx < short_idx


class TestEvaluatePromptGuard:
    """Test the full prompt guard evaluation."""

    @staticmethod
    def _mock_search(**kwargs):
        """Mock search that returns decisions about Redis."""
        query = kwargs.get("query", "")
        project = kwargs.get("project", "")

        if project == "_global":
            return []

        if "Redis" in query or "redis" in query.lower():
            return [
                {
                    "id": "mem_1",
                    "document": (
                        "Chose Redis over RabbitMQ because team has 3yr "
                        "Redis experience and sub-ms latency for session cache"
                    ),
                    "metadata": {
                        "importance": 8.5,
                        "memory_type": "decision",
                        "project": "test_proj",
                    },
                },
                {
                    "id": "mem_2",
                    "document": (
                        "Redis cluster mode disabled: single-node sufficient "
                        "for <100K sessions, cluster adds complexity"
                    ),
                    "metadata": {
                        "importance": 7.0,
                        "memory_type": "decision",
                        "project": "test_proj",
                    },
                },
            ]
        return []

    @staticmethod
    def _mock_search_empty(**kwargs):
        """Mock search that returns nothing."""
        return []

    def test_no_change_intent(self) -> None:
        result = evaluate_prompt_guard(
            "Show me the Redis configuration",
            "test_proj",
            self._mock_search,
        )
        assert result["has_change_intent"] is False
        assert result["output"] == ""

    def test_change_with_matching_memories(self) -> None:
        result = evaluate_prompt_guard(
            "Let's switch to RabbitMQ and replace Redis",
            "test_proj",
            self._mock_search,
        )
        assert result["has_change_intent"] is True
        assert result["memories_found"] >= 1
        assert "이전에 함께 정한 내용" in result["output"] or "Previous Decisions" in result["output"]
        assert "Redis" in result["output"]
        assert "team has 3yr" in result["output"] or "Redis" in result["output"]

    def test_change_with_no_matching_memories(self) -> None:
        result = evaluate_prompt_guard(
            "Let's switch to a completely new framework",
            "test_proj",
            self._mock_search_empty,
        )
        assert result["has_change_intent"] is True
        assert result["memories_found"] == 0
        assert result["output"] == ""

    def test_topics_extracted(self) -> None:
        result = evaluate_prompt_guard(
            "Can we replace Redis with RabbitMQ?",
            "test_proj",
            self._mock_search,
        )
        assert len(result["topics"]) > 0
        topic_lower = [t.lower() for t in result["topics"]]
        assert "rabbitmq" in topic_lower or "redis" in topic_lower

    def test_korean_prompt(self) -> None:
        result = evaluate_prompt_guard(
            "Redis를 RabbitMQ로 바꾸자",
            "test_proj",
            self._mock_search,
        )
        assert result["has_change_intent"] is True

    def test_output_includes_importance(self) -> None:
        result = evaluate_prompt_guard(
            "Let's replace Redis with something else",
            "test_proj",
            self._mock_search,
        )
        if result["memories_found"] > 0:
            assert "imp:" in result["output"] or "📌" in result["output"]

    def test_output_includes_change_advice(self) -> None:
        result = evaluate_prompt_guard(
            "Switch to RabbitMQ for Redis replacement",
            "test_proj",
            self._mock_search,
        )
        if result["memories_found"] > 0:
            assert "store the new decision" in result["output"] or "confirm_change" in result["output"]

    def test_search_exception_graceful(self) -> None:
        def _failing_search(**kwargs):
            raise RuntimeError("DB connection failed")

        result = evaluate_prompt_guard(
            "Let's switch to RabbitMQ",
            "test_proj",
            _failing_search,
        )
        assert result["has_change_intent"] is True
        assert result["memories_found"] == 0
        assert result["output"] == ""

    def test_pool_size_change(self) -> None:
        def _pool_search(**kwargs):
            query = kwargs.get("query", "")
            project = kwargs.get("project", "")
            if project == "_global":
                return []
            if "pool" in query.lower():
                return [{
                    "id": "mem_pool",
                    "document": (
                        "Set pool_size=20 because load testing showed "
                        "<10 causes connection timeouts under 500 RPS"
                    ),
                    "metadata": {
                        "importance": 8.0,
                        "memory_type": "decision",
                        "project": "test_proj",
                    },
                }]
            return []

        result = evaluate_prompt_guard(
            "Can we lower pool_size to 5?",
            "test_proj",
            _pool_search,
        )
        assert result["has_change_intent"] is True
        assert result["memories_found"] >= 1
        assert "pool_size" in result["output"]
