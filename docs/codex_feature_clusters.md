# Codex Feature Clusters

Stored plan for the Codex transcript support work. These clusters describe the implementation shape that should stay coherent as follow-up changes land.

## 1. Transcript and source schema

- Track transcript source explicitly so Claude and Codex records can live in the same SQLite database without guessing from paths.
- Persist enough source metadata to support routing, filtering, display labels, and future migrations.
- Keep the existing message/session model intact where possible; source should qualify records, not fork the whole schema.
- Preserve streaming-snapshot dedup semantics: `(session_id, message_id)` remains the dedup key for joined message logic.

## 2. Codex scanner normalization

- Normalize Codex JSONL records into the dashboard's canonical usage shape before storage.
- Map Codex project roots, session identifiers, message identifiers, model names, usage fields, tool calls, and file references into existing scanner outputs.
- Keep incremental scanning behavior: file mtime and byte offset must still avoid rereading unchanged transcript bytes.
- Treat malformed or partial Codex records like existing transcript edge cases: skip safely, retain useful context, and avoid crashing a scan.

## 3. CLI and API source routing

- Expose source selection through CLI arguments and environment-aware defaults.
- Route scans to Claude, Codex, or combined transcript sources without changing downstream API consumers unnecessarily.
- Include source filters in API endpoints that aggregate overview, prompts, sessions, projects, skills, tips, and settings-adjacent data.
- Keep SQLite queries parameterized; source filters should use bound values unless interpolating internal column or placeholder lists.

## 4. UI source switcher

- Add a dashboard-level source switcher that can view Claude, Codex, or all sources.
- Make the selected source flow through hash-router views, refresh behavior, and ECharts data requests.
- Keep tab behavior stable across Overview, Prompts, Sessions, Projects, Skills, Tips, and Settings.
- Show clear labels for source-specific data while avoiding duplicated UI flows for each source.

## 5. Tests and Playwright verification

- Cover schema migration, Codex scanner normalization, source-aware CLI/API routing, and mixed-source aggregate behavior with unit tests.
- Add regression cases for incremental Codex scans and duplicate streaming snapshots.
- Verify the source switcher with Playwright across the main dashboard views.
- Keep tests offline and stdlib-first, matching the repository's no-install convention.

## 6. Docs and follow-ups

- Update user-facing docs for Codex transcript discovery, source selection, and any new environment variables.
- Record limitations that remain after Codex support, especially partial fields or source-specific gaps.
- Add follow-up notes for future sources only after Codex and Claude behavior are stable together.
- Keep this file as the compact cluster map; deeper implementation notes belong beside the affected code or in targeted docs.
