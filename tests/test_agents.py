import json
import os
import shutil
import sqlite3
import tempfile
import unittest

from token_dashboard.db import init_db, agent_breakdown, agent_run_breakdown
from token_dashboard.scanner import scan_dir


def _msg(uuid, agent_id, session, in_tok, out_tok, model="claude-sonnet-5"):
    return {
        "type": "assistant", "uuid": uuid, "sessionId": session,
        "agentId": agent_id, "isSidechain": True,
        "timestamp": "2026-07-10T00:00:01Z",
        "message": {"id": "m_" + uuid, "model": model,
                    "usage": {"input_tokens": in_tok, "output_tokens": out_tok,
                              "cache_read_input_tokens": 10,
                              "cache_creation": {"ephemeral_5m_input_tokens": 5,
                                                 "ephemeral_1h_input_tokens": 0}}},
    }


class AgentAttributionTests(unittest.TestCase):
    """Subagent transcripts + meta.json sidecars land in the agents table and
    agent_breakdown groups sidechain usage by agent name."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        self.proj_root = os.path.join(self.tmp, "projects")
        sub = os.path.join(self.proj_root, "C--work-sample", "sess-1", "subagents")
        os.makedirs(sub)
        with open(os.path.join(sub, "agent-abc123.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps(_msg("u1", "abc123", "sess-1", 100, 50)) + "\n")
            f.write(json.dumps(_msg("u2", "abc123", "sess-1", 200, 25)) + "\n")
        with open(os.path.join(sub, "agent-abc123.meta.json"), "w", encoding="utf-8") as f:
            json.dump({"agentType": "step-coder", "description": "Implement step 1",
                       "toolUseId": "toolu_x", "spawnDepth": 1}, f)
        # a second agent with no meta sidecar -> grouped as (unknown)
        with open(os.path.join(sub, "agent-nometa.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps(_msg("u3", "nometa", "sess-1", 7, 3)) + "\n")
        init_db(self.db)

    def test_meta_sidecar_ingested(self):
        scan_dir(self.proj_root, self.db)
        with sqlite3.connect(self.db) as c:
            row = c.execute(
                "SELECT agent_type, description, project_slug, spawn_depth "
                "FROM agents WHERE agent_id='abc123'").fetchone()
        self.assertEqual(row, ("step-coder", "Implement step 1", "C--work-sample", 1))

    def test_agent_breakdown_groups_by_type(self):
        scan_dir(self.proj_root, self.db)
        rows = agent_breakdown(self.db)
        by_type = {r["agent_type"]: r for r in rows}
        coder = by_type["step-coder"]
        self.assertEqual(coder["runs"], 1)
        self.assertEqual(coder["input_tokens"], 300)
        self.assertEqual(coder["output_tokens"], 75)
        self.assertEqual(coder["cache_read_tokens"], 20)
        self.assertEqual(coder["cache_create_5m_tokens"], 10)
        # Every agent is counted even if its provider omitted a sidecar.
        self.assertIn("(unknown)", by_type)
        self.assertEqual(by_type["(unknown)"]["input_tokens"], 7)
        self.assertEqual(len(rows), 2)

    def test_each_agent_run_has_its_own_saved_totals(self):
        scan_dir(self.proj_root, self.db)
        rows = {r["agent_id"]: r for r in agent_run_breakdown(self.db)}
        self.assertEqual(set(rows), {"abc123", "nometa"})
        self.assertEqual(rows["abc123"]["input_tokens"], 300)
        self.assertEqual(rows["abc123"]["output_tokens"], 75)
        self.assertEqual(rows["nometa"]["input_tokens"], 7)

    def test_rescan_is_idempotent(self):
        scan_dir(self.proj_root, self.db)
        scan_dir(self.proj_root, self.db)
        with sqlite3.connect(self.db) as c:
            n = c.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
        self.assertEqual(n, 2)
        rows = agent_breakdown(self.db)
        self.assertEqual(rows[0]["input_tokens"], 300)


class CodexAgentAttributionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "codex.db")
        self.sessions_root = os.path.join(self.tmp, "sessions")
        day = os.path.join(self.sessions_root, "2026", "07", "10")
        os.makedirs(day)
        fixture = os.path.join(os.path.dirname(__file__), "fixtures", "codex_agent_session.jsonl")
        with open(fixture, encoding="utf-8") as src, open(
            os.path.join(day, "codex-agent.jsonl"), "w", encoding="utf-8"
        ) as dst:
            dst.write(src.read())
        init_db(self.db)

    def test_child_thread_identity_and_exact_tokens_are_saved(self):
        scan_dir(self.sessions_root, self.db, source="codex")
        with sqlite3.connect(self.db) as c:
            identity = c.execute(
                "SELECT session_id, agent_id, agent_type, agent_name, parent_session_id "
                "FROM agents WHERE source='codex'"
            ).fetchone()
        self.assertEqual(
            identity,
            ("codex-child-1", "codex-child-1", "reviewer", "Ada", "codex-parent-1"),
        )
        rows = agent_run_breakdown(self.db, source="codex")
        self.assertEqual(len(rows), 1)
        run = rows[0]
        self.assertEqual(run["input_tokens"], 80)
        self.assertEqual(run["cache_read_tokens"], 20)
        self.assertEqual(run["output_tokens"], 10)
        self.assertEqual(run["reasoning_output_tokens"], 3)
        self.assertEqual(run["total_tokens"], 110)
        self.assertEqual(run["model_calls"], 1)
        self.assertEqual(run["peak_context_utilization"], 0.1)

    def test_codex_agent_rescan_is_idempotent(self):
        scan_dir(self.sessions_root, self.db, source="codex")
        scan_dir(self.sessions_root, self.db, source="codex")
        rows = agent_run_breakdown(self.db, source="codex")
        self.assertEqual(len(rows), 1)


class CodexForkedChildTests(unittest.TestCase):
    def test_inherited_parent_history_is_not_counted_again(self):
        tmp = tempfile.mkdtemp()
        root = os.path.join(tmp, "sessions")
        os.makedirs(root)
        fixture = os.path.join(os.path.dirname(__file__), "fixtures", "codex_forked_child.jsonl")
        target = os.path.join(root, "child.jsonl")
        shutil.copyfile(fixture, target)
        db = os.path.join(tmp, "fork.db")
        init_db(db)
        scan_dir(root, db, source="codex")
        rows = agent_run_breakdown(db, source="codex")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["session_id"], "child-1")
        self.assertEqual(rows[0]["parent_session_id"], "parent-1")
        self.assertEqual(rows[0]["input_tokens"], 100)
        self.assertEqual(rows[0]["cache_read_tokens"], 20)
        self.assertEqual(rows[0]["output_tokens"], 10)


if __name__ == "__main__":
    unittest.main()
