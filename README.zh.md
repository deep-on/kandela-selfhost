<p align="center">
  <img src="https://kandela.ai/logo.png" width="80" alt="Kandela">
</p>

<h1 align="center">Kandela Self-Hosted</h1>

<p align="center">
  <b>运行您自己的 AI 编程代理持久化记忆服务器。</b><br>
  单用户模式。完全掌控您的数据。一条命令即可启动。
</p>

<p align="center">
  <a href="README.md">English</a> | <a href="README.ko.md">한국어</a> | <a href="README.ja.md">日本語</a> | <a href="README.de.md">Deutsch</a> | <a href="README.fr.md">Français</a> | <a href="README.es.md">Español</a> | <a href="README.pt.md">Português</a> | <b>中文</b>
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

## 为什么需要它

存储和检索记忆，很多工具都可以做到。
Kandela 解决的是更深层的问题 —— **控制 AI 利用记忆避免重复犯错，并让知识在项目之间自动流动**。

### A/B 基准测试实测结果

HIPAA 医疗数据管道场景（8个会话，14个决策陷阱），对比 Kandela 开启/关闭：

| | Kandela ON | Kandela OFF | 差异 |
|---|:-:|:-:|:-:|
| **陷阱规避率** | **100%** | 11.9% | **+88.1pp** |
| **工作时间** | 77.9分钟 | 86.6分钟 | **-10.1%** |
| **生成代码** | 2,152行 | 3,441行 | **-37.5%** |
| **生成文件** | 40个 | 62个 | **-35.5%** |

> 3次重复（seeds=42,123,456），claude-sonnet-4-6，Groq Llama 3.3 70B（Operator）。

**核心发现：**
- **代码之外的决策才是关键**：审计人员姓名、OOM 事件、数据丢失历史等，仅靠阅读代码无法获取的信息
- **消除不必要的代码**：没有 Kandela 时，已经否决的实现会被重新创建，导致 37.5% 的代码浪费

## 主要功能

- **13个 MCP 工具**：存储、搜索、删除、更新、自动回忆、按需搜索、收件箱、项目管理等
- **混合搜索**：语义 + BM25 关键词搜索（RRF 融合）
- **Importance 引擎**：1-10 自动评分 + 18条基于规则的基础设施标记
- **Lazy Retrieval**：brief 模式（~260 tok）+ `memory_context_search` 按需搜索
- **会话连续性**：环境变化检测（CWD、主机、客户端）+ 基础设施记忆自动包含
- **本地缓存 + Auto-Sync**：Stop Hook JSONL 缓存 → SessionStart 时自动同步到服务器
- **Web 仪表板**：按项目查看记忆、搜索、统计、性能监控
- **一键安装**：`curl ... | bash` 自动安装 Hooks + 斜杠命令
- **Prompt Guard**：防止基于过时记忆做出错误决策
- **Circuit Breaker**：检测重复失败模式 + 自动保存 Gotcha
- **跨项目可见性**：按项目设置 searchable 配置，控制跨项目搜索范围
- **多语言嵌入**：paraphrase-multilingual-MiniLM-L12-v2（50+ 种语言）

## 系统要求

- **Python >= 3.11**
- 首次运行时自动下载嵌入模型（~449MB，需1-5分钟）

## 5分钟快速开始

### 方法一：Docker（推荐）

```bash
git clone https://github.com/deep-on/kandela.git && cd kandela/docker
docker compose up -d
# → http://localhost:8321/dashboard
```

### 方法二：本地安装

```bash
git clone https://github.com/deep-on/kandela.git && cd kandela
pip install -e .
python -m memory_mcp --transport http --port 8321
```

### 客户端连接（Claude Code）

```bash
# 1. 注册 MCP 服务器
claude mcp add memory --transport http http://localhost:8321/mcp

# 2. 自动安装 Hooks + 斜杠命令
curl -sf http://localhost:8321/api/install | bash

# 3. 初始化项目
/kd-init
```

### 客户端连接（Claude Desktop / Cursor）

在 `~/.claude.json` 或 `.mcp.json` 中添加：

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

> 安装后，首次对话时记忆系统将自动激活。

## 认证（可选）

从外部网络访问时，建议使用 API 密钥认证。

```bash
# 在 .env 中配置
KANDELA_API_KEY=your-secret-key
KANDELA_REQUIRE_AUTH=true
```

客户端连接时：
```bash
claude mcp add memory --transport http http://your-server:8321/mcp \
  --header "Authorization: Bearer your-secret-key"
```

> 仅在本地使用时，无需认证即可使用。
> 如需外部访问，请配置 reverse proxy + 认证。

## MCP 工具（13个）

| 工具 | 说明 |
|------|------|
| `memory_store` | 存储记忆（项目、内容、类型、标签、重要度） |
| `memory_search` | 搜索相关记忆（语义、BM25 混合、MMR、过滤器） |
| `memory_context_search` | 对话中压缩搜索（~50 tok/条，按需） |
| `memory_delete` | 删除特定记忆 |
| `memory_update` | 修改记忆（内容/类型/重要度/标签，自动重新计算嵌入） |
| `memory_inbox` | 查看/确认未读备忘录 |
| `memory_auto_recall` | 会话开始时自动加载相关记忆（brief/full 模式） |
| `memory_summarize_session` | 保存当前会话摘要 |
| `memory_list_projects` | 已注册项目列表 |
| `memory_stats` | 按项目的记忆统计 |
| `memory_project_rename` | 重命名项目 |
| `memory_project_delete` | 删除项目（包括所有记忆） |
| `memory_get_guide` | 提供 CLAUDE.md 指南模板 |

## Web 仪表板

访问 `http://localhost:8321/dashboard`。

- 服务器状态、记忆统计、存储使用量
- 按项目的记忆列表及详细查看
- 语义搜索（项目过滤）
- Token 使用量 / ROI 分析
- 性能监控（按 endpoint 的 p50/p95/p99）

## Hooks（Claude Code）

在会话开始/结束时自动管理记忆：

| Hook | 事件 | 动作 |
|------|------|------|
| SessionStart | 会话开始 | 调用 `memory_auto_recall` |
| PreCompact | 上下文压缩前 | 调用 `memory_summarize_session` |
| Stop | 会话结束 | 保存本地 JSONL 缓存 |

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `KANDELA_DB_PATH` | ChromaDB 存储路径 | `~/.memory-mcp/data` |
| `KANDELA_EMBEDDING_MODEL` | 嵌入模型名称 | `paraphrase-multilingual-MiniLM-L12-v2` |
| `KANDELA_PORT` | HTTP 端口 | `8321` |
| `KANDELA_API_KEY` | API 密钥（可选） | - |
| `KANDELA_REQUIRE_AUTH` | 强制 API 密钥认证 | `false` |
| `MCP_LOG_LEVEL` | 日志级别 | `INFO` |
| `HF_HOME` | HuggingFace 模型缓存路径 | `~/.cache/huggingface` |

## 项目结构

```
src/memory_mcp/
├── server.py          # MCP 服务器（13个工具）
├── auth.py            # API 密钥认证中间件
├── dashboard.py       # REST API + Web 仪表板
├── install.py         # 一键安装脚本
├── db/
│   ├── store.py       # MemoryStore 核心 CRUD（ChromaDB）
│   ├── bm25.py        # BM25 分词器 + 索引
│   ├── fusion.py      # RRF 融合（语义 + BM25）
│   └── session_env.py # 会话环境检测
├── importance/        # Importance 引擎（规则 + 评分）
├── templates/         # 指南、Hook 提示词、斜杠命令
└── tools/models.py    # Pydantic 输入模型
```

## 托管服务

除自托管外，我们还提供包含附加功能的托管服务：
- 多用户支持 + 账户管理
- Telegram 机器人集成
- 远程命令（Remote Command）
- 活动热力图
- 分级功能（Pro/Max）

详情请访问：[kandela.ai](https://kandela.ai)

## 开发

```bash
pip install -e ".[dev]"
pytest tests/ -v
ruff check src/
```

## 许可证

- **服务器**：[AGPL-3.0](LICENSE) — Copyright (c) 2025-2026 Deep-ON Inc.
- **客户端**（install.py 生成的 hooks 和斜杠命令）：[MIT](LICENSE-CLIENT)

## 免责声明

本软件按"原样（AS IS）"提供，不提供任何明示或暗示的保证。
用户存储数据的备份由用户本人负责。
