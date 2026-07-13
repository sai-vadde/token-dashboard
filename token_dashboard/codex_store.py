"""Codex-owned schema, persistence, and analytics queries."""
from __future__ import annotations

CODEX_SCHEMA = """
CREATE TABLE IF NOT EXISTS codex_turns (
  session_id              TEXT NOT NULL,
  turn_id                 TEXT NOT NULL,
  project_slug            TEXT,
  cwd                     TEXT,
  agent_id                TEXT,
  parent_session_id       TEXT,
  model                   TEXT,
  effort                  TEXT,
  approval_policy         TEXT,
  sandbox_policy          TEXT,
  collaboration_mode      TEXT,
  status                  TEXT,
  started_at              REAL,
  completed_at            REAL,
  duration_ms             INTEGER,
  time_to_first_token_ms  INTEGER,
  context_window          INTEGER,
  first_event_at          TEXT,
  last_event_at           TEXT,
  source_metadata_json    TEXT,
  PRIMARY KEY (session_id, turn_id)
);
CREATE INDEX IF NOT EXISTS idx_codex_turns_last ON codex_turns(last_event_at);
CREATE INDEX IF NOT EXISTS idx_codex_turns_status ON codex_turns(status);

CREATE TABLE IF NOT EXISTS codex_rate_limits (
  snapshot_id             TEXT PRIMARY KEY,
  session_id              TEXT,
  turn_id                 TEXT,
  timestamp               TEXT NOT NULL,
  limit_id                TEXT,
  limit_name              TEXT,
  plan_type               TEXT,
  primary_used_percent    REAL,
  primary_window_minutes  INTEGER,
  primary_resets_at       REAL,
  secondary_used_percent  REAL,
  secondary_window_minutes INTEGER,
  secondary_resets_at     REAL,
  rate_limit_reached_type TEXT,
  credits_json            TEXT,
  source_metadata_json    TEXT
);
CREATE INDEX IF NOT EXISTS idx_codex_limits_time ON codex_rate_limits(timestamp);
"""


def init_codex_schema(conn) -> None:
    """Initialize schema owned exclusively by the Codex pipeline."""
    existing = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('codex_turns','codex_rate_limits')"
        )
    }
    conn.executescript(CODEX_SCHEMA)
    if existing != {"codex_turns", "codex_rate_limits"}:
        # Existing canonical rows remain valid, but custom lifecycle/quota
        # tables need one full Codex replay to backfill provider-only events.
        conn.execute("DELETE FROM files WHERE source='codex'")



def persist_codex_updates(conn, updates: list) -> None:
    for update in updates:
        kind = update.get("kind")
        if kind == "turn":
            _persist_turn(conn, update)
        elif kind == "rate_limit":
            _persist_rate_limit(conn, update)

_TURN_COLUMNS = (
    "session_id", "turn_id", "project_slug", "cwd", "agent_id", "parent_session_id",
    "model", "effort", "approval_policy", "sandbox_policy", "collaboration_mode",
    "status", "started_at", "completed_at", "duration_ms", "time_to_first_token_ms",
    "context_window", "source_metadata_json",
)


def _persist_turn(conn, update: dict) -> None:
    if not update.get("session_id") or not update.get("turn_id"):
        return
    values = {column: update.get(column) for column in _TURN_COLUMNS}
    values["first_event_at"] = update.get("event_at")
    values["last_event_at"] = update.get("event_at")
    columns = (*_TURN_COLUMNS, "first_event_at", "last_event_at")
    placeholders = ", ".join(f":{column}" for column in columns)
    updates = ", ".join(
        f"{column}=COALESCE(excluded.{column}, codex_turns.{column})"
        for column in _TURN_COLUMNS[2:]
    )
    conn.execute(
        f"INSERT INTO codex_turns ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(session_id, turn_id) DO UPDATE SET {updates}, "
        "first_event_at=COALESCE(codex_turns.first_event_at, excluded.first_event_at), "
        "last_event_at=CASE "
        "WHEN codex_turns.last_event_at IS NULL THEN excluded.last_event_at "
        "WHEN excluded.last_event_at IS NULL THEN codex_turns.last_event_at "
        "ELSE MAX(codex_turns.last_event_at, excluded.last_event_at) END",
        values,
    )


def _persist_rate_limit(conn, update: dict) -> None:
    if not update.get("snapshot_id") or not update.get("timestamp"):
        return
    columns = (
        "snapshot_id", "session_id", "turn_id", "timestamp", "limit_id", "limit_name",
        "plan_type", "primary_used_percent", "primary_window_minutes", "primary_resets_at",
        "secondary_used_percent", "secondary_window_minutes", "secondary_resets_at",
        "rate_limit_reached_type", "credits_json", "source_metadata_json",
    )
    conn.execute(
        f"INSERT OR REPLACE INTO codex_rate_limits ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)})",
        tuple(update.get(column) for column in columns),
    )



def _where_range(since=None, until=None, column: str = "last_event_at"):
    clauses, args = [], []
    if since:
        clauses.append(f"{column} >= ?")
        args.append(since)
    if until:
        clauses.append(f"{column} < ?")
        args.append(until)
    return ((" AND " + " AND ".join(clauses)) if clauses else "", args)


def codex_turn_breakdown(db_path, since=None, until=None, limit: int = 100) -> list:
    """Codex-native logical turns joined to canonical usage and tools."""
    from .db import connect

    where, args = _where_range(since, until, "t.last_event_at")
    sql = f"""
      WITH usage AS (
        SELECT session_id, prompt_id AS turn_id,
               COUNT(*) AS model_calls,
               SUM(input_tokens) AS input_tokens,
               SUM(output_tokens) AS output_tokens,
               SUM(cache_read_tokens) AS cache_read_tokens,
               SUM(reasoning_output_tokens) AS reasoning_output_tokens,
               MAX(CASE WHEN context_window > 0
                 THEN 1.0 * (input_tokens + cache_read_tokens) / context_window END)
                 AS peak_context_utilization
          FROM messages
         WHERE source='codex' AND type='assistant' AND prompt_id IS NOT NULL
         GROUP BY session_id, prompt_id
      ), tool_usage AS (
        SELECT m.session_id, m.prompt_id AS turn_id,
               COUNT(CASE WHEN tc.tool_name!='_tool_result' THEN 1 END) AS tool_calls,
               SUM(CASE WHEN tc.is_error=1 THEN 1 ELSE 0 END) AS tool_errors
          FROM tool_calls tc
          JOIN messages m ON m.uuid=tc.message_uuid
         WHERE m.source='codex' AND m.prompt_id IS NOT NULL
         GROUP BY m.session_id, m.prompt_id
      )
      SELECT t.*,
             COALESCE(u.model_calls,0) AS model_calls,
             COALESCE(u.input_tokens,0) AS input_tokens,
             COALESCE(u.output_tokens,0) AS output_tokens,
             COALESCE(u.cache_read_tokens,0) AS cache_read_tokens,
             COALESCE(u.reasoning_output_tokens,0) AS reasoning_output_tokens,
             COALESCE(u.input_tokens,0)+COALESCE(u.output_tokens,0)
               +COALESCE(u.cache_read_tokens,0) AS total_tokens,
             COALESCE(u.peak_context_utilization,0) AS peak_context_utilization,
             COALESCE(tu.tool_calls,0) AS tool_calls,
             COALESCE(tu.tool_errors,0) AS tool_errors
        FROM codex_turns t
        LEFT JOIN usage u ON u.session_id=t.session_id AND u.turn_id=t.turn_id
        LEFT JOIN tool_usage tu ON tu.session_id=t.session_id AND tu.turn_id=t.turn_id
       WHERE 1=1 {where}
       ORDER BY COALESCE(t.completed_at, t.started_at) DESC, t.last_event_at DESC
       LIMIT ?
    """
    with connect(db_path) as conn:
        return [dict(row) for row in conn.execute(sql, (*args, limit))]


def codex_summary(db_path, since=None, until=None) -> dict:
    """High-signal Codex-only lifecycle and resource metrics."""
    from .db import connect

    turn_where, turn_args = _where_range(since, until)
    msg_where, msg_args = _where_range(since, until, "timestamp")
    agent_where, agent_args = _where_range(since, until, "m.timestamp")
    with connect(db_path) as conn:
        turns = dict(conn.execute(f"""
          SELECT COUNT(*) AS turns,
                 SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed_turns,
                 AVG(duration_ms) AS avg_duration_ms,
                 MAX(duration_ms) AS max_duration_ms,
                 AVG(time_to_first_token_ms) AS avg_ttft_ms
            FROM codex_turns WHERE 1=1 {turn_where}
        """, turn_args).fetchone())
        usage = dict(conn.execute(f"""
          SELECT COUNT(*) AS model_calls,
                 COALESCE(SUM(reasoning_output_tokens),0) AS reasoning_output_tokens,
                 COALESCE(MAX(CASE WHEN context_window > 0
                   THEN 1.0 * (input_tokens + cache_read_tokens) / context_window END),0)
                   AS peak_context_utilization
            FROM messages
           WHERE source='codex' AND type='assistant' {msg_where}
        """, msg_args).fetchone())
        agents = conn.execute(f"""
          SELECT COUNT(DISTINCT a.session_id || ':' || a.agent_id)
            FROM agents a JOIN messages m
              ON m.source=a.source AND m.session_id=a.session_id AND m.agent_id=a.agent_id
           WHERE a.source='codex' AND m.type='assistant' {agent_where}
        """, agent_args).fetchone()[0]
        tools = conn.execute(f"""
          SELECT COUNT(CASE WHEN tool_name!='_tool_result' THEN 1 END),
                 SUM(CASE WHEN is_error=1 THEN 1 ELSE 0 END)
            FROM tool_calls WHERE source='codex' {msg_where}
        """, msg_args).fetchone()
        effort_rows = conn.execute(f"""
          SELECT COALESCE(effort, 'unknown'), COUNT(*)
            FROM codex_turns WHERE 1=1 {turn_where}
           GROUP BY effort ORDER BY COUNT(*) DESC
        """, turn_args).fetchall()
        approval_rows = conn.execute(f"""
          SELECT COALESCE(approval_policy, 'unknown'), COUNT(*)
            FROM codex_turns WHERE 1=1 {turn_where}
           GROUP BY approval_policy ORDER BY COUNT(*) DESC
        """, turn_args).fetchall()
        sandbox_rows = _group_codex_turn_field(
            conn, "sandbox_policy", turn_where, turn_args
        )
        collaboration_rows = _group_codex_turn_field(
            conn, "collaboration_mode", turn_where, turn_args
        )
    return {
        **turns, **usage, "agent_runs": agents,
        "tool_calls": tools[0] or 0, "tool_errors": tools[1] or 0,
        "efforts": {row[0]: row[1] for row in effort_rows},
        "approval_policies": {row[0]: row[1] for row in approval_rows},
        "sandbox_policies": {row[0]: row[1] for row in sandbox_rows},
        "collaboration_modes": {row[0]: row[1] for row in collaboration_rows},
    }


def _group_codex_turn_field(conn, field: str, where: str, args: list) -> list:
    if field not in {"sandbox_policy", "collaboration_mode"}:
        raise ValueError("unsupported Codex grouping field")
    return conn.execute(f"""
      SELECT COALESCE({field}, 'unknown'), COUNT(*)
        FROM codex_turns WHERE 1=1 {where}
       GROUP BY {field} ORDER BY COUNT(*) DESC
    """, args).fetchall()


def codex_rate_limit_history(db_path, limit: int = 100) -> list:
    from .db import connect

    with connect(db_path) as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM codex_rate_limits ORDER BY timestamp DESC LIMIT ?", (limit,)
        )]
