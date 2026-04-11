# Changelog

All notable changes to Kandela will be documented in this file.

## [0.5.0-beta] - 2026-04-03

### Added
- **Remote Command Level 2**: PIN SHA-256, high-risk confirmation, result display by risk level, daily soft limit, error recovery UX, dashboard remote section
- **Project Picker**: `/do` inline keyboard for project selection
- **Remote Recall**: Auto-inject remote command results into session start
- **Existing Session Notification**: Prompt-guard-hook notifies about completed remote commands
- **Caffeinate**: macOS sleep prevention for worker

### Changed
- License: Proprietary → AGPL-3.0 (server) + MIT (client)
- DEFAULT_SERVER_URL → api.kandela.ai
- result_summary limit: 80 → 1500 chars
- Documentation reorganized into docs/ subdirectories

### Security
- Infrastructure references anonymized for public repo
- docker/.env excluded from version control
- .publishignore for public repo sanitization

## [0.4.0] - 2026-03-23

### Added
- Core features: ChromaDB + sentence-transformers, 13 MCP tools
- Hybrid Search (Semantic + BM25 + RRF)
- Importance Engine (25 rules + ACT-R decay)
- Prompt Guard + Circuit Breaker
- Telegram bot with LLM intent classification
- Web dashboard with real-time metrics
- Multi-user mode with API key authentication
- Local cache + auto-sync
- 962 tests passing
