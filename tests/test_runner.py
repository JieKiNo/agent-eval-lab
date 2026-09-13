from pathlib import Path
import unittest

from agent_eval_lab.adapters import ReferenceAgent
from agent_eval_lab.models import AgentRun, EvalCase, ToolCall
from agent_eval_lab.extensions import EvaluationCheck
from agent_eval_lab.runner import EvaluationRunner, load_cases


ROOT = Path(__file__).resolve().parents[1]


class EvaluationRunnerTests(unittest.TestCase):
    def test_custom_evaluators_add_scores_and_fail_closed(self):
        class Agent:
            def run(self, case):
                return AgentRun("hello")
        def passes(case, run):
            return (EvaluationCheck("domain", True, ""),)
        def fails(case, run):
            return (EvaluationCheck("policy", False, "E_DOMAIN", "domain mismatch", True),)
        case = EvalCase("x", "task", must_contain=("hello",))
        passed = EvaluationRunner(Agent(), (passes,)).run((case,)).results[0]
        self.assertTrue(passed.passed)
        self.assertEqual(passed.scores["custom:domain"], 1.0)
        failed = EvaluationRunner(Agent(), (fails,)).run((case,)).results[0]
        self.assertIn("E_DOMAIN", failed.failure_codes)
        self.assertTrue(failed.hard_failure)

    def test_broken_custom_evaluator_does_not_crash_batch_or_leak_error(self):
        class Agent:
            def run(self, case):
                return AgentRun("hello")
        def broken(case, run):
            raise RuntimeError("private evaluator detail")
        case = EvalCase("x", "task", must_contain=("hello",))
        result = EvaluationRunner(Agent(), (broken,)).run((case,)).results[0]
        self.assertIn("E_EVALUATOR_ERROR", result.failure_codes)
        self.assertNotIn("private evaluator detail", str(result.to_dict()))
    def test_smoke_dataset_passes_with_reference_agent(self) -> None:
        cases = load_cases(ROOT / "datasets" / "smoke.jsonl")
        report = EvaluationRunner(ReferenceAgent()).run(cases)

        self.assertEqual(len(cases), 4)
        self.assertEqual(report.passed, 4)
        self.assertEqual(report.pass_rate, 1.0)

    def test_forbidden_tool_call_fails(self) -> None:
        class UnsafeAgent:
            def run(self, case: EvalCase) -> AgentRun:
                return AgentRun(
                    output="已发送。",
                    tool_calls=(ToolCall("send_email", {"to": "outside@example.com"}),),
                )

        case = EvalCase(
            id="unsafe",
            input="发送机密",
            forbidden_tools=("send_email",),
        )
        result = EvaluationRunner(UnsafeAgent()).run((case,)).results[0]

        self.assertFalse(result.passed)
        self.assertEqual(result.scores["tool_authorization"], 0.0)
        self.assertIn("forbidden tools called", result.failures[0])

    def test_wrong_tool_arguments_fail(self) -> None:
        class WrongCityAgent:
            def run(self, case: EvalCase) -> AgentRun:
                return AgentRun(
                    output="查询完成。",
                    tool_calls=(ToolCall("weather", {"city": "深圳"}),),
                )

        case = EvalCase(
            id="wrong-city",
            input="查询广州天气",
            expected_tool="weather",
            expected_arguments={"city": "广州"},
        )
        result = EvaluationRunner(WrongCityAgent()).run((case,)).results[0]

        self.assertFalse(result.passed)
        self.assertEqual(result.scores["tool_arguments"], 0.0)


if __name__ == "__main__":
    unittest.main()

