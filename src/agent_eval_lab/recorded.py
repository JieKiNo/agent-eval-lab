"""Framework-neutral JSONL trace ingestion."""
from __future__ import annotations

import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import AgentRun, EvalCase, ToolCall
from .security import canonical_hash


RUN_FIELDS = {"output", "tool_calls", "latency_ms", "prompt_tokens", "completion_tokens",
              "refused", "error_type", "error_message", "trace_available", "trace_events",
              "final_state"}
TOOL_FIELDS = {"name", "arguments", "result", "call_id"}
TRACE_SCHEMA_VERSION = "1.0"


def _tool_call(value: Any) -> ToolCall:
    if not isinstance(value, dict) or set(value) - TOOL_FIELDS:
        raise ValueError("tool call must contain only name, arguments, result and call_id")
    name = value.get("name")
    arguments = value.get("arguments", {})
    call_id = value.get("call_id")
    if not isinstance(name, str) or not name.strip() or not isinstance(arguments, dict):
        raise ValueError("tool call needs a non-empty name and object arguments")
    if call_id is not None and not isinstance(call_id, str):
        raise ValueError("tool call_id must be a string or null")
    return ToolCall(name, arguments, value.get("result"), call_id)


def _agent_run(value: Any) -> AgentRun:
    if not isinstance(value, dict) or set(value) - RUN_FIELDS:
        raise ValueError(f"run contains unknown fields: {sorted(set(value) - RUN_FIELDS) if isinstance(value, dict) else []}")
    output = value.get("output")
    calls = value.get("tool_calls", [])
    events = value.get("trace_events", [])
    if not isinstance(output, str) or not isinstance(calls, list):
        raise ValueError("run needs string output and array tool_calls")
    if not isinstance(events, list) or any(not isinstance(event, dict) for event in events):
        raise ValueError("trace_events must be an array of objects")
    latency = value.get("latency_ms", 0.0)
    if type(latency) not in (int, float) or not math.isfinite(latency) or latency < 0:
        raise ValueError("latency_ms must be a finite non-negative number")
    for field in ("prompt_tokens", "completion_tokens"):
        amount = value.get(field)
        if amount is not None and (type(amount) is not int or amount < 0):
            raise ValueError(f"{field} must be a non-negative integer or null")
    refused = value.get("refused")
    if refused is not None and type(refused) is not bool:
        raise ValueError("refused must be a boolean or null")
    trace_available = value.get("trace_available", True)
    if type(trace_available) is not bool:
        raise ValueError("trace_available must be boolean")
    final_state = value.get("final_state")
    if final_state is not None and not isinstance(final_state, dict):
        raise ValueError("final_state must be an object or null")
    for field in ("error_type", "error_message"):
        if value.get(field) is not None and not isinstance(value[field], str):
            raise ValueError(f"{field} must be a string or null")
    return AgentRun(output, tuple(_tool_call(call) for call in calls), latency_ms=latency,
                    prompt_tokens=value.get("prompt_tokens"), completion_tokens=value.get("completion_tokens"),
                    refused=refused, error_type=value.get("error_type"), error_message=value.get("error_message"),
                    trace_available=trace_available, trace_events=tuple(events), final_state=final_state)


def load_recorded_runs(path: Path) -> dict[str, AgentRun]:
    runs: dict[str, AgentRun] = {}
    with path.open("r", encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                if not isinstance(payload, dict) or set(payload) - (RUN_FIELDS | {"case_id", "schema_version"}):
                    raise ValueError("trace row contains unknown fields")
                if payload.pop("schema_version", None) != TRACE_SCHEMA_VERSION:
                    raise ValueError(f"trace schema_version must be {TRACE_SCHEMA_VERSION}")
                case_id = payload.pop("case_id", None)
                if not isinstance(case_id, str) or not case_id.strip():
                    raise ValueError("trace row needs a non-empty case_id")
                if case_id in runs:
                    raise ValueError(f"duplicate case_id '{case_id}'")
                runs[case_id] = _agent_run(payload)
            except (ValueError, TypeError, json.JSONDecodeError) as error:
                raise ValueError(f"E_TRACE_INVALID at {path}:{line_number}: {error}") from error
    if not runs:
        raise ValueError(f"E_TRACE_INVALID at {path}: trace is empty")
    return runs


def validate_trace_coverage(cases: tuple[EvalCase, ...], runs: dict[str, AgentRun]) -> None:
    expected, actual = {case.id for case in cases}, set(runs)
    if expected != actual:
        raise ValueError(f"E_TRACE_INVALID: missing={sorted(expected - actual)} extra={sorted(actual - expected)}")


class RecordedAgent:
    """Adapter over completed traces from any Agent framework or language."""

    preserve_latency = True

    def __init__(self, runs: dict[str, AgentRun]):
        self.runs = dict(runs)

    def run(self, case: EvalCase) -> AgentRun:
        return self.runs[case.id]

    def public_config(self) -> dict[str, Any]:
        return {"adapter": "recorded_jsonl", "schema_version": TRACE_SCHEMA_VERSION,
                "trace_hash": canonical_hash({
            case_id: asdict(run) for case_id, run in sorted(self.runs.items())
        })}
