"""JSONL transcript walker + parser."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Union

from .db import connect
from .claude import ClaudePipeline, parse_record
from .codex import CodexPipeline, parse_record as parse_codex_record
from .pipeline import get_pipeline, register_pipeline


register_pipeline(ClaudePipeline())
register_pipeline(CodexPipeline())


INSERT_MSG = """
INSERT OR REPLACE INTO messages (
  uuid, parent_uuid, session_id, project_slug, cwd, git_branch, cc_version, entrypoint,
  type, is_sidechain, agent_id, agent_type, agent_name, parent_session_id,
  timestamp, model, stop_reason, prompt_id, message_id,
  input_tokens, output_tokens, cache_read_tokens, cache_create_5m_tokens, cache_create_1h_tokens,
  reasoning_output_tokens, context_window, prompt_text, prompt_chars, response_text,
  source_metadata_json, tool_calls_json, source
) VALUES (
  :uuid, :parent_uuid, :session_id, :project_slug, :cwd, :git_branch, :cc_version, :entrypoint,
  :type, :is_sidechain, :agent_id, :agent_type, :agent_name, :parent_session_id,
  :timestamp, :model, :stop_reason, :prompt_id, :message_id,
  :input_tokens, :output_tokens, :cache_read_tokens, :cache_create_5m_tokens, :cache_create_1h_tokens,
  :reasoning_output_tokens, :context_window, :prompt_text, :prompt_chars, :response_text,
  :source_metadata_json, :tool_calls_json, :source
)
"""

INSERT_TOOL = """
INSERT INTO tool_calls (message_uuid, session_id, project_slug, tool_name, target, result_tokens,
  is_error, call_id, tool_kind, timestamp, source)
VALUES (:message_uuid, :session_id, :project_slug, :tool_name, :target, :result_tokens,
  :is_error, :call_id, :tool_kind, :timestamp, :source)
"""


def _project_slug(file_path: Path, projects_root: Path) -> str:
    rel = file_path.relative_to(projects_root)
    return rel.parts[0]


def _upsert_agent_identity(conn, msg: dict) -> None:
    """Persist every attributed agent, even when no provider sidecar exists."""
    if not msg.get("is_sidechain") or not msg.get("agent_id"):
        return
    conn.execute(
        "INSERT INTO agents "
        "(source, session_id, agent_id, project_slug, agent_type, parent_session_id, agent_name) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(source, session_id, agent_id) DO UPDATE SET "
        "project_slug=excluded.project_slug, "
        "agent_type=COALESCE(excluded.agent_type, agents.agent_type), "
        "parent_session_id=COALESCE(excluded.parent_session_id, agents.parent_session_id), "
        "agent_name=COALESCE(excluded.agent_name, agents.agent_name)",
        (
            msg["source"], msg["session_id"], msg["agent_id"], msg["project_slug"],
            msg.get("agent_type"), msg.get("parent_session_id"), msg.get("agent_name"),
        ),
    )


def _evict_prior_snapshots(conn, source: str, session_id: str, message_id: str, keep_uuid: str) -> None:
    """Remove older streaming snapshots for the same (session_id, message_id).

    Claude Code writes 2–3 JSONL lines per assistant response (partial → final)
    with identical message.id but distinct top-level uuids. Only the final
    tally matches billing, so earlier snapshots must be replaced, not summed.
    """
    old = [r[0] for r in conn.execute(
        "SELECT uuid FROM messages WHERE source=? AND session_id=? AND message_id=? AND uuid!=?",
        (source, session_id, message_id, keep_uuid),
    )]
    if not old:
        return
    placeholders = ",".join("?" * len(old))
    conn.execute(f"DELETE FROM tool_calls WHERE message_uuid IN ({placeholders})", old)
    conn.execute(f"DELETE FROM messages WHERE uuid IN ({placeholders})", old)


def scan_file(path: Path, project_slug: str, conn, start_byte: int = 0, source: str = "claude") -> dict:
    """Ingest new lines from a JSONL file starting at ``start_byte``.

    Returns message/tool counts plus ``end_offset`` — the byte offset just
    past the last fully-parsed line. Callers persist ``end_offset`` as the
    file's high-water mark so a line partially flushed at EOF gets re-read
    once it completes.
    """
    msgs = tools = 0
    end_offset = start_byte
    pipeline = get_pipeline(source)
    context = pipeline.new_context()
    with open(path, "rb") as fb:
        if start_byte:
            fb.seek(start_byte)
        while True:
            raw = fb.readline()
            if not raw:
                break  # EOF
            if not raw.endswith(b"\n"):
                # Partial line — Claude Code is mid-flush. Leave the
                # high-water mark behind the line start so we re-read it
                # once the write completes.
                break
            line_end = fb.tell()
            try:
                line = raw.decode("utf-8", errors="replace").strip()
            except Exception:
                end_offset = line_end
                continue
            if not line:
                end_offset = line_end
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                end_offset = line_end
                continue
            if not isinstance(rec, dict) or not pipeline.accepts(rec):
                end_offset = line_end
                continue
            output = pipeline.parse(rec, project_slug, context, line_end)
            pipeline.persist_updates(conn, output.updates)
            msg, tlist = output.message, output.tools
            if not msg:
                end_offset = line_end
                continue
            if not msg["session_id"] or not msg["timestamp"]:
                end_offset = line_end
                continue
            existed = conn.execute("SELECT 1 FROM messages WHERE uuid=?", (msg["uuid"],)).fetchone() is not None
            if msg["message_id"]:
                _evict_prior_snapshots(conn, msg["source"], msg["session_id"], msg["message_id"], msg["uuid"])
            conn.execute(INSERT_MSG, msg)
            _upsert_agent_identity(conn, msg)
            # tool_calls has no natural unique key; clear any prior rows for
            # this uuid so full rescans stay idempotent instead of
            # duplicating rows.
            conn.execute("DELETE FROM tool_calls WHERE message_uuid=?", (msg["uuid"],))
            for t in tlist:
                conn.execute(INSERT_TOOL, t)
                if not existed:
                    tools += 1
            if not existed:
                msgs += 1
            end_offset = line_end
    return {"messages": msgs, "tools": tools, "end_offset": end_offset}


def scan_dir(projects_root: Union[str, Path], db_path: Union[str, Path], source: str = "claude") -> dict:
    root = Path(projects_root)
    pipeline = get_pipeline(source)
    totals = {"messages": 0, "tools": 0, "files": 0}
    if not root.is_dir():
        return totals
    with connect(db_path) as conn:
        for p in root.rglob("*.jsonl"):
            try:
                stat = p.stat()
            except OSError:
                continue
            row = conn.execute(
                "SELECT mtime, bytes_read FROM files WHERE source=? AND path=?", (source, str(p))
            ).fetchone()
            offset = 0
            if row and row["mtime"] == stat.st_mtime and row["bytes_read"] == stat.st_size:
                continue
            if row and stat.st_size > row["bytes_read"]:
                offset = row["bytes_read"]
            if pipeline.replay_changed_files:
                # Event pipelines may depend on context from earlier records;
                # their deterministic IDs keep full replay idempotent.
                offset = 0
            slug = _project_slug(p, root)
            sub = scan_file(p, slug, conn, start_byte=offset, source=source)
            # Persist the byte offset of the last fully-parsed line (not
            # st_size) so a partial line mid-flush is retried on the next
            # scan instead of being skipped over.
            conn.execute(
                "INSERT OR REPLACE INTO files (source, path, mtime, bytes_read, scanned_at) VALUES (?, ?, ?, ?, ?)",
                (source, str(p), stat.st_mtime, sub["end_offset"], time.time()),
            )
            totals["messages"] += sub["messages"]
            totals["tools"]    += sub["tools"]
            totals["files"]    += 1
        pipeline.after_scan(conn, root)
        conn.commit()
    return totals
