<p align="center">
  <img src="https://kandela.ai/logo.png" width="80" alt="Kandela">
</p>

<h1 align="center">Kandela Self-Hosted</h1>

<p align="center">
  <b>Hebergez votre propre serveur de memoire persistante pour les agents de codage IA.</b><br>
  Mode mono-utilisateur. Controle total de vos donnees. Une seule commande pour demarrer.
</p>

<p align="center">
  <a href="README.md">English</a> | <a href="README.ko.md">한국어</a> | <a href="README.ja.md">日本語</a> | <a href="README.de.md">Deutsch</a> | <b>Français</b> | <a href="README.es.md">Español</a> | <a href="README.pt.md">Português</a> | <a href="README.zh.md">中文</a>
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

## Pourquoi en avez-vous besoin

Stocker et rechercher des souvenirs, beaucoup d'outils le font.
Le probleme que Kandela resout va plus loin — **empecher l'IA de repeter les memes erreurs malgre ses souvenirs, et faire circuler automatiquement les connaissances entre les projets**.

### Resultats du benchmark A/B

Scenario de pipeline de donnees medicales HIPAA (8 sessions, 14 pieges decisionnels) compare avec Kandela ON/OFF :

| | Kandela ON | Kandela OFF | Difference |
|---|:-:|:-:|:-:|
| **Taux d'evitement des pieges** | **100%** | 11.9% | **+88.1pp** |
| **Temps de travail** | 77.9 min | 86.6 min | **-10.1%** |
| **Code genere** | 2 152 lignes | 3 441 lignes | **-37.5%** |
| **Fichiers generes** | 40 | 62 | **-35.5%** |

> 3 repetitions (seeds=42,123,456), claude-sonnet-4-6, Groq Llama 3.3 70B (Operator).

**Principaux enseignements :**
- **Les decisions hors code sont essentielles** : noms des auditeurs, incidents OOM, historique de perte de donnees — des informations introuvables dans le code seul
- **Elimination du code inutile** : sans Kandela, des implementations deja rejetees sont recreees, entrainant 37.5% de gaspillage de code

## Fonctionnalites principales

- **13 outils MCP** : stockage, recherche, suppression, mise a jour, rappel automatique, recherche a la demande, Inbox, gestion de projets, etc.
- **Recherche hybride** : semantique + recherche par mots-cles BM25 (fusion RRF)
- **Moteur d'Importance** : score automatique 1-10 + 18 regles de balisage d'infrastructure
- **Lazy Retrieval** : mode brief (~260 tok) + recherche a la demande `memory_context_search`
- **Continuite de session** : detection des changements d'environnement (CWD, hote, client) + inclusion automatique des memoires d'infrastructure
- **Cache local + Auto-Sync** : cache JSONL du Stop Hook → synchronisation automatique avec le serveur au SessionStart
- **Tableau de bord web** : consultation des memoires par projet, recherche, statistiques, surveillance des performances
- **Installation en un clic** : `curl ... | bash` pour l'installation automatique des Hooks + commandes slash
- **Prompt Guard** : prevention des mauvaises decisions basees sur d'anciens souvenirs
- **Circuit Breaker** : detection des schemas d'echecs repetitifs + sauvegarde automatique des Gotchas
- **Visibilite inter-projets** : parametre searchable par projet pour controler la portee de la recherche inter-projets
- **Embeddings multilingues** : paraphrase-multilingual-MiniLM-L12-v2 (50+ langues)

## Prerequis

- **Python >= 3.11**
- Le modele d'embedding est telecharge automatiquement au premier lancement (~449 Mo, 1 a 5 minutes)

## Demarrage rapide en 5 minutes

### Methode 1 : Docker (recommande)

```bash
git clone https://github.com/deep-on/kandela.git && cd kandela/docker
docker compose up -d
# → http://localhost:8321/dashboard
```

### Methode 2 : Installation locale

```bash
git clone https://github.com/deep-on/kandela.git && cd kandela
pip install -e .
python -m memory_mcp --transport http --port 8321
```

### Connexion client (Claude Code)

```bash
# 1. Enregistrer le serveur MCP
claude mcp add memory --transport http http://localhost:8321/mcp

# 2. Installation automatique des Hooks + commandes slash
curl -sf http://localhost:8321/api/install | bash

# 3. Initialiser le projet
/kd-init
```

### Connexion client (Claude Desktop / Cursor)

Ajoutez dans `~/.claude.json` ou `.mcp.json` :

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

> Apres l'installation, le systeme de memoire s'active automatiquement des la premiere conversation.

## Authentification (optionnel)

L'authentification par cle API est recommandee pour l'acces depuis des reseaux externes.

```bash
# Configurer dans .env
KANDELA_API_KEY=your-secret-key
KANDELA_REQUIRE_AUTH=true
```

Lors de la connexion client :
```bash
claude mcp add memory --transport http http://your-server:8321/mcp \
  --header "Authorization: Bearer your-secret-key"
```

> Pour une utilisation locale uniquement, aucune authentification n'est requise.
> Pour un acces externe, configurez un reverse proxy + authentification.

## Outils MCP (13)

| Outil | Description |
|-------|-------------|
| `memory_store` | Stocker un souvenir (projet, contenu, type, tags, importance) |
| `memory_search` | Rechercher des souvenirs pertinents (semantique, hybride BM25, MMR, filtres) |
| `memory_context_search` | Recherche compressee en cours de conversation (~50 tok/resultat, a la demande) |
| `memory_delete` | Supprimer un souvenir specifique |
| `memory_update` | Modifier un souvenir (contenu/type/importance/tags, recalcul automatique de l'embedding) |
| `memory_inbox` | Consulter/marquer comme lus les memos non lus |
| `memory_auto_recall` | Chargement automatique des souvenirs pertinents au demarrage de session (mode brief/full) |
| `memory_summarize_session` | Sauvegarder le resume de la session en cours |
| `memory_list_projects` | Liste des projets enregistres |
| `memory_stats` | Statistiques memoire par projet |
| `memory_project_rename` | Renommer un projet |
| `memory_project_delete` | Supprimer un projet (y compris tous les souvenirs) |
| `memory_get_guide` | Fournir le modele de guide CLAUDE.md |

## Tableau de bord web

Accessible a `http://localhost:8321/dashboard`.

- Etat du serveur, statistiques memoire, utilisation du stockage
- Liste des memoires par projet et vue detaillee
- Recherche semantique (filtre par projet)
- Consommation de tokens / analyse ROI
- Surveillance des performances (p50/p95/p99 par endpoint)

## Hooks (Claude Code)

Les memoires sont gerees automatiquement au demarrage/a la fin de session :

| Hook | Evenement | Action |
|------|-----------|--------|
| SessionStart | Demarrage de session | Appel de `memory_auto_recall` |
| PreCompact | Avant compression du contexte | Appel de `memory_summarize_session` |
| Stop | Fin de session | Sauvegarde du cache JSONL local |

## Variables d'environnement

| Variable | Description | Valeur par defaut |
|----------|-------------|-------------------|
| `KANDELA_DB_PATH` | Chemin de stockage ChromaDB | `~/.memory-mcp/data` |
| `KANDELA_EMBEDDING_MODEL` | Nom du modele d'embedding | `paraphrase-multilingual-MiniLM-L12-v2` |
| `KANDELA_PORT` | Port HTTP | `8321` |
| `KANDELA_API_KEY` | Cle API (optionnel) | - |
| `KANDELA_REQUIRE_AUTH` | Forcer l'authentification par cle API | `false` |
| `MCP_LOG_LEVEL` | Niveau de log | `INFO` |
| `HF_HOME` | Chemin du cache des modeles HuggingFace | `~/.cache/huggingface` |

## Structure du projet

```
src/memory_mcp/
├── server.py          # Serveur MCP (13 outils)
├── auth.py            # Middleware d'authentification par cle API
├── dashboard.py       # API REST + tableau de bord web
├── install.py         # Script d'installation en un clic
├── db/
│   ├── store.py       # MemoryStore CRUD principal (ChromaDB)
│   ├── bm25.py        # Tokenizer BM25 + index
│   ├── fusion.py      # Fusion RRF (semantique + BM25)
│   └── session_env.py # Detection de l'environnement de session
├── importance/        # Moteur d'Importance (regles + scores)
├── templates/         # Guides, prompts de hooks, commandes slash
└── tools/models.py    # Modeles d'entree Pydantic
```

## Service heberge

En plus du self-hosting, nous proposons un service heberge avec des fonctionnalites supplementaires :
- Support multi-utilisateurs + gestion de comptes
- Integration bot Telegram
- Commandes a distance (Remote Command)
- Heatmap d'activite
- Fonctionnalites par palier (Pro/Max)

Plus de details : [kandela.ai](https://kandela.ai)

## Developpement

```bash
pip install -e ".[dev]"
pytest tests/ -v
ruff check src/
```

## Licence

- **Serveur** : [AGPL-3.0](LICENSE) — Copyright (c) 2025-2026 Deep-ON Inc.
- **Client** (hooks et commandes slash generes par install.py) : [MIT](LICENSE-CLIENT)

## Avertissement

Ce logiciel est fourni "tel quel (AS IS)", sans garantie expresse ou implicite.
La sauvegarde des donnees stockees par l'utilisateur releve de la responsabilite de l'utilisateur.
