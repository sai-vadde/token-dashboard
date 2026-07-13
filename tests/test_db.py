import os
import sqlite3
import tempfile
import unittest
from token_dashboard.db import init_db, connect


class InitDbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "test.db")

    def test_init_creates_expected_tables(self):
        init_db(self.db_path)
        with sqlite3.connect(self.db_path) as c:
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        expected = {
            "files", "messages", "tool_calls", "plan", "platform_settings", "dismissed_tips",
            "codex_turns", "codex_rate_limits",
        }
        self.assertTrue(expected.issubset(tables), f"Missing: {expected - tables}")

    def test_init_is_idempotent(self):
        init_db(self.db_path)

    def test_platform_extension_columns_exist(self):
        init_db(self.db_path)
        with sqlite3.connect(self.db_path) as c:
            message_cols = {row[1] for row in c.execute("PRAGMA table_info(messages)")}
            tool_cols = {row[1] for row in c.execute("PRAGMA table_info(tool_calls)")}
        self.assertTrue({
            "reasoning_output_tokens", "context_window", "response_text", "source_metadata_json",
            "agent_type", "agent_name", "parent_session_id",
        }.issubset(message_cols))
        self.assertTrue({"call_id", "tool_kind"}.issubset(tool_cols))
        with sqlite3.connect(self.db_path) as c:
            agent_cols = {row[1] for row in c.execute("PRAGMA table_info(agents)")}
        self.assertTrue({"parent_session_id", "agent_name"}.issubset(agent_cols))
        init_db(self.db_path)

    def test_connect_returns_row_factory(self):
        init_db(self.db_path)
        with connect(self.db_path) as c:
            r = c.execute("SELECT 1 AS one").fetchone()
        self.assertEqual(r["one"], 1)

    def test_files_cursor_migration_preserves_existing_rows_as_claude(self):
        with sqlite3.connect(self.db_path) as c:
            c.execute("""
              CREATE TABLE files (
                path TEXT PRIMARY KEY,
                mtime REAL NOT NULL,
                bytes_read INTEGER NOT NULL,
                scanned_at REAL NOT NULL
              )
            """)
            c.execute(
                "INSERT INTO files (path, mtime, bytes_read, scanned_at) VALUES ('/tmp/session.jsonl', 1.0, 42, 2.0)"
            )
            c.commit()

        init_db(self.db_path)

        with sqlite3.connect(self.db_path) as c:
            info = list(c.execute("PRAGMA table_info(files)"))
            pk_cols = [row[1] for row in sorted((row for row in info if row[5]), key=lambda row: row[5])]
            row = c.execute("SELECT source, path, mtime, bytes_read, scanned_at FROM files").fetchone()
        self.assertEqual(pk_cols, ["source", "path"])
        self.assertEqual(row, ("claude", "/tmp/session.jsonl", 1.0, 42, 2.0))

    def test_adding_codex_custom_schema_forces_one_codex_backfill(self):
        init_db(self.db_path)
        with sqlite3.connect(self.db_path) as c:
            c.execute(
                "INSERT INTO files (source,path,mtime,bytes_read,scanned_at) VALUES ('claude','c.jsonl',1,1,1)"
            )
            c.execute(
                "INSERT INTO files (source,path,mtime,bytes_read,scanned_at) VALUES ('codex','x.jsonl',1,1,1)"
            )
            c.execute("DROP TABLE codex_turns")
            c.execute("DROP TABLE codex_rate_limits")
            c.commit()

        init_db(self.db_path)

        with sqlite3.connect(self.db_path) as c:
            cursors = list(c.execute("SELECT source,path FROM files ORDER BY source"))
        self.assertEqual(cursors, [("claude", "c.jsonl")])


if __name__ == "__main__":
    unittest.main()
