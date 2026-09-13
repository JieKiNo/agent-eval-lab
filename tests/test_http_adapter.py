import io
import json
import os
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

from agent_eval_lab.http_adapter import CompatibleAgent, ModelConfig
from agent_eval_lab.models import EvalCase, ToolCall
from agent_eval_lab.fake_tools import calculate, execute_fake
from agent_eval_lab.evaluators import evaluate_case


def response(content='{"answer":"4","refused":false}', calls=None, finish="stop"):
    return {"choices": [{"message": {"role": "assistant", "content": content, "tool_calls": calls or []},
                         "finish_reason": finish}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


class HTTPAdapterTests(unittest.TestCase):
    def setUp(self):
        self.config = ModelConfig("https://example.invalid/v1", "test-model", "TEST_MODEL_KEY", "Complete the task")
        self.case = EvalCase("x", "2+2", must_contain=("4",))
        self.environment = patch.dict(os.environ, {"TEST_MODEL_KEY": "unit-secret-key"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_tool_loop_and_token_totals(self):
        responses = [response(None, [{"id": "call1", "type": "function", "function": {
            "name": "calculator", "arguments": '{"expression":"2+2"}'}}], "tool_calls"), response()]
        requests = []
        def transport(request, timeout):
            requests.append(json.loads(request.data))
            return io.BytesIO(json.dumps(responses.pop(0)).encode())
        run = CompatibleAgent(self.config, transport).run(self.case)
        self.assertIsNone(run.error_type)
        self.assertEqual(run.output, "4")
        self.assertEqual(run.tool_calls[0].result, {"result": 4})
        self.assertEqual((run.prompt_tokens, run.completion_tokens), (20, 10))
        self.assertEqual(requests[1]["messages"][-1]["role"], "tool")
        self.assertNotIn("must_contain", str(requests))
        self.assertNotIn("unit-secret-key", str(run))

    def test_tool_evaluation_arrives_before_next_model_request(self):
        case = EvalCase("x", "2+2", expected_tool="calculator", allowed_tools=("calculator",),
                        expected_arguments={"expression": "2+2"},
                        argument_matchers={"expression": "arithmetic_syntax"},
                        require_tool_results=True, expected_tool_result={"result": 4}, expected_output="4")
        responses = [response(None, [{"id": "call1", "type": "function", "function": {
            "name": "calculator", "arguments": '{"expression":" 2 + (2) "}'}}], "tool_calls"), response()]
        observations = []
        request_count = 0
        def transport(request, timeout):
            nonlocal request_count
            if request_count == 1:
                self.assertEqual(len(observations), 1)
                self.assertEqual(observations[0]["failure_codes"], [])
                self.assertNotIn("tool_evaluation", str(json.loads(request.data)["messages"]))
            request_count += 1
            return io.BytesIO(json.dumps(responses.pop(0)).encode())
        run = CompatibleAgent(self.config, transport, on_tool_result=observations.append).run(case)
        self.assertTrue(evaluate_case(case, run).passed)
        self.assertEqual(observations[0], evaluate_case(case, run).evidence["tool_checks"][0])
        self.assertEqual(sum(e["kind"] == "tool_evaluation" for e in run.trace_events), 1)

    def test_live_check_and_final_evaluation_detect_failed_tool(self):
        case = EvalCase("x", "calculate", expected_tool="calculator", must_contain=("4",))
        responses = [response(None, [{"id": "c", "type": "function", "function": {
            "name": "calculator", "arguments": '{"expression":"1/0"}'}}], "tool_calls"), response()]
        seen = []
        run = CompatibleAgent(self.config, lambda *a, **kw: io.BytesIO(json.dumps(responses.pop(0)).encode()),
                              on_tool_result=seen.append).run(case)
        self.assertIn("E_TOOL_EXECUTION", seen[0]["failure_codes"])
        self.assertIn("E_TOOL_EXECUTION", evaluate_case(case, run).failure_codes)

    def test_observer_failure_preserves_trace_and_redacts_secret(self):
        data = json.dumps(response(None, [{"id": "unit-secret-key", "type": "function", "function": {
            "name": "calculator", "arguments": '{"expression":"2+2"}'}}], "tool_calls")).encode()
        def observer(check):
            self.assertNotIn("unit-secret-key", str(check))
            raise RuntimeError("unit-secret-key")
        run = CompatibleAgent(self.config, lambda *a, **kw: io.BytesIO(data), on_tool_result=observer).run(self.case)
        self.assertEqual(run.error_type, "observer")
        self.assertEqual(len(run.tool_calls), 1)
        self.assertEqual(run.tool_calls[0].result, {"result": 4})
        self.assertNotIn("unit-secret-key", str(run))

    def test_fake_tools_validate_arguments_before_execution(self):
        for name, arguments in (("weather", {}), ("weather", {"city": 1}),
                                ("calculator", {"expression": "2+2", "extra": True}),
                                ("make_payment", {"recipient": "x", "amount": True}),
                                ("make_payment", {"recipient": "x", "amount": float("nan")})):
            with self.subTest(name=name, arguments=arguments):
                result = execute_fake(ToolCall(name, arguments), self.case, [name])
                self.assertEqual(result["error"], "invalid tool arguments")
                self.assertFalse(result["external_effect"])

    def test_one_retry_and_timeout(self):
        count = 0
        def transport(request, timeout):
            nonlocal count
            count += 1
            raise TimeoutError("unit-secret-key")
        run = CompatibleAgent(self.config, transport).run(self.case)
        self.assertEqual(count, 2)
        self.assertEqual(run.error_type, "timeout")
        self.assertNotIn("unit-secret-key", str(run))

    def test_auth_error_not_retried_or_echoed(self):
        count = 0
        def transport(request, timeout):
            nonlocal count
            count += 1
            raise HTTPError(request.full_url, 401, "unit-secret-key", {}, None)
        run = CompatibleAgent(self.config, transport).run(self.case)
        self.assertEqual(count, 1)
        self.assertEqual(run.error_message, "model HTTP status 401")

    def test_missing_key_no_network(self):
        with patch.dict(os.environ, {}, clear=True):
            run = CompatibleAgent(self.config, lambda *a, **kw: self.fail("unexpected network")).run(self.case)
        self.assertEqual(run.error_type, "configuration")

    def test_malformed_responses_fail_closed(self):
        for data in (b"broken", b"{}", json.dumps(response(calls=[{"id": "x", "function": {
                "name": "calculator", "arguments": "not-json"}}])).encode()):
            with self.subTest(data=data):
                run = CompatibleAgent(self.config, lambda *a, **kw: io.BytesIO(data)).run(self.case)
                self.assertEqual(run.error_type, "protocol")

    def test_plain_text_is_preserved_with_unknown_refusal(self):
        data = json.dumps(response("收到")).encode()
        run = CompatibleAgent(self.config, lambda *a, **kw: io.BytesIO(data)).run(self.case)
        self.assertIsNone(run.error_type)
        self.assertIsNone(run.refused)
        self.assertEqual(run.output, "收到")

    def test_echoed_key_redacted(self):
        data = json.dumps(response('{"answer":"unit-secret-key","refused":false}')).encode()
        run = CompatibleAgent(self.config, lambda *a, **kw: io.BytesIO(data)).run(self.case)
        self.assertNotIn("unit-secret-key", str(run))
        self.assertEqual(run.output, "[REDACTED]")

    def test_unsafe_endpoint_and_config_rejected(self):
        for url in ("http://example.invalid", "https://user:password@example.invalid", "https://example.invalid?key=x"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                ModelConfig(url, "m", "KEY", "prompt")
        for changes in ({"max_turns": 100}, {"retries": 2}, {"timeout_seconds": float("inf")}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                ModelConfig("https://example.invalid", "m", "KEY", "prompt", **changes)

    def test_calculator_never_executes_code(self):
        self.assertEqual(calculate("(2+3)*4/2"), 10)
        for expression in ("__import__('os').system('echo unsafe')", "2**1000000", "True", "1e999"):
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                calculate(expression)
