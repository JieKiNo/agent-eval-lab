from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .adapters import ReferenceAgent
from .runner import EvaluationRunner, load_cases
from .reporting import compare_reports, load_report, render_html, save_json
from .security import canonical_hash


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate a tool-using AI Agent")
    subcommands = parser.add_subparsers(dest="command", required=True)

    run_parser = subcommands.add_parser("run", help="run an evaluation dataset")
    run_parser.add_argument("--dataset", type=Path, required=True)
    run_parser.add_argument("--out", type=Path, required=True)
    run_parser.add_argument("--config", type=Path, help="JSON model config; keys come from the named environment variable")
    run_parser.add_argument("--evidence-status", choices=("smoke", "development", "screening"), default="development")
    run_parser.add_argument("--protocol", type=Path, help="verify dataset and config against a frozen protocol")
    grade = subcommands.add_parser("grade", help="grade framework-neutral recorded JSONL traces")
    grade.add_argument("--dataset", type=Path, required=True)
    grade.add_argument("--trace", type=Path, required=True)
    grade.add_argument("--out", type=Path, required=True)
    grade.add_argument("--evidence-status", choices=("smoke", "development", "screening"), default="development")
    selftest_parser = subcommands.add_parser("selftest", help="calibrate the safety detectors offline")
    selftest_parser.add_argument("--out", type=Path, required=True)
    compare = subcommands.add_parser("compare", help="compare two sealed reports")
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--candidate", type=Path, required=True)
    compare.add_argument("--out", type=Path, required=True)
    html_parser = subcommands.add_parser("html", help="render a sealed report without a server")
    html_parser.add_argument("--report", type=Path, required=True)
    html_parser.add_argument("--comparison", type=Path)
    html_parser.add_argument("--out", type=Path, required=True)
    freeze = subcommands.add_parser("freeze", help="freeze a screening protocol before model calls")
    freeze.add_argument("--dataset", type=Path, required=True)
    freeze.add_argument("--config", type=Path, required=True)
    freeze.add_argument("--out", type=Path, required=True)
    pair = subcommands.add_parser("pair", help="run one registered paired relation")
    pair.add_argument("--dataset", type=Path, required=True)
    pair.add_argument("--case-id", required=True)
    pair.add_argument("--relation", choices=("MR-AUTH", "MR-CAP", "R0-R1"), required=True)
    pair.add_argument("--config", type=Path, required=True)
    pair.add_argument("--out", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "compare":
        result = compare_reports(load_report(args.baseline), load_report(args.candidate))
        save_json(args.out, result)
        print(f"release_check={result['release_check']} comparison={args.out}")
        return 0
    if args.command == "html":
        comparison = load_report(args.comparison) if args.comparison else None
        report = load_report(args.report)
        if comparison and comparison["candidate_run_id"] != report["metadata"]["run_id"]:
            raise ValueError("comparison does not refer to this candidate report")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(render_html(report, comparison), encoding="utf-8")
        print(f"html={args.out}")
        return 0
    if args.command == "freeze":
        if args.out.exists():
            raise ValueError("protocol already exists; use a new version path")
        from .http_adapter import ModelConfig
        from .selftest import run_selftest
        config = ModelConfig(**json.loads(args.config.read_text(encoding="utf-8-sig")))
        calibration = run_selftest()
        if not calibration["passed"]:
            raise ValueError("safety calibration failed; screening stopped")
        cases = load_cases(args.dataset)
        source = Path(__file__).parent
        save_json(args.out, {"protocol_id": args.out.stem, "evidence_status": "screening",
            "frozen_at": datetime.now(timezone.utc).isoformat(), "repetitions": 1,
            "dataset_hash": canonical_hash([asdict(c) for c in cases]), "config_hash": canonical_hash(asdict(config)),
            "config": asdict(config), "case_ids": [c.id for c in cases],
            "code_hash": canonical_hash({p.name: p.read_text(encoding="utf-8") for p in sorted(source.glob("*.py"))}),
            "gates_before_run": calibration["gates"],
            "stopping_rule": "bounded turns, tokens and retries per case; preserve all failures",
            "max_completion_tokens": len(cases) * config.max_turns * config.max_tokens * (1 + config.retries)})
        print(f"frozen={args.out} cases={len(cases)}")
        return 0
    if args.command == "selftest":
        from .selftest import run_selftest
        result = run_selftest()
        save_json(args.out, result)
        print(f"selftest_passed={result['passed']} report={args.out}")
        return 0 if result["passed"] else 1
    if args.command == "grade":
        from .recorded import RecordedAgent, load_recorded_runs, validate_trace_coverage
        cases = load_cases(args.dataset)
        runs = load_recorded_runs(args.trace)
        validate_trace_coverage(cases, runs)
        report = EvaluationRunner(RecordedAgent(runs)).run(cases, {
            "evidence_status": args.evidence_status, "dataset": str(args.dataset),
            "trace": str(args.trace),
        })
        save_json(args.out, report.to_dict())
        print(f"evaluated={len(report.results)} passed={report.passed} "
              f"pass_rate={report.pass_rate:.1%} report={args.out}")
        return 0 if report.pass_rate == 1.0 else 1
    if args.command not in ("run", "pair"):
        raise ValueError(f"unsupported command: {args.command}")

    cases = load_cases(args.dataset)
    adapter = ReferenceAgent()
    if args.config:
        from .http_adapter import CompatibleAgent, ModelConfig
        adapter = CompatibleAgent(ModelConfig(**json.loads(args.config.read_text(encoding="utf-8-sig"))))
    if args.command == "pair":
        from .relations import run_pair
        case = next((c for c in cases if c.id == args.case_id), None)
        if case is None:
            raise ValueError("case-id not found")
        save_json(args.out, run_pair(adapter, case, args.relation))
        print(f"pair={args.out}")
        return 0
    metadata = {"evidence_status": args.evidence_status, "dataset": str(args.dataset)}
    if args.protocol:
        protocol = load_report(args.protocol)
        if not args.config or protocol["config_hash"] != canonical_hash(asdict(adapter.config)):
            raise ValueError("configuration differs from frozen protocol")
        if protocol["dataset_hash"] != canonical_hash([asdict(c) for c in cases]):
            raise ValueError("dataset differs from frozen protocol")
        source = Path(__file__).parent
        code_hash = canonical_hash({p.name: p.read_text(encoding="utf-8") for p in sorted(source.glob("*.py"))})
        if protocol["code_hash"] != code_hash:
            raise ValueError("code differs from frozen protocol; freeze a new version")
        if args.evidence_status != protocol["evidence_status"]:
            raise ValueError("evidence status differs from protocol")
        metadata["protocol_id"] = protocol["protocol_id"]
        metadata["protocol_hash"] = protocol["artifact_hash"]
    def persist_case(result):
        path = args.out.parent / (args.out.stem + "-cases") / (canonical_hash(result.case_id)[:16] + ".json")
        save_json(path, result.to_dict())
        print(f"case={result.case_id} status={result.status} passed={result.passed} codes={','.join(result.failure_codes)}", flush=True)
    report = EvaluationRunner(adapter).run(cases, metadata, on_result=persist_case)
    save_json(args.out, report.to_dict())
    print(
        f"evaluated={len(report.results)} passed={report.passed} "
        f"pass_rate={report.pass_rate:.1%} report={args.out}"
    )
    return 0 if report.pass_rate == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
