# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Odysseus is a self-hosted AI workspace (FastAPI backend + vanilla-JS SPA front-end): chat, autonomous agent, model "Cookbook" (hardware-aware download/serve), deep research, document editor, persistent memory/skills, email (IMAP/SMTP), calendar (CalDAV), notes/tasks, and image tools. Local-first and privacy-first — it talks to local model servers (vLLM, llama.cpp, Ollama) or API providers (OpenAI, OpenRouter).

## Commands

The server is an ASGI app served by uvicorn. Python 3.11+.

```bash
# Run (manual dev) — venv must be active
python -m uvicorn app:app --host 127.0.0.1 --port 7000

# Windows one-command launcher (creates venv, installs deps, runs setup, starts server)
powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1

# First-run setup (interactive admin credential prompt, DB init)
python setup.py

# Docker (recommended for full stack: app + ChromaDB + SearXNG + ntfy)
docker compose up -d --build
docker compose logs --tail=120 odysseus
```

### Tests & checks

```bash
python -m pytest                      # full suite (pytest-asyncio, asyncio_mode=auto)
python -m pytest tests/test_agent_loop.py            # single file
python -m pytest tests/test_agent_loop.py::test_name  # single test
python -m py_compile app.py routes/*.py src/*.py      # syntax check
node --check static/js/<file>.js                      # JS syntax check (no JS test runner)
```

Many tests named `*_js.py` (e.g. `test_compare_js.py`) assert on front-end JS by reading/parsing the `.js` source — there is no Node test framework, so front-end behavior is guarded from Python.

## Architecture

Request flow: **`app.py`** (slim orchestrator) wires middleware, CORS, security headers, a hard request-timeout, then calls `initialize_managers()` and mounts ~40 routers. Most cross-cutting logic lives in `src/`; routers in `routes/` are thin and receive their dependencies via `setup_*_routes(...)` factory functions called from `app.py`.

- **`core/`** — foundational, framework-light: `auth.py` (AuthManager, 2FA), `database.py` (SQLAlchemy models + `SessionLocal`; SQLite at `data/app.db`), `session_manager.py`, `middleware.py` (security headers + CSP nonce), `models.py`, `constants.py`, `atomic_io.py`, and `platform_compat.py` (centralizes all Windows-vs-POSIX differences — import helpers from here instead of scattering `os.name == "nt"` checks).
- **`src/`** — the bulk of business logic. Key modules:
  - `app_initializer.py` — constructs all managers/handlers and returns them as a dict that `app.py` unpacks.
  - `llm_core.py` — `stream_llm` / `stream_llm_with_fallback`: the OpenAI-compatible HTTP client with caching, retries, dead-host cooldown, Anthropic cache support.
  - `agent_loop.py` + `agent_tools.py` (facade over `tool_parsing.py`, `tool_schemas.py`, `tool_execution.py`, `tool_implementations.py`) — the agent. **Tools are invoked by the model writing fenced code blocks** (e.g. ```bash, ```python, ```web_search) which `agent_loop` parses and executes over up to `MAX_AGENT_ROUNDS` rounds. `TOOL_TAGS` in `agent_tools.py` is the registry of tool names.
  - `tool_index.py` — embeds tool descriptions for RAG-based tool selection (pre-warmed at startup).
  - RAG/memory stack: `rag_manager.py`/`rag_singleton.py`/`rag_vector.py` (document RAG), `memory.py`/`memory_vector.py` (semantic memory), `chroma_client.py`, `embeddings.py` (local ONNX via fastembed). All degrade to keyword fallback if ChromaDB is unreachable.
  - `search/` — multi-provider web search (SearXNG etc.) with caching/ranking.
  - `task_scheduler.py`, `bg_jobs.py`/`bg_monitor.py` (auto-continues agent when a background `#!bg` shell job finishes), `webhook_manager.py`, `mcp_manager.py`/`builtin_mcp.py`.
- **`services/`** — larger feature subsystems: `hwfit/` (Cookbook hardware/VRAM fit scoring + model serving, built on llmfit), `memory/`, `research/`, `search/`, `docs/`, `tts/`, `stt/`.
- **`routes/`** — 48 routers, one per feature area, each exporting a `setup_*_routes()` factory.
- **`mcp_servers/`** — built-in MCP servers (email, image gen, memory, RAG) auto-registered at startup; npx-based ones (browser) only start if already cached.
- **`static/`** — `index.html` SPA + `app.js` + modular `static/js/*.js` (one file per feature, e.g. `cookbook.js`, `emailInbox.js`, `chat.js`). All tool deep-link routes (`/cookbook`, `/email`, `/memory`, …) serve the same `index.html`; JS opens the matching modal from `window.location.pathname`.

### Conventions & gotchas

- **Graceful degradation everywhere**: ChromaDB, MCP servers, RAG, and LLM endpoints are all optional at runtime. Code logs `DEGRADED` warnings and falls back rather than crashing. Preserve this when editing init paths.
- **Startup is non-blocking**: MCP connection, tool-index warmup, and endpoint pings run as fire-and-forget tasks after the server accepts traffic (kept alive via `app.state._startup_tasks`). Don't move slow I/O onto the import path.
- **Auth & privileges**: shell/Python/file tools and admin routes (MCP, tokens, webhooks, serving, backup, settings) are gated. Per-user privileges control most features; many routes scope data by owner — check existing owner-scope tests (`test_*_owner_scope.py`) before touching data access.
- **Windows native support is intentional**: `.env` is loaded with `utf-8-sig` (BOM tolerance), HF symlinks are disabled on NT, JS MIME types are re-registered. Route OS differences through `core/platform_compat.py`.
- **Config**: app-level settings live in the DB/`data/settings.json` and are set in-app via `/setup` or Settings. `.env` is only for deployment-level overrides (`APP_BIND`, `APP_PORT`, `AUTH_ENABLED`, `DATABASE_URL`, `LLM_HOST(S)`, `CHROMADB_*`, `SEARXNG_*`). See `.env.example`.
- **Data** (all gitignored, under `data/`): `app.db`, `memory.json`, `presets.json`, `chroma/`, `uploads/`, `personal_docs/`, `settings.json`, `auth.json`, `ssh/`.

### TDD note

Per the global instructions in this environment, follow red-green-refactor: write the failing test first (`tests/`, function-style, descriptive names), watch it fail, then write minimal code. Bug fixes start with a reproducing test.
