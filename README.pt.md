<p align="center">
  <img src="https://kandela.ai/logo.png" width="80" alt="Kandela">
</p>

<h1 align="center">Kandela Self-Hosted</h1>

<p align="center">
  <b>Execute seu proprio servidor de memoria persistente para agentes de codificacao IA.</b><br>
  Modo de usuario unico. Controle total dos seus dados. Um unico comando para iniciar.
</p>

<p align="center">
  <a href="README.md">English</a> | <a href="README.ko.md">한국어</a> | <a href="README.ja.md">日本語</a> | <a href="README.de.md">Deutsch</a> | <a href="README.fr.md">Français</a> | <a href="README.es.md">Español</a> | <b>Português</b> | <a href="README.zh.md">中文</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/license-AGPL--3.0-blue" alt="License">
  <img src="https://img.shields.io/badge/python-3.11+-green" alt="Python">
  <img src="https://img.shields.io/badge/docker-compose-blue" alt="Docker">
  <img src="https://img.shields.io/badge/MCP_tools-13-brightgreen" alt="MCP Tools">
  <img src="https://img.shields.io/badge/ChromaDB-vector_store-orange" alt="ChromaDB">
  <img src="https://img.shields.io/badge/embeddings-50+_languages-purple" alt="Multilingual">
</p>

---

## Por que e necessario

Armazenar e buscar memorias e algo que muitas ferramentas fazem.
O problema que o Kandela resolve vai alem — **controlar para que a IA nao repita erros apesar de ter memorias, e fazer o conhecimento fluir automaticamente entre projetos**.

### Resultados do benchmark A/B

Cenario de pipeline de dados medicos HIPAA (8 sessoes, 14 armadilhas de decisao) comparando Kandela ON/OFF:

| | Kandela ON | Kandela OFF | Diferenca |
|---|:-:|:-:|:-:|
| **Taxa de evasao de armadilhas** | **100%** | 11.9% | **+88.1pp** |
| **Tempo de trabalho** | 77.9 min | 86.6 min | **-10.1%** |
| **Codigo gerado** | 2.152 linhas | 3.441 linhas | **-37.5%** |
| **Arquivos gerados** | 40 | 62 | **-35.5%** |

> 3 repeticoes (seeds=42,123,456), claude-sonnet-4-6, Groq Llama 3.3 70B (Operator).

**Principais descobertas:**
- **Decisoes fora do codigo sao essenciais**: nomes de auditores, incidentes OOM, historico de perda de dados — informacoes que nao podem ser obtidas lendo o codigo
- **Eliminacao de codigo desnecessario**: sem o Kandela, implementacoes ja descartadas sao recriadas, gerando 37.5% de desperdicio de codigo

## Funcionalidades principais

- **13 ferramentas MCP**: armazenamento, busca, exclusao, atualizacao, recuperacao automatica, busca sob demanda, Inbox, gerenciamento de projetos, etc.
- **Busca hibrida**: semantica + busca por palavras-chave BM25 (fusao RRF)
- **Motor de Importance**: pontuacao automatica 1-10 + 18 regras de marcacao de infraestrutura
- **Lazy Retrieval**: modo brief (~260 tok) + busca sob demanda `memory_context_search`
- **Continuidade de sessao**: deteccao de mudancas de ambiente (CWD, host, cliente) + inclusao automatica de memorias de infraestrutura
- **Cache local + Auto-Sync**: cache JSONL do Stop Hook → sincronizacao automatica com o servidor no SessionStart
- **Painel web**: consulta de memorias por projeto, busca, estatisticas, monitoramento de desempenho
- **Instalacao com um clique**: `curl ... | bash` para instalacao automatica de Hooks + comandos slash
- **Prompt Guard**: prevencao de decisoes erradas baseadas em memorias obsoletas
- **Circuit Breaker**: deteccao de padroes de falha repetitivos + armazenamento automatico de Gotchas
- **Visibilidade entre projetos**: configuracao searchable por projeto para controlar o escopo de busca entre projetos
- **Embeddings multilinguais**: paraphrase-multilingual-MiniLM-L12-v2 (50+ idiomas)

## Requisitos

- **Python >= 3.11**
- O modelo de embedding e baixado automaticamente na primeira execucao (~449MB, 1-5 minutos)

## Inicio rapido em 5 minutos

### Metodo 1: Docker (recomendado)

```bash
git clone https://github.com/deep-on/kandela.git && cd kandela/docker
docker compose up -d
# → http://localhost:8321/dashboard
```

### Metodo 2: Instalacao local

```bash
git clone https://github.com/deep-on/kandela.git && cd kandela
pip install -e .
python -m memory_mcp --transport http --port 8321
```

### Conexao do cliente (Claude Code)

```bash
# 1. Registrar servidor MCP
claude mcp add memory --transport http http://localhost:8321/mcp

# 2. Instalacao automatica de Hooks + comandos slash
curl -sf http://localhost:8321/api/install | bash

# 3. Inicializar projeto
/kd-init
```

### Conexao do cliente (Claude Desktop / Cursor)

Adicionar em `~/.claude.json` ou `.mcp.json`:

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

> Apos a instalacao, o sistema de memoria e ativado automaticamente na primeira conversa.

## Autenticacao (opcional)

Recomenda-se autenticacao por chave API para acesso a partir de redes externas.

```bash
# Configurar em .env
KANDELA_API_KEY=your-secret-key
KANDELA_REQUIRE_AUTH=true
```

Ao conectar o cliente:
```bash
claude mcp add memory --transport http http://your-server:8321/mcp \
  --header "Authorization: Bearer your-secret-key"
```

> Para uso exclusivamente local, nao e necessaria autenticacao.
> Para acesso externo, configure um reverse proxy + autenticacao.

## Ferramentas MCP (13)

| Ferramenta | Descricao |
|------------|-----------|
| `memory_store` | Armazenar uma memoria (projeto, conteudo, tipo, tags, importancia) |
| `memory_search` | Buscar memorias relevantes (semantica, hibrida BM25, MMR, filtros) |
| `memory_context_search` | Busca comprimida durante a conversa (~50 tok/resultado, sob demanda) |
| `memory_delete` | Excluir uma memoria especifica |
| `memory_update` | Modificar uma memoria (conteudo/tipo/importancia/tags, recalculo automatico do embedding) |
| `memory_inbox` | Consultar/marcar como lidos os memos nao lidos |
| `memory_auto_recall` | Carregamento automatico de memorias relevantes no inicio da sessao (modo brief/full) |
| `memory_summarize_session` | Salvar resumo da sessao atual |
| `memory_list_projects` | Lista de projetos registrados |
| `memory_stats` | Estatisticas de memoria por projeto |
| `memory_project_rename` | Renomear um projeto |
| `memory_project_delete` | Excluir um projeto (incluindo todas as memorias) |
| `memory_get_guide` | Fornecer o modelo de guia CLAUDE.md |

## Painel web

Acessivel em `http://localhost:8321/dashboard`.

- Status do servidor, estatisticas de memoria, uso de armazenamento
- Lista de memorias por projeto e visualizacao detalhada
- Busca semantica (filtro por projeto)
- Consumo de tokens / analise ROI
- Monitoramento de desempenho (p50/p95/p99 por endpoint)

## Hooks (Claude Code)

As memorias sao gerenciadas automaticamente no inicio/fim da sessao:

| Hook | Evento | Acao |
|------|--------|------|
| SessionStart | Inicio da sessao | Chamada de `memory_auto_recall` |
| PreCompact | Antes da compressao do contexto | Chamada de `memory_summarize_session` |
| Stop | Fim da sessao | Salvamento do cache JSONL local |

## Variaveis de ambiente

| Variavel | Descricao | Valor padrao |
|----------|-----------|--------------|
| `KANDELA_DB_PATH` | Caminho de armazenamento do ChromaDB | `~/.memory-mcp/data` |
| `KANDELA_EMBEDDING_MODEL` | Nome do modelo de embedding | `paraphrase-multilingual-MiniLM-L12-v2` |
| `KANDELA_PORT` | Porta HTTP | `8321` |
| `KANDELA_API_KEY` | Chave API (opcional) | - |
| `KANDELA_REQUIRE_AUTH` | Forcar autenticacao por chave API | `false` |
| `MCP_LOG_LEVEL` | Nivel de log | `INFO` |
| `HF_HOME` | Caminho do cache de modelos HuggingFace | `~/.cache/huggingface` |

## Estrutura do projeto

```
src/memory_mcp/
├── server.py          # Servidor MCP (13 ferramentas)
├── auth.py            # Middleware de autenticacao por chave API
├── dashboard.py       # API REST + painel web
├── install.py         # Script de instalacao com um clique
├── db/
│   ├── store.py       # MemoryStore CRUD principal (ChromaDB)
│   ├── bm25.py        # Tokenizer BM25 + indice
│   ├── fusion.py      # Fusao RRF (semantica + BM25)
│   └── session_env.py # Deteccao do ambiente de sessao
├── importance/        # Motor de Importance (regras + pontuacao)
├── templates/         # Guias, prompts de hooks, comandos slash
└── tools/models.py    # Modelos de entrada Pydantic
```

## Servico hospedado

Alem do self-hosting, oferecemos um servico hospedado com funcionalidades adicionais:
- Suporte multiusuario + gerenciamento de contas
- Integracao com bot do Telegram
- Comandos remotos (Remote Command)
- Mapa de calor de atividade
- Funcionalidades por nivel (Pro/Max)

Mais detalhes: [kandela.ai](https://kandela.ai)

## Desenvolvimento

```bash
pip install -e ".[dev]"
pytest tests/ -v
ruff check src/
```

## Licenca

- **Servidor**: [AGPL-3.0](LICENSE) — Copyright (c) 2025-2026 Deep-ON Inc.
- **Cliente** (hooks e comandos slash gerados pelo install.py): [MIT](LICENSE-CLIENT)

## Aviso legal

Este software e fornecido "como esta (AS IS)", sem garantias expressas ou implicitas.
O backup dos dados armazenados pelo usuario e de responsabilidade do usuario.
