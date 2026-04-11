"""Importance scoring functions.

All functions are pure (no side effects, no DB access).
Used by store.py for retrieval score computation and decay rate calculation.
"""

from __future__ import annotations

import math

from memory_mcp.constants import (
    DECAY_RATE_MAX,
    IMPORTANCE_MAX,
    IMPORTANCE_MIN,
    RETRIEVAL_WEIGHT_IMPORTANCE,
    RETRIEVAL_WEIGHT_RECENCY,
    RETRIEVAL_WEIGHT_RELEVANCE,
    USAGE_GROWTH_FACTOR,
)


def importance_to_decay_rate(importance: float) -> float:
    """Convert importance score to time-decay rate.

    Higher importance = lower decay (more persistent).
      importance=10.0 → decay=0.0    (no decay, like old CRITICAL)
      importance=5.5  → decay≈0.0025 (moderate)
      importance=1.0  → decay=0.005  (max decay, like old LOW)

    Linear interpolation between DECAY_RATE_MAX and 0.
    """
    # Normalize importance to [0, 1]
    t = (importance - IMPORTANCE_MIN) / (IMPORTANCE_MAX - IMPORTANCE_MIN)
    t = max(0.0, min(1.0, t))
    # Invert: high importance → low decay
    return DECAY_RATE_MAX * (1.0 - t)


def compute_usage_bonus(
    recall_count: int,
    search_count: int,
    growth_factor: float = USAGE_GROWTH_FACTOR,
) -> float:
    """Compute usage-based importance bonus.

    Formula: log(1 + recall_count * 2 + search_count) * growth_factor

    Recall is weighted 2x because auto_recall at session start indicates
    the memory was actively loaded into working context.

    Returns:
        Non-negative bonus value.
    """
    raw = math.log(1 + recall_count * 2 + search_count)
    return raw * growth_factor


def compute_effective_importance(
    stored_importance: float,
    recall_count: int = 0,
    search_count: int = 0,
) -> float:
    """Compute effective importance including usage bonus.

    Returns value clamped to [IMPORTANCE_MIN, IMPORTANCE_MAX].
    """
    bonus = compute_usage_bonus(recall_count, search_count)
    effective = stored_importance + bonus
    return max(IMPORTANCE_MIN, min(IMPORTANCE_MAX, effective))


def compute_retrieval_score(
    relevance: float,
    normalized_importance: float,
    recency: float,
    *,
    alpha: float = RETRIEVAL_WEIGHT_RELEVANCE,
    beta: float = RETRIEVAL_WEIGHT_IMPORTANCE,
    gamma: float = RETRIEVAL_WEIGHT_RECENCY,
) -> float:
    """Compute composite retrieval score.

    All inputs should be in [0, 1] range:
      relevance = 1.0 - cosine_distance
      normalized_importance = (effective_importance - 1.0) / 9.0
      recency = exp(-decay_rate * hours)

    Higher score = better match.

    Args:
        relevance: Semantic relevance (0-1, higher is better).
        normalized_importance: Importance normalized to 0-1.
        recency: Time recency factor (0-1, 1=brand new).
        alpha: Weight for relevance (default 0.6).
        beta: Weight for importance (default 0.25).
        gamma: Weight for recency (default 0.15).

    Returns:
        Composite score in approximately [0, 1].
    """
    return alpha * relevance + beta * normalized_importance + gamma * recency
