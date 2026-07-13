import http.server
import json
import os
import socket
import sqlite3
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from datetime import datetime

from token_dashboard.db import init_db
from token_dashboard.server import build_handler


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        init_db(self.db)
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO messages (uuid, parent_uuid, session_id, project_slug, type, timestamp, model, input_tokens, output_tokens, cache_read_tokens, cache_create_5m_tokens, cache_create_1h_tokens, prompt_text, prompt_chars) VALUES ('u',NULL,'s','p','user','2026-04-19T00:00:00Z',NULL,0,0,0,0,0,'hi',2)")
            c.execute("INSERT INTO messages (uuid, parent_uuid, session_id, project_slug, type, timestamp, model, input_tokens, output_tokens, cache_read_tokens, cache_create_5m_tokens, cache_create_1h_tokens) VALUES ('a','u','s','p','assistant','2026-04-19T00:00:01Z','claude-haiku-4-5',1,1,0,0,0)")
            c.commit()
        self.port = _free_port()
        H = build_handler(self.db, projects_dir="/nonexistent")
        self.httpd = http.server.HTTPServer(("127.0.0.1", self.port), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()

    def _get(self, path):
        return urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}").read()

    def _post(self, path, body):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST",
        )
        return urllib.request.urlopen(req).read()

    def test_index_html(self):
        body = self._get("/")
        self.assertIn(b"Token Dashboard", body)

    def test_overview_json(self):
        body = json.loads(self._get("/api/overview"))
        self.assertIn("sessions", body)
        self.assertEqual(body["sessions"], 1)

    def test_prompts_json(self):
        body = json.loads(self._get("/api/prompts?limit=10"))
        self.assertIsInstance(body, list)

    def test_projects_json(self):
        body = json.loads(self._get("/api/projects"))
        self.assertIsInstance(body, list)
        self.assertEqual(body[0]["project_slug"], "p")

    def test_plan_json(self):
        body = json.loads(self._get("/api/plan"))
        self.assertIn("plan", body)
        self.assertIn("pricing", body)

    def test_platform_selection_is_persisted(self):
        before = json.loads(self._get("/api/platforms"))
        self.assertFalse(before["configured"])
        self._post("/api/platforms", {"platforms": [
            {"source": "claude", "enabled": False, "plan": "api"},
            {"source": "codex", "enabled": True, "plan": "plus"},
        ]})
        after = json.loads(self._get("/api/platforms"))
        self.assertEqual(after["enabled_sources"], ["codex"])
        codex = next(p for p in after["platforms"] if p["source"] == "codex")
        self.assertEqual(codex["plan"], "plus")
        self.assertIsNone(codex["cache"]["create_tokens"])

    def test_platform_api_rejects_arbitrary_scan_root(self):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self._post("/api/platforms", {"platforms": [
                {"source": "claude", "enabled": True, "plan": "api", "scan_root": "C:\\"},
                {"source": "codex", "enabled": False, "plan": "api"},
            ]})
        self.assertEqual(raised.exception.code, 400)

    def test_source_filter_json(self):
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO messages (uuid, parent_uuid, session_id, project_slug, type, timestamp, model, input_tokens, output_tokens, cache_read_tokens, cache_create_5m_tokens, cache_create_1h_tokens, source) VALUES ('cu',NULL,'cs','cp','user','2026-04-20T00:00:00Z',NULL,0,0,0,0,0,'codex')")
            c.execute("INSERT INTO messages (uuid, parent_uuid, session_id, project_slug, type, timestamp, model, input_tokens, output_tokens, cache_read_tokens, cache_create_5m_tokens, cache_create_1h_tokens, source) VALUES ('ca','cu','cs','cp','assistant','2026-04-20T00:00:01Z','gpt-5.4',7,8,0,0,0,'codex')")
            c.commit()
        body = json.loads(self._get("/api/overview?source=codex"))
        self.assertEqual(body["sessions"], 1)
        self.assertEqual(body["input_tokens"], 7)
        self.assertGreater(body["cost_usd"], 0)

    def test_agent_runs_api_and_session_filter(self):
        with sqlite3.connect(self.db) as c:
            c.execute("""
              INSERT INTO agents
                (source, session_id, agent_id, project_slug, agent_type, agent_name, parent_session_id)
              VALUES ('codex','child','child','cp','reviewer','Ada','parent')
            """)
            c.execute("""
              INSERT INTO messages
                (uuid, session_id, project_slug, type, timestamp, model, input_tokens,
                 output_tokens, reasoning_output_tokens, source, is_sidechain, agent_id,
                 agent_type, agent_name, parent_session_id)
              VALUES ('child-a','child','cp','assistant','2026-04-20T00:00:01Z','gpt-5.4',
                      12,4,2,'codex',1,'child','reviewer','Ada','parent')
            """)
            c.commit()
        runs = json.loads(self._get("/api/agent-runs?source=codex"))
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["agent_name"], "Ada")
        self.assertEqual(runs[0]["input_tokens"], 12)
        self.assertEqual(runs[0]["total_tokens"], 16)
        turns = json.loads(self._get("/api/sessions/child?source=codex&agent_id=child"))
        self.assertEqual([turn["uuid"] for turn in turns], ["child-a"])

    def test_pipeline_and_codex_custom_endpoints(self):
        with sqlite3.connect(self.db) as c:
            c.execute("""
              INSERT INTO codex_turns
                (session_id, turn_id, project_slug, status, duration_ms, last_event_at)
              VALUES ('cs','ct','cp','completed',1200,'2026-04-20T00:00:00Z')
            """)
            c.execute("""
              INSERT INTO codex_rate_limits
                (snapshot_id, session_id, turn_id, timestamp, plan_type, primary_used_percent)
              VALUES ('rate-1','cs','ct','2026-04-20T00:00:01Z','plus',42)
            """)
            c.commit()
        pipelines = json.loads(self._get("/api/pipelines"))
        self.assertEqual([row["source"] for row in pipelines], ["claude", "codex"])
        summary = json.loads(self._get("/api/codex/summary"))
        self.assertEqual(summary["completed_turns"], 1)
        turns = json.loads(self._get("/api/codex/turns"))
        self.assertEqual(turns[0]["duration_ms"], 1200)
        limits = json.loads(self._get("/api/codex/rate-limits?limit=1"))
        self.assertEqual(limits[0]["primary_used_percent"], 42)

    def test_tips_source_filter_json(self):
        ts = datetime.utcnow().isoformat()
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, source) VALUES ('tip-m','tip-s','tip-p','assistant',?,'codex')", (ts,))
            for i in range(12):
                c.execute("INSERT INTO tool_calls (message_uuid, session_id, project_slug, tool_name, target, timestamp, is_error, source) VALUES ('tip-m','tip-s','tip-p','Read','src/app.py',?,0,'codex')", (ts,))
            c.commit()
        claude = json.loads(self._get("/api/tips?source=claude"))
        codex = json.loads(self._get("/api/tips?source=codex"))
        self.assertEqual(claude, [])
        self.assertTrue(any(t["category"] == "repeat-file" for t in codex))

    def test_head_returns_200_not_501(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/", method="HEAD")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.read(), b"")

    def test_head_api_endpoint(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/overview", method="HEAD")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.read(), b"")


if __name__ == "__main__":
    unittest.main()
