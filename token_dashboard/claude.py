"""Claude Code source pipeline."""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Tuple

from .pipeline import PipelineOutput
from .platforms import tool_kind


_TARGET_FIELDS = {
    "Read": "file_path", "Edit": "file_path", "Write": "file_path",
    "Glob": "pattern", "Grep": "pattern", "Bash": "command",
    "WebFetch": "url", "WebSearch": "query", "Task": "subagent_type",
    "Skill": "skill",
}

# Claude Code transcripts do not record a context-window size, so it is
# inferred from the model and the observed prompt load. Claude exposes a
# standard 200K window and an opt-in 1M beta window (Sonnet), with nothing in
# between — so a turn whose prompt (new input + cache reads) exceeds 200K can
# only have run under the 1M window. Inferring the window per turn lets
# peak-context utilization work for Claude the way it does for Codex (which
# reports the window directly) without ever exceeding 100%.
_CLAUDE_CONTEXT_WINDOW = 200_000
_CLAUDE_CONTEXT_WINDOW_1M = 1_000_000


def _context_window(model: Optional[str], prompt_load: int) -> Optional[int]:
    if not model:
        return None
    return _CLAUDE_CONTEXT_WINDOW_1M if prompt_load > _CLAUDE_CONTEXT_WINDOW else _CLAUDE_CONTEXT_WINDOW


def _usage(rec: dict) -> dict:
    usage = (rec.get("message") or {}).get("usage") or {}
    cache = usage.get("cache_creation") or {}
    return {
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "cache_read_tokens": int(usage.get("cache_read_input_tokens") or 0),
        "cache_create_5m_tokens": int(cache.get("ephemeral_5m_input_tokens") or 0),
        "cache_create_1h_tokens": int(cache.get("ephemeral_1h_input_tokens") or 0),
        "reasoning_output_tokens": 0,
    }


def _text(rec: dict, record_type: str) -> Optional[str]:
    if rec.get("type") != record_type:
        return None
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        return content or None
    if isinstance(content, list):
        parts = [block.get("text", "") for block in content
                 if isinstance(block, dict) and block.get("type") == "text"]
        separator = "" if record_type == "user" else "\n"
        text = separator.join(part for part in parts if part)
        return text or None
    return None


def _target(name: str, value: dict) -> Optional[str]:
    field = _TARGET_FIELDS.get(name)
    target = value.get(field) if field and isinstance(value, dict) else None
    return target[:500] if isinstance(target, str) else None


def _tools(rec: dict) -> List[dict]:
    rows = []
    content = (rec.get("message") or {}).get("content")
    if not isinstance(content, list):
        return rows
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_use":
            name = block.get("name") or "unknown"
            rows.append({
                "tool_name": name, "target": _target(name, block.get("input") or {}),
                "result_tokens": None, "is_error": 0, "call_id": block.get("id"),
                "tool_kind": tool_kind(name), "timestamp": rec.get("timestamp"),
            })
        elif block.get("type") == "tool_result":
            body = block.get("content")
            if isinstance(body, str):
                chars = len(body)
            elif isinstance(body, list):
                chars = sum(len(part.get("text", "")) for part in body if isinstance(part, dict))
            else:
                chars = 0
            rows.append({
                "tool_name": "_tool_result", "target": block.get("tool_use_id"),
                "result_tokens": chars // 4, "is_error": 1 if block.get("is_error") else 0,
                "call_id": block.get("tool_use_id"), "tool_kind": "result",
                "timestamp": rec.get("timestamp"),
            })
    return rows


def parse_record(rec: dict, project_slug: str, source: str = "claude") -> Tuple[dict, List[dict]]:
    """Compatibility parser returning canonical message and tool rows."""
    msg_obj = rec.get("message") or {}
    prompt = _text(rec, "user")
    usage = _usage(rec)
    prompt_load = usage["input_tokens"] + usage["cache_read_tokens"]
    msg = {
        "uuid": rec.get("uuid"), "parent_uuid": rec.get("parentUuid"),
        "session_id": rec.get("sessionId"), "project_slug": project_slug,
        "cwd": rec.get("cwd"), "git_branch": rec.get("gitBranch"),
        "cc_version": rec.get("version"), "entrypoint": rec.get("entrypoint"),
        "type": rec.get("type"), "is_sidechain": 1 if rec.get("isSidechain") else 0,
        "agent_id": rec.get("agentId"), "agent_type": None, "agent_name": None,
        "parent_session_id": None, "timestamp": rec.get("timestamp"),
        "model": msg_obj.get("model"), "stop_reason": msg_obj.get("stop_reason"),
        "prompt_id": rec.get("promptId"), "message_id": msg_obj.get("id"),
        "prompt_text": prompt, "prompt_chars": len(prompt) if prompt else None,
        "response_text": _text(rec, "assistant"), "source_metadata_json": None,
        "tool_calls_json": None,
        "context_window": _context_window(msg_obj.get("model"), prompt_load),
        "source": source,
        **usage,
    }
    tools = _tools(rec)
    if tools:
        msg["tool_calls_json"] = json.dumps([
            {"name": tool["tool_name"], "target": tool["target"]}
            for tool in tools if tool["tool_name"] != "_tool_result"
        ])
    for tool in tools:
        tool.update(
            message_uuid=msg["uuid"], session_id=msg["session_id"],
            project_slug=project_slug, source=source,
        )
    return msg, tools


def _project_slug(path: Path, root: Path) -> str:
    return path.relative_to(root).parts[0]


def ingest_agent_meta(conn, root: Path, source: str = "claude") -> int:
    """Enrich Claude sidechain identities from immutable meta sidecars."""
    ingested = 0
    for path in root.rglob("agent-*.meta.json"):
        if path.parent.name != "subagents":
            continue
        agent_id = path.name[len("agent-"):-len(".meta.json")]
        session_id = path.parent.parent.name
        existing = conn.execute(
            "SELECT agent_type FROM agents WHERE source=? AND session_id=? AND agent_id=?",
            (source, session_id, agent_id),
        ).fetchone()
        if existing and existing["agent_type"]:
            continue
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(meta, dict):
            continue
        conn.execute(
            "INSERT INTO agents "
            "(source, session_id, agent_id, project_slug, agent_type, description, tool_use_id, spawn_depth) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(source, session_id, agent_id) DO UPDATE SET "
            "project_slug=excluded.project_slug, agent_type=excluded.agent_type, "
            "description=excluded.description, tool_use_id=excluded.tool_use_id, spawn_depth=excluded.spawn_depth",
            (source, session_id, agent_id, _project_slug(path, root), meta.get("agentType"),
             meta.get("description"), meta.get("toolUseId"), meta.get("spawnDepth")),
        )
        ingested += 1
    return ingested


class ClaudePipeline:
    source = "claude"
    replay_changed_files = False
    features = ("messages", "tools", "cache", "sidechain-agents", "skills")

    def new_context(self) -> dict:
        return {}

    def accepts(self, record: dict) -> bool:
        return bool(record.get("uuid"))

    def parse(self, record: dict, fallback_slug: str, context: dict, offset: int) -> PipelineOutput:
        message, tools = parse_record(record, fallback_slug, source=self.source)
        return PipelineOutput(message=message, tools=tools)

    def persist_updates(self, conn, updates: List[dict]) -> None:
        return None

    def after_scan(self, conn, root: Path) -> None:
        ingest_agent_meta(conn, root, source=self.source)
