"""Optional LangGraph/LangChain adapters with no hard framework dependency."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from typing import Any

from .framework_utils import content_text, get_field, jsonable, normalize_result, parse_json_object, type_name
from .live import EvaluationSession, ToolBlocked
from .models import AgentRun, EvalCase, ToolCall

try:  # Loaded only when LangChain is installed by the integrating application.
    from langchain.agents.middleware import AgentMiddleware as _AgentMiddleware
except ImportError:  # pragma: no cover - the dependency-free fallback is tested instead.
    class _AgentMiddleware:  # type: ignore[no-redef]
        pass


def _request_call(request: Any) -> tuple[str, dict[str, Any], str | None]:
    call = get_field(request, "tool_call")
    if call is None:
        raise ValueError("LangGraph ToolCallRequest has no tool_call")
    name = get_field(call, "name")
    arguments = get_field(call, "args", get_field(call, "arguments", {}))
    call_id = get_field(call, "id", get_field(call, "call_id"))
    if not isinstance(name, str) or not name:
        raise ValueError("LangGraph tool call has no name")
    if not isinstance(arguments, Mapping):
        arguments = parse_json_object(arguments)
    else:
        arguments = dict(arguments)
    return name, deepcopy(arguments), call_id if isinstance(call_id, str) else None


def _tool_response_result(response: Any) -> Any:
    update = get_field(response, "update")
    messages = get_field(update, "messages") if update is not None else None
    if isinstance(messages, (list, tuple)) and messages:
        return normalize_result(messages[-1])
    result = normalize_result(response)
    status = get_field(response, "status")
    if status in ("error", "failed"):
        return {"status": status, "error": result}
    return result


class LangGraphEvaluationMiddleware(_AgentMiddleware):
    """Apply an EvaluationSession around every LangChain/LangGraph tool call.

    Pass an instance in ``create_agent(..., middleware=[middleware])``. A blocked
    request is returned to the model as a ToolMessage by default, without calling
    the real tool handler.
    """

    def __init__(self, session: EvaluationSession, *, on_blocked: str = "message"):
        if on_blocked not in ("message", "raise"):
            raise ValueError("on_blocked must be 'message' or 'raise'")
        try:
            super().__init__()
        except TypeError:
            pass
        self.session = session
        self.on_blocked = on_blocked

    def _blocked_response(self, call_id: str | None, decision: dict[str, Any]) -> Any:
        if self.on_blocked == "raise":
            raise ToolBlocked(decision)
        try:
            from langchain.messages import ToolMessage
        except ImportError as error:  # Makes misuse clear when the optional package is absent.
            raise ToolBlocked(decision) from error
        kwargs = {
            "content": "Tool request blocked by evaluation policy.",
            "tool_call_id": call_id or "agent-eval-blocked",
        }
        try:
            return ToolMessage(status="error", **kwargs)
        except TypeError:  # Compatibility with versions that do not expose status.
            return ToolMessage(**kwargs)

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        name, arguments, call_id = _request_call(request)
        decision = self.session.before_tool(name, arguments, call_id)
        if not decision["allowed"]:
            return self._blocked_response(call_id, decision)
        try:
            response = handler(request)
        except Exception as error:
            self.session.after_tool(name, arguments, {"error": type(error).__name__}, call_id)
            raise
        self.session.after_tool(name, arguments, _tool_response_result(response), call_id)
        return response

    async def awrap_tool_call(self, request: Any,
                              handler: Callable[[Any], Awaitable[Any]]) -> Any:
        name, arguments, call_id = _request_call(request)
        decision = self.session.before_tool(name, arguments, call_id)
        if not decision["allowed"]:
            return self._blocked_response(call_id, decision)
        try:
            response = await handler(request)
        except Exception as error:
            self.session.after_tool(name, arguments, {"error": type(error).__name__}, call_id)
            raise
        self.session.after_tool(name, arguments, _tool_response_result(response), call_id)
        return response


def _messages(result: Any) -> list[Any]:
    messages = get_field(result, "messages")
    if isinstance(messages, (list, tuple)):
        return list(messages)
    if isinstance(result, (list, tuple)):
        return list(result)
    return []


def _langgraph_usage(messages: list[Any]) -> tuple[int | None, int | None]:
    prompt = completion = 0
    observed = False
    for message in messages:
        usage = get_field(message, "usage_metadata")
        if usage is None:
            response_metadata = get_field(message, "response_metadata", {})
            usage = get_field(response_metadata, "token_usage", {})
        input_tokens = get_field(usage, "input_tokens", get_field(usage, "prompt_tokens"))
        output_tokens = get_field(usage, "output_tokens", get_field(usage, "completion_tokens"))
        if type(input_tokens) is int and input_tokens >= 0:
            prompt += input_tokens
            observed = True
        if type(output_tokens) is int and output_tokens >= 0:
            completion += output_tokens
            observed = True
    return (prompt, completion) if observed else (None, None)


def normalize_langgraph_run(result: Any, *, session: EvaluationSession | None = None,
                            output_parser: Callable[[Any], str] | None = None,
                            final_state_parser: Callable[[Any], dict[str, Any] | None] | None = None,
                            refusal_parser: Callable[[Any], bool | None] | None = None) -> AgentRun:
    """Normalize a completed LangGraph state into the framework-neutral AgentRun."""
    messages = _messages(result)
    result_by_id: dict[str, Any] = {}
    requested: list[ToolCall] = []
    trace = []
    for message in messages:
        message_type = type_name(message)
        role = get_field(message, "role", get_field(message, "source"))
        tool_calls = get_field(message, "tool_calls", ()) or ()
        trace.append({"kind": "langgraph_message", "message_type": message_type,
                      "role": role, "content": jsonable(get_field(message, "content")),
                      "tool_calls": jsonable(tool_calls)})
        for call in tool_calls:
            name = get_field(call, "name")
            if not isinstance(name, str) or not name:
                continue
            arguments = get_field(call, "args", get_field(call, "arguments", {}))
            arguments = dict(arguments) if isinstance(arguments, Mapping) else parse_json_object(arguments)
            call_id = get_field(call, "id", get_field(call, "call_id"))
            requested.append(ToolCall(name, arguments,
                                      call_id=call_id if isinstance(call_id, str) else None))
        lowered = message_type.lower()
        if lowered in ("toolmessage", "tool") or get_field(message, "tool_call_id") is not None:
            call_id = get_field(message, "tool_call_id", get_field(message, "call_id"))
            if isinstance(call_id, str):
                result_by_id[call_id] = _tool_response_result(message)

    observed_calls = tuple(ToolCall(call.name, call.arguments,
                                    result_by_id.get(call.call_id), call.call_id)
                           for call in requested)
    tool_calls = session.tool_calls if session is not None else observed_calls
    if output_parser is not None:
        output = output_parser(result)
    else:
        output = ""
        for message in reversed(messages):
            lowered = type_name(message).lower()
            role = str(get_field(message, "role", "")).lower()
            if lowered in ("toolmessage", "tool", "humanmessage", "human",
                           "systemmessage", "system") or role in (
                "tool", "user", "human", "system"):
                continue
            candidate = content_text(get_field(message, "content"))
            if candidate:
                output = candidate
                break
    if not isinstance(output, str):
        raise TypeError("LangGraph output_parser must return a string")
    if final_state_parser is not None:
        final_state = final_state_parser(result)
    else:
        normalized = jsonable(result)
        final_state = normalized if isinstance(normalized, dict) else None
    if final_state is not None and not isinstance(final_state, dict):
        raise TypeError("LangGraph final_state_parser must return an object or null")
    refused = refusal_parser(result) if refusal_parser is not None else None
    prompt_tokens, completion_tokens = _langgraph_usage(messages)
    session_trace = list(session.trace_events) if session is not None else []
    return AgentRun(output=output, tool_calls=tool_calls,
                    prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                    refused=refused, trace_events=tuple(session_trace + trace),
                    final_state=final_state)


class _LangGraphAdapterBase:
    def __init__(self, graph: Any | None = None, *,
                 graph_factory: Callable[[EvaluationSession], Any] | None = None,
                 input_builder: Callable[[EvalCase], Any] | None = None,
                 config: Mapping[str, Any] | None = None,
                 config_builder: Callable[[EvalCase], Mapping[str, Any] | None] | None = None,
                 output_parser: Callable[[Any], str] | None = None,
                 final_state_parser: Callable[[Any], dict[str, Any] | None] | None = None,
                 refusal_parser: Callable[[Any], bool | None] | None = None):
        if (graph is None) == (graph_factory is None):
            raise ValueError("provide exactly one of graph or graph_factory")
        if config is not None and config_builder is not None:
            raise ValueError("provide config or config_builder, not both")
        self.graph = graph
        self.graph_factory = graph_factory
        self.input_builder = input_builder or (
            lambda case: {"messages": [{"role": "user", "content": case.input}]})
        self.config = deepcopy(dict(config)) if config is not None else None
        self.config_builder = config_builder
        self.output_parser = output_parser
        self.final_state_parser = final_state_parser
        self.refusal_parser = refusal_parser

    def _prepare(self, case: EvalCase) -> tuple[Any, EvaluationSession | None, Any, Any]:
        session = EvaluationSession(case) if self.graph_factory is not None else None
        graph = self.graph_factory(session) if self.graph_factory is not None else self.graph
        payload = self.input_builder(case)
        config = self.config_builder(case) if self.config_builder is not None else deepcopy(self.config)
        return graph, session, payload, config

    def _normalize(self, result: Any, session: EvaluationSession | None) -> AgentRun:
        return normalize_langgraph_run(
            result, session=session, output_parser=self.output_parser,
            final_state_parser=self.final_state_parser, refusal_parser=self.refusal_parser)

    def public_config(self) -> dict[str, Any]:
        return {"framework": "langgraph", "guarded": self.graph_factory is not None}


class LangGraphAdapter(_LangGraphAdapterBase):
    """Synchronous adapter for a compiled LangGraph or LangChain create_agent result."""

    def run(self, case: EvalCase) -> AgentRun:
        graph, session, payload, config = self._prepare(case)
        result = graph.invoke(payload) if config is None else graph.invoke(payload, config=config)
        return self._normalize(result, session)


class AsyncLangGraphAdapter(_LangGraphAdapterBase):
    """Async adapter for ``ainvoke``; use with AsyncEvaluationRunner."""

    async def run(self, case: EvalCase) -> AgentRun:
        graph, session, payload, config = self._prepare(case)
        result = await graph.ainvoke(payload) if config is None else await graph.ainvoke(
            payload, config=config)
        return self._normalize(result, session)
