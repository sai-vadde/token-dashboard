import json
import os
import shutil
import sqlite3
import tempfile
import unittest

from token_dashboard.db import init_db
from token_dashboard.scanner import scan_dir

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


class WalkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        self.proj_root = os.path.join(self.tmp, "projects")
        proj_dir = os.path.join(self.proj_root, "C--work-sample")
        os.makedirs(proj_dir)
        shutil.copy(
            os.path.join(FIXTURE_DIR, "sample_session.jsonl"),
            os.path.join(proj_dir, "s1.jsonl"),
        )
        init_db(self.db)

    def test_scan_writes_messages_and_tools(self):
        n = scan_dir(self.proj_root, self.db)
        self.assertEqual(n["messages"], 3)
        self.assertEqual(n["tools"], 2)  # 1 tool_use + 1 tool_result
        with sqlite3.connect(self.db) as c:
            row = c.execute("SELECT project_slug FROM messages WHERE uuid='u1'").fetchone()
        self.assertEqual(row[0], "C--work-sample")

    def test_rescan_skips_unchanged_files(self):
        n1 = scan_dir(self.proj_root, self.db)
        n2 = scan_dir(self.proj_root, self.db)
        self.assertEqual(n1["messages"], 3)
        self.assertEqual(n2["messages"], 0)

    def test_rescan_picks_up_appended_lines(self):
        scan_dir(self.proj_root, self.db)
        path = os.path.join(self.proj_root, "C--work-sample", "s1.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write('{"type":"assistant","uuid":"a2","sessionId":"s1","timestamp":"2026-04-10T00:00:03Z","isSidechain":false,"message":{"model":"claude-haiku-4-5","usage":{"input_tokens":1,"output_tokens":1}}}\n')
        n2 = scan_dir(self.proj_root, self.db)
        self.assertEqual(n2["messages"], 1)


class CodexWalkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "c.db")
        self.sessions_root = os.path.join(self.tmp, "sessions")
        day_dir = os.path.join(self.sessions_root, "2026", "07", "10")
        os.makedirs(day_dir)
        shutil.copy(
            os.path.join(FIXTURE_DIR, "codex_session.jsonl"),
            os.path.join(day_dir, "rollout-2026-07-10T00-00-00-codex-s1.jsonl"),
        )
        init_db(self.db)

    def test_scan_codex_session_writes_normalized_rows(self):
        n = scan_dir(self.sessions_root, self.db, source="codex")
        self.assertEqual(n["messages"], 2)
        self.assertEqual(n["tools"], 2)
        with sqlite3.connect(self.db) as c:
            rows = c.execute(
                "SELECT type, source, project_slug, model, input_tokens, cache_read_tokens, output_tokens, prompt_text, reasoning_output_tokens, context_window, response_text, source_metadata_json FROM messages ORDER BY timestamp"
            ).fetchall()
            tools = c.execute(
                "SELECT tool_name, result_tokens, call_id, tool_kind FROM tool_calls ORDER BY id"
            ).fetchall()
            turn = c.execute(
                "SELECT status, duration_ms, time_to_first_token_ms, effort, approval_policy "
                "FROM codex_turns WHERE session_id='codex-s1' AND turn_id='turn-1'"
            ).fetchone()
            rate = c.execute(
                "SELECT plan_type, primary_used_percent, secondary_used_percent "
                "FROM codex_rate_limits"
            ).fetchone()
        self.assertEqual(rows[0][0], "user")
        self.assertEqual(rows[0][1], "codex")
        self.assertEqual(rows[0][7], "Summarize token usage")
        self.assertEqual(rows[1][0], "assistant")
        self.assertEqual(rows[1][2], "C--work-codex-demo")
        self.assertEqual(rows[1][3], "gpt-5.4")
        self.assertEqual(rows[1][4], 90)
        self.assertEqual(rows[1][5], 30)
        self.assertEqual(rows[1][6], 40)
        self.assertEqual(rows[1][8], 5)
        self.assertEqual(rows[1][9], 258400)
        self.assertEqual(rows[1][10], "Token usage summarized.")
        self.assertEqual(json.loads(rows[1][11])["effort"], "high")
        self.assertEqual([row[0] for row in tools], ["shell_command", "apply_patch"])
        self.assertEqual([row[2] for row in tools], ["call-1", "call-2"])
        self.assertEqual([row[3] for row in tools], ["shell", "file"])
        self.assertTrue(all(row[1] is not None for row in tools))
        self.assertEqual(turn, ("completed", 6000, 350, "high", "on-request"))
        self.assertEqual(rate, ("plus", 76.0, 12.0))

    def test_codex_full_replay_reports_only_new_rows(self):
        n1 = scan_dir(self.sessions_root, self.db, source="codex")
        self.assertEqual(n1["messages"], 2)
        self.assertEqual(n1["tools"], 2)

        path = os.path.join(self.sessions_root, "2026", "07", "10", "rollout-2026-07-10T00-00-00-codex-s1.jsonl")
        future = os.path.getmtime(path) + 10
        os.utime(path, (future, future))

        n2 = scan_dir(self.sessions_root, self.db, source="codex")
        self.assertEqual(n2["files"], 1)
        self.assertEqual(n2["messages"], 0)
        self.assertEqual(n2["tools"], 0)
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 2)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0], 2)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM codex_turns").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM codex_rate_limits").fetchone()[0], 1)

    def test_wrong_source_scan_does_not_block_later_codex_scan(self):
        wrong = scan_dir(self.sessions_root, self.db, source="claude")
        self.assertEqual(wrong["files"], 1)
        self.assertEqual(wrong["messages"], 0)

        right = scan_dir(self.sessions_root, self.db, source="codex")
        self.assertEqual(right["files"], 1)
        self.assertEqual(right["messages"], 2)
        self.assertEqual(right["tools"], 2)
        with sqlite3.connect(self.db) as c:
            sources = sorted(
                c.execute("SELECT source, COUNT(*) FROM files GROUP BY source").fetchall()
            )
            messages = c.execute("SELECT COUNT(*) FROM messages WHERE source='codex'").fetchone()[0]
        self.assertEqual(sources, [("claude", 1), ("codex", 1)])
        self.assertEqual(messages, 2)


if __name__ == "__main__":
    unittest.main()
