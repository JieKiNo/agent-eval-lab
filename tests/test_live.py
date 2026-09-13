import unittest

from agent_eval_lab.live import EvaluationSession, ToolBlocked
from agent_eval_lab.extensions import EvaluationCheck
from agent_eval_lab.models import EvalCase


class LiveEvaluationTests(unittest.TestCase):
    def test_instrumented_workflow_checks_before_and_after_tools(self):
        events = []
        case = EvalCase("x", "close ticket", allowed_tools=("lookup", "close"),
                        required_tools=("lookup", "close"), tool_sequence=("lookup", "close"),
                        require_tool_results=True, expected_state={"status": "closed"})
        session = EvaluationSession(case, events.append)
        lookup = session.instrument("lookup", lambda ticket_id: {"ticket_id": ticket_id, "status": "open"})
        close = session.instrument("close", lambda ticket_id: {"ticket_id": ticket_id, "status": "closed"})
        self.assertEqual(lookup("T-1")["status"], "open")
        self.assertEqual(close(ticket_id="T-1")["status"], "closed")
        result = session.evaluate("done", final_state={"status": "closed"})
        self.assertTrue(result.passed)
        self.assertEqual([event["kind"] for event in events], [
            "tool_request_evaluation", "tool_evaluation",
            "tool_request_evaluation", "tool_evaluation",
        ])

    def test_policy_blocks_tool_before_executor_runs(self):
        executed = False
        case = EvalCase("x", "read", allowed_tools=("read",))
        session = EvaluationSession(case)
        def executor():
            nonlocal executed
            executed = True
        with self.assertRaises(ToolBlocked) as caught:
            session.execute_tool("delete", {"path": "x"}, executor)
        self.assertFalse(executed)
        self.assertIn("E_TOOL_FORBIDDEN", caught.exception.decision["failure_codes"])
        result = session.evaluate("blocked")
        self.assertIn("E_TOOL_FORBIDDEN", result.failure_codes)
        self.assertFalse(session.tool_calls[0].result["external_effect"])

    def test_tool_exception_is_recorded_and_re_raised(self):
        session = EvaluationSession(EvalCase("x", "work", allowed_tools=("work",)))
        def broken():
            raise RuntimeError("private detail")
        with self.assertRaisesRegex(RuntimeError, "private detail"):
            session.execute_tool("work", {}, broken)
        self.assertEqual(session.tool_calls[0].result, {"error": "RuntimeError"})
        self.assertIn("E_TOOL_EXECUTION", session.evaluate("failed").failure_codes)

    def test_event_callback_cannot_mutate_session_trace(self):
        def mutate(event):
            event["tool"] = "changed"
        session = EvaluationSession(EvalCase("x", "work", allowed_tools=("work",)), mutate)
        session.execute_tool("work", {}, lambda: {"ok": True})
        self.assertTrue(all(event["tool"] == "work" for event in session.trace_events))

    def test_manual_hooks_and_input_validation(self):
        session = EvaluationSession(EvalCase("x", "work", allowed_tools=("work",)))
        decision = session.before_tool("work", {"n": 1}, "c1")
        self.assertTrue(decision["allowed"])
        check = session.after_tool("work", {"n": 1}, {"ok": True}, "c1")
        self.assertEqual(check["result_state"], "observed")
        for name, arguments in (("", {}), (1, {}), ("work", [])):
            with self.subTest(name=name, arguments=arguments), self.assertRaises(ValueError):
                session.before_tool(name, arguments)

    def test_tool_budget_is_enforced_before_second_execution(self):
        session = EvaluationSession(EvalCase("x", "work", allowed_tools=("work",), max_tool_calls=1))
        self.assertEqual(session.execute_tool("work", {}, lambda: 1), 1)
        executed = False
        def second():
            nonlocal executed
            executed = True
        with self.assertRaises(ToolBlocked) as caught:
            session.execute_tool("work", {}, second)
        self.assertFalse(executed)
        self.assertIn("E_TOOL_CALL_LIMIT", caught.exception.decision["failure_codes"])
        blocked = session.trace_events[-1]
        self.assertEqual(blocked["kind"], "tool_blocked")
        self.assertFalse(blocked["allowed"])
        self.assertIn("E_TOOL_CALL_LIMIT", blocked["failure_codes"])

    def test_custom_evaluator_participates_in_session_result(self):
        def domain_check(case, run):
            yield EvaluationCheck("ticket_format", run.output.startswith("T-"),
                                  "E_TICKET_FORMAT", "output needs a ticket id")
        session = EvaluationSession(EvalCase("x", "work", expected_output="done"),
                                    evaluators=(domain_check,))
        result = session.evaluate("done")
        self.assertIn("E_TICKET_FORMAT", result.failure_codes)
        self.assertEqual(result.scores["custom:ticket_format"], 0.0)

    def test_unapproved_high_risk_tool_is_blocked_before_execution(self):
        case = EvalCase("x", "send", allowed_tools=("send_email",), tags=("security",),
                        metadata={"security": {
                            "source": {"facts": "", "attack": ""},
                            "authority": {"approved": False, "grants": []},
                            "capabilities": ["send_email"], "threat_tier": "development",
                        }})
        session = EvaluationSession(case)
        with self.assertRaises(ToolBlocked) as caught:
            session.execute_tool("send_email", {"to": "test@example.invalid", "body": "hello"},
                                 lambda: self.fail("unapproved tool executed"))
        self.assertIn("E_APPROVAL_BYPASS", caught.exception.decision["failure_codes"])

    def test_exact_approval_allows_high_risk_tool(self):
        arguments = {"to": "test@example.invalid", "body": "hello"}
        case = EvalCase("x", "send", allowed_tools=("send_email",), tags=("security",),
                        metadata={"security": {
                            "source": {"facts": "", "attack": ""},
                            "authority": {"approved": True, "grants": [
                                {"tool": "send_email", "arguments": arguments}]},
                            "capabilities": ["send_email"], "threat_tier": "development",
                        }})
        session = EvaluationSession(case)
        result = session.execute_tool("send_email", arguments,
                                      lambda: {"status": "recorded", "external_effect": False})
        self.assertEqual(result["status"], "recorded")
        self.assertNotIn("E_APPROVAL_BYPASS", session.evaluate("sent").failure_codes)


class AsyncLiveEvaluationTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_tool_is_instrumented(self):
        session = EvaluationSession(EvalCase("x", "lookup", allowed_tools=("lookup",),
                                                     require_tool_results=True))
        async def lookup(ticket_id):
            return {"ticket_id": ticket_id, "status": "open"}
        wrapped = session.instrument("lookup", lookup)
        self.assertEqual((await wrapped("T-1"))["status"], "open")
        self.assertEqual(len(session.tool_calls), 1)
        self.assertTrue(session.evaluate("done").passed)

    async def test_async_block_happens_before_coroutine_runs(self):
        session = EvaluationSession(EvalCase("x", "read", allowed_tools=("read",)))
        executed = False
        async def remove():
            nonlocal executed
            executed = True
        wrapped = session.instrument("delete", remove)
        with self.assertRaises(ToolBlocked):
            await wrapped()
        self.assertFalse(executed)
