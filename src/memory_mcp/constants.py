"""Shared constants and types for kandela."""

import os
from enum import Enum
from pathlib import Path

__version__ = "0.1.0"


class MemoryType(str, Enum):
    """Memory classification types."""

    FACT = "fact"          # 영구 사실: 선호도, 환경, 기술스택
    DECISION = "decision"  # 설계 결정, trade-off 기록
    SUMMARY = "summary"    # 세션 요약
    SNIPPET = "snippet"    # 코드 패턴, 설정값


class MemoryPriority(str, Enum):
    """Memory priority levels for recall ordering.

    - CRITICAL: Must always be loaded at session start. Forgetting causes wasted effort.
    - NORMAL: Loaded via semantic search when relevant topic arises.
    - LOW: Safety-net auto-saved content. Only used as fallback.
    """

    CRITICAL = "critical"  # 잊으면 삽질: 배포 경로, 환경 gotcha, 아키텍처 제약
    NORMAL = "normal"      # 있으면 도움: 완료 작업, 설계 결정, 코드 패턴
    LOW = "low"            # 안전망: auto-saved 원문, 상세 로그


# ChromaDB collection prefix
COLLECTION_PREFIX = "project_"

# Default search results count
DEFAULT_N_RESULTS = 5
MAX_N_RESULTS = 20

# Embedding model defaults
DEFAULT_EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"


def _default_db_path() -> str:
    """Resolve default DB path with priority: env var > legacy path > new path.

    Default: ~/.kandela/data (legacy: ~/.memory-mcp/data).
    Ensures the same data directory is used regardless of CWD,
    which is critical for zero-install (uvx/npx) execution.
    """
    env = os.environ.get("KANDELA_DB_PATH",
              os.environ.get("MEMORY_MCP_DB_PATH",
                  os.environ.get("MEMORY_DB_PATH")))
    if env:
        return env
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        legacy_xdg = Path(xdg) / "memory-mcp" / "db"
        if legacy_xdg.exists():
            return str(legacy_xdg)
        return str(Path(xdg) / "kandela" / "db")
    # Legacy path (backward compat)
    legacy = Path.home() / ".memory-mcp" / "data"
    if legacy.exists():
        return str(legacy)
    return str(Path.home() / ".kandela" / "data")


DEFAULT_DB_PATH = _default_db_path()

# ── RAG pipeline constants ────────────────────────────────────

# Time-decay rates for time-weighted retrieval (per hour)
# final_distance = distance * (1 + decay_rate) ^ hours_passed
DECAY_RATES: dict[str, float] = {
    "critical": 0.0,   # No decay — always relevant
    "normal": 0.001,   # Gentle decay
    "low": 0.005,      # Stronger decay for auto-saved
}

# MMR (Maximum Marginal Relevance)
DEFAULT_MMR_LAMBDA = 0.7       # 0=full diversity, 1=full relevance
DEFAULT_MMR_LAMBDA_RECALL = 0.5  # auto_recall: more diversity for topic coverage (H-2.4)
DEFAULT_MMR_FETCH_K = 3        # fetch_k multiplier over n_results

# ── Phase 9: Importance-based system ─────────────────────────────

# Importance score range (replaces discrete priority enum)
IMPORTANCE_MIN = 1.0
IMPORTANCE_MAX = 10.0
IMPORTANCE_DEFAULT = 5.0

# Priority → importance conversion (backward compat)
PRIORITY_TO_IMPORTANCE: dict[str, float] = {
    "critical": 9.0,
    "normal": 5.0,
    "low": 2.0,
}

# Importance thresholds (semantic equivalents of old priority levels)
IMPORTANCE_CRITICAL_THRESHOLD = 9.0   # >= this is "critical"
IMPORTANCE_LOW_THRESHOLD = 3.0        # < this is "low"

# Retrieval score weights (alpha + beta + gamma = 1.0)
RETRIEVAL_WEIGHT_RELEVANCE = 0.6     # α — semantic similarity
RETRIEVAL_WEIGHT_IMPORTANCE = 0.25   # β — importance score
RETRIEVAL_WEIGHT_RECENCY = 0.15      # γ — time freshness

# Time-decay: importance→decay_rate mapping
# Linear interpolation: importance=10→decay=0.0, importance=1→decay=MAX
DECAY_RATE_MAX = 0.005   # matches old LOW
DECAY_RATE_MIN = 0.0     # matches old CRITICAL

# Usage-based importance growth
USAGE_GROWTH_FACTOR = 0.5  # multiplier for log(1 + recall*2 + search)

# ── Duplicate detection ─────────────────────────────────────────
DUPLICATE_DISTANCE_THRESHOLD = 0.15  # cosine distance < this = near-duplicate

# ── Cross-project ─────────────────────────────────────────────
GLOBAL_PROJECT_NAME = "_global"  # 모든 프로젝트에 공통 적용되는 글로벌 기억 공간

# Cross-project discovery in auto_recall (semantic search)
CROSS_DISCOVERY_MAX_PROJECTS = 15      # 최대 스캔 프로젝트 수
CROSS_DISCOVERY_MAX_RESULTS = 5        # 추천 결과 최대 개수
CROSS_DISCOVERY_DISTANCE_THRESHOLD = 0.5  # 코사인 거리 임계값

# Cross-project pattern detection (at store time)
CROSS_PROJECT_SIMILARITY_THRESHOLD = 0.20  # dedup보다 완화 (0.15 vs 0.20)
CROSS_PROJECT_MIN_MATCHES = 2             # 2개+ 다른 프로젝트에서 발견 시
CROSS_PROJECT_MAX_SCAN = 20               # 최대 스캔 컬렉션 수

# ── Lazy Retrieval (brief mode) ──────────────────────────────
BRIEF_MAX_CRITICAL = 20              # brief 모드에서 로드할 최대 critical 기억 수
BRIEF_SUMMARY_SNIPPET_LEN = 120      # 최근 세션 요약 스니펫 길이 (문자)
COMPACT_RESULT_CONTENT_LEN = 80      # compact 포맷 1줄 내용 최대 길이 (문자)
COMPACT_RESULT_CONTENT_LEN_CRITICAL = 150  # importance >= 9.0 메모리용 확장 길이
CONTEXT_SEARCH_DEFAULT_N = 3         # context_search 기본 결과 수
CONTEXT_SEARCH_MAX_N = 10            # context_search 최대 결과 수
