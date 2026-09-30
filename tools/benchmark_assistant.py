"""Run local models over tests/assistant_cases.json; record answers and latency, not fabricated grades.

Cases go through the same routing as the chat panel: local small-talk and
checked calculations first, then the model. Cases with "rag": true query the
real knowledge base; "hits" are fixed fixture excerpts.

python tools/benchmark_assistant.py --models qwen3:4b-instruct --cases power real_u546 --repeats 2
"""
import argparse
import datetime
import json
import pathlib
import platform
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vmi.assistant_core import answer_case

ANALYSIS_TYPES = ("Powertrain Sizing", "Acceleration", "Parametric Study", "Drive Cycle",
                  "Drive Cycle Efficiency", "Compare Standard Motor Data", "Engine analysis",
                  "Range analysis", "MTPA / MTPV (PMSM)", "Mechanical Design (Motor)",
                  "Motor BOM (Cost & Weight)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--cases", nargs="*")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--output", default="reports/assistant_benchmark.jsonl")
    args = parser.parse_args()
    cases = json.loads((ROOT / "tests/assistant_cases.json").read_text(encoding="utf-8"))
    if args.cases:
        unknown = set(args.cases) - {c["id"] for c in cases}
        if unknown:
            parser.error(f"Unknown cases: {sorted(unknown)}")
        cases = [c for c in cases if c["id"] in args.cases]
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    for model in args.models:
        for case in cases:
            for repeat in range(args.repeats):
                record = {"timestamp": datetime.datetime.now().isoformat(), "platform": platform.platform(),
                          "model": model, "case": case["id"], "repeat": repeat + 1,
                          "question": case["question"], "rubric": case["rubric"],
                          "quality_review": "pending human engineering review"}
                start = time.perf_counter()
                try:
                    reply, metrics, sources = answer_case(case, model, ANALYSIS_TYPES)
                    record.update(answer=reply, metrics=metrics, sources=sources, status="ok")
                except Exception as exc:
                    record.update(status="error", error=str(exc), elapsed_s=time.perf_counter() - start)
                with output.open("a", encoding="utf-8") as file:
                    file.write(json.dumps(record, ensure_ascii=False) + "\n")
                print(f"{model} {case['id']} #{repeat+1}: {record['status']} ({time.perf_counter()-start:.1f}s)", flush=True)


if __name__ == "__main__":
    main()
