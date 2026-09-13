"""Audit the delivered screening evidence without making model requests."""
import json
from dataclasses import asdict
from pathlib import Path

from agent_eval_lab.reporting import compare_reports, load_report, metrics, save_json
from agent_eval_lab.runner import load_cases
from agent_eval_lab.security import canonical_hash


ROOT = Path(__file__).resolve().parents[1]


def main():
    cases = load_cases(ROOT / "datasets/regression-v1.jsonl")
    assert len(cases) == 30
    assert sum("normal" in c.tags for c in cases) == 15
    assert sum("attack" in c.tags for c in cases) == 10
    assert sum("edge" in c.tags for c in cases) == 5
    by_id = {c.id: c for c in cases}
    for n in range(1, 11):
        control = by_id[f"control-{n:02}"]
        attack = by_id[f"attack-{n:02}"]
        assert control.input == attack.input
        assert control.metadata["security"]["source"]["facts"] == attack.metadata["security"]["source"]["facts"]
        assert not control.metadata["security"]["source"]["attack"]
        assert attack.metadata["security"]["source"]["attack"]
    source = ROOT / "src/agent_eval_lab"
    code_hash = canonical_hash({p.name: p.read_text(encoding="utf-8") for p in sorted(source.glob("*.py"))})
    dataset_hash = canonical_hash([asdict(c) for c in cases])
    reports = {}
    total_rows = 0
    screening_code_hash = None
    screening_dataset_hash = None
    for version in ("baseline", "candidate"):
        folder = ROOT / "runs/screening"
        report = load_report(folder / f"{version}-v2.json")
        protocol = load_report(folder / f"protocol-{version}-v2.json")
        config = json.loads((ROOT / f"configs/deepseek-{version}.json").read_text(encoding="utf-8"))
        assert report["metadata"]["code_hash"] == protocol["code_hash"]
        screening_code_hash = screening_code_hash or protocol["code_hash"]
        assert protocol["code_hash"] == screening_code_hash
        assert report["metadata"]["dataset_hash"] == protocol["dataset_hash"]
        screening_dataset_hash = screening_dataset_hash or protocol["dataset_hash"]
        assert protocol["dataset_hash"] == screening_dataset_hash
        assert report["metadata"]["protocol_hash"] == protocol["artifact_hash"]
        assert protocol["config_hash"] == canonical_hash(config)
        assert {r["case_id"] for r in report["results"]} == set(by_id)
        per_case = [load_report(p) for p in (folder / f"{version}-v2-cases").glob("*.json")]
        assert len(per_case) == 30
        saved = {r["case_id"]: {k: v for k, v in r.items() if k != "artifact_hash"} for r in per_case}
        assert len(saved) == 30
        for row in report["results"]:
            assert row == saved[row["case_id"]]
            assert row["status"] == "completed"
            assert row["run"]["error_type"] is None
            events = [e["response"] for e in row["run"]["trace_events"] if e["kind"] == "response"]
            assert events
            for name in ("prompt_tokens", "completion_tokens"):
                assert row["run"][name] == sum(e["usage"][name] for e in events)
            for action in row.get("evidence", {}).get("action_ledger", []):
                assert action["simulated"] and not action["external_effect"]
        assert report["summary"]["passed"] == metrics(report)["passed"]
        total_rows += len(per_case)
        reports[version] = report
    a = json.loads((ROOT / "configs/deepseek-baseline.json").read_text(encoding="utf-8"))
    b = json.loads((ROOT / "configs/deepseek-candidate.json").read_text(encoding="utf-8"))
    assert {k for k in a if a[k] != b[k]} == {"system_prompt"}
    diff = compare_reports(reports["baseline"], reports["candidate"])
    saved_diff = load_report(ROOT / "runs/screening/comparison-v2.json")
    assert diff == {k: v for k, v in saved_diff.items() if k != "artifact_hash"}
    assert diff["groups"]["improvement"] == ["control-04", "control-07", "edge-missing-context"]
    assert not diff["groups"]["regression"]
    pairs = {}
    for name in ("auth", "cap", "replay"):
        pair = load_report(ROOT / f"runs/development/{name}-candidate-v2.json")
        assert pair["manifest"]["valid"] and pair["observable"]
        pairs[name] = pair["outcome"]
    result = {"audit": "passed", "current_code_hash": code_hash,
              "screening_code_hash": screening_code_hash,
              "current_code_matches_screening": code_hash == screening_code_hash,
              "current_dataset_hash": dataset_hash,
              "screening_dataset_hash": screening_dataset_hash,
              "current_dataset_schema_matches_screening": dataset_hash == screening_dataset_hash,
              "verified_case_artifacts": total_rows, "comparison_rebuilt": True,
              "raw_token_totals_rebuilt": True, "only_config_difference": "system_prompt",
              "relation_outcomes": pairs, "publication": "not_evaluated",
              "formal_multi_repeat_experiment": "not_evaluated"}
    save_json(ROOT / "runs/audit/artifact-audit.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
