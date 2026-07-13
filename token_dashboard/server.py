"""HTTP server: static frontend + JSON endpoints + SSE diff stream."""
from __future__ import annotations

import http.server
import json
import mimetypes
import queue
import threading
import time
from pathlib import Path
from typing import Optional, Union
from urllib.parse import urlparse, parse_qs

from .db import (
    overview_totals, expensive_prompts, project_summary,
    tool_token_breakdown, recent_sessions, session_turns,
    daily_token_breakdown, model_breakdown, skill_breakdown, source_summary,
    agent_breakdown, agent_run_breakdown,
)
from .pricing import load_pricing, cost_for, financial_summary, get_plan, set_plan
from .tips import all_tips, dismiss_tip
from .scanner import scan_dir
from .skills import cached_catalog
from .codex_store import codex_rate_limit_history, codex_summary, codex_turn_breakdown
from .pipeline import get_pipeline, registered_sources
from .analytics import platform_analytics
from .providers import PLATFORMS, effective_scan_roots, enabled_sources, save_platform_settings


WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
PRICING_JSON = Path(__file__).resolve().parent.parent / "pricing.json"

EVENTS: "queue.Queue[dict]" = queue.Queue()

MAX_POST_BYTES = 1_000_000  # 1 MB — we only accept tiny JSON bodies (plan, tip key)
MAX_LIMIT = 1000


def _send_json(handler, obj, status: int = 200) -> None:
    body = json.dumps(obj, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _send_error(handler, status: int, msg: str) -> None:
    _send_json(handler, {"error": msg}, status=status)


def _clamp_limit(raw, default: int) -> int:
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, min(v, MAX_LIMIT))


def _source(qs) -> Optional[str]:
    raw = (qs.get("source", ["all"])[0] or "all").lower()
    return raw if raw in {p["source"] for p in PLATFORMS if p["status"] == "available"} else None


def scan_all(scan_roots, require_configured: bool = True) -> dict:
    totals = {"messages": 0, "tools": 0, "files": 0, "sources": {}}
    if not scan_roots:
        return totals
    db_path = scan_roots[0][2]
    for source, root, db_path in effective_scan_roots(db_path, scan_roots, require_configured):
        n = scan_dir(root, db_path, source=source)
        totals["sources"][source] = n
        totals["messages"] += n["messages"]
        totals["tools"] += n["tools"]
        totals["files"] += n["files"]
    return totals


def _serve_static(handler, rel: str) -> None:
    rel = rel.lstrip("/")
    p = (WEB_ROOT / rel).resolve()
    if not str(p).startswith(str(WEB_ROOT.resolve())) or not p.is_file():
        handler.send_response(404)
        handler.end_headers()
        return
    body = p.read_bytes()
    ctype, _ = mimetypes.guess_type(str(p))
    handler.send_response(200)
    handler.send_header("Content-Type", ctype or "application/octet-stream")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def build_handler(db_path: str, projects_dir: Union[str, list]):
    pricing = load_pricing(PRICING_JSON)
    scan_roots = (
        [(source, root, db_path) for source, root in projects_dir]
        if isinstance(projects_dir, list)
        else [("claude", projects_dir, db_path)]
    )

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_HEAD(self):
            return self.do_GET()

        def do_GET(self):
            url = urlparse(self.path)
            qs = parse_qs(url.query or "")
            path = url.path
            since = qs.get("since", [None])[0]
            until = qs.get("until", [None])[0]
            source = _source(qs)
            scope = source or enabled_sources(db_path)
            agent_id = qs.get("agent_id", [None])[0]
            if path in ("/", "/index.html"):
                return _serve_static(self, "index.html")
            if path.startswith("/web/"):
                return _serve_static(self, path[5:])
            if path == "/api/overview":
                totals = overview_totals(db_path, since, until, source=scope)
                financial = financial_summary(model_breakdown(db_path, since, until, source=scope), pricing)
                totals["cost_usd"] = financial["api_equivalent_usd"]
                totals["financial"] = financial
                return _send_json(self, totals)
            if path == "/api/prompts":
                limit = _clamp_limit(qs.get("limit", ["50"])[0], 50)
                sort = qs.get("sort", ["tokens"])[0]
                rows = expensive_prompts(db_path, limit=limit, sort=sort, source=scope)
                for r in rows:
                    c = cost_for(r["model"], r, pricing)
                    r["estimated_cost_usd"] = c["usd"]
                return _send_json(self, rows)
            if path == "/api/projects":
                return _send_json(self, project_summary(db_path, since, until, source=scope))
            if path == "/api/tools":
                return _send_json(self, tool_token_breakdown(db_path, since, until, source=scope))
            if path == "/api/sessions":
                return _send_json(self, recent_sessions(
                    db_path, limit=_clamp_limit(qs.get("limit", ["20"])[0], 20),
                    since=since, until=until, source=scope,
                ))
            if path == "/api/daily":
                return _send_json(self, daily_token_breakdown(db_path, since, until, source=scope))
            if path == "/api/skills":
                rows = skill_breakdown(db_path, since, until, source=scope)
                catalog = cached_catalog()
                for r in rows:
                    info = catalog.get(r["skill"])
                    r["tokens_per_call"] = info["tokens"] if info else None
                return _send_json(self, rows)
            if path == "/api/agents":
                rows = agent_breakdown(db_path, since, until, source=scope)
                for r in rows:
                    c = cost_for(r["model"], r, pricing)
                    r["cost_usd"] = c["usd"]
                    r["cost_estimated"] = c["estimated"]
                return _send_json(self, rows)
            if path == "/api/pipelines":
                return _send_json(self, [
                    {
                        "source": name,
                        "replay_changed_files": get_pipeline(name).replay_changed_files,
                        "features": list(getattr(get_pipeline(name), "features", ())),
                    }
                    for name in registered_sources()
                ])
            if path == "/api/codex/summary":
                summary = codex_summary(db_path, since, until)
                item = next(p for p in platform_analytics(db_path, pricing, since, until)["platforms"] if p["source"] == "codex")
                summary.update({key: item.get(key) for key in ("financial", "cache", "credits", "plan", "plan_label", "subscription_usd")})
                return _send_json(self, summary)
            if path == "/api/codex/turns":
                limit = _clamp_limit(qs.get("limit", ["100"])[0], 100)
                return _send_json(self, codex_turn_breakdown(
                    db_path, since, until, limit=limit
                ))
            if path == "/api/codex/rate-limits":
                limit = _clamp_limit(qs.get("limit", ["100"])[0], 100)
                return _send_json(self, codex_rate_limit_history(db_path, limit=limit))
            if path == "/api/agent-runs":
                rows = agent_run_breakdown(db_path, since, until, source=scope)
                for r in rows:
                    c = cost_for(r["model"], r, pricing)
                    r["cost_usd"] = c["usd"]
                    r["cost_estimated"] = c["estimated"]
                return _send_json(self, rows)
            if path == "/api/by-model":
                rows = model_breakdown(db_path, since, until, source=scope)
                for r in rows:
                    c = cost_for(r["model"], r, pricing)
                    r["cost_usd"] = c["usd"]
                    r["cost_estimated"] = c["estimated"]
                return _send_json(self, rows)
            if path.startswith("/api/sessions/"):
                sid = path.rsplit("/", 1)[1]
                return _send_json(self, session_turns(
                    db_path, sid, source=source, agent_id=agent_id
                ))
            if path == "/api/tips":
                if isinstance(scope, list):
                    tips = []
                    for enabled_source in scope:
                        tips.extend(all_tips(db_path, source=enabled_source))
                    return _send_json(self, tips)
                return _send_json(self, all_tips(db_path, source=scope))
            if path == "/api/sources":
                data = platform_analytics(db_path, pricing, since, until)
                return _send_json(self, [p for p in data["platforms"] if p["source"] in data["enabled_sources"]])
            if path == "/api/platforms":
                return _send_json(self, platform_analytics(db_path, pricing, since, until))
            if path == "/api/plan":
                plan_source = source or "claude"
                return _send_json(self, {"source": plan_source, "plan": get_plan(db_path, source=plan_source), "pricing": pricing})
            if path == "/api/scan":
                n = scan_all(scan_roots)
                return _send_json(self, n)
            if path == "/api/stream":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                while True:
                    try:
                        evt = EVENTS.get(timeout=15)
                        chunk = f"data: {json.dumps(evt, default=str)}\n\n".encode()
                    except queue.Empty:
                        chunk = b": ping\n\n"
                    try:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        return
            self.send_response(404)
            self.end_headers()

        def do_POST(self):
            url = urlparse(self.path)
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return _send_error(self, 400, "invalid Content-Length")
            if length < 0 or length > MAX_POST_BYTES:
                return _send_error(self, 413, f"body too large (max {MAX_POST_BYTES} bytes)")
            try:
                body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            except json.JSONDecodeError:
                return _send_error(self, 400, "invalid JSON")
            if not isinstance(body, dict):
                return _send_error(self, 400, "body must be a JSON object")
            if url.path == "/api/plan":
                plan_source = str(body.get("source") or "claude")
                plan = str(body.get("plan") or "api")
                if plan not in pricing.get("provider_plans", {}).get(plan_source, {}):
                    return _send_error(self, 400, "unknown provider plan")
                set_plan(db_path, plan, source=plan_source)
                return _send_json(self, {"ok": True})
            if url.path == "/api/platforms":
                items = body.get("platforms")
                if not isinstance(items, list):
                    return _send_error(self, 400, "platforms must be a list")
                for item in items:
                    source_name = str(item.get("source") or "") if isinstance(item, dict) else ""
                    if isinstance(item, dict) and "scan_root" in item:
                        return _send_error(self, 400, "scan roots can only be set by CLI or environment configuration")
                    if not isinstance(item, dict) or str(item.get("plan") or "api") not in pricing.get("provider_plans", {}).get(source_name, {}):
                        return _send_error(self, 400, "unknown provider plan")
                try:
                    save_platform_settings(db_path, items)
                except ValueError as exc:
                    return _send_error(self, 400, str(exc))
                scanned = scan_all(scan_roots)
                return _send_json(self, {"ok": True, "scan": scanned})
            if url.path == "/api/tips/dismiss":
                dismiss_tip(db_path, body.get("key", ""))
                return _send_json(self, {"ok": True})
            self.send_response(404)
            self.end_headers()

    return H


def _scan_loop(scan_roots, interval: float = 30.0):
    while True:
        try:
            n = scan_all(scan_roots)
            if n["messages"] > 0:
                EVENTS.put({"type": "scan", "n": n, "ts": time.time()})
        except Exception as e:
            EVENTS.put({"type": "error", "message": str(e)})
        time.sleep(interval)


def run(host: str, port: int, db_path: str, projects_dir: Union[str, list]):
    scan_roots = (
        [(source, root, db_path) for source, root in projects_dir]
        if isinstance(projects_dir, list)
        else [("claude", projects_dir, db_path)]
    )
    threading.Thread(target=_scan_loop, args=(scan_roots,), daemon=True).start()
    H = build_handler(db_path, projects_dir)
    httpd = http.server.ThreadingHTTPServer((host, port), H)
    httpd.serve_forever()
