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


def _db_path(args) -> str:
    return args.db or os.environ.get("TOKEN_DASHBOARD_DB") or str(default_db_path())


def _default_root(source: str) -> str:
    if source == "codex":
        return os.environ.get("CODEX_SESSIONS_DIR") or str(Path.home() / ".codex" / "sessions")
    return os.environ.get("CLAUDE_PROJECTS_DIR") or str(Path.home() / ".claude" / "projects")


def _source(args) -> str:
    return (args.source or os.environ.get("TOKEN_DASHBOARD_SOURCE") or "all").lower()


def _scan_roots(args):
    source = _source(args)
    if args.projects_dir:
        selected = args.source if args.source in ("claude", "codex") else "claude"
        return [(selected, args.projects_dir)]
    if source in ("claude", "codex"):
        return [(source, _default_root(source))]
    return [("claude", _default_root("claude")), ("codex", _default_root("codex"))]


def _scan_all(args, db: str) -> dict:
    totals = {"messages": 0, "tools": 0, "files": 0, "sources": {}}
    for source, root in _scan_roots(args):
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


def cmd_scan(args):
    db = _db_path(args)
    init_db(db)
    n = _scan_all(args, db)
    print(f"Token Dashboard: scanned {n['files']} files, {n['messages']} messages, {n['tools']} tool calls")


def cmd_today(args):
    db = _db_path(args)
    init_db(db)
    s, e = _today_range()
    source = None if _source(args) == "all" else _source(args)
    t = overview_totals(db, since=s, until=e, source=source)
    print("Token Dashboard — today")
    print(f"  sessions: {t['sessions']}    turns: {t['turns']}")
    print(f"  input:    {t['input_tokens']:>12,}    output: {t['output_tokens']:>12,}")
    print(f"  cache rd: {t['cache_read_tokens']:>12,}    cache cr: {t['cache_create_5m_tokens']+t['cache_create_1h_tokens']:>12,}")


def cmd_stats(args):
    db = _db_path(args)
    init_db(db)
    source = None if _source(args) == "all" else _source(args)
    t = overview_totals(db, source=source)
    print("Token Dashboard — all time")
    print(f"  sessions: {t['sessions']}    turns: {t['turns']}")
    print(f"  input:    {t['input_tokens']:>12,}    output: {t['output_tokens']:>12,}")


def cmd_tips(args):
    db = _db_path(args)
    init_db(db)
    source = None if _source(args) == "all" else _source(args)
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
    if not args.no_scan:
        _scan_all(args, db)
    from token_dashboard.server import run

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8080"))
    url = f"http://{host}:{port}/"
    if not args.no_open:
        webbrowser.open(url)
    print(f"Token Dashboard listening on {url}")
    run(host, port, db, _scan_roots(args))


def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", help="SQLite path (default ~/.codex/token-dashboard.db)")
    common.add_argument("--projects-dir", help="Single JSONL root override")
    common.add_argument("--source", choices=("all", "claude", "codex"), help="Transcript source to scan or display (default all)")

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
