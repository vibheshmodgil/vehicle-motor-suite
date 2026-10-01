"""Measure retrieval separately from generation and citation quality."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vmi import rag_store  # noqa: E402


def expectation(question):
    q = question.casefold()
    if "tsi" in q:
        return "absent", None  # no TSI document is in this repository's KB
    if "u546" in q:
        return "source", "U546_eff_map" if "efficiency" in q else "U546_torque_speed_map"
    if "temperature-rise test procedure" in q:
        return "source", "EV_Motor_Testing"
    if "goodman" in q or "mechanical design handbook" in q:
        return "source", "Formula_Handbook"
    if "mtpa angle" in q:
        return "source", "MTPA_MTPV"
    if "hub motor" in q or "mid-mount" in q or "testing procedure" in q:
        return "source", "EV_Motor_Testing"
    return "unverified", None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "assistant_rag_probe.jsonl")
    args = parser.parse_args()
    cases = json.loads((ROOT / "tests" / "assistant_qa_cases.json").read_text(encoding="utf-8"))
    rows = []
    for case in cases:
        if case["category"] != "rag":
            continue
        expected, needle = expectation(case["input"])
        start = time.perf_counter()
        try:
            hits = rag_store.query(case["input"])
            sources = [h["source"] for h in hits]
            passed = ((not sources) if expected == "absent" else
                      any(needle.casefold() in s.casefold() for s in sources)
                      if expected == "source" else None)
            status = "unverified" if passed is None else "pass" if passed else "fail"
            error = None
        except Exception as exc:
            sources, status, error = [], "fail", str(exc)
        rows.append({"id": case["id"], "question": case["input"],
                     "expected": expected, "expected_source": needle,
                     "sources": sources, "latency_s": time.perf_counter() - start,
                     "status": status, "error": error})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print(json.dumps({status: sum(r["status"] == status for r in rows)
                      for status in ("pass", "fail", "unverified")}, indent=2))
    return 1 if any(r["status"] == "fail" for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
