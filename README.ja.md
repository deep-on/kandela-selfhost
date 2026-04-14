<p align="center">
  <img src="https://kandela.ai/logo.png" width="80" alt="Kandela">
</p>

<h1 align="center">Kandela Self-Hosted</h1>

<p align="center">
  <b>AIコーディングエージェントのための永続メモリサーバーを自分で運用。</b><br>
  シングルユーザーモード。データの完全な管理。ワンコマンドで起動。
</p>

<p align="center">
  <a href="README.md">English</a> | <a href="README.ko.md">한국어</a> | <b>日本語</b> | <a href="README.de.md">Deutsch</a> | <a href="README.fr.md">Français</a> | <a href="README.es.md">Español</a> | <a href="README.pt.md">Português</a> | <a href="README.zh.md">中文</a>
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

## なぜ必要なのか

記憶を保存・検索する機能は他にもあります。
Kandelaが解決するのはその先の課題 — **AIが記憶を活用して同じミスを繰り返さないよう制御し、プロジェクト間で知識が自動的に流れる**ようにすることです。

### A/Bベンチマーク実測結果

HIPAA医療データパイプラインシナリオ（8セッション、14の意思決定トラップ）をKandela ON/OFFで比較：

| | Kandela ON | Kandela OFF | 差分 |
|---|:-:|:-:|:-:|
| **トラップ回避率** | **100%** | 11.9% | **+88.1pp** |
| **作業時間** | 77.9分 | 86.6分 | **-10.1%** |
| **生成コード** | 2,152行 | 3,441行 | **-37.5%** |
| **生成ファイル** | 40個 | 62個 | **-35.5%** |

> 3回繰り返し(seeds=42,123,456)、claude-sonnet-4-6、Groq Llama 3.3 70B (Operator)。

**主な知見：**
- **コードにない決定が鍵**：監査担当者名、OOM事象、データ損失履歴など、コードを読んでもわからない情報を記憶
- **不要コードの排除**：Kandelaなしでは既に却下された実装を再作成し、37.5%のコード無駄が発生

## 主要機能

- **13のMCPツール**：保存、検索、削除、更新、自動想起、オンデマンド検索、Inbox、プロジェクト管理など
- **ハイブリッド検索**：セマンティック + BM25キーワード検索（RRFフュージョン）
- **Importanceエンジン**：1〜10自動スコア + 18ルールベースのインフラタギング
- **Lazy Retrieval**：briefモード（~260 tok） + `memory_context_search` オンデマンド検索
- **セッション継続性**：環境変化検知（CWD、ホスト、クライアント） + インフラメモリ自動包含
- **ローカルキャッシュ + Auto-Sync**：Stop Hook JSONLキャッシュ → SessionStart時にサーバー自動同期
- **Webダッシュボード**：プロジェクト別メモリ閲覧、検索、統計、パフォーマンス監視
- **ワンクリックインストール**：`curl ... | bash`でHooks + スラッシュコマンド自動インストール
- **Prompt Guard**：古い記憶に基づく誤った判断を防止
- **Circuit Breaker**：繰り返し失敗パターン検知 + 自動Gotcha保存
- **クロスプロジェクト可視性**：プロジェクト別searchable設定でクロス検索範囲を制御
- **多言語埋め込み**：paraphrase-multilingual-MiniLM-L12-v2（50+言語）

## 要件

- **Python >= 3.11**
- 初回起動時に埋め込みモデルを自動ダウンロード（~449MB、1〜5分所要）

## 5分クイックスタート

### 方法1：Docker（推奨）

```bash
git clone https://github.com/deep-on/kandela.git && cd kandela/docker
docker compose up -d
# → http://localhost:8321/dashboard
```

### 方法2：ローカルインストール

```bash
git clone https://github.com/deep-on/kandela.git && cd kandela
pip install -e .
python -m memory_mcp --transport http --port 8321
```

### クライアント接続（Claude Code）

```bash
# 1. MCPサーバー登録
claude mcp add memory --transport http http://localhost:8321/mcp

# 2. Hooks + スラッシュコマンド自動インストール
curl -sf http://localhost:8321/api/install | bash

# 3. プロジェクト初期化
/kd-init
```

### クライアント接続（Claude Desktop / Cursor）

`~/.claude.json` または `.mcp.json` に追加：

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

> インストール後、最初の会話で自動的にメモリシステムが有効化されます。

## 認証（オプション）

外部ネットワークからアクセスする場合、APIキー認証を推奨します。

```bash
# .envに設定
KANDELA_API_KEY=your-secret-key
KANDELA_REQUIRE_AUTH=true
```

クライアント接続時：
```bash
claude mcp add memory --transport http http://your-server:8321/mcp \
  --header "Authorization: Bearer your-secret-key"
```

> ローカルのみで使用する場合、認証なしで利用可能です。
> 外部からアクセスする場合はreverse proxy + 認証を設定してください。

## MCPツール（13個）

| ツール | 説明 |
|--------|------|
| `memory_store` | 記憶を保存（プロジェクト、内容、タイプ、タグ、重要度） |
| `memory_search` | 関連記憶を検索（セマンティック、BM25ハイブリッド、MMR、フィルター） |
| `memory_context_search` | 会話中の圧縮検索（~50 tok/件、オンデマンド） |
| `memory_delete` | 特定の記憶を削除 |
| `memory_update` | 記憶を更新（内容/タイプ/重要度/タグ、埋め込み自動再計算） |
| `memory_inbox` | 未確認メモの閲覧/確認処理 |
| `memory_auto_recall` | セッション開始時に関連記憶を自動ロード（brief/fullモード） |
| `memory_summarize_session` | 現在のセッション要約を保存 |
| `memory_list_projects` | 登録済みプロジェクト一覧 |
| `memory_stats` | プロジェクト別メモリ統計 |
| `memory_project_rename` | プロジェクト名の変更 |
| `memory_project_delete` | プロジェクトの削除（全記憶を含む） |
| `memory_get_guide` | CLAUDE.mdガイドテンプレートを提供 |

## Webダッシュボード

`http://localhost:8321/dashboard` でアクセス可能。

- サーバーステータス、メモリ統計、ストレージ使用量
- プロジェクト別メモリ一覧と詳細表示
- セマンティック検索（プロジェクトフィルター）
- トークン使用量 / ROI分析
- パフォーマンス監視（エンドポイント別 p50/p95/p99）

## Hooks（Claude Code）

セッションの開始/終了時に自動でメモリを管理します：

| Hook | イベント | 動作 |
|------|----------|------|
| SessionStart | セッション開始 | `memory_auto_recall` を呼び出し |
| PreCompact | コンテキスト圧縮前 | `memory_summarize_session` を呼び出し |
| Stop | セッション終了 | ローカルJSONLキャッシュを保存 |

## 環境変数

| 変数 | 説明 | デフォルト |
|------|------|------------|
| `KANDELA_DB_PATH` | ChromaDB保存パス | `~/.memory-mcp/data` |
| `KANDELA_EMBEDDING_MODEL` | 埋め込みモデル名 | `paraphrase-multilingual-MiniLM-L12-v2` |
| `KANDELA_PORT` | HTTPポート | `8321` |
| `KANDELA_API_KEY` | APIキー（オプション） | - |
| `KANDELA_REQUIRE_AUTH` | APIキー認証を強制 | `false` |
| `MCP_LOG_LEVEL` | ログレベル | `INFO` |
| `HF_HOME` | HuggingFaceモデルキャッシュパス | `~/.cache/huggingface` |

## プロジェクト構造

```
src/memory_mcp/
├── server.py          # MCPサーバー（13ツール）
├── auth.py            # APIキー認証ミドルウェア
├── dashboard.py       # REST API + Webダッシュボード
├── install.py         # ワンクリックインストールスクリプト
├── db/
│   ├── store.py       # MemoryStoreコアCRUD（ChromaDB）
│   ├── bm25.py        # BM25トークナイザー + インデックス
│   ├── fusion.py      # RRFフュージョン（セマンティック + BM25）
│   └── session_env.py # セッション環境検知
├── importance/        # Importanceエンジン（ルール + スコア）
├── templates/         # ガイド、フックプロンプト、スラッシュコマンド
└── tools/models.py    # Pydantic入力モデル
```

## ホスティングサービス

セルフホスティング以外に、追加機能を備えたホスティングサービスも提供しています：
- マルチユーザー対応 + アカウント管理
- Telegramボット連携
- リモートコマンド（Remote Command）
- アクティビティヒートマップ
- ティア別機能（Pro/Max）

詳細：[kandela.ai](https://kandela.ai)

## 開発

```bash
pip install -e ".[dev]"
pytest tests/ -v
ruff check src/
```

## ライセンス

- **サーバー**：[AGPL-3.0](LICENSE) — Copyright (c) 2025-2026 Deep-ON Inc.
- **クライアント**（install.pyが生成するhooks、slash commands）：[MIT](LICENSE-CLIENT)

## 免責事項

本ソフトウェアは「現状のまま（AS IS）」提供され、明示的または黙示的な保証なく使用されます。
ユーザーが保存したデータのバックアップはユーザー自身の責任です。
