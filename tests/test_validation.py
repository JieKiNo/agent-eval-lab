import json
import tempfile
import unittest
from pathlib import Path

from agent_eval_lab.models import EvalCase
from agent_eval_lab.runner import load_cases


class ValidationTests(unittest.TestCase):
    def test_invalid_payloads_rejected(self):
        valid = {"id": "x", "input": "task", "must_contain": ["ok"]}
        changes = [
            {"id": 1}, {"input": " "}, {"must_contain": "ok"},
            {"must_contain": [""]}, {"unexpected": True},
            {"max_latency_ms": float("nan")}, {"max_latency_ms": float("inf")},
            {"max_latency_ms": True}, {"max_total_tokens": 0},
            {"max_total_tokens": 1.5}, {"expected_refusal": "false"},
            {"expected_arguments": {"a": 1}}, {"metadata": []},
            {"expected_tool": "send", "forbidden_tools": ["send"]},
            {"must_not_contain": ["OK"]}, {"metadata": {"security": {}}},
            {"allowed_tools": "tool"}, {"allowed_tools": [""]},
            {"allowed_tools": [], "expected_tool": "tool"},
            {"allowed_tools": ["tool"], "forbidden_tools": ["tool"]},
            {"argument_matchers": {"x": "exact"}}, {"argument_matchers": []},
            {"expected_tool_result": {}}, {"expected_tool_result": {"ok": True}},
            {"expected_state": []}, {"expected_state": {}},
            {"expected_output": 4}, {"require_tool_results": "true"},
            {"required_tools": "tool"}, {"tool_sequence": [""]},
            {"allowed_tools": ["a"], "required_tools": ["b"]},
            {"allowed_tools": ["a"], "tool_sequence": ["b"]},
            {"required_tools": ["a"], "forbidden_tools": ["a"]},
            {"max_tool_calls": -1}, {"max_tool_calls": 1.5},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises((ValueError, TypeError)):
                EvalCase.from_dict(valid | change)
        with self.assertRaises(ValueError):
            EvalCase("x", "task")

    def test_arithmetic_matcher_validation(self):
        for value in ("broken(", "foo()", "2**1000", "True", "1e999", 4):
            with self.subTest(value=value), self.assertRaises(ValueError):
                EvalCase("x", "task", expected_tool="calc", expected_arguments={"expression": value},
                         argument_matchers={"expression": "arithmetic_syntax"})
        with self.assertRaises(ValueError):
            EvalCase("x", "task", expected_tool="calc", expected_arguments={"expression": "2+2"},
                     argument_matchers={"expression": "unknown"})

    def test_new_conditions_are_evaluable_and_round_trip(self):
        from dataclasses import asdict
        for condition in ({"allowed_tools": []}, {"expected_output": ""},
                          {"expected_state": {"done": True}}, {"require_tool_results": True},
                          {"required_tools": ["a"]}, {"tool_sequence": ["a", "b"]},
                          {"max_tool_calls": 0}):
            with self.subTest(condition=condition):
                case = EvalCase.from_dict({"id": "x", "input": "task"} | condition)
                self.assertEqual(case, EvalCase.from_dict(json.loads(json.dumps(asdict(case)))))

    def test_duplicate_and_malformed_lines_have_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cases.jsonl"
            row = json.dumps({"id": "x", "input": "task", "must_contain": ["ok"]})
            for content in (row + "\n" + row, row + "\n{broken"):
                path.write_text(content, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, r"E_INPUT_INVALID.*:2:"):
                    load_cases(path)

    def test_empty_dataset_rejected_and_bom_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cases.jsonl"
            path.write_text("\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "dataset is empty"):
                load_cases(path)
            path.write_text(json.dumps({"id": "x", "input": "task", "expected_refusal": False}), encoding="utf-8-sig")
            self.assertFalse(load_cases(path)[0].expected_refusal)
