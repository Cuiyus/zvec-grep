"""Synthetic contract tests; fresh native Docker evidence validates the shape."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from native_claude.accounting import analyze, FIELDS


MODEL = "qwen3.8-max"


def usage(n=1):
    return dict(zip(FIELDS, (n, n + 1, n + 2, n + 3)))


def assistant(mid, blocks, n=1, agent=None, stop="end_turn", uuid=None):
    row = dict(type="assistant", uuid=uuid or "uuid-" + mid, sessionId="root-1",
               message=dict(id=mid, role="assistant", model=MODEL, content=blocks,
                            usage=usage(n), stop_reason=stop))
    if agent:
        row.update(agentId=agent, isSidechain=True)
    return row


def text(s):
    return dict(type="text", text=s)


def tool(tid, name="Read"):
    return dict(type="tool_use", id=tid, name=name, input={"file_path": "/workspace/source/file"})


def result(tid, content="read content", agent=None, child=None, failed=False):
    row = dict(type="user", uuid="uuid-result-" + tid, sessionId="root-1",
               message=dict(role="user", content=[dict(type="tool_result", tool_use_id=tid,
                                                        content=content, is_error=failed)]))
    if agent:
        row.update(agentId=agent, isSidechain=True)
    if child:
        row["toolUseResult"] = dict(agentId=child, content=[text(content)], status="completed")
    return row


class Fixture:
    def __init__(self, path):
        self.path = Path(path)
        self.root = [assistant("root-read", [tool("read-1")], stop="tool_use"),
                     result("read-1"), assistant("root-final", [text("FINAL")], n=2)]
        self.children = {}
        self.extra_stream = []
        self.result = dict(type="result", subtype="success", is_error=False,
                           session_id="root-1", result="FINAL", stop_reason="end_turn",
                           terminal_reason="completed", usage=usage(10000))

    def child(self):
        self.root[0] = assistant("root-spawn", [tool("spawn-1", "Agent")], stop="tool_use")
        self.root[1] = result("spawn-1", "CHILD FINAL", child="child-a")
        self.children["child-a"] = [assistant("child-read", [tool("child-read-1")], n=3,
                                               agent="child-a", stop="tool_use"),
                                    result("child-read-1", agent="child-a"),
                                    assistant("child-final", [text("CHILD FINAL")], n=4, agent="child-a")]
        return self

    def write(self):
        project = self.path / "sessions/projects/-workspace"
        project.mkdir(parents=True, exist_ok=True)
        self._write(project / "root-1.jsonl", self.root)
        for aid, rows in self.children.items():
            child = project / "root-1/subagents" / f"agent-{aid}.jsonl"
            child.parent.mkdir(parents=True, exist_ok=True)
            self._write(child, rows)
        stream = [dict(type="system", subtype="init", model=MODEL, session_id="root-1")]
        for row in self.root:
            if row.get("type") == "assistant":
                stream.append(dict(type="assistant", message=copy.deepcopy(row["message"]),
                                   parent_tool_use_id=None, session_id="root-1"))
        stream += self.extra_stream + [self.result]
        self._write(self.path / "claude-code.txt", stream)
        return analyze(self.path)

    @staticmethod
    def _write(path, rows):
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))


class AccountingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.f = Fixture(self.tmp.name)

    def assert_complete(self, report):
        self.assertTrue(report["complete"], report["errors"])

    def codes(self, report):
        self.assertFalse(report["complete"])
        return {e["code"] for e in report["errors"]}

    def test_root_only_terminal_usage_and_result_not_doublecounted(self):
        r = self.f.write()
        self.assert_complete(r)
        self.assertTrue(r["root_only"])
        self.assertEqual((r["root_count"], r["child_count"]), (1, 0))
        self.assertEqual(r["usage"]["llm_calls"], 2)
        self.assertEqual(r["usage"]["input_tokens"], 15)
        self.assertEqual(r["usage"]["output_tokens"], 9)
        self.assertEqual(r["final"]["text"], "FINAL")
        self.assertFalse(r["root_result_usage_counted"])
        self.assertTrue(all(m["session_files"] for m in r["messages"]))

    def test_child_and_recursive_tree_count_each_message_once(self):
        self.f.child()
        self.f.children["child-a"][0] = assistant("child-spawn", [tool("spawn-2", "Task")], n=3,
                                                     agent="child-a", stop="tool_use")
        self.f.children["child-a"][1] = result("spawn-2", "GRANDCHILD FINAL", agent="child-a", child="child-b")
        self.f.children["child-b"] = [assistant("grand-final", [text("GRANDCHILD FINAL")], n=5, agent="child-b")]
        r = self.f.write()
        self.assert_complete(r)
        self.assertFalse(r["root_only"])
        self.assertEqual(r["child_count"], 2)
        self.assertEqual(r["usage"]["llm_calls"], 5)
        self.assertEqual(r["usage"]["uncached_input_tokens"], 15)
        self.assertEqual(r["usage"]["output_tokens"], 30)
        self.assertEqual(r["children"][1]["parent_session"], "agent:child-a")

    def test_split_blocks_terminal_update_and_exact_replays_deduplicated(self):
        first = assistant("root-read", [dict(type="thinking", thinking="private fixture")], stop=None, uuid="split-1")
        first["message"]["usage"] = dict(input_tokens=1, output_tokens=0)
        tool_event = copy.deepcopy(self.f.root[0])
        self.f.root = [first, tool_event, self.f.root[1], self.f.root[2], copy.deepcopy(first)]
        r = self.f.write()
        self.assert_complete(r)
        self.assertEqual(r["usage"]["llm_calls"], 2)
        self.assertEqual(r["usage"]["tool_calls"], 1)
        self.assertEqual(r["usage"]["output_tokens"], 9)
        self.assertNotIn("private fixture", json.dumps(r))

    def test_terminal_usage_missing_fields_does_not_merge_previous_fields(self):
        last = copy.deepcopy(self.f.root[-1])
        last["message"]["usage"] = {"input_tokens": 2, "output_tokens": 5}
        self.f.root.append(last)
        r = self.f.write()
        self.assertIn("invalid_usage_fields", self.codes(r))
        self.assertEqual(r["usage"]["unknown_usage_messages"], 1)
        self.assertEqual(r["usage"]["counted_usage_messages"], 1)

    def test_invalid_numeric_usage_values_fail_closed(self):
        for value in (None, True, "2", -1, float("inf"), float("nan")):
            with self.subTest(value=value):
                self.f.root[-1]["message"]["usage"]["output_tokens"] = value
                r = self.f.write()
                self.assertFalse(r["complete"])
                self.assertTrue(r["usage_partial"])
                self.assertGreaterEqual(r["usage"]["counted_usage_messages"], 1)

    def test_usage_regression_across_partial_update(self):
        initial = copy.deepcopy(self.f.root[-1])
        initial["message"]["usage"]["output_tokens"] = 7
        middle = copy.deepcopy(initial)
        middle["message"]["usage"] = {"input_tokens": 2}
        self.f.root[-1:-1] = [initial, middle]
        self.assertIn("usage_regression", self.codes(self.f.write()))

    def test_missing_or_error_root_result_and_final_mismatch(self):
        self.f.result["is_error"] = True
        self.assertIn("root_result_not_success", self.codes(self.f.write()))
        self.f.result["is_error"] = False
        self.f.result["result"] = "FINAL "
        self.assertIn("final_text_mismatch", self.codes(self.f.write()))
        self.f.write()
        path = self.f.path / "claude-code.txt"
        path.write_text("\n".join(path.read_text().splitlines()[:-1]) + "\n")
        self.assertIn("expected_one_root_result", self.codes(analyze(self.f.path)))

    def test_tool_completion_and_terminal_failure(self):
        self.f.root[1]["message"]["content"][0]["is_error"] = True
        r = self.f.write()
        self.assert_complete(r)
        self.assertEqual(r["usage"]["tool_errors"], 1)
        self.f.root.pop(1)
        self.assertIn("unfinished_tool", self.codes(self.f.write()))

    def test_orphan_tool_and_async_incomplete(self):
        self.f.root[1]["message"]["content"][0]["tool_use_id"] = "unknown"
        self.assertIn("orphan_tool_result", self.codes(self.f.write()))
        self.f.root[1]["message"]["content"][0]["tool_use_id"] = "read-1"
        self.f.root[1]["toolUseResult"] = {"status": "async_launched"}
        self.assertIn("unfinished_tool_metadata", self.codes(self.f.write()))

    def test_missing_child_file_link_or_child_result(self):
        self.f.child()
        self.f.children.clear()
        self.assertIn("missing_child_transcript", self.codes(self.f.write()))
        self.f.child()
        del self.f.root[1]["toolUseResult"]
        codes = self.codes(self.f.write())
        self.assertIn("missing_child_link", codes)
        self.assertIn("unlinked_or_cyclic_child", codes)
        self.f.child()
        self.f.root[1]["toolUseResult"]["content"] = [text("DIFFERENT")]
        self.assertIn("child_final_not_in_parent_result", self.codes(self.f.write()))

    def test_duplicate_child_parent_rejected(self):
        self.f.child()
        self.f.root[2:2] = [assistant("spawn-other", [tool("spawn-2", "Agent")], stop="tool_use"),
                            result("spawn-2", "CHILD FINAL", child="child-a")]
        self.assertIn("child_has_multiple_parents", self.codes(self.f.write()))

    def test_conflicting_uuid_tool_and_foreign_model(self):
        changed = copy.deepcopy(self.f.root[0])
        changed["message"]["content"][0]["input"] = {"file_path": "other"}
        self.f.root.insert(1, changed)
        codes = self.codes(self.f.write())
        self.assertIn("conflicting_event_uuid", codes)
        self.assertIn("conflicting_tool_use", codes)
        self.f.root[-1]["message"]["model"] = "other-model"
        self.assertIn("assistant_identity_mismatch", self.codes(self.f.write()))

    def test_terminal_stop_and_native_session_mismatch(self):
        self.f.root[-1]["message"]["stop_reason"] = "max_tokens"
        self.assertIn("nonterminal_assistant_stop", self.codes(self.f.write()))
        self.f.root[-1]["sessionId"] = "another-session"
        self.assertIn("foreign_root_session", self.codes(self.f.write()))

    def test_malformed_json_and_missing_file_preserve_partial_diagnostics(self):
        self.f.write()
        native = self.f.path / "sessions/projects/-workspace/root-1.jsonl"
        with native.open("a") as out:
            out.write("{broken json\n")
        r = analyze(self.f.path)
        self.assertIn("malformed_jsonl", self.codes(r))
        self.assertEqual(r["usage"]["llm_calls"], 2)
        (self.f.path / "claude-code.txt").unlink()
        r = analyze(self.f.path)
        self.assertIn("evidence_unreadable", self.codes(r))
        self.assertEqual(r["usage"]["llm_calls"], 2)

    def test_malformed_stream_message_id_does_not_raise(self):
        self.f.extra_stream = [dict(type="assistant", message={"id": []}),
                               dict(type="assistant", message=[])]
        codes = self.codes(self.f.write())
        self.assertIn("malformed_stream_message_id", codes)
        self.assertIn("malformed_stream_message", codes)

    def test_child_stream_link_and_tool_identity(self):
        self.f.child()
        self.f.extra_stream = [dict(type="assistant", message=copy.deepcopy(self.f.children["child-a"][-1]["message"]),
                                   session_id="root-1", parent_tool_use_id="spawn-1")]
        self.assert_complete(self.f.write())
        self.f.extra_stream[0]["parent_tool_use_id"] = "wrong-parent"
        self.assertIn("stream_child_link_mismatch", self.codes(self.f.write()))
        self.f.children["child-a"][0]["message"]["content"][0]["id"] = "spawn-1"
        self.assertIn("tool_id_in_multiple_sessions", self.codes(self.f.write()))


if __name__ == "__main__":
    unittest.main()
