import unittest

from agent_eval_lab.evaluators import evaluate_case, evaluate_tool_call
from agent_eval_lab.models import AgentRun, EvalCase, ToolCall
from agent_eval_lab.runner import EvaluationRunner


class EvaluatorTests(unittest.TestCase):
    def test_numeric_fragments_match_whole_values(self):
        case = EvalCase("x", "calculate", must_contain=("4",))
        for text in ("结果是14", "结果是-4", "结果是0.4", "结果是4e2", "结果是40"):
            with self.subTest(text=text):
                self.assertFalse(evaluate_case(case, AgentRun(text)).passed)
        for text in ("结果是4。", "结果是4.0", "结果是+4", "结果是4e0"):
            with self.subTest(text=text):
                self.assertTrue(evaluate_case(case, AgentRun(text)).passed)

    def test_exact_output_rejects_negated_answer_and_extra_text(self):
        case = EvalCase("x", "calculate", expected_output="4")
        for text in ("不是4", "4，实际上是14", "4 "):
            self.assertIn("E_OUTPUT_CRITERIA", evaluate_case(case, AgentRun(text)).failure_codes)
        self.assertTrue(evaluate_case(case, AgentRun("4")).passed)
        self.assertTrue(evaluate_case(EvalCase("empty", "stay silent", expected_output=""), AgentRun("")).passed)

    def test_extreme_numeric_output_does_not_crash_evaluator(self):
        case = EvalCase("x", "calculate", must_contain=("4",))
        run = AgentRun("4e" + "9" * 100)
        self.assertIn("E_OUTPUT_CRITERIA", evaluate_case(case, run).failure_codes)

    def test_arithmetic_matcher_preserves_operations_and_other_fields(self):
        case = EvalCase("x", "calculate", expected_tool="calc",
                        expected_arguments={"expression": "2+2", "label": "a b"},
                        argument_matchers={"expression": "arithmetic_syntax"})
        for expression, passed in ((" 2 + (2) ", True), ("2*2", False), ("4", False),
                                   ("__import__('os')", False), ("2**2", False), (4, False)):
            with self.subTest(expression=expression):
                run = AgentRun("", (ToolCall("calc", {"expression": expression, "label": "a b"}),))
                self.assertEqual(evaluate_case(case, run).passed, passed)
        run = AgentRun("", (ToolCall("calc", {"expression": "2+2", "label": "ab"}),))
        self.assertIn("E_TOOL_ARGS", evaluate_case(case, run).failure_codes)

    def test_allowlist_supports_multiple_tools_and_explicit_no_tools(self):
        case = EvalCase("x", "task", expected_tool="calc", allowed_tools=("calc", "lookup"))
        good = AgentRun("", (ToolCall("lookup"), ToolCall("calc")))
        self.assertTrue(evaluate_case(case, good).passed)
        bad = AgentRun("", good.tool_calls + (ToolCall("weather"),))
        self.assertTrue(evaluate_case(case, bad).hard_failure)
        empty = EvalCase("empty", "no tools", allowed_tools=())
        self.assertTrue(evaluate_case(empty, AgentRun("")).passed)
        self.assertFalse(evaluate_case(empty, AgentRun("", (ToolCall("anything"),))).passed)
        self.assertIn("E_TRACE_MISSING", evaluate_case(empty, AgentRun("", trace_available=False)).failure_codes)

    def test_tool_errors_cannot_be_hidden_by_answer_or_later_success(self):
        case = EvalCase("x", "calculate", expected_tool="calc", must_contain=("4",))
        for result in ({"error": "failed"}, {"status": "denied"}, {"status": "failed"},
                       {"isError": True}, {"success": False}):
            with self.subTest(result=result):
                run = AgentRun("4", (ToolCall("calc", result=result), ToolCall("calc", result={"result": 4})))
                self.assertIn("E_TOOL_EXECUTION", evaluate_case(case, run).failure_codes)

    def test_missing_and_wrong_tool_results(self):
        case = EvalCase("x", "calculate", expected_tool="calc", require_tool_results=True,
                        expected_tool_result={"result": 4})
        for result, code in ((None, "E_TOOL_RESULT_MISSING"), ({"result": 14}, "E_TOOL_RESULT")):
            run = AgentRun("4", (ToolCall("calc", result=result),))
            self.assertIn(code, evaluate_case(case, run).failure_codes)
        good = AgentRun("4", (ToolCall("calc", result={"result": 4, "units": "items"}),))
        self.assertTrue(evaluate_case(case, good).passed)
        # Legacy traces remain loadable, with missing evidence explicitly recorded.
        legacy = evaluate_case(EvalCase("old", "task", expected_tool="calc"),
                               AgentRun("", (ToolCall("calc"),)))
        self.assertEqual(legacy.evidence["tool_checks"][0]["result_state"], "missing")
        self.assertNotIn("tool_execution", legacy.scores)

    def test_final_state_is_independent_of_claimed_completion(self):
        case = EvalCase("x", "send message", expected_state={"outbox": [{"to": "a", "body": "hello"}]})
        self.assertIn("E_STATE_MISSING", evaluate_case(case, AgentRun("sent")).failure_codes)
        self.assertIn("E_STATE_MISMATCH", evaluate_case(case, AgentRun("sent", final_state={"outbox": []})).failure_codes)
        self.assertTrue(evaluate_case(case, AgentRun("sent", final_state={
            "outbox": [{"to": "a", "body": "hello", "id": "1"}], "other": True})).passed)

    def test_per_call_checks_agree_with_batch_checks(self):
        case = EvalCase("x", "task", expected_tool="calc", allowed_tools=("calc",), require_tool_results=True)
        call = ToolCall("weather", result={"error": "unavailable"})
        check = evaluate_tool_call(case, call)
        batch = evaluate_case(case, AgentRun("", (call,)))
        self.assertEqual(check, batch.evidence["tool_checks"][0])
        self.assertTrue(set(check["failure_codes"]) <= set(batch.failure_codes))
        self.assertTrue(check["hard_failure"])

    def test_multiple_required_tools_sequence_and_budget(self):
        case = EvalCase("x", "workflow", allowed_tools=("search", "draft", "send"),
                        required_tools=("search", "send"), tool_sequence=("search", "draft", "send"),
                        max_tool_calls=3)
        good = AgentRun("done", tuple(ToolCall(name) for name in ("search", "draft", "send")))
        self.assertTrue(evaluate_case(case, good).passed)
        wrong = AgentRun("done", tuple(ToolCall(name) for name in ("draft", "search", "send", "draft")))
        result = evaluate_case(case, wrong)
        self.assertIn("E_TOOL_SEQUENCE", result.failure_codes)
        self.assertIn("E_TOOL_CALL_LIMIT", result.failure_codes)
        missing = evaluate_case(case, AgentRun("done", (ToolCall("draft"),)))
        self.assertIn("E_TOOL_NOT_CALLED", missing.failure_codes)

    def test_nested_subset_and_type_mismatch(self):
        case = EvalCase("x", "task", expected_tool="t", expected_arguments={"payload": {"n": 1}})
        for args, passed in [({"payload": {"n": 1, "extra": 2}}, True),
                             ({"payload": {"n": True}}, False),
                             ({"payload": {}}, False)]:
            with self.subTest(args=args):
                result = evaluate_case(case, AgentRun("", (ToolCall("t", args),)))
                self.assertEqual(result.passed, passed)

    def test_second_bad_call_cannot_hide_behind_good_call(self):
        case = EvalCase("x", "task", expected_tool="t", expected_arguments={"n": 1})
        result = evaluate_case(case, AgentRun("", (ToolCall("t", {"n": 1}), ToolCall("t", {"n": 2}))))
        self.assertIn("E_TOOL_ARGS", result.failure_codes)

    def test_tool_selection_codes(self):
        case = EvalCase("x", "task", expected_tool="t")
        self.assertIn("E_TOOL_NOT_CALLED", evaluate_case(case, AgentRun("")).failure_codes)
        self.assertIn("E_TOOL_WRONG", evaluate_case(case, AgentRun("", (ToolCall("other"),))).failure_codes)

    def test_text_refusal_and_limits_pass_and_fail(self):
        case = EvalCase("x", "task", must_contain=("ok",), must_not_contain=("secret",),
                        expected_refusal=False, max_latency_ms=100, max_total_tokens=20)
        good = AgentRun("OK", latency_ms=100, prompt_tokens=10, completion_tokens=10, refused=False)
        self.assertTrue(evaluate_case(case, good).passed)
        bad = AgentRun("secret", latency_ms=101, prompt_tokens=11, completion_tokens=10, refused=True)
        self.assertEqual(set(evaluate_case(case, bad).failure_codes),
                         {"E_OUTPUT_CRITERIA", "E_UNEXPECTED_REFUSAL", "E_LATENCY_LIMIT", "E_TOKEN_LIMIT"})

    def test_missing_observations_are_not_passes(self):
        case = EvalCase("x", "task", forbidden_tools=("send",), expected_refusal=True, max_total_tokens=20)
        result = evaluate_case(case, AgentRun("I refuse", trace_available=False))
        self.assertEqual(set(result.failure_codes), {"E_TRACE_MISSING", "E_REFUSAL_UNOBSERVED", "E_USAGE_MISSING"})
        self.assertTrue(result.hard_failure)
        self.assertIn("E_REFUSAL_MISSING", evaluate_case(case, AgentRun("ok", refused=False)).failure_codes)

    def test_forbidden_is_hard_failure_even_with_good_answer(self):
        case = EvalCase("x", "task", forbidden_tools=("send",), must_contain=("ok",))
        result = evaluate_case(case, AgentRun("ok", (ToolCall("send"),)))
        self.assertTrue(result.hard_failure)
        self.assertFalse(result.passed)

    def test_exception_is_sanitized_and_following_case_runs(self):
        class Broken:
            def run(self, case):
                if case.id == "one":
                    raise RuntimeError("Authorization: secret-key")
                return AgentRun("ok")
        cases = tuple(EvalCase(i, "task", must_contain=("ok",)) for i in ("one", "two"))
        report = EvaluationRunner(Broken()).run(cases)
        self.assertIn("E_MODEL_ERROR", report.results[0].failure_codes)
        self.assertTrue(report.results[1].passed)
        self.assertNotIn("secret-key", str(report.to_dict()))

    def test_timeout_is_classified(self):
        class TimedOut:
            def run(self, case):
                raise TimeoutError("secret-key")
        report = EvaluationRunner(TimedOut()).run((EvalCase("x", "task", must_contain=("ok",)),))
        self.assertIn("E_AGENT_TIMEOUT", report.results[0].failure_codes)
        self.assertNotIn("secret-key", str(report.to_dict()))

    def test_cancellation_preserves_unrun_cases(self):
        class Interrupted:
            def run(self, case):
                raise KeyboardInterrupt()
        cases = tuple(EvalCase(i, "task", must_contain=("ok",)) for i in ("one", "two"))
        report = EvaluationRunner(Interrupted()).run(cases)
        self.assertEqual([r.status for r in report.results], ["cancelled", "not_evaluated"])
        self.assertEqual(report.to_dict()["summary"]["not_evaluated"], 1)
