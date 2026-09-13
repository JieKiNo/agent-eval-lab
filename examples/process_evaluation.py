"""Offline tool-loop integration: python examples/process_evaluation.py [--fail-update]."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from agent_eval_lab.live import EvaluationSession
from agent_eval_lab.models import EvalCase
from agent_eval_lab.reporting import save_json
from agent_eval_lab.runner import EvaluationRunner


class TicketAgent:
    def __init__(self, fail_update=False):
        self.fail_update = fail_update

    def run(self, case):
        environment = {"tickets": {"T-1": {"status": "open"}}}
        def show(event):
            if event["kind"] in ("tool_evaluation", "tool_blocked"):
                print(json.dumps(event, ensure_ascii=False))
        session = EvaluationSession(case, show)

        def lookup_ticket(ticket_id):
            return deepcopy(environment["tickets"][ticket_id])

        def close_ticket(ticket_id):
            if self.fail_update:
                return {"error": "simulated backend failure"}
            environment["tickets"][ticket_id]["status"] = "closed"
            return {"status": "closed"}

        lookup = session.instrument("lookup_ticket", lookup_ticket)
        close = session.instrument("close_ticket", close_ticket)
        lookup("T-1")
        close("T-1")
        closed = environment["tickets"]["T-1"]["status"] == "closed"
        return session.build_run("已关闭" if closed else "处理失败",
                                 final_state=deepcopy(environment))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fail-update", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    case = EvalCase("close-ticket", "关闭工单 T-1", expected_tool="close_ticket",
                    expected_arguments={"ticket_id": "T-1"},
                    allowed_tools=("lookup_ticket", "close_ticket"), require_tool_results=True,
                    required_tools=("lookup_ticket", "close_ticket"),
                    tool_sequence=("lookup_ticket", "close_ticket"), max_tool_calls=2,
                    expected_tool_result={"status": "closed"}, expected_output="已关闭",
                    expected_state={"tickets": {"T-1": {"status": "closed"}}}, tags=("normal",))
    report = EvaluationRunner(TicketAgent(args.fail_update)).run((case,), {"evidence_status": "development"})
    if args.out:
        save_json(args.out, report.to_dict())
    print(f"passed={report.passed}/{len(report.results)} codes={report.results[0].failure_codes}")
    return 0 if report.passed == len(report.results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
