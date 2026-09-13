from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
import math


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    call_id: str | None = None


@dataclass(frozen=True)
class EvalCase:
    id: str
    input: str
    expected_tool: str | None = None
    expected_arguments: dict[str, Any] = field(default_factory=dict)
    forbidden_tools: tuple[str, ...] = ()
    must_contain: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    must_not_contain: tuple[str, ...] = ()
    expected_refusal: bool | None = None
    max_latency_ms: float | None = None
    max_total_tokens: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    allowed_tools: tuple[str, ...] | None = None
    argument_matchers: dict[str, str] = field(default_factory=dict)
    expected_tool_result: dict[str, Any] | None = None
    require_tool_results: bool = False
    expected_output: str | None = None
    expected_state: dict[str, Any] | None = None
    required_tools: tuple[str, ...] = ()
    tool_sequence: tuple[str, ...] = ()
    max_tool_calls: int | None = None

    def __post_init__(self) -> None:
        for name in ("id", "input"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if self.expected_tool is not None and (
            not isinstance(self.expected_tool, str) or not self.expected_tool.strip()
        ):
            raise ValueError("expected_tool must be a non-empty string or null")
        for name in ("forbidden_tools", "must_contain", "must_not_contain", "tags",
                     "required_tools", "tool_sequence"):
            value = getattr(self, name)
            if not isinstance(value, (tuple, list)) or any(
                not isinstance(item, str) or not item.strip() for item in value
            ):
                raise ValueError(f"{name} must contain non-empty strings")
        for name in ("expected_arguments", "metadata"):
            if not isinstance(getattr(self, name), dict):
                raise ValueError(f"{name} must be an object")
        if self.allowed_tools is not None:
            if not isinstance(self.allowed_tools, (tuple, list)) or any(
                not isinstance(item, str) or not item.strip() for item in self.allowed_tools
            ):
                raise ValueError("allowed_tools must be an array of non-empty strings or null")
            if self.expected_tool and self.expected_tool not in self.allowed_tools:
                raise ValueError("expected_tool must be in allowed_tools")
            if set(self.allowed_tools) & set(self.forbidden_tools):
                raise ValueError("allowed_tools conflicts with forbidden_tools")
            if any(name not in self.allowed_tools for name in self.required_tools + self.tool_sequence):
                raise ValueError("required_tools and tool_sequence must be in allowed_tools")
        if set(self.required_tools + self.tool_sequence) & set(self.forbidden_tools):
            raise ValueError("required_tools or tool_sequence conflicts with forbidden_tools")
        if not isinstance(self.argument_matchers, dict) or any(
            key not in self.expected_arguments or not isinstance(key, str)
            or mode not in ("exact", "arithmetic_syntax")
            for key, mode in self.argument_matchers.items()
        ):
            raise ValueError("argument_matchers must map expected argument keys to exact or arithmetic_syntax")
        for key, mode in self.argument_matchers.items():
            if mode == "arithmetic_syntax":
                from .matching import arithmetic_syntax
                arithmetic_syntax(self.expected_arguments[key])
        for name in ("expected_tool_result", "expected_state"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, dict) or not value):
                raise ValueError(f"{name} must be a non-empty object or null")
        if self.expected_tool_result is not None and not self.expected_tool:
            raise ValueError("expected_tool_result requires expected_tool")
        if type(self.require_tool_results) is not bool:
            raise ValueError("require_tool_results must be boolean")
        if self.expected_output is not None and not isinstance(self.expected_output, str):
            raise ValueError("expected_output must be a string or null")
        if self.max_tool_calls is not None and (
            type(self.max_tool_calls) is not int or self.max_tool_calls < 0
        ):
            raise ValueError("max_tool_calls must be a non-negative integer or null")
        if self.expected_refusal is not None and type(self.expected_refusal) is not bool:
            raise ValueError("expected_refusal must be a boolean or null")
        if self.max_latency_ms is not None and (
            type(self.max_latency_ms) not in (int, float)
            or not math.isfinite(self.max_latency_ms) or self.max_latency_ms <= 0
        ):
            raise ValueError("max_latency_ms must be a finite positive number")
        if self.max_total_tokens is not None and (
            type(self.max_total_tokens) is not int or self.max_total_tokens <= 0
        ):
            raise ValueError("max_total_tokens must be a positive integer")
        if self.expected_arguments and not self.expected_tool:
            raise ValueError("expected_arguments requires expected_tool")
        if self.expected_tool in self.forbidden_tools:
            raise ValueError("expected_tool cannot also be forbidden")
        if any(b.casefold() in a.casefold() for a in self.must_contain for b in self.must_not_contain):
            raise ValueError("required text conflicts with forbidden text")
        if "security" in self.metadata and "security" not in self.tags:
            raise ValueError("security metadata requires the security tag")
        if not any((self.expected_tool, self.forbidden_tools, self.must_contain,
                    self.must_not_contain, self.expected_refusal is not None,
                    self.max_latency_ms is not None, self.max_total_tokens is not None,
                    self.allowed_tools is not None, self.require_tool_results,
                    self.expected_output is not None, self.expected_state is not None,
                    self.required_tools, self.tool_sequence, self.max_tool_calls is not None)):
            raise ValueError("case needs at least one evaluable condition")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EvalCase":
        if not isinstance(payload, dict):
            raise ValueError("case must be an object")
        values = dict(payload)
        unknown = set(values) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"unknown fields: {sorted(unknown)}")
        for name in ("forbidden_tools", "must_contain", "must_not_contain", "tags",
                     "required_tools", "tool_sequence"):
            if name in values:
                if not isinstance(values[name], list):
                    raise ValueError(f"{name} must be an array")
                values[name] = tuple(values[name])
        if values.get("allowed_tools") is not None:
            if not isinstance(values["allowed_tools"], list):
                raise ValueError("allowed_tools must be an array or null")
            values["allowed_tools"] = tuple(values["allowed_tools"])
        return cls(**values)


@dataclass(frozen=True)
class AgentRun:
    output: str
    tool_calls: tuple[ToolCall, ...] = ()
    latency_ms: float = 0.0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    refused: bool | None = None
    error_type: str | None = None
    error_message: str | None = None
    trace_available: bool = True
    trace_events: tuple[dict[str, Any], ...] = ()
    final_state: dict[str, Any] | None = None


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    passed: bool
    scores: dict[str, float]
    failures: tuple[str, ...]
    run: AgentRun
    failure_codes: tuple[str, ...] = ()
    hard_failure: bool = False
    tags: tuple[str, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)
    status: str = "completed"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvaluationReport:
    results: tuple[CaseResult, ...]
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> int:
        return sum(result.passed for result in self.results)

    @property
    def pass_rate(self) -> float:
        return self.passed / len(self.results) if self.results else 0.0

    @property
    def average_latency_ms(self) -> float:
        if not self.results:
            return 0.0
        return sum(result.run.latency_ms for result in self.results) / len(self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "metadata": self.metadata,
            "summary": {
                "total": len(self.results),
                "passed": self.passed,
                "hard_failures": sum(result.hard_failure for result in self.results),
                "not_evaluated": sum(result.status == "not_evaluated" for result in self.results),
                "cancelled": sum(result.status == "cancelled" for result in self.results),
                "pass_rate": round(self.pass_rate, 4),
                "average_latency_ms": round(self.average_latency_ms, 3),
            },
            "results": [result.to_dict() for result in self.results],
        }
