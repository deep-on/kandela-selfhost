<p align="center">
  <img src="https://kandela.ai/logo.png" width="80" alt="Kandela">
</p>

<h1 align="center">Kandela Self-Hosted</h1>

<p align="center">
  <b>Run your own persistent memory server for AI coding agents.</b><br>
  Single-user mode. Full control over your data. One command to start.
</p>

<p align="center">
  <b>English</b> | <a href="README.ko.md">한국어</a> | <a href="README.ja.md">日本語</a> | <a href="README.de.md">Deutsch</a> | <a href="README.fr.md">Français</a> | <a href="README.es.md">Español</a> | <a href="README.pt.md">Português</a> | <a href="README.zh.md">中文</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-0.5.0-blue" alt="Version">
  <img src="https://img.shields.io/badge/license-AGPL--3.0-blue" alt="License">
  <img src="https://img.shields.io/badge/python-3.11+-green" alt="Python">
  <img src="https://img.shields.io/badge/docker-compose-blue" alt="Docker">
  <img src="https://img.shields.io/badge/MCP_tools-13-brightgreen" alt="MCP Tools">
  <img src="https://img.shields.io/badge/ChromaDB-vector_store-orange" alt="ChromaDB">
  <img src="https://img.shields.io/badge/embeddings-50+_languages-purple" alt="Multilingual">
</p>

---

## Why Kandela

76% of developers using AI coding tools repeat the same explanations every session.
Kandela gives your AI persistent memory — so it remembers past decisions, environments, and failure experiences across sessions.

## Key Features

- **13 MCP Tools** — Store, search, delete, update, auto-recall, on-demand search, inbox, project management
- **Hybrid Search** — Semantic + BM25 keyword search (RRF fusion)
- **Importance Engine** — Auto-scoring 1~10 + 18 rule-based infra tagging
- **Lazy Retrieval** — Brief mode (~260 tokens) + `memory_context_search` on-demand
- **Session Continuity** — Detects environment changes (CWD, host, client) + auto-includes infra memories
- **Local Cache + Auto-Sync** — Stop Hook JSONL cache, auto-synced on SessionStart
- **Web Dashboard** — Per-project memory browser, search, stats, performance monitoring
- **One-click Install** — Auto-installs hooks + slash commands via `curl ... | bash`
- **Prompt Guard** — Surfaces stored decisions when prompts conflict with them
- **Circuit Breaker** — Surfaces past failures when similar patterns recur
- **Cross-project Visibility** — Per-project searchable settings for cross-search scope control
- **Multilingual Embeddings** — paraphrase-multilingual-MiniLM-L12-v2 (50+ languages)

## Requirements

- **Python >= 3.11**
- Embedding model auto-downloads on first run (~449MB, 1~5 min)

## Quick Start

### Option 1: Docker (Recommended)

```bash
git clone https://github.com/deep-on/kandela-selfhost.git && cd kandela-selfhost/docker
docker compose up -d
# → http://localhost:8321/dashboard
```

### Option 2: Local Install

```bash
git clone https://github.com/deep-on/kandela-selfhost.git && cd kandela-selfhost
pip install -e .
python -m memory_mcp --transport http --port 8321
```

### Connect Claude Code

```bash
# 1. Register MCP server
claude mcp add memory --transport http http://localhost:8321/mcp

# 2. Auto-install hooks + slash commands
curl -sf http://localhost:8321/api/install | bash

# 3. Initialize project
/kd-init
```

### Connect Claude Desktop / Cursor

Add to `~/.claude.json` or `.mcp.json`:

```json
{
  "mcpServers": {
    "memory": {
      "type": "http",
      "url": "http://localhost:8321/mcp"
    }
  }
}
```

> The memory system activates automatically on your first conversation after setup.

## Authentication (Optional)

For external network access, API key authentication is recommended.

```bash
# Set in .env
KANDELA_API_KEY=your-secret-key
KANDELA_REQUIRE_AUTH=true
```

When connecting clients:
```bash
claude mcp add memory --transport http http://your-server:8321/mcp \
  --header "Authorization: Bearer your-secret-key"
```

> No authentication needed for localhost-only usage.
> For external access, set up a reverse proxy with authentication.

## MCP Tools (13)

| Tool | Description |
|------|-------------|
| `memory_store` | Store a memory (project, content, type, tags, importance) |
| `memory_search` | Search memories (semantic, BM25 hybrid, MMR, filters) |
| `memory_context_search` | Compact mid-conversation search (~50 tok/result, on-demand) |
| `memory_delete` | Delete a specific memory |
| `memory_update` | Update a memory (content/type/importance/tags, auto re-embeds) |
| `memory_inbox` | View/acknowledge unread memos |
| `memory_auto_recall` | Auto-load relevant memories on session start (brief/full mode) |
| `memory_summarize_session` | Store current session summary |
| `memory_list_projects` | List registered projects |
| `memory_stats` | Per-project memory statistics |
| `memory_project_rename` | Rename a project |
| `memory_project_delete` | Delete a project (including all memories) |
| `memory_get_guide` | Provide CLAUDE.md guide template |

## Web Dashboard

Available at `http://localhost:8321/dashboard`.

- Server status, memory stats, storage usage
- Per-project memory list and detail view
- Semantic search with project filter
- Token usage / ROI analysis
- Performance monitoring (per-endpoint p50/p95/p99)

## Hooks (Claude Code)

Automatically manages memories on session start/end:

| Hook | Event | Action |
|------|-------|--------|
| SessionStart | Session begins | Calls `memory_auto_recall` |
| PreCompact | Before context compaction | Calls `memory_summarize_session` |
| Stop | Session ends | Saves to local JSONL cache |

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `KANDELA_DB_PATH` | ChromaDB storage path | `~/.memory-mcp/data` |
| `KANDELA_EMBEDDING_MODEL` | Embedding model name | `paraphrase-multilingual-MiniLM-L12-v2` |
| `KANDELA_PORT` | HTTP port | `8321` |
| `KANDELA_API_KEY` | API key (optional) | — |
| `KANDELA_REQUIRE_AUTH` | Enforce API key auth | `false` |
| `MCP_LOG_LEVEL` | Log level | `INFO` |
| `HF_HOME` | HuggingFace model cache path | `~/.cache/huggingface` |

## Project Structure

```
src/memory_mcp/
├── server.py          # MCP server (13 tools)
├── auth.py            # API key auth middleware
├── dashboard.py       # REST API + web dashboard
├── install.py         # One-click install script
├── db/
│   ├── store.py       # MemoryStore core CRUD (ChromaDB)
│   ├── bm25.py        # BM25 tokenizer + index
│   ├── fusion.py      # RRF fusion (semantic + BM25)
│   └── session_env.py # Session environment detection
├── importance/        # Importance engine (rules + scoring)
├── templates/         # Guide, hook prompts, slash commands
└── tools/models.py    # Pydantic input models
```

## Hosted Service

A hosted service with additional features is also available:
- Multi-user support + account management
- Telegram bot integration
- Remote commands
- Activity heatmap
- Tier features (Pro/Max)

Learn more: [kandela.ai](https://kandela.ai)

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v
ruff check src/
```

## License

- **Server**: [AGPL-3.0](LICENSE) — Copyright (c) 2025-2026 Deep-ON Inc.
- **Client-side files** (hooks, slash commands generated by install.py): [MIT](LICENSE-CLIENT)

## Disclaimer

This software is provided "AS IS" without warranty of any kind.
Users are responsible for backing up their own data.
