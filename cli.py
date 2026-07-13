"""Token Dashboard CLI entrypoint."""
from __future__ import annotations

import argparse
import os
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

from token_dashboard.db import init_db, default_db_path, overview_totals
from token_dashboard.scanner import scan_dir
from token_dashboard.tips import all_tips
from token_dashboard.providers import PLATFORMS, effective_scan_roots, enabled_sources, platforms_configured


AVAILABLE_SOURCES = tuple(p["source"] for p in PLATFORMS if p["status"] == "available")


def _db_path(args) -> str:
    return args.db or os.environ.get("TOKEN_DASHBOARD_DB") or str(default_db_path())


def _default_root(source: str) -> str:
    provider = next((p for p in PLATFORMS if p["source"] == source and p["status"] == "available"), None)
    if not provider:
        raise ValueError(f"unsupported source: {source}")
    return os.environ.get(provider["root_env"]) or str(Path.home() / Path(provider["default_root"]))


def _source(args) -> str:
    return (args.source or os.environ.get("TOKEN_DASHBOARD_SOURCE") or "all").lower()


def _scan_roots(args, db: str = None, respect_enabled: bool = True):
    source = _source(args)
    if args.projects_dir:
        selected = args.source if args.source in AVAILABLE_SOURCES else "claude"
        return [(selected, args.projects_dir)]
    if source in AVAILABLE_SOURCES:
        return [(source, _default_root(source))]
    roots = [(provider, _default_root(provider)) for provider in AVAILABLE_SOURCES]
    if db and respect_enabled and platforms_configured(db):
        expanded = [(provider, root, db) for provider, root in roots]
        return [(provider, root) for provider, root, _ in effective_scan_roots(db, expanded)]
    return roots


def _scan_all(args, db: str) -> dict:
    totals = {"messages": 0, "tools": 0, "files": 0, "sources": {}}
    for source, root in _scan_roots(args, db):
        n = scan_dir(root, db, source=source)
        totals["sources"][source] = n
        totals["messages"] += n["messages"]
        totals["tools"] += n["tools"]
        totals["files"] += n["files"]
    return totals


def _today_range():
    now = datetime.now(timezone.utc)
    start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc).isoformat()
    end = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    return start, end


def _display_scope(args, db: str):
    selected = _source(args)
    if selected != "all":
        return selected
    return enabled_sources(db) if platforms_configured(db) else None


def cmd_scan(args):
    db = _db_path(args)
    init_db(db)
    n = _scan_all(args, db)
    print(f"Token Dashboard: scanned {n['files']} files, {n['messages']} messages, {n['tools']} tool calls")


def cmd_today(args):
    db = _db_path(args)
    init_db(db)
    s, e = _today_range()
    source = _display_scope(args, db)
    t = overview_totals(db, since=s, until=e, source=source)
    print("Token Dashboard — today")
    print(f"  sessions: {t['sessions']}    turns: {t['turns']}")
    print(f"  input:    {t['input_tokens']:>12,}    output: {t['output_tokens']:>12,}")
    print(f"  cache rd: {t['cache_read_tokens']:>12,}    cache cr: {t['cache_create_5m_tokens']+t['cache_create_1h_tokens']:>12,}")


def cmd_stats(args):
    db = _db_path(args)
    init_db(db)
    source = _display_scope(args, db)
    t = overview_totals(db, source=source)
    print("Token Dashboard — all time")
    print(f"  sessions: {t['sessions']}    turns: {t['turns']}")
    print(f"  input:    {t['input_tokens']:>12,}    output: {t['output_tokens']:>12,}")


def cmd_tips(args):
    db = _db_path(args)
    init_db(db)
    source = _display_scope(args, db)
    tips = []
    if isinstance(source, list):
        for enabled_source in source:
            tips.extend(all_tips(db, source=enabled_source))
    else:
        tips = all_tips(db, source=source)
    if not tips:
        print("Token Dashboard: no suggestions")
        return
    for tip in tips:
        print(f"[{tip['category']}] {tip['title']}")
        print(f"  {tip['body']}\n")


def cmd_dashboard(args):
    db = _db_path(args)
    init_db(db)
    if not args.no_scan and platforms_configured(db):
        _scan_all(args, db)
    from token_dashboard.server import run

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8080"))
    url = f"http://{host}:{port}/"
    if not args.no_open:
        webbrowser.open(url)
    print(f"Token Dashboard listening on {url}")
    run(host, port, db, _scan_roots(args, db, respect_enabled=False))


def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", help="SQLite path (default ~/.codex/token-dashboard.db)")
    common.add_argument("--projects-dir", help="Single JSONL root override")
    common.add_argument("--source", choices=("all", *AVAILABLE_SOURCES), help="Transcript source to scan or display (default all enabled)")

    p = argparse.ArgumentParser(prog="token-dashboard", description="Local Claude/Codex usage dashboard", parents=[common])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan",  parents=[common]).set_defaults(func=cmd_scan)
    sub.add_parser("today", parents=[common]).set_defaults(func=cmd_today)
    sub.add_parser("stats", parents=[common]).set_defaults(func=cmd_stats)
    sub.add_parser("tips",  parents=[common]).set_defaults(func=cmd_tips)
    d = sub.add_parser("dashboard", parents=[common])
    d.add_argument("--no-scan", action="store_true")
    d.add_argument("--no-open", action="store_true")
    d.set_defaults(func=cmd_dashboard)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
