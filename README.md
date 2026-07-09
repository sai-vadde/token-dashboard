# Token Dashboard

A local dashboard that reads JSONL transcripts from Claude Code and Codex, then turns them into per-prompt cost analytics, tool/file heatmaps, subagent attribution, cache analytics, project comparisons, source comparisons, and a rule-based tips engine.

**Everything runs locally.** No data leaves your machine: no telemetry, no API calls for your data, no login.

![Overview tab - totals and daily charts](docs/images/dashboard-overview-top.jpg)

![Overview tab - per-project, per-model, top tools, recent sessions](docs/images/dashboard-overview-bottom.jpg)

## What this is useful for

- Seeing which of your prompts are expensive.
- Comparing token usage across projects and between Claude/Codex transcript sources.
- Spotting wasteful patterns: the same file read twenty times in a session, a tool call returning 80k tokens, or low cache reuse.
- Understanding what a "cache hit" actually saves you.
- If you're on Pro or Max, confirming you're getting your money's worth in API-equivalent dollars.

## Prerequisites

- **Python 3.8 or newer**. On Windows: `winget install Python.Python.3.12` or download from python.org.
- **Claude Code and/or Codex** with at least one local session written to disk.
- **A web browser.** Any modern one.

No `pip install`. No Node.js. No build step.

## Quickstart

```bash
git clone https://github.com/sai-vadde/token-dashboard.git
cd token-dashboard
python3 cli.py dashboard
```

> On Windows, if `python3` isn't on your PATH, substitute `py -3` for `python3` in every command below.

The command:

1. Scans Claude transcripts from `~/.claude/projects/` and Codex transcripts from `~/.codex/sessions/` when those folders exist.
2. Starts a local server at http://127.0.0.1:8080.
3. Opens your default browser to that URL.

Leave it running; it re-scans every 30 seconds and pushes updates live. Stop with `Ctrl+C`.

## Where the data comes from

Claude Code writes one JSONL file per session under its project folder:

| OS | Path |
|---|---|
| macOS / Linux | `~/.claude/projects/<project-slug>/<session-id>.jsonl` |
| Windows | `C:\Users\<you>\.claude\projects\<project-slug>\<session-id>.jsonl` |

Codex writes dated session JSONL files under:

| OS | Path |
|---|---|
| macOS / Linux | `~/.codex/sessions/YYYY/MM/DD/*.jsonl` |
| Windows | `C:\Users\<you>\.codex\sessions\YYYY\MM\DD\*.jsonl` |

The dashboard never modifies those transcript files. It only reads them and keeps a local SQLite cache at `~/.codex/token-dashboard.db`.

To scan a single custom root:

```bash
python3 cli.py dashboard --source claude --projects-dir /path/to/projects --db /path/to/cache.db
python3 cli.py dashboard --source codex --projects-dir /path/to/sessions --db /path/to/cache.db
```

If `--projects-dir` is provided without `--source`, it is treated as a Claude-style project root for backward compatibility.

### Environment variables

| Var | Default | Purpose |
|---|---|---|
| `PORT` | `8080` | Port the local web server listens on |
| `HOST` | `127.0.0.1` | Bind address. Keep the default. Setting `0.0.0.0` exposes your prompt history to anyone on your local network. |
| `TOKEN_DASHBOARD_SOURCE` | `all` | Source to scan/display by default: `all`, `claude`, or `codex` |
| `CLAUDE_PROJECTS_DIR` | `~/.claude/projects` | Claude transcript root |
| `CODEX_SESSIONS_DIR` | `~/.codex/sessions` | Codex transcript root |
| `TOKEN_DASHBOARD_DB` | `~/.codex/token-dashboard.db` | SQLite cache location |

Pricing lives in [`pricing.json`](pricing.json). Edit it directly if model prices change or to add a new plan.

## CLI reference

```bash
python3 cli.py scan              # populate / refresh the local DB, then exit
python3 cli.py today             # today's totals
python3 cli.py stats             # all-time totals
python3 cli.py tips              # active suggestions
python3 cli.py dashboard         # scan + serve the UI at http://localhost:8080

# source selection
python3 cli.py scan --source all
python3 cli.py scan --source claude
python3 cli.py scan --source codex

# dashboard flags
python3 cli.py dashboard --no-open
python3 cli.py dashboard --no-scan
```

Change the port: `PORT=9000 python3 cli.py dashboard`.

## The 7 tabs

The dashboard is a single page with a hash-router tab bar across the top. Each tab is backed by its own JSON API under `/api/`. A source switcher in the top bar lets you view all transcripts together or filter to Claude/Codex.

- **Overview** - all-time input/output/cache tokens, sessions, turns, estimated cost on your chosen plan, daily work and cache-read charts, tokens-by-project, token share by model, top tools by call count, and recent sessions.
- **Prompts** - your most expensive user prompts ranked by tokens. Click any row to see the assistant response, tool calls made, and the size of each tool result.
- **Sessions** - turn-by-turn view of any single session, with per-turn tokens and tool calls. Session links preserve source so duplicate IDs do not collide.
- **Projects** - per-project comparison: tokens, session counts, and which files were touched most.
- **Skills** - which skills you invoke most often, and where measurable, their token cost. See [limitations](docs/KNOWN_LIMITATIONS.md#skills-token-counts-are-partial).
- **Tips** - rule-based suggestions for reducing token usage.
- **Settings** - switch pricing between API / Pro / Max / Max-20x so cost figures everywhere else reflect your actual plan.

The Overview tab also has a built-in "What do these numbers mean?" panel that explains input/output/cache tokens in plain English.

## Troubleshooting

**"No data" or empty charts.** Run `python3 cli.py scan` once to populate the DB, then reload.

**Port 8080 already in use.** `PORT=9000 python3 cli.py dashboard`.

**Numbers look wrong / stuck.** The DB lives at `~/.codex/token-dashboard.db` unless `TOKEN_DASHBOARD_DB` is set. Delete it and re-run `python3 cli.py scan` to rebuild from scratch.

**Running the dashboard twice at the same time.** Don't. Both processes will fight over the SQLite DB. Stop all instances before starting a new one.

## Accuracy note

Claude Code writes each assistant response 2-3 times to disk while it streams. The dashboard dedupes these by `message.id` so the final tally matches the billed API message more closely than tools that sum every JSONL row.

Codex transcripts are event-oriented rather than message-oriented. The scanner normalizes user prompts, tool calls, tool results, and token-count events into the shared dashboard schema. Codex changed files are replayed from the start because later token records depend on earlier context records; deterministic message IDs keep that replay idempotent.

## Privacy

Nothing leaves your machine. No telemetry. No remote calls for your data. The browser fetches its JSON from `127.0.0.1`, and all JS/CSS/fonts are served from that same local server. ECharts is vendored into `web/`, and the UI falls back to system fonts rather than pulling from a font CDN.

## Tech stack

Python 3 (stdlib only) for the CLI, scanner, and HTTP server. SQLite for the local cache. Vanilla JS + ECharts for the UI, no build step. Dark theme, hash-based router, server-sent events for live refresh.

Data flow: `cli.py` -> `token_dashboard/scanner.py` -> SQLite DB; `token_dashboard/server.py` exposes `/api/*` JSON routes and serves `web/`.

## Further reading

- [`CLAUDE.md`](CLAUDE.md) - conventions and architecture overview
- [`AGENTS.md`](AGENTS.md) - Codex-specific repository guidance
- [`CONTRIBUTING.md`](CONTRIBUTING.md) - how to develop and test
- [`docs/KNOWN_LIMITATIONS.md`](docs/KNOWN_LIMITATIONS.md) - rough edges
- [`docs/codex_feature_clusters.md`](docs/codex_feature_clusters.md) - implementation map for Codex transcript support
- [`docs/inspiration.md`](docs/inspiration.md) - prior art and how this project diverges

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). Short version: fork, `python3 -m unittest discover tests` before opening a PR, keep it stdlib-only.

## License

[MIT](LICENSE).
