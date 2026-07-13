"""Canonical cross-platform normalization helpers."""


def tool_kind(name: str) -> str:
    """Map provider-specific tool names to stable analytics categories."""
    value = (name or "").lower().replace("-", "_")
    leaf = value.rsplit(".", 1)[-1]
    if leaf in {"read", "edit", "write", "glob", "grep", "apply_patch", "view_image"}:
        return "file"
    if "search" in leaf or leaf in {"find", "rg"}:
        return "search"
    if leaf in {"bash", "shell", "shell_command", "exec_command"}:
        return "shell"
    if value.startswith("web__") or "browser" in value or leaf in {"webfetch", "web_run", "open", "click"}:
        return "web"
    if "agent" in value or leaf in {"task", "spawn", "send_message", "followup_task"}:
        return "agent"
    if "skill" in value:
        return "skill"
    if leaf in {"update_plan", "request_user_input", "tool_search"}:
        return "orchestration"
    return "other"
