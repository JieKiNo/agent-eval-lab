from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import asdict, replace
from datetime import datetime, timezone
import uuid
from pathlib import Path
from time import perf_counter

from .adapters import AgentAdapter, AsyncAgentAdapter
from .evaluators import EVALUATOR_VERSION, evaluate_case
from .extensions import RunEvaluator, apply_checks, collect_checks
from .models import AgentRun, CaseResult, EvalCase, EvaluationReport
from .security import canonical_hash, evaluate_security, prepare_case, validate_security


def load_cases(path: Path) -> tuple[EvalCase, ...]:
    cases: list[EvalCase] = []
    seen: dict[str, int] = {}
    with path.open("r", encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                case = EvalCase.from_dict(payload)
                if "security" in case.metadata:
                    validate_security(case.metadata["security"])
                if case.id in seen:
                    raise ValueError(f"duplicate id '{case.id}', first seen at line {seen[case.id]}")
                seen[case.id] = line_number
                cases.append(case)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise ValueError(f"E_INPUT_INVALID at {path}:{line_number}: {error}") from error
    if not cases:
        raise ValueError(f"E_INPUT_INVALID at {path}: dataset is empty")
    return tuple(cases)


class EvaluationRunner:
    def __init__(self, adapter: AgentAdapter, evaluators: tuple[RunEvaluator, ...] = ()):
        self.adapter = adapter
        self.evaluators = tuple(evaluators)

    def run(self, cases: tuple[EvalCase, ...], metadata: dict | None = None, on_result=None) -> EvaluationReport:
        started_at = datetime.now(timezone.utc).isoformat()
        dataset_hash = canonical_hash([asdict(case) for case in cases])
        results = []
        cancelled = False
        for case in cases:
            if cancelled:
                result = CaseResult(case.id, False, {}, ("not run after cancellation",),
                                    AgentRun("", trace_available=False), tags=case.tags, status="not_evaluated")
                results.append(result)
                if on_result:
                    on_result(result)
                continue
            case = prepare_case(case)
            started = perf_counter()
            try:
                agent_run = self.adapter.run(case)
                if not isinstance(agent_run, AgentRun):
                    raise TypeError("adapter returned an invalid run")
            except KeyboardInterrupt:
                cancelled = True
                agent_run = AgentRun("", error_type="cancelled", error_message="run cancelled", trace_available=False)
            except TimeoutError:
                agent_run = AgentRun("", error_type="timeout", error_message="agent timed out", trace_available=False)
            except Exception as error:
                # Exception bodies can contain request headers or credentials.
                agent_run = AgentRun("", error_type="adapter_error",
                                     error_message=f"adapter failed ({type(error).__name__})", trace_available=False)
            elapsed_ms = (perf_counter() - started) * 1000
            measured_run = agent_run if getattr(self.adapter, "preserve_latency", False) else replace(
                agent_run, latency_ms=elapsed_ms)
            result = evaluate_security(case, evaluate_case(case, measured_run))
            result = apply_checks(result, collect_checks(self.evaluators, case, measured_run))
            results.append(replace(result, evidence=result.evidence | {"input": case.input},
                                   status="cancelled" if cancelled else "completed"))
            if on_result:
                on_result(results[-1])
        source = Path(__file__).parent
        run_metadata = dict(metadata or {}) | {
            "run_id": str(uuid.uuid4()), "dataset_hash": dataset_hash,
            "started_at": started_at, "finished_at": datetime.now(timezone.utc).isoformat(),
            "code_hash": canonical_hash({p.name: p.read_text(encoding="utf-8") for p in sorted(source.glob("*.py"))}),
            "adapter": type(self.adapter).__name__,
            "evaluator_version": EVALUATOR_VERSION,
        }
        run_metadata.setdefault("evidence_status", "development")
        if hasattr(self.adapter, "public_config"):
            run_metadata["config"] = self.adapter.public_config()
        return EvaluationReport(tuple(results), run_metadata)


class AsyncEvaluationRunner:
    """Async equivalent of EvaluationRunner for streaming Agent frameworks."""

    def __init__(self, adapter: AsyncAgentAdapter, evaluators: tuple[RunEvaluator, ...] = ()):
        self.adapter = adapter
        self.evaluators = tuple(evaluators)

    async def run(self, cases: tuple[EvalCase, ...], metadata: dict | None = None,
                  on_result=None) -> EvaluationReport:
        started_at = datetime.now(timezone.utc).isoformat()
        dataset_hash = canonical_hash([asdict(case) for case in cases])
        results = []
        cancelled = False
        for case in cases:
            if cancelled:
                result = CaseResult(case.id, False, {}, ("not run after cancellation",),
                                    AgentRun("", trace_available=False), tags=case.tags,
                                    status="not_evaluated")
                results.append(result)
                if on_result:
                    callback_result = on_result(result)
                    if inspect.isawaitable(callback_result):
                        await callback_result
                continue
            case = prepare_case(case)
            started = perf_counter()
            try:
                agent_run = await self.adapter.run(case)
                if not isinstance(agent_run, AgentRun):
                    raise TypeError("adapter returned an invalid run")
            except asyncio.CancelledError:
                cancelled = True
                agent_run = AgentRun("", error_type="cancelled", error_message="run cancelled",
                                     trace_available=False)
            except TimeoutError:
                agent_run = AgentRun("", error_type="timeout", error_message="agent timed out",
                                     trace_available=False)
            except Exception as error:
                agent_run = AgentRun("", error_type="adapter_error",
                                     error_message=f"adapter failed ({type(error).__name__})",
                                     trace_available=False)
            elapsed_ms = (perf_counter() - started) * 1000
            measured_run = agent_run if getattr(self.adapter, "preserve_latency", False) else replace(
                agent_run, latency_ms=elapsed_ms)
            result = evaluate_security(case, evaluate_case(case, measured_run))
            result = apply_checks(result, collect_checks(self.evaluators, case, measured_run))
            results.append(replace(result, evidence=result.evidence | {"input": case.input},
                                   status="cancelled" if cancelled else "completed"))
            if on_result:
                callback_result = on_result(results[-1])
                if inspect.isawaitable(callback_result):
                    await callback_result
        source = Path(__file__).parent
        run_metadata = dict(metadata or {}) | {
            "run_id": str(uuid.uuid4()), "dataset_hash": dataset_hash,
            "started_at": started_at, "finished_at": datetime.now(timezone.utc).isoformat(),
            "code_hash": canonical_hash({
                p.name: p.read_text(encoding="utf-8") for p in sorted(source.glob("*.py"))}),
            "adapter": type(self.adapter).__name__,
            "evaluator_version": EVALUATOR_VERSION,
        }
        run_metadata.setdefault("evidence_status", "development")
        if hasattr(self.adapter, "public_config"):
            run_metadata["config"] = self.adapter.public_config()
        return EvaluationReport(tuple(results), run_metadata)
