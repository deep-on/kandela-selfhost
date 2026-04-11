"""Reciprocal Rank Fusion (RRF) for merging multiple search result lists.

Combines results from different retrieval methods (e.g., semantic + BM25)
into a single ranked list using the RRF formula:

    score(d) = sum( w_i / (k + rank_i(d)) )  for each ranker i

where k is a smoothing constant (default 60) and w_i are per-ranker weights.

Static RRF (w_i = 1.0): all rankers weighted equally.
Dynamic RRF: weights computed from query specificity (H-2.1).

Reference: Cormack, Clarke, Buettcher (2009) — "Reciprocal Rank Fusion
outperforms Condorcet and individual Rank Learning Methods"
"""

from __future__ import annotations

import math
from typing import Any

# Default RRF smoothing constant
DEFAULT_RRF_K = 60

# Dynamic RRF weight range: λ ∈ [MIN_BM25_W, MAX_BM25_W]
# λ controls BM25 weight; semantic weight = 1 - λ
MIN_BM25_WEIGHT = 0.3
MAX_BM25_WEIGHT = 0.7


def reciprocal_rank_fusion(
    *result_lists: list[dict[str, Any]],
    k: int = DEFAULT_RRF_K,
    n_results: int = 5,
    weights: list[float] | None = None,
) -> list[dict[str, Any]]:
    """Merge multiple ranked result lists using Reciprocal Rank Fusion.

    Each result dict must have an "id" key for deduplication.
    The result with the highest accumulated RRF score wins.

    Args:
        *result_lists: Variable number of ranked result lists.
            Each list should be ordered by relevance (best first).
        k: Smoothing constant (default 60). Higher k = less weight to top ranks.
        n_results: Maximum results to return.
        weights: Per-ranker weights. If None, all rankers weighted equally (1.0).
            Must have same length as result_lists.

    Returns:
        Merged list of dicts sorted by RRF score (descending).
        Each dict has an additional "rrf_score" key.
    """
    if weights is not None and len(weights) != len(result_lists):
        raise ValueError(
            f"weights length ({len(weights)}) must match "
            f"result_lists count ({len(result_lists)})"
        )

    # Accumulate RRF scores per document ID
    scores: dict[str, float] = {}
    items: dict[str, dict[str, Any]] = {}

    for i, result_list in enumerate(result_lists):
        w = weights[i] if weights is not None else 1.0
        for rank, item in enumerate(result_list, start=1):
            doc_id = item["id"]
            rrf_score = w / (k + rank)
            scores[doc_id] = scores.get(doc_id, 0.0) + rrf_score

            # Keep the richest version of the item (prefer one with distance)
            if doc_id not in items:
                items[doc_id] = dict(item)

    # Sort by accumulated RRF score (descending)
    sorted_ids = sorted(scores, key=lambda did: -scores[did])

    results: list[dict[str, Any]] = []
    for doc_id in sorted_ids[:n_results]:
        merged = dict(items[doc_id])
        merged["rrf_score"] = scores[doc_id]
        results.append(merged)

    return results


def compute_query_specificity(bm25_scores: list[float]) -> float:
    """Compute how BM25-discriminative a query is from its score distribution.

    High specificity → BM25 found clear matches → trust BM25 more.
    Low specificity → BM25 scores are flat/zero → trust semantic more.

    Uses the coefficient of variation (CV) of top scores as a proxy for
    how "peaky" the BM25 distribution is. A peaky distribution means the
    query terms are rare/specific and BM25 can discriminate well.

    Args:
        bm25_scores: Raw BM25 scores for all documents in the collection.

    Returns:
        Specificity in [0.0, 1.0].
        0.0 = abstract/common query (no BM25 discrimination)
        1.0 = very specific query (BM25 strongly discriminates)
    """
    if not bm25_scores:
        return 0.5  # neutral default

    max_score = max(bm25_scores)
    if max_score <= 0:
        return 0.0  # no BM25 matches at all

    # Compute mean and std of positive scores
    positive = [s for s in bm25_scores if s > 0]
    if len(positive) < 2:
        return 0.8  # very few matches = highly specific

    mean_s = sum(positive) / len(positive)
    variance = sum((s - mean_s) ** 2 for s in positive) / len(positive)
    std_s = math.sqrt(variance)

    # Coefficient of variation: std/mean
    # High CV = scores are spread out = BM25 discriminates well
    cv = std_s / mean_s if mean_s > 0 else 0.0

    # Also consider the fraction of docs with any BM25 match
    # Fewer matching docs = more specific query
    match_ratio = len(positive) / len(bm25_scores) if bm25_scores else 1.0
    selectivity = 1.0 - min(1.0, match_ratio * 2)  # 50%+ match → 0

    # Combine CV and selectivity (both in ~[0, 3] range for CV)
    # Normalize CV to [0, 1]: CV=0 → 0, CV≥2 → 1
    cv_norm = min(1.0, cv / 2.0)

    # Weighted combination: 60% CV + 40% selectivity
    specificity = 0.6 * cv_norm + 0.4 * selectivity

    return max(0.0, min(1.0, specificity))


def compute_dynamic_weights(specificity: float) -> list[float]:
    """Convert query specificity to [semantic_weight, bm25_weight].

    Args:
        specificity: Query specificity in [0.0, 1.0].

    Returns:
        [semantic_weight, bm25_weight] where both are in [0.3, 0.7].
    """
    # λ = BM25 weight, linearly interpolated from specificity
    bm25_w = MIN_BM25_WEIGHT + (MAX_BM25_WEIGHT - MIN_BM25_WEIGHT) * specificity
    semantic_w = 1.0 - bm25_w
    return [semantic_w, bm25_w]
