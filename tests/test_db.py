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
        expected = {"files", "messages", "tool_calls", "plan", "dismissed_tips"}
        self.assertTrue(expected.issubset(tables), f"Missing: {expected - tables}")

    def test_init_is_idempotent(self):
        init_db(self.db_path)
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


if __name__ == "__main__":
    unittest.main()
