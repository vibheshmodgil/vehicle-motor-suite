"""Summarize checkpointed benchmark rows without treating rule matches as truth."""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path


def pct(values, fraction):
    values = sorted(values)
    if not values:
        return None
    p = (len(values) - 1) * fraction
    lo = int(p)
    return values[lo] + (values[min(lo + 1, len(values) - 1)] - values[lo]) * (p - lo)


def metrics(rows):
    scored = [r for r in rows if "score" in r]
    lat = [r["end_to_end_latency_s"] for r in scored]
    rules = [r["score"]["rule_score"] for r in scored if r["score"]["rule_score"] is not None]
    voice = [r for r in scored if r["category"] in
             ("powertrain_direct", "acceleration_direct", "conversation")]
    generated = [r for r in scored if r.get("model_actual") not in ("local", None)]
    model_rules = [r["score"]["rule_score"] for r in generated
                   if r["score"]["rule_score"] is not None]
    model_lat = [r["end_to_end_latency_s"] for r in generated]
    first_tokens = [r["ttft_s"] for r in generated if r.get("ttft_s") is not None]
    return {
        "cases": len(rows), "scored": len(scored),
        "rule_score_mean": statistics.mean(rules) if rules else None,
        "latency_median_s": pct(lat, .5), "latency_p90_s": pct(lat, .9),
        "model_calls": len(generated),
        "model_only_rule_score_mean": statistics.mean(model_rules) if model_rules else None,
        "model_only_latency_median_s": pct(model_lat, .5),
        "model_only_latency_p90_s": pct(model_lat, .9),
        "model_only_ttft_median_s": pct(first_tokens, .5),
        "voice_eligible_le_55_words": (sum(r["score"]["word_count"] <= 55 for r in voice) /
                                     len(voice)) if voice else None,
        "failures": dict(Counter(r.get("primary_failure") for r in scored if r.get("primary_failure"))),
    }


def summarize(path):
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    groups = defaultdict(list)
    for row in rows:
        groups[row.get("category", "error")].append(row)
    return {"path": str(path), "overall": metrics(rows),
            "categories": {name: metrics(group) for name, group in sorted(groups.items())}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = [summarize(path) for path in args.paths]
    output = json.dumps(result, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    print(output)
