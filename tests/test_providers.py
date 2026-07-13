import os
import sqlite3
import tempfile
import unittest

from token_dashboard.analytics import platform_analytics
from token_dashboard.db import init_db, overview_totals
from token_dashboard.pricing import load_pricing
from token_dashboard.providers import (
    effective_scan_roots, enabled_sources, platform_catalog,
    platforms_configured, save_platform_settings,
)


PRICING = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "pricing.json"))


class ProviderSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "providers.db")
        init_db(self.db)

    def test_fresh_database_waits_for_platform_setup(self):
        self.assertFalse(platforms_configured(self.db))
        self.assertEqual(enabled_sources(self.db), ["claude", "codex"])
        self.assertEqual(effective_scan_roots(
            self.db, [("claude", "c", self.db), ("codex", "o", self.db)]
        ), [])

    def test_codex_only_selection_persists_and_filters_scan_roots(self):
        save_platform_settings(self.db, [
            {"source": "claude", "enabled": False, "plan": "pro"},
            {"source": "codex", "enabled": True, "plan": "plus"},
        ])
        self.assertTrue(platforms_configured(self.db))
        self.assertEqual(enabled_sources(self.db), ["codex"])
        roots = effective_scan_roots(
            self.db, [("claude", "c", self.db), ("codex", "o", self.db)]
        )
        self.assertEqual(roots, [("codex", "o", self.db)])
        codex = next(p for p in platform_catalog(self.db) if p["source"] == "codex")
        self.assertEqual(codex["plan"], "plus")

    def test_cannot_disable_every_available_platform(self):
        with self.assertRaises(ValueError):
            save_platform_settings(self.db, [
                {"source": "claude", "enabled": False},
                {"source": "codex", "enabled": False},
            ])

    def test_all_analytics_excludes_disabled_historical_rows(self):
        with sqlite3.connect(self.db) as conn:
            for source, model, value in (("claude", "claude-haiku-4-5", 100), ("codex", "gpt-5.4", 30)):
                conn.execute("""INSERT INTO messages
                  (uuid, session_id, project_slug, type, timestamp, model, input_tokens, source)
                  VALUES (?, ?, 'p', 'assistant', '2026-07-11T00:00:00Z', ?, ?, ?)""",
                  (source, source + "-s", model, value, source))
            conn.commit()
        save_platform_settings(self.db, [
            {"source": "claude", "enabled": False, "plan": "api"},
            {"source": "codex", "enabled": True, "plan": "plus"},
        ])
        data = platform_analytics(self.db, load_pricing(PRICING))
        self.assertEqual(data["all"]["input_tokens"], 30)
        self.assertIsNone(next(p for p in data["platforms"] if p["source"] == "codex")["cache"]["create_tokens"])
        self.assertEqual(next(p for p in data["platforms"] if p["source"] == "claude")["cache"]["create_tokens"], 0)


if __name__ == "__main__":
    unittest.main()
