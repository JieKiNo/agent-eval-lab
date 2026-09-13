"""Synchronous hooks for evaluating and guarding an Agent while it runs."""
from __future__ import annotations

from copy import deepcopy
from functools import wraps
import inspect
from typing import Any, Awaitable, Callable

from .evaluators import evaluate_case, evaluate_tool_call, evaluate_tool_request
from .extensions import RunEvaluator, apply_checks, collect_checks
from .models import AgentRun, CaseResult, EvalCase, ToolCall
from .security import evaluate_security, prepare_case


class ToolBlocked(RuntimeError):
    """Raised when an instrumented tool request violates the case policy."""

    def __init__(self, decision: dict[str, Any]):
        super().__init__("tool request blocked by evaluation policy")
        self.decision = deepcopy(decision)


class EvaluationSession:
    """Collect one Agent run and enforce deterministic pre-tool policy."""

    def __init__(self, case: EvalCase, on_event: Callable[[dict[str, Any]], None] | None = None,
                 evaluators: tuple[RunEvaluator, ...] = ()):
        self.case = prepare_case(case)
        self._on_event = on_event
        self._evaluators = tuple(evaluators)
        self._calls: list[ToolCall] = []
        self._events: list[dict[str, Any]] = []

    @property
    def tool_calls(self) -> tuple[ToolCall, ...]:
        return tuple(deepcopy(self._calls))

    @property
    def trace_events(self) -> tuple[dict[str, Any], ...]:
        return tuple(deepcopy(self._events))

    def _emit(self, event: dict[str, Any]) -> None:
        snapshot = deepcopy(event)
        self._events.append(snapshot)
        if self._on_event is not None:
            self._on_event(deepcopy(snapshot))

    def before_tool(self, name: str, arguments: dict[str, Any], call_id: str | None = None) -> dict[str, Any]:
        """Return a decision before execution and record blocked attempts."""
        if not isinstance(name, str) or not name.strip() or not isinstance(arguments, dict):
            raise ValueError("tool request needs a non-empty name and object arguments")
        if call_id is not None and not isinstance(call_id, str):
            raise ValueError("tool call_id must be a string or null")
        call = ToolCall(name, deepcopy(arguments), call_id=call_id)
        decision = evaluate_tool_request(self.case, call)
        if self.case.max_tool_calls is not None and len(self._calls) >= self.case.max_tool_calls:
            decision = deepcopy(decision)
            decision["allowed"] = False
            decision["failure_codes"].append("E_TOOL_CALL_LIMIT")
            decision["failures"].append(
                f"tool call count would exceed {self.case.max_tool_calls}")
        event = {"kind": "tool_request_evaluation", "sequence": len(self._calls) + 1, **decision}
        self._emit(event)
        if not decision["allowed"]:
            blocked = ToolCall(name, deepcopy(arguments),
                               result={"status": "blocked", "external_effect": False}, call_id=call_id)
            self._calls.append(blocked)
            blocked_check = evaluate_tool_call(self.case, blocked)
            blocked_check["phase"] = "blocked"
            blocked_check["allowed"] = False
            for code, message in zip(decision["failure_codes"], decision["failures"]):
                if code not in blocked_check["failure_codes"]:
                    blocked_check["failure_codes"].append(code)
                    blocked_check["failures"].append(message)
            self._emit({"kind": "tool_blocked", "sequence": len(self._calls), **blocked_check})
        return deepcopy(decision)

    def after_tool(self, name: str, arguments: dict[str, Any], result: Any,
                   call_id: str | None = None) -> dict[str, Any]:
        """Record and check a completed tool call."""
        call = ToolCall(name, deepcopy(arguments), result=deepcopy(result), call_id=call_id)
        self._calls.append(call)
        check = evaluate_tool_call(self.case, call)
        self._emit({"kind": "tool_evaluation", "sequence": len(self._calls), **check})
        return deepcopy(check)

    def execute_tool(self, name: str, arguments: dict[str, Any], executor: Callable[[], Any],
                     call_id: str | None = None) -> Any:
        """Guard a tool request, execute it when allowed, and record its result."""
        decision = self.before_tool(name, arguments, call_id)
        if not decision["allowed"]:
            raise ToolBlocked(decision)
        try:
            result = executor()
        except Exception as error:
            self.after_tool(name, arguments, {"error": type(error).__name__}, call_id)
            raise
        self.after_tool(name, arguments, result, call_id)
        return result

    async def execute_tool_async(self, name: str, arguments: dict[str, Any],
                                 executor: Callable[[], Awaitable[Any]],
                                 call_id: str | None = None) -> Any:
        """Async equivalent of execute_tool with the same preflight policy."""
        decision = self.before_tool(name, arguments, call_id)
        if not decision["allowed"]:
            raise ToolBlocked(decision)
        try:
            result = await executor()
        except Exception as error:
            self.after_tool(name, arguments, {"error": type(error).__name__}, call_id)
            raise
        self.after_tool(name, arguments, result, call_id)
        return result

    def instrument(self, name: str, tool: Callable[..., Any]) -> Callable[..., Any]:
        """Wrap a synchronous or asynchronous Python callable."""
        signature = inspect.signature(tool)

        if inspect.iscoroutinefunction(tool):
            @wraps(tool)
            async def async_wrapped(*args, **kwargs):
                bound = signature.bind(*args, **kwargs)
                bound.apply_defaults()
                arguments = dict(bound.arguments)
                return await self.execute_tool_async(
                    name, arguments, lambda: tool(*args, **kwargs))

            return async_wrapped

        @wraps(tool)
        def wrapped(*args, **kwargs):
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            arguments = dict(bound.arguments)
            return self.execute_tool(name, arguments, lambda: tool(*args, **kwargs))

        return wrapped

    def build_run(self, output: str, *, final_state: dict[str, Any] | None = None,
                  refused: bool | None = None, prompt_tokens: int | None = None,
                  completion_tokens: int | None = None, latency_ms: float = 0.0,
                  error_type: str | None = None, error_message: str | None = None) -> AgentRun:
        return AgentRun(output, self.tool_calls, latency_ms=latency_ms,
                        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                        refused=refused, error_type=error_type, error_message=error_message,
                        trace_events=self.trace_events, final_state=deepcopy(final_state))

    def evaluate(self, output: str, **run_fields: Any) -> CaseResult:
        """Finalize this session and evaluate answer, process, security, and state."""
        run = self.build_run(output, **run_fields)
        result = evaluate_security(self.case, evaluate_case(self.case, run))
        return apply_checks(result, collect_checks(self._evaluators, self.case, run))
