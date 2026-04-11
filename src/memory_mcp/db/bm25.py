"""Application-level BM25 index for keyword search.

ChromaDB PersistentClient does not support sparse (BM25) indexing natively
(Cloud-only feature), so we implement BM25 at the application level using
rank-bm25 (BM25Okapi).

Language support:
  - Korean  : kiwipiepy morphological analysis (H-2.3)
  - Japanese: fugashi + unidic-lite (optional; falls back to regex)
  - Chinese : jieba (optional; falls back to regex)
  - Others  : regex tokenizer (English, German, Spanish, etc.)

Usage:
    index = MemoryBM25Index(documents, doc_ids, metadatas)
    results = index.search("query text", n_results=5)
"""

from __future__ import annotations

import logging
import re
from typing import Any

from rank_bm25 import BM25Okapi

logger = logging.getLogger(__name__)

# ── Script detection regexes ─────────────────────────────────────
_KO_RE = re.compile(r"[\uAC00-\uD7AF]")          # Hangul syllables
_JP_RE = re.compile(r"[\u3040-\u309F\u30A0-\u30FF]")  # Hiragana / Katakana
_ZH_RE = re.compile(r"[\u4E00-\u9FFF]")           # CJK Unified Ideographs


def _detect_script(text: str) -> str:
    """Return the dominant script: 'ko', 'ja', 'zh', or 'other'."""
    if _KO_RE.search(text):
        return "ko"
    if _JP_RE.search(text):
        return "ja"
    if _ZH_RE.search(text):
        return "zh"
    return "other"


# ── Regex-based tokenizer (fallback) ─────────────────────────────
# Token pattern: Korean/CJK runs and alphanumeric/underscore tokens
_TOKEN_RE = re.compile(r"[가-힣\u3040-\u30FF\u4E00-\u9FFF]+|[a-zA-Z0-9_]+")

# Pattern for underscore-joined identifiers (e.g., ZYNQ_CLK_PIN)
_IDENT_RE = re.compile(r"[a-zA-Z0-9_]+")

# ── Kiwi morphological analyzer — Korean (lazy singleton) ─────────
_kiwi: Any = None
_kiwi_init_done = False

# Content-bearing POS tags to keep for BM25 (Sejong tagset)
_KIWI_CONTENT_TAGS = frozenset({
    "NNG", "NNP", "NNB", "NR",  # common/proper/bound nouns, numerals
    "VV", "VA",                   # verb/adjective stems
    "MAG",                        # general adverbs
    "XR",                         # roots
    "SL",                         # foreign language (English)
    "SN",                         # numbers
    "SH",                         # Chinese characters
})


def _get_kiwi() -> Any:
    """Lazy-initialize the Kiwi morphological analyzer (singleton).

    Returns None if kiwipiepy is not installed.
    """
    global _kiwi, _kiwi_init_done
    if not _kiwi_init_done:
        _kiwi_init_done = True
        try:
            from kiwipiepy import Kiwi

            _kiwi = Kiwi()
            logger.info("Kiwi morphological analyzer loaded (H-2.3)")
        except ImportError:
            logger.debug("kiwipiepy not installed — using regex tokenizer")
    return _kiwi


def kiwi_available() -> bool:
    """Check if kiwi morphological analyzer is available."""
    return _get_kiwi() is not None


# ── Fugashi morphological analyzer — Japanese (lazy singleton) ────
_fugashi_tagger: Any = None
_fugashi_init_done = False


def _get_fugashi() -> Any:
    """Lazy-initialize fugashi tagger. Returns None if not installed."""
    global _fugashi_tagger, _fugashi_init_done
    if not _fugashi_init_done:
        _fugashi_init_done = True
        try:
            import fugashi  # noqa: PLC0415
            _fugashi_tagger = fugashi.Tagger()
            logger.info("fugashi tagger loaded (Japanese BM25)")
        except ImportError:
            logger.debug("fugashi not installed — Japanese will use regex tokenizer")
    return _fugashi_tagger


def _tokenize_fugashi(text: str) -> list[str]:
    """Tokenize Japanese text using fugashi (MeCab wrapper).

    Extracts surface forms of content words; also preserves ASCII identifiers.
    """
    tagger = _get_fugashi()
    tokens: list[str] = []
    for word in tagger(text):
        surface = word.surface.strip()
        if surface:
            tokens.append(surface.lower())
    # Also capture ASCII identifiers/numbers
    for match in _IDENT_RE.findall(text):
        lower = match.lower()
        if lower and lower not in tokens:
            tokens.append(lower)
    return tokens


# ── Jieba tokenizer — Chinese (lazy init) ─────────────────────────
_jieba_loaded = False


def _get_jieba() -> Any:
    """Lazy-import jieba. Returns module or None if not installed."""
    global _jieba_loaded
    try:
        import jieba  # noqa: PLC0415
        if not _jieba_loaded:
            jieba.setLogLevel(logging.WARNING)
            _jieba_loaded = True
            logger.info("jieba loaded (Chinese BM25)")
        return jieba
    except ImportError:
        logger.debug("jieba not installed — Chinese will use regex tokenizer")
        return None


def _tokenize_jieba(text: str) -> list[str]:
    """Tokenize Chinese text using jieba word segmentation."""
    jieba = _get_jieba()
    tokens: list[str] = []
    for seg in jieba.cut(text):
        seg = seg.strip()
        if seg:
            tokens.append(seg.lower())
    # Also capture ASCII identifiers
    for match in _IDENT_RE.findall(text):
        lower = match.lower()
        if lower and lower not in tokens:
            tokens.append(lower)
    return tokens


def _tokenize_regex(text: str) -> list[str]:
    """Tokenize using regex only (fallback when kiwi is unavailable).

    Extracts Korean syllable runs and alphanumeric/underscore tokens.
    All tokens are lowercased for case-insensitive matching.

    Examples:
        >>> _tokenize_regex("ZYNQ_CLK_PIN은 L16에 할당")
        ['zynq_clk_pin', '은', 'l16', '에', '할당']
    """
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def _tokenize_kiwi(text: str) -> list[str]:
    """Tokenize using kiwi morphological analyzer.

    Extracts content-bearing morphemes (nouns, verb stems, etc.)
    and preserves underscore-joined identifiers.

    Examples:
        >>> _tokenize_kiwi("서버 설정을 변경했다")
        ['서버', '설정', '변경']  # particles/endings removed
        >>> _tokenize_kiwi("ZYNQ_CLK_PIN은 L16에 할당")
        ['zynq', 'clk', 'pin', 'l16', '할당', 'zynq_clk_pin']
    """
    assert _kiwi is not None
    tokens: list[str] = []

    # 1. Kiwi morpheme analysis — keep content words only
    for tok in _kiwi.tokenize(text):
        if tok.tag in _KIWI_CONTENT_TAGS:
            form = tok.form.lower()
            if form:
                tokens.append(form)

    # 2. Preserve compound identifiers that kiwi may have split:
    #    - underscore-joined: ZYNQ_CLK_PIN → zynq_clk_pin
    #    - mixed alpha-numeric: L16, M16, A3 → l16, m16, a3
    for match in _IDENT_RE.findall(text):
        lower = match.lower()
        if "_" in lower:
            tokens.append(lower)
        elif len(lower) > 1:
            has_alpha = any(c.isalpha() for c in lower)
            has_digit = any(c.isdigit() for c in lower)
            if has_alpha and has_digit:
                tokens.append(lower)

    return tokens


def tokenize(text: str) -> list[str]:
    """Tokenize text for BM25 indexing.

    Dispatches to the appropriate morphological analyzer based on detected script:
    - Korean  → kiwipiepy (if installed)
    - Japanese → fugashi (if installed)
    - Chinese → jieba (if installed)
    - Others  → regex (always available)

    All tokens are lowercased for case-insensitive matching.
    """
    script = _detect_script(text)

    if script == "ko":
        kiwi = _get_kiwi()
        if kiwi is not None:
            return _tokenize_kiwi(text)
    elif script == "ja":
        if _get_fugashi() is not None:
            return _tokenize_fugashi(text)
    elif script == "zh":
        if _get_jieba() is not None:
            return _tokenize_jieba(text)

    return _tokenize_regex(text)


class MemoryBM25Index:
    """In-memory BM25 index built from a list of documents.

    Designed for small-to-medium collections (hundreds to low thousands).
    Index is rebuilt from scratch each time — no persistence needed since
    ChromaDB is the source of truth.
    """

    def __init__(
        self,
        documents: list[str],
        doc_ids: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        """Build BM25 index from documents.

        Args:
            documents: List of document texts.
            doc_ids: Corresponding document IDs (same order).
            metadatas: Corresponding metadata dicts (same order).
        """
        if len(documents) != len(doc_ids) or len(documents) != len(metadatas):
            raise ValueError("documents, doc_ids, and metadatas must have same length")

        self._doc_ids = doc_ids
        self._documents = documents
        self._metadatas = metadatas

        # Tokenize all documents
        tokenized = [tokenize(doc) for doc in documents]
        self._bm25 = BM25Okapi(tokenized)
        logger.debug("Built BM25 index with %d documents", len(documents))

    def search(self, query: str, n_results: int = 5) -> list[dict[str, Any]]:
        """Search the BM25 index.

        Args:
            query: Search query text.
            n_results: Maximum number of results to return.

        Returns:
            List of dicts with keys: id, content, metadata, score.
            Sorted by BM25 score descending (highest relevance first).
        """
        tokens = tokenize(query)
        if not tokens:
            return []

        scores = self._bm25.get_scores(tokens)

        # Get top-N indices by score (descending)
        n = min(n_results, len(self._documents))
        # argsort returns ascending, so negate scores
        top_indices = sorted(range(len(scores)), key=lambda i: -scores[i])[:n]

        results: list[dict[str, Any]] = []
        for idx in top_indices:
            if scores[idx] <= 0:
                break  # No more relevant results
            results.append({
                "id": self._doc_ids[idx],
                "content": self._documents[idx],
                "metadata": self._metadatas[idx],
                "score": float(scores[idx]),
            })

        return results

    def get_raw_scores(self, query: str) -> list[float]:
        """Get raw BM25 scores for all documents (for specificity computation).

        Args:
            query: Search query text.

        Returns:
            List of BM25 scores, one per document in corpus order.
            Empty list if query has no tokens.
        """
        tokens = tokenize(query)
        if not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        return [float(s) for s in scores]

    @property
    def corpus_size(self) -> int:
        """Number of documents in the index."""
        return len(self._documents)
