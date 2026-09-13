from pathlib import Path
import unittest

from agent_eval_lab.evaluators import evaluate_case
from agent_eval_lab.models import AgentRun, ToolCall
from agent_eval_lab.runner import load_cases


ROOT = Path(__file__).resolve().parents[1]


class StrictDatasetTests(unittest.TestCase):
    def test_v2_preserves_case_ids_and_declares_process_rules(self):
        old = load_cases(ROOT / "datasets/regression-v1.jsonl")
        new = load_cases(ROOT / "datasets/regression-v2.jsonl")
        self.assertEqual([c.id for c in old], [c.id for c in new])
        self.assertTrue(all(c.require_tool_results and c.allowed_tools is not None for c in new))

    def test_original_four_counterexamples_on_new_dataset(self):
        case = load_cases(ROOT / "datasets/regression-v2.jsonl")[0]
        call = ToolCall("calculator", {"expression": "2+2"}, result={"result": 4})
        variants = [
            ("wrong answer", AgentRun("14", (call,)), False, "E_OUTPUT_CRITERIA"),
            ("equivalent syntax", AgentRun("4", (ToolCall("calculator", {"expression": "2 + 2"},
                                                           result={"result": 4}),)), True, None),
            ("failed execution", AgentRun("4", (ToolCall("calculator", {"expression": "2+2"},
                                                         result={"error": "failed"}),)), False, "E_TOOL_EXECUTION"),
            ("extra call", AgentRun("4", (call, ToolCall("weather", {"city": "北京"},
                                                       result={"temperature_c": 25}))), False, "E_TOOL_FORBIDDEN"),
        ]
        for label, run, passed, code in variants:
            with self.subTest(label=label):
                result = evaluate_case(case, run)
                self.assertEqual(result.passed, passed)
                if code:
                    self.assertIn(code, result.failure_codes)
