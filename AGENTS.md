# AGENTS.md

Guidance for Codex when working in this repository.

## Project overview

**Token Dashboard** is a local dashboard for tracking Claude Code and Codex token usage, costs, and session history. It reads local JSONL transcripts, stores normalized rows in SQLite, and exposes per-prompt cost analytics, tool/file heatmaps, subagent attribution, cache analytics, project/source comparisons, and a rule-based tips engine.

Inspired by [phuryn/claude-usage](https://github.com/phuryn/claude-usage) but diverges in UI (vanilla JS + ECharts, dark theme, hash router, SSE refresh) and scope (expensive-prompt drill-down, skills view, tips engine, streaming-snapshot dedup, Codex source support). See `docs/inspiration.md` for the original's feature set and known limitations.

## Status

Working codebase. Python unit tests run with `python3 -m unittest discover tests`. Nine UI tabs are wired up (Overview, Prompts, Sessions, Projects, Skills, Agents, Platforms, Tips, Settings), with Codex-native analytics opened from its platform card. Runs on macOS, Windows, and Linux.

## Architecture

- `cli.py` -> shared `token_dashboard/scanner.py` -> registered source pipeline (`claude.py` / `codex.py`) -> `~/.codex/token-dashboard.db`
- `token_dashboard/server.py` exposes JSON APIs (`/api/*`) + SSE stream (`/api/stream`) + static frontend (`web/`)
- `web/` is vanilla JS, no build step: hash router + ECharts + top-bar source switcher

## Data sources

Claude Code writes one JSONL file per session to `~/.claude/projects/<project-slug>/<session-id>.jsonl`. Each line is a message record; usage fields live at `message.usage` and model identifier at `message.model`.

Codex writes dated session JSONL files under `~/.codex/sessions/YYYY/MM/DD/*.jsonl`. Its pipeline normalizes shared messages/tools while separately persisting task lifecycle and rate-limit events to Codex-owned tables.

The scanner is incremental. Claude scans use each file's mtime and byte offset. Codex changed files replay from byte zero because later usage records depend on earlier context records; deterministic message UUIDs keep that replay idempotent.

## Conventions

- **Fully local.** No telemetry, no remote calls for user data. Tests run offline.
- **Stdlib only.** No `pip install`. If a new feature needs a third-party library, argue for it first.
- **SQLite parameter binding always.** Any f-string in a SQL statement must interpolate only internal, caller-controlled values (column names, placeholder lists). User-reachable values go through `?`.
- **Source-aware queries.** Claude and Codex rows share the same schema. Keep `source` filters explicit and count sessions as `source || ':' || session_id` where mixed-source collisions are possible.
- **Small files with clear responsibilities.** If a file grows past ~400 lines or accretes three distinct concerns, split it.
- **Streaming-snapshot dedup.** When adding scanner logic that joins the `messages` table, remember `(session_id, message_id)` is the dedup key, not `uuid`. See `scanner._evict_prior_snapshots` and the migration note in `db._migrate_add_message_id`.

## Customizing

Env vars: `PORT` (default 8080), `HOST` (default 127.0.0.1), `TOKEN_DASHBOARD_SOURCE` (default `all`), `CLAUDE_PROJECTS_DIR`, `CODEX_SESSIONS_DIR`, and `TOKEN_DASHBOARD_DB`. Pricing lives in `pricing.json`. See README.md Environment variables for details.

## Known limitations

See `docs/KNOWN_LIMITATIONS.md`. Current summary: Skills `tokens_per_call` is populated only from the scanned skill catalog roots, Codex cache-create buckets are not currently available from token-count events, and remote/server-side sessions that never write local JSONL cannot be scanned.

## Verifying changes

```bash
python3 -m unittest discover tests        # all tests
python3 cli.py dashboard --no-open        # start the server
curl http://127.0.0.1:8080/api/overview   # sanity-check an endpoint
```
