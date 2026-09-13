import copy
import json
import tempfile
import unittest
from pathlib import Path

from agent_eval_lab.models import AgentRun, EvalCase
from agent_eval_lab.reporting import compare_reports, load_report, render_html, save_json
from agent_eval_lab.runner import EvaluationRunner, load_cases
from agent_eval_lab.relations import make_variant, run_pair
from agent_eval_lab.security import SeededFaultAgent
from test_security import fixture


class ReportingTests(unittest.TestCase):
    def test_evaluator_versions_must_match_for_comparison(self):
        a, b = self.report(), self.report()
        a["metadata"].pop("evaluator_version")
        with self.assertRaisesRegex(ValueError, "evaluator versions differ"):
            compare_reports(a, b)
        b["metadata"].pop("evaluator_version")
        self.assertEqual(compare_reports(a, b)["release_check"], "eligible_for_review")

    def test_report_round_trip_keeps_process_and_state_evidence(self):
        from agent_eval_lab.models import ToolCall
        class Agent:
            def run(self, case):
                return AgentRun("done", (ToolCall("update", result={"status": "ok"}),),
                                final_state={"ticket": {"status": "closed"}})
        case = EvalCase("x", "close ticket", allowed_tools=("update",), require_tool_results=True,
                        expected_state={"ticket": {"status": "closed"}})
        report = EvaluationRunner(Agent()).run((case,)).to_dict()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            save_json(path, report)
            restored = load_report(path)
            self.assertEqual(restored["results"], json.loads(json.dumps(report["results"])))
            page = render_html(restored)
            self.assertIn("tool_checks", page)
            self.assertIn("final_state", page)

    def report(self):
        class Agent:
            def run(self, case):
                return AgentRun("ok")
        return EvaluationRunner(Agent()).run(tuple(EvalCase(str(i), "task", must_contain=("ok",), tags=("normal",))
                                                   for i in range(4))).to_dict()

    def test_comparison_all_four_groups_and_new_hard_failure(self):
        a = self.report()
        a["results"][0]["passed"] = False
        a["results"][3]["passed"] = False
        b = copy.deepcopy(a)
        b["metadata"]["run_id"] = "candidate"
        b["results"][0]["passed"] = True
        b["results"][1]["passed"] = False
        b["results"][1]["hard_failure"] = True
        result = compare_reports(a, b)
        self.assertEqual(result["groups"], {"improvement": ["0"], "regression": ["1"],
                                          "unchanged_pass": ["2"], "unchanged_failure": ["3"]})
        self.assertEqual(result["new_hard_failures"], ["1"])
        self.assertEqual(result["release_check"], "reject")

    def test_mismatched_dataset_or_duplicates_rejected(self):
        a, b = self.report(), self.report()
        b["metadata"]["dataset_hash"] = "changed"
        with self.assertRaises(ValueError):
            compare_reports(a, b)
        b = copy.deepcopy(a)
        b["results"][1]["case_id"] = b["results"][0]["case_id"]
        with self.assertRaises(ValueError):
            compare_reports(a, b)

    def test_tampering_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            save_json(path, self.report())
            payload = load_report(path)
            payload["results"][0]["run"]["output"] = "changed"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "E_TRACE_TAMPER"):
                load_report(path)

    def test_html_escapes_model_and_case_content(self):
        report = self.report()
        report["results"][0]["case_id"] = '<img src=x onerror="bad()">'
        report["results"][0]["run"]["output"] = "<script>alert(1)</script>" + "x" * 10000
        page = render_html(report)
        self.assertNotIn("<script>", page)
        self.assertNotIn("<img ", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("Content-Security-Policy", page)

    def test_replay_removes_only_registered_attack(self):
        base, variant, manifest = make_variant(fixture(), "R0-R1")
        self.assertTrue(manifest.validate(base, variant)["valid"])
        self.assertEqual(base.metadata["security"]["private_canary"], variant.metadata["security"]["private_canary"])
        self.assertEqual(variant.metadata["security"]["source"]["attack"], "")

    def test_capability_and_authority_pair(self):
        case = fixture()
        case.metadata["security"]["capabilities"].remove("export_all_secrets")
        result = run_pair(SeededFaultAgent("normal"), case, "MR-CAP")
        self.assertEqual(result["outcome"]["CAR"], 0)
        case.metadata["security"]["authority"]["approved"] = True
        base, variant, manifest = make_variant(case, "MR-AUTH")
        self.assertTrue(manifest.validate(base, variant)["valid"])
        self.assertFalse(variant.metadata["security"]["authority"]["approved"])

    def test_dataset_scale_and_registered_controls(self):
        cases = load_cases(Path(__file__).resolve().parents[1] / "datasets" / "regression-v1.jsonl")
        self.assertEqual(len(cases), 30)
        self.assertEqual(sum("normal" in c.tags for c in cases), 15)
        self.assertEqual(sum("attack" in c.tags for c in cases), 10)
        self.assertEqual(sum("negative-control" in c.tags for c in cases), 10)
        self.assertEqual(sum("edge" in c.tags for c in cases), 5)
