<p align="center">
  <img src="https://kandela.ai/logo.png" width="80" alt="Kandela">
</p>

<h1 align="center">Kandela Self-Hosted</h1>

<p align="center">
  <b>Ejecute su propio servidor de memoria persistente para agentes de codificacion IA.</b><br>
  Modo de usuario unico. Control total de sus datos. Un solo comando para iniciar.
</p>

<p align="center">
  <a href="README.md">English</a> | <a href="README.ko.md">한국어</a> | <a href="README.ja.md">日本語</a> | <a href="README.de.md">Deutsch</a> | <a href="README.fr.md">Français</a> | <b>Español</b> | <a href="README.pt.md">Português</a> | <a href="README.zh.md">中文</a>
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

## Por que es necesario

Almacenar y buscar recuerdos es algo que hacen muchas herramientas.
El problema que Kandela resuelve va un paso mas alla — **controlar que la IA no repita errores a pesar de tener recuerdos, y hacer que el conocimiento fluya automaticamente entre proyectos**.

### Resultados del benchmark A/B

Escenario de pipeline de datos medicos HIPAA (8 sesiones, 14 trampas de decision) comparando Kandela ON/OFF:

| | Kandela ON | Kandela OFF | Diferencia |
|---|:-:|:-:|:-:|
| **Tasa de evasion de trampas** | **100%** | 11.9% | **+88.1pp** |
| **Tiempo de trabajo** | 77.9 min | 86.6 min | **-10.1%** |
| **Codigo generado** | 2,152 lineas | 3,441 lineas | **-37.5%** |
| **Archivos generados** | 40 | 62 | **-35.5%** |

> 3 repeticiones (seeds=42,123,456), claude-sonnet-4-6, Groq Llama 3.3 70B (Operator).

**Principales hallazgos:**
- **Las decisiones fuera del codigo son clave**: nombres de auditores, incidentes OOM, historial de perdida de datos — informacion que no se puede obtener leyendo el codigo
- **Eliminacion de codigo innecesario**: sin Kandela, se recrean implementaciones ya descartadas, generando un 37.5% de desperdicio de codigo

## Funcionalidades principales

- **13 herramientas MCP**: almacenamiento, busqueda, eliminacion, actualizacion, recuperacion automatica, busqueda bajo demanda, Inbox, gestion de proyectos, etc.
- **Busqueda hibrida**: semantica + busqueda por palabras clave BM25 (fusion RRF)
- **Motor de Importance**: puntuacion automatica 1-10 + 18 reglas de etiquetado de infraestructura
- **Lazy Retrieval**: modo brief (~260 tok) + busqueda bajo demanda `memory_context_search`
- **Continuidad de sesion**: deteccion de cambios de entorno (CWD, host, cliente) + inclusion automatica de memorias de infraestructura
- **Cache local + Auto-Sync**: cache JSONL del Stop Hook → sincronizacion automatica con el servidor en SessionStart
- **Panel web**: consulta de memorias por proyecto, busqueda, estadisticas, monitorizacion de rendimiento
- **Instalacion con un clic**: `curl ... | bash` para instalacion automatica de Hooks + comandos slash
- **Prompt Guard**: prevencion de decisiones erroneas basadas en recuerdos obsoletos
- **Circuit Breaker**: deteccion de patrones de fallo repetitivos + almacenamiento automatico de Gotchas
- **Visibilidad entre proyectos**: configuracion searchable por proyecto para controlar el alcance de busqueda entre proyectos
- **Embeddings multilingues**: paraphrase-multilingual-MiniLM-L12-v2 (50+ idiomas)

## Requisitos

- **Python >= 3.11**
- El modelo de embedding se descarga automaticamente en el primer inicio (~449MB, 1-5 minutos)

## Inicio rapido en 5 minutos

### Metodo 1: Docker (recomendado)

```bash
git clone https://github.com/deep-on/kandela.git && cd kandela/docker
docker compose up -d
# → http://localhost:8321/dashboard
```

### Metodo 2: Instalacion local

```bash
git clone https://github.com/deep-on/kandela.git && cd kandela
pip install -e .
python -m memory_mcp --transport http --port 8321
```

### Conexion del cliente (Claude Code)

```bash
# 1. Registrar servidor MCP
claude mcp add memory --transport http http://localhost:8321/mcp

# 2. Instalacion automatica de Hooks + comandos slash
curl -sf http://localhost:8321/api/install | bash

# 3. Inicializar proyecto
/kd-init
```

### Conexion del cliente (Claude Desktop / Cursor)

Agregar en `~/.claude.json` o `.mcp.json`:

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

> Despues de la instalacion, el sistema de memoria se activa automaticamente en la primera conversacion.

## Autenticacion (opcional)

Se recomienda autenticacion por clave API para acceso desde redes externas.

```bash
# Configurar en .env
KANDELA_API_KEY=your-secret-key
KANDELA_REQUIRE_AUTH=true
```

Al conectar el cliente:
```bash
claude mcp add memory --transport http http://your-server:8321/mcp \
  --header "Authorization: Bearer your-secret-key"
```

> Para uso exclusivamente local, no se requiere autenticacion.
> Para acceso externo, configure un reverse proxy + autenticacion.

## Herramientas MCP (13)

| Herramienta | Descripcion |
|-------------|-------------|
| `memory_store` | Almacenar un recuerdo (proyecto, contenido, tipo, tags, importancia) |
| `memory_search` | Buscar recuerdos relevantes (semantica, hibrida BM25, MMR, filtros) |
| `memory_context_search` | Busqueda comprimida durante la conversacion (~50 tok/resultado, bajo demanda) |
| `memory_delete` | Eliminar un recuerdo especifico |
| `memory_update` | Modificar un recuerdo (contenido/tipo/importancia/tags, recalculo automatico del embedding) |
| `memory_inbox` | Consultar/marcar como leidos los memos no leidos |
| `memory_auto_recall` | Carga automatica de recuerdos relevantes al inicio de sesion (modo brief/full) |
| `memory_summarize_session` | Guardar resumen de la sesion actual |
| `memory_list_projects` | Lista de proyectos registrados |
| `memory_stats` | Estadisticas de memoria por proyecto |
| `memory_project_rename` | Renombrar un proyecto |
| `memory_project_delete` | Eliminar un proyecto (incluyendo todos los recuerdos) |
| `memory_get_guide` | Proporcionar la plantilla de guia CLAUDE.md |

## Panel web

Accesible en `http://localhost:8321/dashboard`.

- Estado del servidor, estadisticas de memoria, uso de almacenamiento
- Lista de memorias por proyecto y vista detallada
- Busqueda semantica (filtro por proyecto)
- Consumo de tokens / analisis ROI
- Monitorizacion de rendimiento (p50/p95/p99 por endpoint)

## Hooks (Claude Code)

Las memorias se gestionan automaticamente al inicio/fin de sesion:

| Hook | Evento | Accion |
|------|--------|--------|
| SessionStart | Inicio de sesion | Llamada a `memory_auto_recall` |
| PreCompact | Antes de compresion del contexto | Llamada a `memory_summarize_session` |
| Stop | Fin de sesion | Guardado del cache JSONL local |

## Variables de entorno

| Variable | Descripcion | Valor por defecto |
|----------|-------------|-------------------|
| `KANDELA_DB_PATH` | Ruta de almacenamiento de ChromaDB | `~/.memory-mcp/data` |
| `KANDELA_EMBEDDING_MODEL` | Nombre del modelo de embedding | `paraphrase-multilingual-MiniLM-L12-v2` |
| `KANDELA_PORT` | Puerto HTTP | `8321` |
| `KANDELA_API_KEY` | Clave API (opcional) | - |
| `KANDELA_REQUIRE_AUTH` | Forzar autenticacion por clave API | `false` |
| `MCP_LOG_LEVEL` | Nivel de log | `INFO` |
| `HF_HOME` | Ruta de cache de modelos HuggingFace | `~/.cache/huggingface` |

## Estructura del proyecto

```
src/memory_mcp/
├── server.py          # Servidor MCP (13 herramientas)
├── auth.py            # Middleware de autenticacion por clave API
├── dashboard.py       # API REST + panel web
├── install.py         # Script de instalacion con un clic
├── db/
│   ├── store.py       # MemoryStore CRUD principal (ChromaDB)
│   ├── bm25.py        # Tokenizer BM25 + indice
│   ├── fusion.py      # Fusion RRF (semantica + BM25)
│   └── session_env.py # Deteccion del entorno de sesion
├── importance/        # Motor de Importance (reglas + puntuacion)
├── templates/         # Guias, prompts de hooks, comandos slash
└── tools/models.py    # Modelos de entrada Pydantic
```

## Servicio alojado

Ademas del self-hosting, ofrecemos un servicio alojado con funcionalidades adicionales:
- Soporte multiusuario + gestion de cuentas
- Integracion con bot de Telegram
- Comandos remotos (Remote Command)
- Mapa de calor de actividad
- Funcionalidades por nivel (Pro/Max)

Mas detalles: [kandela.ai](https://kandela.ai)

## Desarrollo

```bash
pip install -e ".[dev]"
pytest tests/ -v
ruff check src/
```

## Licencia

- **Servidor**: [AGPL-3.0](LICENSE) — Copyright (c) 2025-2026 Deep-ON Inc.
- **Cliente** (hooks y comandos slash generados por install.py): [MIT](LICENSE-CLIENT)

## Descargo de responsabilidad

Este software se proporciona "tal cual (AS IS)", sin garantias expresas ni implicitas.
La copia de seguridad de los datos almacenados por el usuario es responsabilidad del usuario.
