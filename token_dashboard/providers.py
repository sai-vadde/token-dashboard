"""Persisted provider selection plus source capability metadata."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable, Union


PLATFORMS = (
    {
        "source": "claude", "name": "Claude Code", "mark": "A", "accent": "#D97757",
        "description": "Claude Code transcripts, explicit cache writes, tools, skills and agents.",
        "status": "available", "route": None, "root_env": "CLAUDE_PROJECTS_DIR",
        "default_root": ".claude/projects",
        "capabilities": {"cache_read": "reported", "cache_create": "reported", "rate_limits": "not_reported"},
    },
    {
        "source": "codex", "name": "Codex", "mark": "O", "accent": "#10A37F",
        "description": "Codex tasks, reasoning, cached input, rate limits and child agents.",
        "status": "available", "route": "/codex", "root_env": "CODEX_SESSIONS_DIR",
        "default_root": ".codex/sessions",
        "capabilities": {"cache_read": "reported", "cache_create": "not_reported", "rate_limits": "reported"},
    },
    {
        "source": "gemini", "name": "Gemini CLI", "mark": "G", "accent": "#4285F4",
        "description": "Reserved for a future local Gemini transcript adapter.",
        "status": "coming_soon", "route": None, "root_env": "GEMINI_SESSIONS_DIR",
        "default_root": ".gemini/sessions",
        "capabilities": {"cache_read": "unknown", "cache_create": "unknown", "rate_limits": "unknown"},
    },
)


def ensure_platform_rows(conn) -> None:
    """Seed available providers without overwriting a user's choices."""
    legacy = conn.execute("SELECT v FROM plan WHERE k='plan'").fetchone()
    legacy_plan = legacy[0] if legacy else "api"
    defaults = tuple(
        (platform["source"], legacy_plan if platform["source"] == "claude" else "api", (index + 1) * 10)
        for index, platform in enumerate(PLATFORMS) if platform["status"] == "available"
    )
    for source, plan, order in defaults:
        conn.execute(
            """INSERT OR IGNORE INTO platform_settings
                 (source, enabled, configured, plan, display_order, updated_at)
               VALUES (?, 1, 0, ?, ?, ?)""",
            (source, plan, order, time.time()),
        )
    conn.commit()


def platform_catalog(db_path: Union[str, Path]) -> list:
    from .db import connect
    with connect(db_path) as conn:
        rows = {r["source"]: dict(r) for r in conn.execute(
            "SELECT source, enabled, configured, plan, scan_root, display_order FROM platform_settings"
        )}
    out = []
    for definition in PLATFORMS:
        item = dict(definition)
        item["capabilities"] = dict(definition["capabilities"])
        saved = rows.get(item["source"], {})
        item.update({
            "enabled": bool(saved.get("enabled", 0)) if item["status"] == "available" else False,
            "configured": bool(saved.get("configured", 0)),
            "plan": saved.get("plan", "api"),
            "scan_root": saved.get("scan_root"),
            "display_order": saved.get("display_order", 999),
        })
        out.append(item)
    return sorted(out, key=lambda p: (p["display_order"], p["name"]))


def enabled_sources(db_path: Union[str, Path]) -> list:
    return [p["source"] for p in platform_catalog(db_path) if p["status"] == "available" and p["enabled"]]


def platforms_configured(db_path: Union[str, Path]) -> bool:
    available = [p for p in platform_catalog(db_path) if p["status"] == "available"]
    return bool(available) and all(p["configured"] for p in available)


def save_platform_settings(db_path: Union[str, Path], items: Iterable[dict]) -> None:
    from .db import connect
    known = {p["source"] for p in PLATFORMS if p["status"] == "available"}
    received = {str(item.get("source", "")).lower(): item for item in items}
    if not (known & received.keys()) or not any(
        bool(item.get("enabled")) for source, item in received.items() if source in known
    ):
        raise ValueError("select at least one available platform")
    with connect(db_path) as conn:
        for source in known:
            item = received.get(source, {})
            enabled = 1 if item.get("enabled", False) else 0
            plan = str(item.get("plan") or "api")[:80]
            root = item.get("scan_root")
            root = str(root).strip()[:2000] if root else None
            conn.execute(
                """INSERT INTO platform_settings
                     (source, enabled, configured, plan, scan_root, display_order, updated_at)
                   VALUES (?, ?, 1, ?, ?, COALESCE((SELECT display_order FROM platform_settings WHERE source=?), 0), ?)
                   ON CONFLICT(source) DO UPDATE SET enabled=excluded.enabled,
                     configured=1, plan=excluded.plan, scan_root=excluded.scan_root,
                     updated_at=excluded.updated_at""",
                (source, enabled, plan, root, source, time.time()),
            )
        conn.commit()


def provider_plan(db_path: Union[str, Path], source: str, default: str = "api") -> str:
    from .db import connect
    with connect(db_path) as conn:
        row = conn.execute("SELECT plan FROM platform_settings WHERE source=?", (source,)).fetchone()
    return row["plan"] if row else default


def effective_scan_roots(db_path: Union[str, Path], roots: Iterable[tuple], require_configured: bool = True) -> list:
    """Return only enabled roots, applying any persisted per-provider override."""
    if require_configured and not platforms_configured(db_path):
        return []
    settings = {p["source"]: p for p in platform_catalog(db_path)}
    out = []
    for source, root, target_db in roots:
        item = settings.get(source)
        if item and item["enabled"]:
            out.append((source, item.get("scan_root") or root, target_db))
    return out
