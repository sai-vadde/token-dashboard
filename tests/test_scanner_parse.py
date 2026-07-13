import json
import os
import unittest
from token_dashboard.scanner import parse_codex_record, parse_record

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _load(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return json.load(f)


class ParseRecordTests(unittest.TestCase):
    def test_parses_assistant_usage(self):
        msg, tools = parse_record(_load("simple_assistant.json"), project_slug="proj-x")
        self.assertEqual(msg["uuid"], "msg-1")
        self.assertEqual(msg["session_id"], "sess-1")
        self.assertEqual(msg["project_slug"], "proj-x")
        self.assertEqual(msg["model"], "claude-opus-4-7")
        self.assertEqual(msg["input_tokens"], 10)
        self.assertEqual(msg["output_tokens"], 5)
        self.assertEqual(msg["cache_read_tokens"], 100)
        self.assertEqual(msg["cache_create_5m_tokens"], 30)
        self.assertEqual(msg["cache_create_1h_tokens"], 20)
        self.assertEqual(msg["is_sidechain"], 0)
        self.assertIsNone(msg["agent_id"])
        self.assertEqual(tools, [])


class ClaudeContextWindowTests(unittest.TestCase):
    """Claude transcripts omit the context window, so it is inferred per turn."""

    def _assistant(self, input_tokens, cache_read_tokens, model="claude-opus-4-7"):
        return {
            "type": "assistant", "uuid": "u", "sessionId": "s", "timestamp": "t",
            "message": {"model": model, "usage": {
                "input_tokens": input_tokens, "output_tokens": 1,
                "cache_read_input_tokens": cache_read_tokens,
            }},
        }

    def test_standard_window_for_normal_load(self):
        msg, _ = parse_record(self._assistant(10, 100), project_slug="p")
        self.assertEqual(msg["context_window"], 200_000)

    def test_one_million_window_when_load_exceeds_200k(self):
        # A turn whose prompt exceeds 200K can only be the 1M beta window, so
        # utilization stays <= 100% instead of the impossible >200%.
        msg, _ = parse_record(self._assistant(50_000, 400_000), project_slug="p")
        self.assertEqual(msg["context_window"], 1_000_000)

    def test_no_window_without_model(self):
        rec = {"type": "user", "uuid": "u", "sessionId": "s", "timestamp": "t",
               "message": {"role": "user", "content": "hi"}}
        msg, _ = parse_record(rec, project_slug="p")
        self.assertIsNone(msg["context_window"])


class ToolExtractionTests(unittest.TestCase):
    def test_extracts_tool_uses(self):
        rec = _load("tool_use_assistant.json")
        msg, tools = parse_record(rec, project_slug="p")
        self.assertEqual(len(tools), 2)
        names = [t["tool_name"] for t in tools]
        self.assertEqual(names, ["Read", "Bash"])
        self.assertEqual(tools[0]["target"], "C:/proj/foo.py")
        self.assertEqual(tools[1]["target"], "npm run lint")
        self.assertIsNotNone(msg["tool_calls_json"])
        parsed = json.loads(msg["tool_calls_json"])
        self.assertEqual(parsed[0]["name"], "Read")
        self.assertEqual(parsed[1]["target"], "npm run lint")


class SidechainTests(unittest.TestCase):
    def test_is_sidechain_flag_propagates(self):
        rec = {
            "type": "assistant", "uuid": "u", "sessionId": "s",
            "timestamp": "t", "isSidechain": True, "agentId": "agent-explore-1",
            "message": {"model": "claude-sonnet-4-6", "usage": {"input_tokens": 1, "output_tokens": 1}},
        }
        msg, _ = parse_record(rec, project_slug="p")
        self.assertEqual(msg["is_sidechain"], 1)
        self.assertEqual(msg["agent_id"], "agent-explore-1")

    def test_tool_result_estimates_tokens(self):
        rec = {
            "type": "user", "uuid": "u2", "sessionId": "s",
            "timestamp": "t", "isSidechain": False,
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu1", "content": "x" * 4000, "is_error": False}
            ]},
        }
        msg, tools = parse_record(rec, project_slug="p")
        self.assertEqual(msg["type"], "user")
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["tool_name"], "_tool_result")
        self.assertAlmostEqual(tools[0]["result_tokens"], 1000, delta=10)


class CodexParseTests(unittest.TestCase):
    def test_custom_exec_expands_nested_tools_and_attaches_result(self):
        context = {"session_id": "s", "turn_id": "t", "model": "gpt-5", "cwd": "/work"}
        call = {
            "type": "response_item", "timestamp": "2026-01-01T00:00:00Z",
            "payload": {"type": "custom_tool_call", "name": "exec", "call_id": "c1",
                        "input": "await tools.shell_command({}); await tools.web__run({});"},
        }
        output = {
            "type": "response_item", "timestamp": "2026-01-01T00:00:01Z",
            "payload": {"type": "custom_tool_call_output", "call_id": "c1", "output": "x" * 80},
        }
        usage = {
            "type": "event_msg", "timestamp": "2026-01-01T00:00:02Z",
            "payload": {"type": "token_count", "info": {"model_context_window": 1000,
                "last_token_usage": {"input_tokens": 100, "cached_input_tokens": 20,
                                     "output_tokens": 10, "reasoning_output_tokens": 4}}},
        }
        self.assertEqual(parse_codex_record(call, "fallback", context, 1), (None, []))
        self.assertEqual(parse_codex_record(output, "fallback", context, 2), (None, []))
        msg, tools = parse_codex_record(usage, "fallback", context, 3)
        self.assertEqual([tool["tool_name"] for tool in tools], ["shell_command", "web__run"])
        self.assertEqual(sum(tool["result_tokens"] for tool in tools), 20)
        self.assertEqual(msg["reasoning_output_tokens"], 4)
        self.assertEqual(msg["context_window"], 1000)

    def test_session_meta_context_window_dict_does_not_leak_into_sqlite_row(self):
        context = {}
        meta = {
            "type": "session_meta", "timestamp": "2026-01-01T00:00:00Z",
            "payload": {
                "id": "s",
                "cwd": "/work",
                "context_window": {"window_id": "019f48b0-c366-76a3-b8e1-dcb7ff6fff3e"},
            },
        }
        user = {
            "type": "event_msg", "timestamp": "2026-01-01T00:00:01Z",
            "payload": {"type": "user_message", "message": "hello"},
        }
        usage = {
            "type": "event_msg", "timestamp": "2026-01-01T00:00:02Z",
            "payload": {"type": "token_count", "info": {
                "last_token_usage": {"input_tokens": 10, "output_tokens": 5}
            }},
        }
        self.assertEqual(parse_codex_record(meta, "fallback", context, 1), (None, []))
        msg, _ = parse_codex_record(user, "fallback", context, 2)
        self.assertIsNone(msg["context_window"])
        msg, _ = parse_codex_record(usage, "fallback", context, 3)
        self.assertIsNone(msg["context_window"])


if __name__ == "__main__":
    unittest.main()
