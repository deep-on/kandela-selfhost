<p align="center">
  <img src="https://kandela.ai/logo.png" width="80" alt="Kandela">
</p>

<h1 align="center">Kandela Self-Hosted</h1>

<p align="center">
  <b>Betreiben Sie Ihren eigenen persistenten Memory-Server fuer KI-Coding-Agenten.</b><br>
  Einzelbenutzer-Modus. Volle Kontrolle ueber Ihre Daten. Ein Befehl zum Starten.
</p>

<p align="center">
  <a href="README.md">English</a> | <a href="README.ko.md">한국어</a> | <a href="README.ja.md">日本語</a> | <b>Deutsch</b> | <a href="README.fr.md">Français</a> | <a href="README.es.md">Español</a> | <a href="README.pt.md">Português</a> | <a href="README.zh.md">中文</a>
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

## Warum wird das gebraucht

Erinnerungen speichern und abrufen koennen viele Tools.
Das Problem, das Kandela loest, liegt eine Stufe darueber — **KI daran hindern, trotz vorhandener Erinnerungen dieselben Fehler zu wiederholen, und Wissen automatisch zwischen Projekten fliessen lassen**.

### A/B-Benchmark-Ergebnisse

HIPAA-Szenario fuer medizinische Datenpipeline (8 Sitzungen, 14 Entscheidungsfallen) im Vergleich Kandela ON/OFF:

| | Kandela ON | Kandela OFF | Differenz |
|---|:-:|:-:|:-:|
| **Fallenvermeidungsrate** | **100%** | 11.9% | **+88.1pp** |
| **Arbeitszeit** | 77.9 Min. | 86.6 Min. | **-10.1%** |
| **Generierter Code** | 2.152 Zeilen | 3.441 Zeilen | **-37.5%** |
| **Generierte Dateien** | 40 | 62 | **-35.5%** |

> 3 Durchlaeufe (seeds=42,123,456), claude-sonnet-4-6, Groq Llama 3.3 70B (Operator).

**Wichtige Erkenntnisse:**
- **Entscheidungen ausserhalb des Codes sind entscheidend**: Pruefernamen, OOM-Vorfaelle, Datenverlusthistorie — Informationen, die aus dem Code allein nicht ersichtlich sind
- **Vermeidung von ueberfluessigem Code**: Ohne Kandela werden bereits verworfene Implementierungen erneut erstellt, was zu 37.5% Code-Verschwendung fuehrt

## Hauptfunktionen

- **13 MCP-Tools**: Speichern, Suchen, Loeschen, Aktualisieren, automatischer Abruf, On-Demand-Suche, Inbox, Projektverwaltung u.v.m.
- **Hybride Suche**: Semantisch + BM25-Stichwortsuche (RRF-Fusion)
- **Importance-Engine**: Automatische Bewertung 1–10 + 18 regelbasierte Infrastruktur-Tags
- **Lazy Retrieval**: Brief-Modus (~260 Tok) + `memory_context_search` On-Demand-Suche
- **Sitzungskontinuitaet**: Erkennung von Umgebungsaenderungen (CWD, Host, Client) + automatische Einbindung von Infrastruktur-Erinnerungen
- **Lokaler Cache + Auto-Sync**: Stop-Hook-JSONL-Cache → automatische Serversynchronisierung bei SessionStart
- **Web-Dashboard**: Projektbezogene Erinnerungsansicht, Suche, Statistiken, Leistungsueberwachung
- **Ein-Klick-Installation**: `curl ... | bash` fuer automatische Installation von Hooks + Slash-Befehlen
- **Prompt Guard**: Verhindert falsche Entscheidungen basierend auf veralteten Erinnerungen
- **Circuit Breaker**: Erkennung wiederkehrender Fehlermuster + automatische Gotcha-Speicherung
- **Projektuebergreifende Sichtbarkeit**: Searchable-Einstellung pro Projekt zur Steuerung des projektuebergreifenden Suchbereichs
- **Mehrsprachige Embeddings**: paraphrase-multilingual-MiniLM-L12-v2 (50+ Sprachen)

## Voraussetzungen

- **Python >= 3.11**
- Beim ersten Start wird das Embedding-Modell automatisch heruntergeladen (~449MB, 1–5 Minuten)

## 5-Minuten-Schnellstart

### Methode 1: Docker (empfohlen)

```bash
git clone https://github.com/deep-on/kandela.git && cd kandela/docker
docker compose up -d
# → http://localhost:8321/dashboard
```

### Methode 2: Lokale Installation

```bash
git clone https://github.com/deep-on/kandela.git && cd kandela
pip install -e .
python -m memory_mcp --transport http --port 8321
```

### Client-Verbindung (Claude Code)

```bash
# 1. MCP-Server registrieren
claude mcp add memory --transport http http://localhost:8321/mcp

# 2. Hooks + Slash-Befehle automatisch installieren
curl -sf http://localhost:8321/api/install | bash

# 3. Projekt initialisieren
/kd-init
```

### Client-Verbindung (Claude Desktop / Cursor)

In `~/.claude.json` oder `.mcp.json` hinzufuegen:

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

> Nach der Installation wird das Erinnerungssystem im ersten Gespraech automatisch aktiviert.

## Authentifizierung (optional)

Fuer den Zugriff aus externen Netzwerken wird API-Schluessel-Authentifizierung empfohlen.

```bash
# In .env konfigurieren
KANDELA_API_KEY=your-secret-key
KANDELA_REQUIRE_AUTH=true
```

Bei der Client-Verbindung:
```bash
claude mcp add memory --transport http http://your-server:8321/mcp \
  --header "Authorization: Bearer your-secret-key"
```

> Bei ausschliesslich lokaler Nutzung ist keine Authentifizierung erforderlich.
> Fuer externen Zugriff richten Sie einen Reverse Proxy + Authentifizierung ein.

## MCP-Tools (13)

| Tool | Beschreibung |
|------|--------------|
| `memory_store` | Erinnerung speichern (Projekt, Inhalt, Typ, Tags, Wichtigkeit) |
| `memory_search` | Relevante Erinnerungen suchen (semantisch, BM25-Hybrid, MMR, Filter) |
| `memory_context_search` | Komprimierte Suche waehrend der Konversation (~50 Tok/Treffer, On-Demand) |
| `memory_delete` | Bestimmte Erinnerung loeschen |
| `memory_update` | Erinnerung aktualisieren (Inhalt/Typ/Wichtigkeit/Tags, automatische Embedding-Neuberechnung) |
| `memory_inbox` | Ungelesene Memos anzeigen/als gelesen markieren |
| `memory_auto_recall` | Automatisches Laden relevanter Erinnerungen bei Sitzungsstart (Brief-/Full-Modus) |
| `memory_summarize_session` | Aktuelle Sitzungszusammenfassung speichern |
| `memory_list_projects` | Liste registrierter Projekte |
| `memory_stats` | Erinnerungsstatistik pro Projekt |
| `memory_project_rename` | Projekt umbenennen |
| `memory_project_delete` | Projekt loeschen (einschliesslich aller Erinnerungen) |
| `memory_get_guide` | CLAUDE.md-Leitfadenvorlage bereitstellen |

## Web-Dashboard

Erreichbar unter `http://localhost:8321/dashboard`.

- Serverstatus, Erinnerungsstatistiken, Speicherauslastung
- Projektbezogene Erinnerungsliste und Detailansicht
- Semantische Suche (Projektfilter)
- Token-Verbrauch / ROI-Analyse
- Leistungsueberwachung (p50/p95/p99 pro Endpunkt)

## Hooks (Claude Code)

Erinnerungen werden beim Sitzungsstart/-ende automatisch verwaltet:

| Hook | Ereignis | Aktion |
|------|----------|--------|
| SessionStart | Sitzungsstart | `memory_auto_recall` aufrufen |
| PreCompact | Vor Kontextkomprimierung | `memory_summarize_session` aufrufen |
| Stop | Sitzungsende | Lokalen JSONL-Cache speichern |

## Umgebungsvariablen

| Variable | Beschreibung | Standard |
|----------|--------------|----------|
| `KANDELA_DB_PATH` | ChromaDB-Speicherpfad | `~/.memory-mcp/data` |
| `KANDELA_EMBEDDING_MODEL` | Name des Embedding-Modells | `paraphrase-multilingual-MiniLM-L12-v2` |
| `KANDELA_PORT` | HTTP-Port | `8321` |
| `KANDELA_API_KEY` | API-Schluessel (optional) | - |
| `KANDELA_REQUIRE_AUTH` | API-Schluessel-Authentifizierung erzwingen | `false` |
| `MCP_LOG_LEVEL` | Log-Level | `INFO` |
| `HF_HOME` | HuggingFace-Modell-Cache-Pfad | `~/.cache/huggingface` |

## Projektstruktur

```
src/memory_mcp/
├── server.py          # MCP-Server (13 Tools)
├── auth.py            # API-Schluessel-Authentifizierungsmiddleware
├── dashboard.py       # REST-API + Web-Dashboard
├── install.py         # Ein-Klick-Installationsskript
├── db/
│   ├── store.py       # MemoryStore-Kern-CRUD (ChromaDB)
│   ├── bm25.py        # BM25-Tokenizer + Index
│   ├── fusion.py      # RRF-Fusion (semantisch + BM25)
│   └── session_env.py # Sitzungsumgebungserkennung
├── importance/        # Importance-Engine (Regeln + Bewertung)
├── templates/         # Leitfaeden, Hook-Prompts, Slash-Befehle
└── tools/models.py    # Pydantic-Eingabemodelle
```

## Gehosteter Service

Neben Self-Hosting bieten wir auch einen gehosteten Service mit zusaetzlichen Funktionen:
- Mehrbenutzersupport + Kontoverwaltung
- Telegram-Bot-Integration
- Remote-Befehle (Remote Command)
- Aktivitaets-Heatmap
- Tierstufen-Funktionen (Pro/Max)

Weitere Details: [kandela.ai](https://kandela.ai)

## Entwicklung

```bash
pip install -e ".[dev]"
pytest tests/ -v
ruff check src/
```

## Lizenz

- **Server**: [AGPL-3.0](LICENSE) — Copyright (c) 2025-2026 Deep-ON Inc.
- **Client** (von install.py generierte Hooks, Slash-Befehle): [MIT](LICENSE-CLIENT)

## Haftungsausschluss

Diese Software wird "wie besehen (AS IS)" bereitgestellt, ohne ausdrueckliche oder stillschweigende Garantien.
Die Sicherung der vom Benutzer gespeicherten Daten liegt in der Verantwortung des Benutzers.
