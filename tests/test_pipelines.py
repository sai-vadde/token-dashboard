import os
import shutil
import tempfile
import unittest

from token_dashboard.codex_store import codex_rate_limit_history, codex_summary, codex_turn_breakdown
from token_dashboard.db import init_db
from token_dashboard.pipeline import get_pipeline, registered_sources
from token_dashboard.scanner import scan_dir


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


class PipelineRegistryTests(unittest.TestCase):
    def test_sources_have_separate_registered_pipelines(self):
        self.assertEqual(registered_sources(), ["claude", "codex"])
        claude = get_pipeline("claude")
        codex = get_pipeline("codex")
        self.assertFalse(claude.replay_changed_files)
        self.assertTrue(codex.replay_changed_files)
        self.assertIn("rate-limits", codex.features)
        self.assertNotIn("rate-limits", claude.features)

    def test_unknown_source_is_rejected(self):
        with self.assertRaises(ValueError):
            get_pipeline("unknown")


class CodexCustomAnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "codex.db")
        self.root = os.path.join(self.tmp, "sessions")
        day = os.path.join(self.root, "2026", "07", "10")
        os.makedirs(day)
        shutil.copy(
            os.path.join(FIXTURES, "codex_session.jsonl"),
            os.path.join(day, "session.jsonl"),
        )
        init_db(self.db)
        scan_dir(self.root, self.db, source="codex")

    def test_summary_combines_lifecycle_and_global_usage(self):
        summary = codex_summary(self.db)
        self.assertEqual(summary["turns"], 1)
        self.assertEqual(summary["completed_turns"], 1)
        self.assertEqual(summary["model_calls"], 1)
        self.assertEqual(summary["avg_duration_ms"], 6000)
        self.assertEqual(summary["avg_ttft_ms"], 350)
        self.assertEqual(summary["reasoning_output_tokens"], 5)
        self.assertEqual(summary["efforts"], {"high": 1})
        self.assertEqual(summary["approval_policies"], {"on-request": 1})

    def test_logical_turn_aggregates_model_tools_and_context(self):
        turns = codex_turn_breakdown(self.db)
        self.assertEqual(len(turns), 1)
        turn = turns[0]
        self.assertEqual(turn["status"], "completed")
        self.assertEqual(turn["model_calls"], 1)
        self.assertEqual(turn["tool_calls"], 2)
        self.assertEqual(turn["total_tokens"], 160)
        self.assertEqual(turn["reasoning_output_tokens"], 5)

    def test_rate_limit_history_is_persisted_idempotently(self):
        first = codex_rate_limit_history(self.db)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["primary_used_percent"], 76.0)
        scan_dir(self.root, self.db, source="codex")
        self.assertEqual(len(codex_rate_limit_history(self.db)), 1)


if __name__ == "__main__":
    unittest.main()
