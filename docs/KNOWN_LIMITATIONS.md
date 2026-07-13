# Known Limitations

None of these are blockers. The dashboard still gives you useful information, but these are the rough edges you'll notice if you look hard.

## Skills token counts are partial

The Skills route shows every explicit `Skill` tool invocation, how many times, across how many sessions, and when. The **tokens-per-call** column is populated for discovered `SKILL.md` files under Claude roots, `~/.codex/skills`, the Codex plugin cache, and `~/.agents/skills`. Project-local skills, non-standard roots, and skills loaded implicitly or through a subagent can still lack direct invocation or token attribution.

It's still a useful view: you can see which skills dominate your session time. Just don't expect a complete per-skill token cost. PRs to broaden the catalog scan welcome.

## API-equivalent usage is not subscription spend

The Platforms route saves a separate plan for every provider. API-equivalent token cost and monthly subscription commitment are deliberately shown as different values. The former is useful for workload comparison; it is not a claim about the user's actual subscription bill. Unknown models make the estimate a lower bound and reduce its displayed pricing coverage.

## Remote/server-side sessions are invisible

If a tool mode does not write local JSONL transcripts, the dashboard cannot see it. That includes Claude Cowork/server-side sessions and any Codex session that is not persisted under the configured `CODEX_SESSIONS_DIR`.

## Claude context-window size is inferred, not recorded

Codex token events report the model context window directly, but Claude Code transcripts omit it. The scanner infers it per turn: Claude exposes a standard 200K window and an opt-in 1M beta window (Sonnet) with nothing in between, so any turn whose prompt (new input + cache reads) exceeds 200K is attributed to the 1M window and everything else to 200K. This keeps peak-context utilization meaningful and bounded at 100%, but a 1M-beta session that never crosses 200K of load is measured against the 200K window, so its utilization reads higher than the true 1M figure.

## Codex cache writes are not reported by current local token events

Codex token-count records expose input, cached input, and output tokens in the scanner paths covered today. The dashboard maps cached input to `cache_read_tokens`, but presents write tokens/events as unavailable—not zero. A later cache read does not prove that a write happened inside the scanned session, so the dashboard does not reverse-infer creation counts. Cache-read savings remain an estimate against the model's uncached API input rate.

## Codex changed files replay from the beginning

Claude project JSONL files can be resumed from the last byte offset. Codex records depend on earlier session metadata and turn context, so when a Codex file changes the scanner replays that file from byte zero. Inserts are idempotent, so totals stay stable, but very large active Codex transcripts may take longer to rescan.

## Codex lifecycle and quota fields depend on transcript version

The Codex tab records task timing, policies, collaboration mode, and quota windows only when those events exist in the local JSONL. Older transcripts remain valid and continue contributing canonical token/tool totals, but their Codex-only fields may be blank. Quota snapshots describe the local transcript at the time of each token event; they are not fetched from a remote billing API.

## Non-standard model names get tier-fallback pricing

If a transcript references a model ID not in `pricing.json` (for example a future Claude or OpenAI model), cost is estimated from known tier substrings where possible. If the model name contains no recognized pricing signal, cost is reported as null.

## First scan can be slow

The first `python3 cli.py scan` on a heavy user's machine can read tens of MB across hundreds of JSONLs. Subsequent scans are incremental (mtime + byte-offset tracking in the `files` table), so they're fast.

## Running two dashboards against the same DB

Both will fight over the SQLite file and you'll see inconsistent numbers and occasional `database is locked` errors. Only run one at a time. If you want to view the dashboard from a second device, use `HOST=0.0.0.0` on the one running machine and point the second device's browser at it.
