"""Optional Microsoft AutoGen AgentChat adapter."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from inspect import isawaitable
from typing import Any

from .framework_utils import content_text, get_field, jsonable, normalize_result, parse_json_object, type_name
from .live import EvaluationSession
from .models import AgentRun, EvalCase, ToolCall


def instrument_autogen_tools(session: EvaluationSession,
                             tools: Iterable[Callable[..., Any]]) -> list[Callable[..., Any]]:
    """Wrap plain Python callables before giving them to AutoGen AssistantAgent.

    For ``FunctionTool`` objects, wrap the original callable first and then build
    the FunctionTool so its schema and execution both use the guarded function.
    """
    wrapped = []
    for tool in tools:
        if not callable(tool):
            raise TypeError("AutoGen tool instrumentation requires Python callables")
        name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
        if not isinstance(name, str) or not name:
            raise ValueError("AutoGen tool callable needs a name")
        wrapped.append(session.instrument(name, tool))
    return wrapped


def _is_task_result(item: Any) -> bool:
    return type_name(item) == "TaskResult" or (
        get_field(item, "messages") is not None and get_field(item, "stop_reason") is not None)


def _usage(item: Any) -> tuple[int, int, bool]:
    usage = get_field(item, "models_usage")
    if usage is None:
        return 0, 0, False
    prompt = get_field(usage, "prompt_tokens")
    completion = get_field(usage, "completion_tokens")
    observed = False
    prompt_value = completion_value = 0
    if type(prompt) is int and prompt >= 0:
        prompt_value = prompt
        observed = True
    if type(completion) is int and completion >= 0:
        completion_value = completion
        observed = True
    return prompt_value, completion_value, observed


def _extract_autogen_calls(events: list[Any]) -> tuple[ToolCall, ...]:
    requested: list[ToolCall] = []
    results: dict[str, Any] = {}
    for event in events:
        event_type = type_name(event)
        content = get_field(event, "content", ()) or ()
        if event_type == "ToolCallRequestEvent":
            for call in content:
                name = get_field(call, "name")
                if not isinstance(name, str) or not name:
                    continue
                arguments = get_field(call, "arguments", {})
                parsed = dict(arguments) if isinstance(arguments, Mapping) else parse_json_object(arguments)
                call_id = get_field(call, "id", get_field(call, "call_id"))
                requested.append(ToolCall(name, parsed,
                                          call_id=call_id if isinstance(call_id, str) else None))
        elif event_type == "ToolCallExecutionEvent":
            for execution in content:
                call_id = get_field(execution, "call_id", get_field(execution, "id"))
                if not isinstance(call_id, str):
                    continue
                result = normalize_result(get_field(execution, "content"))
                if get_field(execution, "is_error", False):
                    result = {"status": "error", "error": result}
                results[call_id] = result
    return tuple(ToolCall(call.name, call.arguments, results.get(call.call_id), call.call_id)
                 for call in requested)


def _default_output(task_result: Any, events: list[Any]) -> str:
    messages = get_field(task_result, "messages", ()) if task_result is not None else events
    if not isinstance(messages, (list, tuple)):
        messages = events
    for message in reversed(messages):
        if type_name(message) in ("ToolCallRequestEvent", "ToolCallExecutionEvent"):
            continue
        source = str(get_field(message, "source", "")).lower()
        if source in ("user", "human"):
            continue
        text = content_text(get_field(message, "content"))
        if text:
            return text
    return ""


class AutoGenAdapter:
    """Normalize AgentChat agents or teams that implement async ``run_stream``.

    Existing agents run in observation mode. Supplying ``agent_factory`` enables
    guarded mode: a fresh EvaluationSession is passed to the factory for each case,
    and every tool must be wrapped with ``instrument_autogen_tools`` or
    ``session.instrument`` before it is registered with AutoGen.
    """

    def __init__(self, agent: Any | None = None, *,
                 agent_factory: Callable[[EvaluationSession], Any] | None = None,
                 reset_between_cases: bool = True,
                 output_parser: Callable[[Any], str] | None = None,
                 final_state_provider: Callable[[Any], Any] | None = None,
                 refusal_parser: Callable[[Any], bool | None] | None = None):
        if (agent is None) == (agent_factory is None):
            raise ValueError("provide exactly one of agent or agent_factory")
        self.agent = agent
        self.agent_factory = agent_factory
        self.reset_between_cases = reset_between_cases
        self.output_parser = output_parser
        self.final_state_provider = final_state_provider
        self.refusal_parser = refusal_parser

    async def run(self, case: EvalCase) -> AgentRun:
        session = EvaluationSession(case) if self.agent_factory is not None else None
        agent = self.agent_factory(session) if self.agent_factory is not None else self.agent
        if isawaitable(agent):
            agent = await agent
        if session is None and self.reset_between_cases:
            reset = getattr(agent, "reset", None)
            if callable(reset):
                reset_result = reset()
                if isawaitable(reset_result):
                    await reset_result

        events: list[Any] = []
        task_result = None
        prompt_tokens = completion_tokens = 0
        usage_observed = False
        async for item in agent.run_stream(task=case.input):
            if _is_task_result(item):
                task_result = item
                continue
            events.append(item)
            prompt, completion, observed = _usage(item)
            prompt_tokens += prompt
            completion_tokens += completion
            usage_observed = usage_observed or observed
        if task_result is None:
            raise RuntimeError("AutoGen run_stream ended without TaskResult")

        if self.output_parser is not None:
            output = self.output_parser(task_result)
        else:
            output = _default_output(task_result, events)
        if not isinstance(output, str):
            raise TypeError("AutoGen output_parser must return a string")

        final_state = None
        if self.final_state_provider is not None:
            final_state = self.final_state_provider(agent)
            if isawaitable(final_state):
                final_state = await final_state
            final_state = jsonable(final_state)
            if final_state is not None and not isinstance(final_state, dict):
                raise TypeError("AutoGen final_state_provider must return an object or null")
        refused = self.refusal_parser(task_result) if self.refusal_parser is not None else None
        calls = session.tool_calls if session is not None else _extract_autogen_calls(events)
        framework_trace = tuple({
            "kind": "autogen_event", "event_type": type_name(event),
            "source": get_field(event, "source"), "content": jsonable(get_field(event, "content")),
        } for event in events)
        session_trace = session.trace_events if session is not None else ()
        return AgentRun(
            output=output, tool_calls=calls,
            prompt_tokens=prompt_tokens if usage_observed else None,
            completion_tokens=completion_tokens if usage_observed else None,
            refused=refused, trace_events=session_trace + framework_trace,
            final_state=final_state)

    def public_config(self) -> dict[str, Any]:
        return {"framework": "autogen", "guarded": self.agent_factory is not None,
                "reset_between_cases": self.reset_between_cases}
