"""Offline assistant routing QA. Run with --legacy for the pre-fix baseline.

Writes one JSONL result per case. `unverified` means semantic, GUI, voice, or
real-model behavior was not evaluated; it is never counted as a pass.
"""
import argparse
import collections
import datetime
import json
import pathlib
import re
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vmi.assistant_core import checked_calculation_reply, small_talk_reply  # noqa: E402


def legacy_route(question, history):
    # These two phrases were added to the deterministic small-talk helper by
    # this stabilization. Keep the baseline adapter faithful to the old path.
    if " ".join(re.sub(r"[!?,.]", " ", question.casefold()).split()) in {
            "hey how are you", "how are you doing today"}:
        return "model_rag", "qwen3:4b-instruct", ["rag.query", "llm.chat"]
    if small_talk_reply(question) or checked_calculation_reply(question):
        return "local", "local", []
    return "model_rag", "qwen3:4b-instruct", ["rag.query", "llm.chat"]


def current_route(question, history):
    from vmi.assistant_core import plan_request
    plan = plan_request(question, history)
    return plan["route"], plan["model"], plan["operations"]


def run(legacy=False, output=None):
    cases = json.loads((ROOT / "tests" / "assistant_qa_cases.json").read_text(encoding="utf-8"))
    route = legacy_route if legacy else current_route
    rows = []
    for case in cases:
        start = time.perf_counter()
        record = {"timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  "session_id": case["id"], "id": case["id"], "category": case["category"],
                  "input": case["input"], "expected_route": case.get("expected_route"),
                  "expected_behavior": case["expected_behavior"],
                  "expected_tools": case["expected_tools"],
                  "expected_parameters": case["expected_parameters"]}
        unsupported = sorted(set(case["expected_tools"]) - {"state.read", "rag.query"})
        record["tool_contract_status"] = ("not_implemented: " + ", ".join(unsupported)
                                          if unsupported else "route_only_not_executed")
        try:
            actual, model, operations = route(case["input"], case["history"])
            record.update(actual_route=actual, model=model, operations=operations)
            expected = case.get("expected_route")
            if expected is None:
                record.update(status="unverified", failure_reason="Requires semantic, GUI, voice, or real-model review")
            elif actual == expected:
                record.update(status="pass", failure_reason=None)
            else:
                record.update(status="fail", failure_reason=f"Expected {expected}, got {actual}",
                              failure_category="Routing failure")
        except Exception as exc:
            record.update(status="fail", failure_reason=str(exc), failure_category="Error handling failure")
        record["latency_s"] = time.perf_counter() - start
        rows.append(record)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    counts = collections.Counter(row["status"] for row in rows)
    categories = collections.Counter(row.get("failure_category") for row in rows if row["status"] == "fail")
    print(json.dumps({"total": len(rows), "passed": counts["pass"], "failed": counts["fail"],
                      "unverified": counts["unverified"],
                      "target_tool_cases_with_missing_capability": sum(
                          r["tool_contract_status"].startswith("not_implemented") for r in rows),
                      "failure_categories": categories}, indent=2))
    return 1 if counts["fail"] else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy", action="store_true", help="Measure the original routing")
    parser.add_argument("--output", type=pathlib.Path)
    args = parser.parse_args()
    raise SystemExit(run(args.legacy, args.output))


if __name__ == "__main__":
    main()
