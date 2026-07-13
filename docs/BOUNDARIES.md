# Boundaries — frozen invariants for Token Dashboard

These are the load-bearing invariants an autonomous step must **not** cross
without an explicit human decision. They restate the hard rules from `CLAUDE.md`
so the loop's coder and reviewer have one address for "what must stay true."

## Frozen

1. **Fully local, no telemetry.** No remote calls for user data; tests run
   offline. A step that adds a network dependency for user data is blocked.
2. **Stdlib only.** No `pip install` / third-party runtime dependency. A feature
   that needs one must argue for it to the human first, not add it silently.
3. **SQLite parameter binding always.** Any f-string in SQL may interpolate only
   internal, caller-controlled values (column names, placeholder lists);
   user-reachable values go through `?`. No exceptions.
4. **Source-aware queries.** Claude and Codex rows share one schema. `source`
   filters stay explicit, and session counts use
   `source || ':' || session_id` wherever mixed-source collisions are possible.
   A query helper that accepts `source` must handle the list form (source="all")
   as well as a single value — see `db._range_clause`.
5. **Streaming-snapshot dedup key is `(session_id, message_id)`**, not `uuid`.
   Scanner logic joining `messages` must respect it (`scanner._evict_prior_snapshots`,
   `db._migrate_add_message_id`).
6. **Small files, clear responsibility.** A file past ~400 lines or accreting a
   third concern gets split, not extended.

## Change requires a human decision

- The SQLite schema / migrations (`db.SCHEMA`, `db._migrate_*`).
- Canonical column meanings shared across sources (token buckets,
  `context_window`, `source`).
- `pricing.json` structure and the public `/api/*` response shapes the frontend
  depends on.
