"""SQLite schema, connection, and shared query helpers."""
from __future__ import annotations

import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Union

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
  source      TEXT    NOT NULL DEFAULT 'claude',
  path        TEXT    NOT NULL,
  mtime       REAL    NOT NULL,
  bytes_read  INTEGER NOT NULL,
  scanned_at  REAL    NOT NULL,
  PRIMARY KEY (source, path)
);

CREATE TABLE IF NOT EXISTS messages (
  uuid                    TEXT PRIMARY KEY,
  parent_uuid             TEXT,
  session_id              TEXT NOT NULL,
  project_slug            TEXT NOT NULL,
  cwd                     TEXT,
  git_branch              TEXT,
  cc_version              TEXT,
  entrypoint              TEXT,
  type                    TEXT NOT NULL,
  is_sidechain            INTEGER NOT NULL DEFAULT 0,
  agent_id                TEXT,
  agent_type              TEXT,
  agent_name              TEXT,
  parent_session_id       TEXT,
  timestamp               TEXT NOT NULL,
  model                   TEXT,
  stop_reason             TEXT,
  prompt_id               TEXT,
  message_id              TEXT,
  input_tokens            INTEGER NOT NULL DEFAULT 0,
  output_tokens           INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens       INTEGER NOT NULL DEFAULT 0,
  cache_create_5m_tokens  INTEGER NOT NULL DEFAULT 0,
  cache_create_1h_tokens  INTEGER NOT NULL DEFAULT 0,
  reasoning_output_tokens INTEGER NOT NULL DEFAULT 0,
  context_window          INTEGER,
  prompt_text             TEXT,
  prompt_chars            INTEGER,
  response_text           TEXT,
  source_metadata_json    TEXT,
  tool_calls_json         TEXT,
  source                  TEXT NOT NULL DEFAULT 'claude'
);
CREATE INDEX IF NOT EXISTS idx_messages_session   ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_project   ON messages(project_slug);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_model     ON messages(model);
CREATE INDEX IF NOT EXISTS idx_messages_msgid     ON messages(session_id, message_id);
CREATE INDEX IF NOT EXISTS idx_messages_source    ON messages(source);

CREATE TABLE IF NOT EXISTS tool_calls (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  message_uuid  TEXT    NOT NULL,
  session_id    TEXT    NOT NULL,
  project_slug  TEXT    NOT NULL,
  tool_name     TEXT    NOT NULL,
  target        TEXT,
  result_tokens INTEGER,
  is_error      INTEGER NOT NULL DEFAULT 0,
  call_id       TEXT,
  tool_kind     TEXT,
  timestamp     TEXT    NOT NULL,
  source        TEXT    NOT NULL DEFAULT 'claude'
);
CREATE INDEX IF NOT EXISTS idx_tools_session ON tool_calls(session_id);
CREATE INDEX IF NOT EXISTS idx_tools_name    ON tool_calls(tool_name);
CREATE INDEX IF NOT EXISTS idx_tools_target  ON tool_calls(target);
CREATE INDEX IF NOT EXISTS idx_tools_source  ON tool_calls(source);

CREATE TABLE IF NOT EXISTS plan (
  k TEXT PRIMARY KEY,
  v TEXT
);

CREATE TABLE IF NOT EXISTS platform_settings (
  source        TEXT PRIMARY KEY,
  enabled       INTEGER NOT NULL DEFAULT 1,
  configured    INTEGER NOT NULL DEFAULT 0,
  plan          TEXT NOT NULL DEFAULT 'api',
  scan_root     TEXT,
  display_order INTEGER NOT NULL DEFAULT 0,
  updated_at    REAL
);

CREATE TABLE IF NOT EXISTS dismissed_tips (
  tip_key       TEXT PRIMARY KEY,
  dismissed_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
  source       TEXT NOT NULL DEFAULT 'claude',
  session_id   TEXT NOT NULL,
  agent_id     TEXT NOT NULL,
  project_slug TEXT NOT NULL,
  agent_type   TEXT,
  description  TEXT,
  tool_use_id  TEXT,
  spawn_depth  INTEGER,
  parent_session_id TEXT,
  agent_name   TEXT,
  PRIMARY KEY (source, session_id, agent_id)
);
CREATE INDEX IF NOT EXISTS idx_agents_type ON agents(agent_type);
CREATE INDEX IF NOT EXISTS idx_agents_parent ON agents(source, parent_session_id);
"""


def default_db_path() -> Path:
    return Path.home() / ".codex" / "token-dashboard.db"


def init_db(path: Union[str, Path]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    try:
        _migrate_source_file_cursors(c)
        _migrate_add_message_id(c)
        _migrate_add_source(c)
        _migrate_add_platform_metrics(c)
        c.executescript(SCHEMA)
        # Provider-owned schemas stay with their pipelines; the global DB
        # initializer only invokes their explicit setup hooks.
        from .codex_store import init_codex_schema
        init_codex_schema(c)
        from .providers import ensure_platform_rows
        ensure_platform_rows(c)
    finally:
        c.close()


def _migrate_add_message_id(conn) -> None:
    """Add messages.message_id for streaming-snapshot dedup.

    Why: pre-migration rows were summed from all streaming snapshots (over-count).
    How to apply: if the old table exists without the column, add it and clear
    messages/tool_calls/files so the next scan replays JSONLs cleanly. Source
    of truth is on disk; rescanning is cheap.
    """
    has_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='messages'"
    ).fetchone()
    if not has_table:
        return
    cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
    if "message_id" in cols:
        return
    conn.execute("ALTER TABLE messages ADD COLUMN message_id TEXT")
    conn.execute("DELETE FROM messages")
    conn.execute("DELETE FROM tool_calls")
    conn.execute("DELETE FROM files")
    conn.commit()


def _migrate_add_source(conn) -> None:
    """Add source columns so Claude and Codex transcripts can coexist."""
    for table in ("messages", "tool_calls"):
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not has_table:
            continue
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if "source" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN source TEXT NOT NULL DEFAULT 'claude'")
    conn.commit()


def _migrate_source_file_cursors(conn) -> None:
    """Scope file high-water marks by transcript source.

    Older databases keyed ``files`` only by path. Preserve those cursors as
    Claude cursors and recreate the table with a composite primary key so a
    wrong-source scan cannot suppress a later correct-source scan.
    """
    has_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='files'"
    ).fetchone()
    if not has_table:
        return

    info = list(conn.execute("PRAGMA table_info(files)"))
    cols = {row[1] for row in info}
    pk_cols = [row[1] for row in sorted((row for row in info if row[5]), key=lambda row: row[5])]
    if "source" in cols and pk_cols == ["source", "path"]:
        return

    conn.execute("""
      CREATE TABLE files_new (
        source      TEXT    NOT NULL DEFAULT 'claude',
        path        TEXT    NOT NULL,
        mtime       REAL    NOT NULL,
        bytes_read  INTEGER NOT NULL,
        scanned_at  REAL    NOT NULL,
        PRIMARY KEY (source, path)
      )
    """)
    if "source" in cols:
        conn.execute("""
          INSERT OR REPLACE INTO files_new (source, path, mtime, bytes_read, scanned_at)
          SELECT COALESCE(NULLIF(source, ''), 'claude'), path, mtime, bytes_read, scanned_at
            FROM files
        """)
    else:
        conn.execute("""
          INSERT OR REPLACE INTO files_new (source, path, mtime, bytes_read, scanned_at)
          SELECT 'claude', path, mtime, bytes_read, scanned_at
            FROM files
        """)
    conn.execute("DROP TABLE files")
    conn.execute("ALTER TABLE files_new RENAME TO files")
    conn.commit()


def _migrate_add_platform_metrics(conn) -> None:
    """Add canonical extension fields used by Codex and future sources.

    Source-native details stay in ``source_metadata_json`` while metrics that
    are useful across platforms get typed columns. Clearing file cursors makes
    the next scan backfill the new fields from the local source transcripts.
    """
    additions = {
        "messages": (
            ("reasoning_output_tokens", "INTEGER NOT NULL DEFAULT 0"),
            ("context_window", "INTEGER"),
            ("response_text", "TEXT"),
            ("source_metadata_json", "TEXT"),
            ("agent_type", "TEXT"),
            ("agent_name", "TEXT"),
            ("parent_session_id", "TEXT"),
        ),
        "tool_calls": (
            ("call_id", "TEXT"),
            ("tool_kind", "TEXT"),
        ),
        "agents": (
            ("parent_session_id", "TEXT"),
            ("agent_name", "TEXT"),
        ),
    }
    changed = False
    for table, columns in additions.items():
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            continue
        current = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, declaration in columns:
            if name not in current:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
                changed = True
    if changed and conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='files'"
    ).fetchone():
        conn.execute("DELETE FROM files")
    conn.commit()


@contextmanager
def connect(path: Union[str, Path]):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def _range_clause(since, until, col: str = "timestamp", source: Optional[str] = None, source_col: str = "source"):
    where, args = [], []
    if since:
        where.append(f"{col} >= ?"); args.append(since)
    if until:
        where.append(f"{col} < ?"); args.append(until)
    if isinstance(source, (list, tuple, set)):
        values = [str(value) for value in source if value]
        if values:
            where.append(f"{source_col} IN ({','.join('?' for _ in values)})")
            args.extend(values)
        else:
            where.append("1=0")
    elif source:
        where.append(f"{source_col} = ?"); args.append(source)
    return ((" AND " + " AND ".join(where)) if where else "", args)


def _encode_slug(path: str) -> str:
    """Claude Code's project-slug encoding: each of `:`, `\\`, `/`, space → one `-`."""
    return re.sub(r"[:\\/ ]", "-", path)


def _walk_to_root(cwd: str, slug: str) -> Optional[str]:
    """If any ancestor of cwd encodes to slug, return that ancestor's basename."""
    if not cwd or not slug:
        return None
    trimmed = cwd.rstrip("/\\")
    sep = "\\" if "\\" in trimmed else "/"
    parts = trimmed.split(sep)
    for i in range(len(parts), 0, -1):
        if _encode_slug(sep.join(parts[:i])) == slug:
            name = parts[i - 1]
            if name:
                return name
    return None


def project_name_for(cwd: Optional[str], fallback_slug: str) -> str:
    """Pretty project name from a single cwd + slug (best-effort).

    For the multi-cwd case, prefer `best_project_name`.
    """
    name = _walk_to_root(cwd or "", fallback_slug or "")
    if name:
        return name
    if cwd:
        trimmed = cwd.rstrip("/\\")
        sep = "\\" if "\\" in trimmed else "/"
        tail = trimmed.split(sep)[-1]
        if tail:
            return tail
    if fallback_slug:
        parts = [p for p in re.split(r"-+", fallback_slug) if p]
        if parts:
            return parts[-1]
    return fallback_slug or ""


def best_project_name(cwds, slug: str) -> str:
    """Pick a pretty name from a list of cwds.

    Prefer a cwd whose walk-up matches `slug` (a true descendant of the project
    root). If none match, fall back to `project_name_for` on the first cwd,
    then to the slug's last segment.
    """
    cwds = [c for c in (cwds or []) if c]
    for cwd in cwds:
        name = _walk_to_root(cwd, slug)
        if name:
            return name
    return project_name_for(cwds[0] if cwds else None, slug)


def overview_totals(db_path, since=None, until=None, source=None) -> dict:
    rng, args = _range_clause(since, until, source=source)
    sql = f"""
      SELECT COUNT(DISTINCT source || ':' || session_id) AS sessions,
             SUM(CASE WHEN type='user' THEN 1 ELSE 0 END) AS turns,
             COALESCE(SUM(input_tokens),0)            AS input_tokens,
             COALESCE(SUM(output_tokens),0)           AS output_tokens,
             COALESCE(SUM(cache_read_tokens),0)       AS cache_read_tokens,
             COALESCE(SUM(cache_create_5m_tokens),0)  AS cache_create_5m_tokens,
             COALESCE(SUM(cache_create_1h_tokens),0)  AS cache_create_1h_tokens,
             COALESCE(SUM(reasoning_output_tokens),0) AS reasoning_output_tokens,
             COUNT(CASE WHEN type='assistant' THEN 1 END) AS model_calls,
             COALESCE(MAX(CASE WHEN context_window > 0
               THEN 1.0 * (input_tokens + cache_read_tokens) / context_window END), 0)
               AS peak_context_utilization
        FROM messages WHERE 1=1 {rng}
    """
    with connect(db_path) as c:
        return dict(c.execute(sql, args).fetchone())


def expensive_prompts(db_path, limit: int = 50, sort: str = "tokens", source=None) -> list:
    """User prompt joined with the immediately-following assistant turn's tokens.

    sort="tokens" (default) → largest billable first.
    sort="recent"           → newest first.
    """
    order = "u.timestamp DESC" if sort == "recent" else "billable_tokens DESC"
    user_source, user_args = _range_clause(None, None, source=source, source_col="u.source")
    assistant_source, assistant_args = _range_clause(None, None, source=source, source_col="a.source")
    sql = f"""
       SELECT u.uuid AS user_uuid, u.session_id, u.project_slug, u.source, u.timestamp,
              u.prompt_text, u.prompt_chars,
              MIN(a.uuid) AS assistant_uuid,
              GROUP_CONCAT(DISTINCT a.model) AS model,
              COUNT(a.uuid) AS model_calls,
              COALESCE(SUM(a.input_tokens),0) AS input_tokens,
              COALESCE(SUM(a.output_tokens),0) AS output_tokens,
              COALESCE(SUM(a.cache_create_5m_tokens),0) AS cache_create_5m_tokens,
              COALESCE(SUM(a.cache_create_1h_tokens),0) AS cache_create_1h_tokens,
              COALESCE(SUM(a.input_tokens),0)+COALESCE(SUM(a.output_tokens),0)
               +COALESCE(SUM(a.cache_create_5m_tokens),0)+COALESCE(SUM(a.cache_create_1h_tokens),0) AS billable_tokens,
              COALESCE(SUM(a.cache_read_tokens),0) AS cache_read_tokens,
              COALESCE(SUM(a.reasoning_output_tokens),0) AS reasoning_output_tokens,
              GROUP_CONCAT(NULLIF(a.response_text, ''), '\n') AS response_text
        FROM messages u
        JOIN messages a ON a.parent_uuid = u.uuid AND a.type='assistant'
       WHERE u.type='user' AND u.prompt_text IS NOT NULL
         {user_source} {assistant_source}
       GROUP BY u.uuid, u.session_id, u.project_slug, u.source, u.timestamp,
                u.prompt_text, u.prompt_chars
       ORDER BY {order}
       LIMIT ?
    """
    with connect(db_path) as c:
        return [dict(r) for r in c.execute(sql, (*user_args, *assistant_args, limit))]


def project_summary(db_path, since=None, until=None, source=None) -> list:
    rng, args = _range_clause(since, until, source=source)
    sql = f"""
      SELECT project_slug,
             COUNT(DISTINCT source || ':' || session_id) AS sessions,
             SUM(CASE WHEN type='user' THEN 1 ELSE 0 END) AS turns,
             COALESCE(SUM(input_tokens), 0)  AS input_tokens,
             COALESCE(SUM(output_tokens), 0) AS output_tokens,
             SUM(input_tokens)+SUM(output_tokens)
               +SUM(cache_create_5m_tokens)+SUM(cache_create_1h_tokens) AS billable_tokens,
             SUM(cache_read_tokens) AS cache_read_tokens
        FROM messages m
       WHERE 1=1 {rng}
       GROUP BY project_slug
       ORDER BY billable_tokens DESC
    """
    with connect(db_path) as c:
        rows = [dict(r) for r in c.execute(sql, args)]
        cwd_source, cwd_args = _range_clause(None, None, source=source)
        for r in rows:
            cwds = [row["cwd"] for row in c.execute(
                f"SELECT DISTINCT cwd FROM messages WHERE project_slug=? AND cwd IS NOT NULL {cwd_source}",
                (r["project_slug"], *cwd_args),
            )]
            r["project_name"] = best_project_name(cwds, r["project_slug"])
    return rows


def tool_token_breakdown(db_path, since=None, until=None, source=None) -> list:
    rng, args = _range_clause(since, until, source=source)
    sql = f"""
      SELECT tool_name,
             COUNT(*) AS calls,
             COALESCE(SUM(result_tokens),0) AS result_tokens,
             SUM(CASE WHEN is_error=1 THEN 1 ELSE 0 END) AS errors,
             COALESCE(tool_kind, 'other') AS tool_kind
        FROM tool_calls
       WHERE tool_name != '_tool_result' {rng}
       GROUP BY tool_name, COALESCE(tool_kind, 'other')
       ORDER BY calls DESC
    """
    with connect(db_path) as c:
        return [dict(r) for r in c.execute(sql, args)]


def recent_sessions(db_path, limit: int = 20, since=None, until=None, source=None) -> list:
    rng, args = _range_clause(since, until, source=source)
    sql = f"""
      SELECT source, session_id, project_slug,
             MIN(timestamp) AS started, MAX(timestamp) AS ended,
             SUM(CASE WHEN type='user' THEN 1 ELSE 0 END) AS turns,
             SUM(input_tokens)+SUM(output_tokens) AS tokens,
             SUM(reasoning_output_tokens) AS reasoning_output_tokens,
             COUNT(CASE WHEN type='assistant' THEN 1 END) AS model_calls,
             COALESCE(MAX(CASE WHEN context_window > 0
               THEN 1.0 * (input_tokens + cache_read_tokens) / context_window END), 0)
               AS peak_context_utilization
        FROM messages m
       WHERE 1=1 {rng}
       GROUP BY source, session_id
       ORDER BY ended DESC
       LIMIT ?
    """
    with connect(db_path) as c:
        rows = [dict(r) for r in c.execute(sql, (*args, limit))]
        # Cache per-slug name lookups so we don't query once per session.
        slug_cache = {}
        for r in rows:
            slug = r["project_slug"]
            cache_key = (r["source"], slug)
            if cache_key not in slug_cache:
                # Each grouped row already carries a single concrete source, so
                # scope the name lookup to that row's source. Using the request
                # `source` directly would bind a list here when source="all".
                cwds = [row["cwd"] for row in c.execute(
                    "SELECT DISTINCT cwd FROM messages WHERE project_slug=? AND cwd IS NOT NULL AND source=?",
                    (slug, r["source"]),
                )]
                slug_cache[cache_key] = best_project_name(cwds, slug)
            r["project_name"] = slug_cache[cache_key]
    return rows


def session_turns(db_path, session_id: str, source=None, agent_id=None) -> list:
    sql = """
      SELECT uuid, parent_uuid, type, timestamp, model, is_sidechain, agent_id,
             agent_type, agent_name, parent_session_id,
             input_tokens, output_tokens, cache_read_tokens,
              cache_create_5m_tokens, cache_create_1h_tokens,
              reasoning_output_tokens, context_window,
              prompt_text, prompt_chars, response_text, source_metadata_json,
              tool_calls_json, project_slug, cwd, source
        FROM messages
       WHERE session_id = ? AND (? IS NULL OR source = ?)
         AND (? IS NULL OR agent_id = ?)
       ORDER BY timestamp ASC
    """
    with connect(db_path) as c:
        return [dict(r) for r in c.execute(sql, (session_id, source, source, agent_id, agent_id))]


def daily_token_breakdown(db_path, since=None, until=None, source=None) -> list:
    """One row per day: stacked bar data for input/output/cache_read/cache_create."""
    rng, args = _range_clause(since, until, source=source)
    sql = f"""
      SELECT substr(timestamp, 1, 10) AS day,
             COALESCE(SUM(input_tokens),0)      AS input_tokens,
             COALESCE(SUM(output_tokens),0)     AS output_tokens,
             COALESCE(SUM(cache_read_tokens),0) AS cache_read_tokens,
             COALESCE(SUM(cache_create_5m_tokens),0)
               + COALESCE(SUM(cache_create_1h_tokens),0) AS cache_create_tokens,
             COALESCE(SUM(reasoning_output_tokens),0) AS reasoning_output_tokens
        FROM messages
       WHERE timestamp IS NOT NULL {rng}
       GROUP BY day
       ORDER BY day ASC
    """
    with connect(db_path) as c:
        return [dict(r) for r in c.execute(sql, args)]


def skill_breakdown(db_path, since=None, until=None, source=None) -> list:
    """Per-skill invocation counts, distinct sessions, last-used timestamp.

    Token attribution per skill is not included: in Claude Code, a Skill's
    content is loaded via a system-reminder on the next turn, not as the
    tool_result body — so `result_tokens` on _tool_result rows reflects the
    activation ack (tiny), not the skill definition (which is what actually
    fills context). A future schema change (storing tool_use_id on the
    invocation row) could enable precise attribution; for now we only expose
    the reliable counts.
    """
    rng, args = _range_clause(since, until, source=source)
    sql = f"""
      SELECT target AS skill,
             COUNT(*) AS invocations,
             COUNT(DISTINCT source || ':' || session_id) AS sessions,
             MAX(timestamp) AS last_used
        FROM tool_calls
       WHERE tool_name = 'Skill' AND target IS NOT NULL AND target != '' {rng}
       GROUP BY target
       ORDER BY invocations DESC
    """
    with connect(db_path) as c:
        return [dict(r) for r in c.execute(sql, args)]


def agent_breakdown(db_path, since=None, until=None, source=None) -> list:
    """Per-agent-role usage for Claude sidecars and Codex child threads."""
    rng, args = _range_clause(since, until, source=source)
    sql = f"""
      WITH attributed AS (
        SELECT COALESCE(a.agent_type, a.agent_name, '(unknown)') AS agent_type, m.*
          FROM agents a
          JOIN messages m ON m.source=a.source AND m.session_id=a.session_id AND m.agent_id=a.agent_id
        UNION ALL
        SELECT COALESCE(m.agent_type, m.agent_name, m.agent_id, '(unknown)') AS agent_type, m.*
          FROM messages m
         WHERE m.is_sidechain=1
           AND NOT EXISTS (
             SELECT 1 FROM agents a
              WHERE a.source=m.source AND a.session_id=m.session_id AND a.agent_id=m.agent_id
           )
      )
      SELECT agent_type,
             COALESCE(model, 'unknown') AS model,
             COUNT(DISTINCT source || ':' || session_id || ':' || COALESCE(agent_id,'')) AS runs,
             COUNT(DISTINCT source || ':' || session_id) AS sessions,
             COALESCE(SUM(input_tokens),0)           AS input_tokens,
             COALESCE(SUM(output_tokens),0)          AS output_tokens,
             COALESCE(SUM(cache_read_tokens),0)      AS cache_read_tokens,
             COALESCE(SUM(cache_create_5m_tokens),0) AS cache_create_5m_tokens,
             COALESCE(SUM(cache_create_1h_tokens),0) AS cache_create_1h_tokens,
             COALESCE(SUM(reasoning_output_tokens),0) AS reasoning_output_tokens,
             COALESCE(SUM(input_tokens + output_tokens + cache_read_tokens
               + cache_create_5m_tokens + cache_create_1h_tokens),0) AS total_tokens,
             MAX(timestamp) AS last_used
        FROM attributed
       WHERE model IS NOT NULL {rng}
       GROUP BY agent_type, model
       ORDER BY (SUM(input_tokens) + SUM(output_tokens) + SUM(cache_read_tokens)
                 + SUM(cache_create_5m_tokens) + SUM(cache_create_1h_tokens)) DESC
    """
    with connect(db_path) as c:
        return [dict(r) for r in c.execute(sql, args)]


def agent_run_breakdown(db_path, since=None, until=None, source=None) -> list:
    """Token totals for every individual agent identity, split by model."""
    rng, args = _range_clause(
        since, until, col="m.timestamp", source=source, source_col="m.source"
    )
    sql = f"""
      WITH identities AS (
        SELECT source, session_id, agent_id, project_slug, agent_type, agent_name,
               parent_session_id, description, spawn_depth
          FROM agents
        UNION ALL
        SELECT DISTINCT m.source, m.session_id, m.agent_id, m.project_slug,
               m.agent_type, m.agent_name, m.parent_session_id, NULL, NULL
          FROM messages m
         WHERE m.is_sidechain=1 AND m.agent_id IS NOT NULL
           AND NOT EXISTS (
             SELECT 1 FROM agents a
              WHERE a.source=m.source AND a.session_id=m.session_id AND a.agent_id=m.agent_id
           )
      )
      SELECT a.source, a.session_id, a.agent_id,
             COALESCE(a.agent_type, a.agent_name, '(unknown)') AS agent_type,
             a.agent_name, a.parent_session_id, a.description, a.spawn_depth,
             m.project_slug, COALESCE(m.model, 'unknown') AS model,
             MIN(m.timestamp) AS started, MAX(m.timestamp) AS ended,
             COUNT(*) AS model_calls,
             COALESCE(SUM(m.input_tokens),0) AS input_tokens,
             COALESCE(SUM(m.output_tokens),0) AS output_tokens,
             COALESCE(SUM(m.cache_read_tokens),0) AS cache_read_tokens,
             COALESCE(SUM(m.cache_create_5m_tokens),0) AS cache_create_5m_tokens,
             COALESCE(SUM(m.cache_create_1h_tokens),0) AS cache_create_1h_tokens,
             COALESCE(SUM(m.reasoning_output_tokens),0) AS reasoning_output_tokens,
             COALESCE(SUM(m.input_tokens + m.output_tokens + m.cache_read_tokens
               + m.cache_create_5m_tokens + m.cache_create_1h_tokens),0) AS total_tokens,
             COALESCE(MAX(CASE WHEN m.context_window > 0
               THEN 1.0 * (m.input_tokens + m.cache_read_tokens) / m.context_window END), 0)
               AS peak_context_utilization
        FROM identities a
        JOIN messages m
          ON m.source=a.source AND m.session_id=a.session_id AND m.agent_id=a.agent_id
       WHERE m.type='assistant' {rng}
       GROUP BY a.source, a.session_id, a.agent_id, a.agent_type, a.agent_name,
                a.parent_session_id, a.description, a.spawn_depth, m.project_slug, m.model
       ORDER BY ended DESC
    """
    with connect(db_path) as c:
        return [dict(r) for r in c.execute(sql, args)]


def model_breakdown(db_path, since=None, until=None, source=None) -> list:
    """Per-model token totals + turn count. Caller computes cost via pricing."""
    rng, args = _range_clause(since, until, source=source)
    sql = f"""
      SELECT source, COALESCE(model, 'unknown') AS model,
             COUNT(*) AS turns,
             COALESCE(SUM(input_tokens),0)            AS input_tokens,
             COALESCE(SUM(output_tokens),0)           AS output_tokens,
             COALESCE(SUM(cache_read_tokens),0)       AS cache_read_tokens,
             COALESCE(SUM(cache_create_5m_tokens),0)  AS cache_create_5m_tokens,
             COALESCE(SUM(cache_create_1h_tokens),0)  AS cache_create_1h_tokens
             ,COALESCE(SUM(reasoning_output_tokens),0) AS reasoning_output_tokens
             ,COALESCE(MAX(CASE WHEN context_window > 0
               THEN 1.0 * (input_tokens + cache_read_tokens) / context_window END), 0)
               AS peak_context_utilization
        FROM messages
       WHERE type = 'assistant' {rng}
       GROUP BY source, model
       ORDER BY (input_tokens + output_tokens + cache_create_5m_tokens + cache_create_1h_tokens) DESC
    """
    with connect(db_path) as c:
        return [dict(r) for r in c.execute(sql, args)]


def source_summary(db_path, since=None, until=None, source=None) -> list:
    rng, args = _range_clause(since, until, col="m.timestamp", source=source, source_col="m.source")
    tool_rng, tool_args = _range_clause(since, until, source=source)
    sql = f"""
      WITH tool_stats AS (
        SELECT source, COUNT(*) AS tool_calls,
               SUM(CASE WHEN is_error=1 THEN 1 ELSE 0 END) AS tool_errors
          FROM tool_calls
         WHERE 1=1 {tool_rng}
         GROUP BY source
      )
      SELECT m.source,
             COUNT(DISTINCT m.source || ':' || m.session_id) AS sessions,
             SUM(CASE WHEN m.type='user' THEN 1 ELSE 0 END) AS turns,
             COALESCE(SUM(input_tokens),0) AS input_tokens,
             COALESCE(SUM(output_tokens),0) AS output_tokens,
             COALESCE(SUM(cache_read_tokens),0) AS cache_read_tokens,
             COALESCE(SUM(cache_create_5m_tokens),0) AS cache_create_5m_tokens,
             COALESCE(SUM(cache_create_1h_tokens),0) AS cache_create_1h_tokens,
             SUM(CASE WHEN m.type='assistant' AND cache_read_tokens > 0 THEN 1 ELSE 0 END)
               AS cache_read_events,
             SUM(CASE WHEN m.type='assistant' AND
               (cache_create_5m_tokens > 0 OR cache_create_1h_tokens > 0) THEN 1 ELSE 0 END)
               AS cache_create_events,
             COALESCE(SUM(input_tokens),0) + COALESCE(SUM(output_tokens),0)
               + COALESCE(SUM(cache_read_tokens),0)
               + COALESCE(SUM(cache_create_5m_tokens),0)
               + COALESCE(SUM(cache_create_1h_tokens),0) AS tokens,
             COALESCE(SUM(reasoning_output_tokens),0) AS reasoning_output_tokens,
             COUNT(CASE WHEN type='assistant' THEN 1 END) AS model_calls,
             COALESCE(MAX(CASE WHEN context_window > 0
               THEN 1.0 * (input_tokens + cache_read_tokens) / context_window END), 0)
               AS peak_context_utilization,
             COALESCE(MAX(ts.tool_calls),0) AS tool_calls,
             COALESCE(MAX(ts.tool_errors),0) AS tool_errors
        FROM messages m
        LEFT JOIN tool_stats ts ON ts.source=m.source
       WHERE 1=1 {rng}
       GROUP BY m.source
       ORDER BY m.source ASC
    """
    with connect(db_path) as c:
        return [dict(r) for r in c.execute(sql, (*tool_args, *args))]
