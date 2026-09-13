"""Extension points for domain-specific deterministic evaluation checks."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any, Callable, Iterable

from .models import AgentRun, CaseResult, EvalCase


@dataclass(frozen=True)
class EvaluationCheck:
    name: str
    passed: bool
    code: str
    message: str = ""
    hard_failure: bool = False
    evidence: Any = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("check name must be a non-empty string")
        if type(self.passed) is not bool or type(self.hard_failure) is not bool:
            raise ValueError("check passed and hard_failure must be boolean")
        if not isinstance(self.code, str) or (not self.passed and not self.code.strip()):
            raise ValueError("a failed check needs a non-empty code")
        if not isinstance(self.message, str):
            raise ValueError("check message must be a string")


RunEvaluator = Callable[[EvalCase, AgentRun], Iterable[EvaluationCheck]]


def collect_checks(evaluators: tuple[RunEvaluator, ...], case: EvalCase,
                   run: AgentRun) -> tuple[EvaluationCheck, ...]:
    checks: list[EvaluationCheck] = []
    for evaluator in evaluators:
        name = getattr(evaluator, "__name__", type(evaluator).__name__)
        try:
            produced = tuple(evaluator(case, run))
            if any(not isinstance(check, EvaluationCheck) for check in produced):
                raise TypeError("evaluator returned an invalid check")
            checks.extend(produced)
        except Exception:
            checks.append(EvaluationCheck(
                f"evaluator_error:{name}", False, "E_EVALUATOR_ERROR",
                f"custom evaluator failed ({type(evaluator).__name__})",
            ))
    return tuple(checks)


def apply_checks(result: CaseResult, checks: tuple[EvaluationCheck, ...]) -> CaseResult:
    if not checks:
        return result
    scores = dict(result.scores)
    codes = list(result.failure_codes)
    failures = list(result.failures)
    for check in checks:
        scores[f"custom:{check.name}"] = float(check.passed)
        if not check.passed:
            codes.append(check.code)
            failures.append(check.message or f"custom check failed: {check.name}")
    evidence = result.evidence | {"custom_checks": [asdict(check) for check in checks]}
    return replace(result, passed=result.passed and all(check.passed for check in checks),
                   scores=scores, failure_codes=tuple(codes), failures=tuple(failures),
                   hard_failure=result.hard_failure or any(
                       not check.passed and check.hard_failure for check in checks),
                   evidence=evidence)
