"""Codex event-stream adapter for the canonical dashboard schema."""
from __future__ import annotations

import json
import re
from typing import List, Optional, Tuple

from .pipeline import PipelineOutput
from .platforms import tool_kind
from .codex_store import persist_codex_updates


def _json_value(value) -> Optional[str]:
    if value is None:
        return None
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True)


def _emit(context: dict, kind: str, **values) -> None:
    context.setdefault("_pipeline_updates", []).append({"kind": kind, **values})


def _empty_usage() -> dict:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_create_5m_tokens": 0,
        "cache_create_1h_tokens": 0,
        "reasoning_output_tokens": 0,
    }


def _target(name: str, raw_args) -> Optional[str]:
    if isinstance(raw_args, str):
        try:
            inp = json.loads(raw_args)
        except json.JSONDecodeError:
            return raw_args[:500]
    elif isinstance(raw_args, dict):
        inp = raw_args
    else:
        return None
    for field in ("path", "file_path", "command", "url", "query", "q", "code", "name"):
        value = inp.get(field)
        if isinstance(value, str) and value:
            return value[:500]
    return None


def _usage(payload: dict) -> dict:
    usage = ((payload.get("info") or {}).get("last_token_usage") or {})
    input_tokens = int(usage.get("input_tokens") or 0)
    cached_tokens = int(usage.get("cached_input_tokens") or 0)
    return {
        "input_tokens": max(0, input_tokens - cached_tokens),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "cache_read_tokens": cached_tokens,
        "cache_create_5m_tokens": 0,
        "cache_create_1h_tokens": 0,
        "reasoning_output_tokens": int(usage.get("reasoning_output_tokens") or 0),
    }


def _int_value(value) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _context_window_value(value) -> Optional[int]:
    if isinstance(value, dict):
        for key in ("max_input_tokens", "max_tokens", "window", "size", "tokens", "value"):
            coerced = _int_value(value.get(key))
            if coerced is not None:
                return coerced
        return None
    return _int_value(value)


def _slug_from_cwd(cwd: Optional[str], fallback: str) -> str:
    if not cwd:
        return fallback
    return cwd.replace(":", "-").replace("\\", "-").replace("/", "-").replace(" ", "-")


_EXEC_TOOL_RE = re.compile(r"\btools\.([A-Za-z_][A-Za-z0-9_]*)\s*\(")


def _call_rows(payload: dict, timestamp: Optional[str]) -> List[dict]:
    """Expand a Codex call into canonical tools.

    Desktop Codex can wrap several real calls in one ``exec`` custom call.
    Extracting nested names avoids reporting every operation as ``exec``.
    """
    name = payload.get("name") or "unknown"
    namespace = payload.get("namespace")
    raw_args = payload.get("arguments") if payload.get("type") == "function_call" else payload.get("input")
    names = []
    if payload.get("type") == "custom_tool_call" and name == "exec" and isinstance(raw_args, str):
        names = _EXEC_TOOL_RE.findall(raw_args)
    if not names:
        names = [f"{namespace}.{name}" if namespace else name]
    target = _target(name, raw_args)
    return [{
        "tool_name": tool_name,
        "target": target,
        "result_tokens": None,
        "is_error": 0,
        "call_id": payload.get("call_id"),
        "tool_kind": tool_kind(tool_name),
        "timestamp": timestamp,
        "source": "codex",
    } for tool_name in names]


def _attach_result(context: dict, call_id: Optional[str], body, failed: bool) -> None:
    chars = len(body) if isinstance(body, str) else len(json.dumps(body)) if body is not None else 0
    matches = [tool for tool in context.setdefault("pending_tools", [])
               if call_id and tool.get("call_id") == call_id]
    if matches:
        share = (chars // 4) // len(matches)
        for tool in matches:
            tool["result_tokens"] = share
            tool["is_error"] = 1 if failed else 0
        return
    context["pending_tools"].append({
        "tool_name": "_tool_result",
        "target": call_id,
        "result_tokens": chars // 4,
        "is_error": 1 if failed else 0,
        "call_id": call_id,
        "tool_kind": "result",
        "timestamp": None,
        "source": "codex",
    })


def parse_record(rec: dict, fallback_slug: str, context: dict, offset: int) -> Tuple[Optional[dict], List[dict]]:
    """Normalize one Codex event record into the shared row shape."""
    payload = rec.get("payload") or {}
    timestamp = rec.get("timestamp") or payload.get("timestamp")
    top_type = rec.get("type")
    payload_type = payload.get("type")

    if top_type == "session_meta":
        context["session_id"] = payload.get("session_id") or payload.get("id") or context.get("session_id")
        context["cwd"] = payload.get("cwd") or context.get("cwd")
        context["version"] = payload.get("cli_version") or context.get("version")
        for key in ("model_provider", "originator", "thread_source", "parent_thread_id",
                    "agent_role", "agent_nickname", "context_window"):
            value = payload.get(key)
            if value is None:
                continue
            if key == "context_window":
                value = _context_window_value(value)
                if value is None:
                    continue
            context[key] = value
        return None, []

    if top_type == "turn_context":
        context["turn_id"] = payload.get("turn_id") or context.get("turn_id")
        context["cwd"] = payload.get("cwd") or context.get("cwd")
        context["model"] = payload.get("model") or context.get("model")
        for key in ("effort", "approval_policy", "sandbox_policy", "collaboration_mode"):
            if payload.get(key) is not None:
                context[key] = payload.get(key)
        _emit(
            context, "turn", session_id=context.get("session_id"),
            turn_id=context.get("turn_id"),
            project_slug=_slug_from_cwd(context.get("cwd"), fallback_slug),
            cwd=context.get("cwd"), model=context.get("model"),
            effort=context.get("effort"), approval_policy=context.get("approval_policy"),
            sandbox_policy=_json_value(context.get("sandbox_policy")),
            collaboration_mode=_json_value(context.get("collaboration_mode")),
            agent_id=context.get("session_id") if context.get("parent_thread_id") else None,
            parent_session_id=context.get("parent_thread_id"),
            event_at=timestamp,
        )
        return None, []

    session_id = context.get("session_id")
    project_slug = _slug_from_cwd(context.get("cwd"), fallback_slug)
    is_child = bool(context.get("parent_thread_id"))
    agent_id = session_id if is_child else None
    agent_type = (context.get("agent_role") or "codex-agent") if is_child else None
    agent_name = context.get("agent_nickname") if is_child else None

    if top_type == "event_msg" and payload_type == "user_message":
        text = payload.get("message") if isinstance(payload.get("message"), str) else None
        uid = f"codex:{session_id or 'unknown'}:{offset}:user"
        context["last_user_uuid"] = uid
        _emit(
            context, "turn", session_id=session_id, turn_id=context.get("turn_id"),
            project_slug=project_slug, cwd=context.get("cwd"), model=context.get("model"),
            status="running", agent_id=agent_id,
            parent_session_id=context.get("parent_thread_id"), event_at=timestamp,
        )
        return {
            "uuid": uid, "parent_uuid": None, "session_id": session_id,
            "project_slug": project_slug, "cwd": context.get("cwd"), "git_branch": None,
            "cc_version": context.get("version"), "entrypoint": "codex", "type": "user",
            "is_sidechain": 1 if is_child else 0, "agent_id": agent_id,
            "agent_type": agent_type, "agent_name": agent_name,
            "parent_session_id": context.get("parent_thread_id") if is_child else None,
            "timestamp": timestamp, "model": None,
            "stop_reason": None, "prompt_id": context.get("turn_id"), "message_id": uid,
            "prompt_text": text, "prompt_chars": len(text) if text else None,
            "response_text": None, "source_metadata_json": None, "tool_calls_json": None,
            "context_window": context.get("context_window"), "source": "codex", **_empty_usage(),
        }, []

    if top_type == "response_item" and payload_type == "message" and payload.get("role") == "assistant":
        parts = [item["text"] for item in payload.get("content") or []
                 if isinstance(item, dict) and isinstance(item.get("text"), str)]
        if parts:
            context.setdefault("pending_response_parts", []).extend(parts)
            context["response_phase"] = payload.get("phase")
        return None, []

    if top_type == "event_msg" and payload_type == "agent_message":
        text = payload.get("message")
        if isinstance(text, str) and text:
            context.setdefault("fallback_response_parts", []).append(text)
        return None, []

    if top_type == "event_msg" and payload_type == "task_started":
        context_window = _context_window_value(payload.get("model_context_window"))
        if context_window is not None:
            context["context_window"] = context_window
        context["turn_id"] = payload.get("turn_id") or context.get("turn_id")
        _emit(
            context, "turn", session_id=session_id, turn_id=context.get("turn_id"),
            project_slug=project_slug, cwd=context.get("cwd"), model=context.get("model"),
            status="running", started_at=payload.get("started_at"),
            context_window=context.get("context_window"),
            collaboration_mode=_json_value(payload.get("collaboration_mode_kind")),
            agent_id=agent_id, parent_session_id=context.get("parent_thread_id"),
            event_at=timestamp,
        )
        return None, []

    if top_type == "event_msg" and payload_type == "task_complete":
        context["turn_id"] = payload.get("turn_id") or context.get("turn_id")
        _emit(
            context, "turn", session_id=session_id, turn_id=context.get("turn_id"),
            project_slug=project_slug, cwd=context.get("cwd"), model=context.get("model"),
            status="completed", completed_at=payload.get("completed_at"),
            duration_ms=payload.get("duration_ms"),
            time_to_first_token_ms=payload.get("time_to_first_token_ms"),
            context_window=context.get("context_window"), agent_id=agent_id,
            parent_session_id=context.get("parent_thread_id"), event_at=timestamp,
        )
        return None, []

    if top_type == "response_item" and payload_type in {"function_call", "custom_tool_call"}:
        context.setdefault("pending_tools", []).extend(_call_rows(payload, timestamp))
        return None, []

    if top_type == "response_item" and payload_type in {"function_call_output", "custom_tool_call_output"}:
        _attach_result(context, payload.get("call_id"), payload.get("output"), payload.get("status") == "failed")
        return None, []

    if top_type == "response_item" and payload_type == "tool_search_call":
        row_payload = dict(payload)
        row_payload.update(name="tool_search", arguments=payload.get("arguments"))
        context.setdefault("pending_tools", []).extend(_call_rows(row_payload, timestamp))
        return None, []

    if top_type == "response_item" and payload_type == "tool_search_output":
        _attach_result(context, payload.get("call_id"), payload.get("tools"), payload.get("status") == "failed")
        return None, []

    if top_type == "event_msg" and payload_type in {"web_search_end", "patch_apply_end"}:
        call_id = payload.get("call_id")
        pending = context.setdefault("pending_tools", [])
        if not any(tool.get("call_id") == call_id for tool in pending):
            name = "web_search" if payload_type == "web_search_end" else "apply_patch"
            pending.append({
                "tool_name": name, "target": payload.get("query"), "result_tokens": None,
                "is_error": 0 if payload.get("success", True) else 1, "call_id": call_id,
                "tool_kind": tool_kind(name), "timestamp": timestamp, "source": "codex",
            })
        return None, []

    if top_type != "event_msg" or payload_type != "token_count":
        return None, []

    rate_limits = payload.get("rate_limits")
    if isinstance(rate_limits, dict):
        primary = rate_limits.get("primary") or {}
        secondary = rate_limits.get("secondary") or {}
        _emit(
            context, "rate_limit", snapshot_id=f"{session_id or 'unknown'}:{offset}:rate",
            session_id=session_id, turn_id=context.get("turn_id"), timestamp=timestamp,
            limit_id=rate_limits.get("limit_id"), limit_name=rate_limits.get("limit_name"),
            plan_type=rate_limits.get("plan_type"),
            primary_used_percent=primary.get("used_percent"),
            primary_window_minutes=primary.get("window_minutes"),
            primary_resets_at=primary.get("resets_at"),
            secondary_used_percent=secondary.get("used_percent"),
            secondary_window_minutes=secondary.get("window_minutes"),
            secondary_resets_at=secondary.get("resets_at"),
            rate_limit_reached_type=rate_limits.get("rate_limit_reached_type"),
            credits_json=_json_value(rate_limits.get("credits")),
            source_metadata_json=_json_value(rate_limits.get("individual_limit")),
        )

    uid = f"codex:{session_id or 'unknown'}:{offset}:assistant"
    tools = context.pop("pending_tools", [])
    response_parts = context.pop("pending_response_parts", [])
    fallback_parts = context.pop("fallback_response_parts", [])
    info = payload.get("info") or {}
    metadata = {key: context.get(key) for key in (
        "effort", "approval_policy", "sandbox_policy", "collaboration_mode", "model_provider",
        "originator", "thread_source", "parent_thread_id", "agent_role", "agent_nickname",
    ) if context.get(key) is not None}
    if context.get("response_phase") is not None:
        metadata["phase"] = context.pop("response_phase")
    msg = {
        "uuid": uid, "parent_uuid": context.get("last_user_uuid"), "session_id": session_id,
        "project_slug": project_slug, "cwd": context.get("cwd"), "git_branch": None,
        "cc_version": context.get("version"), "entrypoint": "codex", "type": "assistant",
        "is_sidechain": 1 if is_child else 0, "agent_id": agent_id,
        "agent_type": agent_type, "agent_name": agent_name,
        "parent_session_id": context.get("parent_thread_id") if is_child else None,
        "timestamp": timestamp, "model": context.get("model"), "stop_reason": None,
        "prompt_id": context.get("turn_id"), "message_id": uid, "prompt_text": None,
        "prompt_chars": None, "response_text": "\n".join(response_parts or fallback_parts) or None,
        "source_metadata_json": json.dumps(metadata) if metadata else None, "tool_calls_json": None,
        "context_window": _context_window_value(info.get("model_context_window"))
        or context.get("context_window"),
        "source": "codex", **_usage(payload),
    }
    if tools:
        msg["tool_calls_json"] = json.dumps([
            {"name": tool["tool_name"], "target": tool["target"]}
            for tool in tools if tool["tool_name"] != "_tool_result"
        ])
    for tool in tools:
        tool["message_uuid"] = uid
        tool["session_id"] = session_id
        tool["project_slug"] = project_slug
        tool["timestamp"] = tool.get("timestamp") or timestamp
    return msg, tools


class CodexPipeline:
    source = "codex"
    replay_changed_files = True
    features = (
        "messages", "tools", "cache", "reasoning", "context-pressure",
        "turn-lifecycle", "time-to-first-token", "rate-limits", "child-agents",
    )

    def new_context(self) -> dict:
        return {}

    def accepts(self, record: dict) -> bool:
        return bool(record.get("type"))

    def parse(self, record: dict, fallback_slug: str, context: dict, offset: int) -> PipelineOutput:
        # Child-thread files start with a child header, then embed a complete
        # parent transcript between two parent session_meta records. Keep the
        # child's own identity and ignore that inherited history; otherwise
        # every fork duplicates the parent's tokens, tools, and cost.
        if record.get("type") == "session_meta":
            payload = record.get("payload") or {}
            child_id = payload.get("id")
            parent_id = payload.get("parent_thread_id")
            if parent_id and child_id and child_id != parent_id and not context.get("_child_header"):
                context["_child_header"] = dict(payload, session_id=child_id)
                context["_parent_meta_count"] = 0
                record = dict(record, payload=context["_child_header"])
            elif context.get("_child_header") and payload.get("id") == context["_child_header"].get("parent_thread_id"):
                count = context.get("_parent_meta_count", 0) + 1
                context["_parent_meta_count"] = count
                if count == 1:
                    context["_skip_inherited_history"] = True
                else:
                    # The second parent header is followed by a short tail of
                    # parent reasoning/message/token events. The child's own
                    # append begins at its task_started boundary.
                    context["_skip_inherited_history"] = True
                    context["_wait_child_task_start"] = True
                    child = context["_child_header"]
                    context.update({
                        "session_id": child.get("id"), "cwd": child.get("cwd"),
                        "version": child.get("cli_version"), "thread_source": child.get("thread_source"),
                        "parent_thread_id": child.get("parent_thread_id"),
                        "agent_role": child.get("agent_role"), "agent_nickname": child.get("agent_nickname"),
                    })
                return PipelineOutput()
        elif context.get("_skip_inherited_history"):
            payload = record.get("payload") or {}
            is_child_start = (
                context.get("_wait_child_task_start")
                and record.get("type") == "event_msg"
                and payload.get("type") == "task_started"
            )
            if not is_child_start:
                return PipelineOutput()
            context["_skip_inherited_history"] = False
            context["_wait_child_task_start"] = False
        message, tools = parse_record(record, fallback_slug, context, offset)
        updates = context.pop("_pipeline_updates", [])
        return PipelineOutput(message=message, tools=tools, updates=updates)

    def persist_updates(self, conn, updates: List[dict]) -> None:
        persist_codex_updates(conn, updates)

    def after_scan(self, conn, root) -> None:
        return None
