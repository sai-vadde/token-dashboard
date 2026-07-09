# Known Limitations

None of these are blockers. The dashboard still gives you useful information, but these are the rough edges you'll notice if you look hard.

## Skills token counts are partial

The Skills route shows every skill invoked, how many times, across how many sessions, and when. The **tokens-per-call** column is populated only for skills whose `SKILL.md` lives under the catalog roots currently scanned by `token_dashboard/skills.py`, such as `~/.claude/skills/`, `~/.claude/scheduled-tasks/`, and `~/.claude/plugins/`. Skills registered elsewhere (project-local skills, Codex plugin cache paths not included in the catalog, or invocations that go through a subagent rather than a direct `Skill` tool call) show invocation counts but leave the token column blank.

It's still a useful view: you can see which skills dominate your session time. Just don't expect a complete per-skill token cost. PRs to broaden the catalog scan welcome.

## Cost for Pro / Max / Max-20x users is shown as API-equivalent, not subscription value

The Settings route lets you select your pricing plan, but the Overview cost number is always the API-equivalent (what the same usage would have cost on pay-per-token rates). If you're on Pro you pay a flat $20/month regardless of how much of that API-equivalent number you rack up. We don't do "subscription ROI" math yet because plan limits are not all published as public machine-readable pricing data.

## Remote/server-side sessions are invisible

If a tool mode does not write local JSONL transcripts, the dashboard cannot see it. That includes Claude Cowork/server-side sessions and any Codex session that is not persisted under the configured `CODEX_SESSIONS_DIR`.

## Codex cache-create buckets are not available yet

Codex token-count records expose input, cached input, and output tokens in the scanner paths covered today. The dashboard maps cached input to `cache_read_tokens`, but leaves the 5-minute and 1-hour cache-create buckets at zero for Codex rows unless future transcript records expose those fields.

## Codex changed files replay from the beginning

Claude project JSONL files can be resumed from the last byte offset. Codex records depend on earlier session metadata and turn context, so when a Codex file changes the scanner replays that file from byte zero. Inserts are idempotent, so totals stay stable, but very large active Codex transcripts may take longer to rescan.

## Non-standard model names get tier-fallback pricing

If a transcript references a model ID not in `pricing.json` (for example a future Claude or OpenAI model), cost is estimated from known tier substrings where possible. If the model name contains no recognized pricing signal, cost is reported as null.

## First scan can be slow

The first `python3 cli.py scan` on a heavy user's machine can read tens of MB across hundreds of JSONLs. Subsequent scans are incremental (mtime + byte-offset tracking in the `files` table), so they're fast.

## Running two dashboards against the same DB

Both will fight over the SQLite file and you'll see inconsistent numbers and occasional `database is locked` errors. Only run one at a time. If you want to view the dashboard from a second device, use `HOST=0.0.0.0` on the one running machine and point the second device's browser at it.
