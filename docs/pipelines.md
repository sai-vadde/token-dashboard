# Source pipelines

Token Dashboard separates shared analytics from provider-native behavior.

## Shared ingestion core

`token_dashboard/scanner.py` owns only JSONL walking, file cursors, canonical message/tool persistence, streaming-snapshot deduplication, and agent identity persistence. It selects a registered pipeline through `token_dashboard/pipeline.py`; it does not inspect provider record types.

Every pipeline supplies:

- a stable source name and feature list;
- record acceptance and normalization into canonical message/tool rows;
- context and replay behavior;
- provider-owned update persistence;
- an optional post-scan hook.

Canonical tables (`messages`, `tool_calls`, and `agents`) power Overview, Prompts, Sessions, Projects, Skills, Agents, Tips, and cross-source comparisons.

## Claude pipeline

`token_dashboard/claude.py` parses Claude message records incrementally and enriches sidechain identities from `agent-*.meta.json` files. Claude-specific file layout and content blocks do not leak into the shared scanner.

## Codex pipeline

`token_dashboard/codex.py` replays changed event streams because later records require session and turn context and normalizes shared records. `token_dashboard/codex_store.py` owns the custom schema, persistence, and queries:

- `codex_turns`: task lifecycle, status, duration, time to first token, effort, approval/sandbox/collaboration modes, and context window;
- `codex_rate_limits`: timestamped primary/secondary quota snapshots and reset windows;
- Codex-only summary, logical-turn, and rate-limit queries used by the Codex UI.

Provider-specific tables should not be queried by global views. New sources register a pipeline and add custom tables only for genuinely source-exclusive concepts.
