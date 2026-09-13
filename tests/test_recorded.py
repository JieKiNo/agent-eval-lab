import json
import tempfile
import unittest
from pathlib import Path

from agent_eval_lab.models import EvalCase
from agent_eval_lab.recorded import RecordedAgent, load_recorded_runs, validate_trace_coverage
from agent_eval_lab.runner import EvaluationRunner


class RecordedTraceTests(unittest.TestCase):
    def write(self, directory, rows):
        path = Path(directory) / "trace.jsonl"
        path.write_text("\n".join(json.dumps({"schema_version": "1.0"} | row) for row in rows),
                        encoding="utf-8")
        return path

    def test_framework_neutral_trace_is_graded_and_preserves_latency(self):
        row = {"case_id": "x", "output": "done", "latency_ms": 123.5,
               "tool_calls": [{"name": "lookup", "arguments": {"id": "1"},
                               "result": {"status": "ok"}, "call_id": "c1"}],
               "final_state": {"done": True}}
        with tempfile.TemporaryDirectory() as tmp:
            runs = load_recorded_runs(self.write(tmp, [row]))
        case = EvalCase("x", "task", allowed_tools=("lookup",), require_tool_results=True,
                        expected_state={"done": True})
        validate_trace_coverage((case,), runs)
        report = EvaluationRunner(RecordedAgent(runs)).run((case,))
        self.assertTrue(report.results[0].passed)
        self.assertEqual(report.results[0].run.latency_ms, 123.5)
        self.assertEqual(report.metadata["config"]["adapter"], "recorded_jsonl")

    def test_invalid_rows_and_coverage_fail_closed(self):
        rows = [
            {"case_id": "x", "output": "ok", "unknown": True},
            {"case_id": "x", "output": "ok", "tool_calls": [{}]},
            {"case_id": "x", "output": "ok", "latency_ms": -1},
            {"case_id": "x", "output": "ok", "final_state": []},
            {"schema_version": "9.0", "case_id": "x", "output": "ok"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for index, row in enumerate(rows):
                with self.subTest(index=index), self.assertRaisesRegex(ValueError, "E_TRACE_INVALID"):
                    load_recorded_runs(self.write(tmp, [row]))
            path = self.write(tmp, [{"case_id": "x", "output": "ok"},
                                    {"case_id": "x", "output": "again"}])
            with self.assertRaisesRegex(ValueError, "duplicate case_id"):
                load_recorded_runs(path)
        with self.assertRaisesRegex(ValueError, "missing=.*y"):
            validate_trace_coverage((EvalCase("x", "x", expected_output="ok"),
                                     EvalCase("y", "y", expected_output="ok")),
                                    {"x": RecordedAgent({}).run if False else None})
