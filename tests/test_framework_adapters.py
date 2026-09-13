import unittest

from agent_eval_lab import (
    AsyncEvaluationRunner,
    AsyncLangGraphAdapter,
    AutoGenAdapter,
    EvaluationRunner,
    EvaluationSession,
    LangGraphAdapter,
    LangGraphEvaluationMiddleware,
    ToolBlocked,
    instrument_autogen_tools,
)
from agent_eval_lab.models import EvalCase


class Message:
    def __init__(self, message_type, content, **fields):
        self.type = message_type
        self.content = content
        for name, value in fields.items():
            setattr(self, name, value)


class FakeLangGraph:
    def __init__(self, result):
        self.result = result
        self.inputs = []

    def invoke(self, payload, config=None):
        self.inputs.append((payload, config))
        return self.result

    async def ainvoke(self, payload, config=None):
        self.inputs.append((payload, config))
        return self.result


def langgraph_result():
    return {
        "messages": [
            Message("human", "广州天气如何？", role="user"),
            Message("ai", "", tool_calls=[{
                "name": "weather", "args": {"city": "广州"}, "id": "call-1"}],
                    usage_metadata={"input_tokens": 10, "output_tokens": 4}),
            Message("tool", '{"city":"广州","weather":"晴"}',
                    tool_call_id="call-1", status="success"),
            Message("ai", "广州天气晴。", usage_metadata={
                "input_tokens": 15, "output_tokens": 6}),
        ],
        "ticket": {"status": "done"},
    }


class LangGraphAdapterTests(unittest.TestCase):
    def test_sync_adapter_normalizes_messages_calls_usage_and_state(self):
        graph = FakeLangGraph(langgraph_result())
        adapter = LangGraphAdapter(graph, config={"configurable": {"thread_id": "case"}})
        case = EvalCase(
            "langgraph", "广州天气如何？", expected_tool="weather",
            expected_arguments={"city": "广州"},
            expected_tool_result={"weather": "晴"}, must_contain=("晴",),
            expected_state={"ticket": {"status": "done"}}, max_total_tokens=40)

        report = EvaluationRunner(adapter).run((case,))
        run = report.results[0].run

        self.assertTrue(report.results[0].passed)
        self.assertEqual(run.tool_calls[0].call_id, "call-1")
        self.assertEqual(run.tool_calls[0].result["weather"], "晴")
        self.assertEqual((run.prompt_tokens, run.completion_tokens), (25, 10))
        self.assertEqual(graph.inputs[0][0]["messages"][0]["content"], case.input)

    def test_middleware_records_successful_tool_call(self):
        case = EvalCase("middleware", "查天气", expected_tool="weather",
                        expected_arguments={"city": "广州"}, require_tool_results=True)
        session = EvaluationSession(case)
        middleware = LangGraphEvaluationMiddleware(session)
        request = type("Request", (), {"tool_call": {
            "name": "weather", "args": {"city": "广州"}, "id": "call-2"}})()
        response = Message("tool", '{"weather":"晴"}', tool_call_id="call-2")

        returned = middleware.wrap_tool_call(request, lambda _: response)

        self.assertIs(returned, response)
        self.assertEqual(session.tool_calls[0].result, {"weather": "晴"})
        self.assertEqual(session.trace_events[-1]["kind"], "tool_evaluation")

    def test_middleware_blocks_without_running_handler(self):
        case = EvalCase("blocked", "不要发送", forbidden_tools=("send_email",))
        session = EvaluationSession(case)
        middleware = LangGraphEvaluationMiddleware(session, on_blocked="raise")
        request = type("Request", (), {"tool_call": {
            "name": "send_email", "args": {"to": "outside@example.com"}, "id": "call-3"}})()
        executed = []

        with self.assertRaises(ToolBlocked):
            middleware.wrap_tool_call(request, lambda _: executed.append(True))

        self.assertEqual(executed, [])
        self.assertEqual(session.tool_calls[0].result["external_effect"], False)


class AsyncFrameworkAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_langgraph_adapter_uses_async_runner(self):
        graph = FakeLangGraph(langgraph_result())
        adapter = AsyncLangGraphAdapter(graph)
        case = EvalCase("async-langgraph", "广州天气如何？", expected_tool="weather",
                        expected_arguments={"city": "广州"}, must_contain=("晴",))

        report = await AsyncEvaluationRunner(adapter).run((case,))

        self.assertTrue(report.results[0].passed)
        self.assertEqual(report.metadata["adapter"], "AsyncLangGraphAdapter")

    async def test_autogen_stream_is_normalized_and_agent_is_reset(self):
        class FunctionCall:
            def __init__(self):
                self.id = "call-a"
                self.arguments = '{"city":"广州"}'
                self.name = "weather"

        class FunctionExecutionResult:
            def __init__(self):
                self.content = '{"city":"广州","weather":"晴"}'
                self.name = "weather"
                self.call_id = "call-a"
                self.is_error = False

        class Usage:
            prompt_tokens = 20
            completion_tokens = 5

        class ToolCallRequestEvent:
            type = "ToolCallRequestEvent"
            source = "assistant"
            content = [FunctionCall()]
            models_usage = Usage()

        class ToolCallExecutionEvent:
            type = "ToolCallExecutionEvent"
            source = "assistant"
            content = [FunctionExecutionResult()]
            models_usage = None

        final = Message("TextMessage", "广州天气晴。", source="assistant")

        class TaskResult:
            def __init__(self):
                self.messages = [ToolCallRequestEvent(), ToolCallExecutionEvent(), final]
                self.stop_reason = "done"

        class Agent:
            def __init__(self):
                self.reset_count = 0

            async def reset(self):
                self.reset_count += 1

            async def run_stream(self, task):
                yield ToolCallRequestEvent()
                yield ToolCallExecutionEvent()
                yield final
                yield TaskResult()

        agent = Agent()
        adapter = AutoGenAdapter(agent)
        case = EvalCase("autogen", "广州天气如何？", expected_tool="weather",
                        expected_arguments={"city": "广州"},
                        expected_tool_result={"weather": "晴"}, must_contain=("晴",),
                        max_total_tokens=30)

        report = await AsyncEvaluationRunner(adapter).run((case,))
        run = report.results[0].run

        self.assertTrue(report.results[0].passed)
        self.assertEqual(agent.reset_count, 1)
        self.assertEqual(run.tool_calls[0].result["weather"], "晴")
        self.assertEqual((run.prompt_tokens, run.completion_tokens), (20, 5))

    async def test_autogen_guarded_factory_blocks_real_callable(self):
        side_effects = []

        async def send_email(to):
            side_effects.append(to)
            return {"sent": True}

        class TaskResult:
            stop_reason = "done"

            def __init__(self, message):
                self.messages = [message]

        def factory(session):
            guarded = instrument_autogen_tools(session, [send_email])[0]

            class Agent:
                async def run_stream(self, task):
                    try:
                        await guarded("outside@example.com")
                    except ToolBlocked:
                        pass
                    message = Message("TextMessage", "已阻止。", source="assistant")
                    yield message
                    yield TaskResult(message)

            return Agent()

        adapter = AutoGenAdapter(agent_factory=factory)
        case = EvalCase("autogen-block", "不要发送", forbidden_tools=("send_email",),
                        must_contain=("阻止",))

        run = await adapter.run(case)

        self.assertEqual(side_effects, [])
        self.assertEqual(run.tool_calls[0].result,
                         {"status": "blocked", "external_effect": False})


if __name__ == "__main__":
    unittest.main()
