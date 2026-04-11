# =============================================================================
# Multi-stage production Dockerfile for kandela-server
# Target: mcp (default)
# Usage:
#   docker compose -f docker/compose.yaml build kandela
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: shared dependency layer
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS deps

WORKDIR /app

# Build deps for native extensions (sentence-transformers, chromadb)
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ gosu && \
    rm -rf /var/lib/apt/lists/*

# Install torch CPU-only FIRST — prevents pip from pulling CUDA torch (~1.8GB).
# CPU-only is ~200MB. Pattern from Dockerfile.bench (line 18-19).
RUN pip install --no-cache-dir \
    torch --index-url https://download.pytorch.org/whl/cpu

# Copy only build metadata for dep resolution (src changes won't bust this cache).
# Stub trick from Dockerfile.dev (line 13-14).
COPY pyproject.toml README.md ./
RUN mkdir -p src/memory_mcp && \
    echo '__version__ = "0.1.0"' > src/memory_mcp/__init__.py && \
    pip install --no-cache-dir . && \
    pip uninstall -y kandela-server && \
    rm -rf src/

# Pre-download the embedding model into a fixed cache path (not /root/.cache)
ENV HF_HOME=/app/.cache/huggingface
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')"

# ---------------------------------------------------------------------------
# Stage 2: MCP server
# ---------------------------------------------------------------------------
FROM deps AS mcp

RUN groupadd -r memuser && useradd -r -g memuser memuser

COPY src/ src/
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN pip install --no-cache-dir --no-deps . && \
    chown -R memuser:memuser /app && \
    chmod +x /usr/local/bin/entrypoint.sh

ENV HF_HOME=/app/.cache/huggingface

VOLUME /data/memory_db
EXPOSE 8321
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python", "-m", "memory_mcp", "--db-path", "/data/memory_db"]
