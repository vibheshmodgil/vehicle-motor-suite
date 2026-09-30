"""Run tests/assistant_questionnaire.json against local models and auto-score it.

Each question goes through the same path as the chat panel (_chat_worker):
local small-talk / checked calculation first, else knowledge-base search, app
state only when wants_screen_context() would send it, then the model. Scores
are mechanical (required facts found, forbidden content absent, word budget,
expected document retrieved) -- read the answers too before trusting them.

python tools/questionnaire.py --models qwen3:4b-instruct gemma3:4b
python tools/questionnaire.py --report reports/questionnaire_*.jsonl
"""
import argparse
import datetime
import json
import pathlib
import re
import statistics
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
from vmi import llm_client, rag_store  # noqa: E402
from vmi.assistant_core import (MAX_TOKENS, build_messages, checked_calculation_reply,  # noqa: E402
                                small_talk_reply, system_prompt, wants_screen_context)
from tools.benchmark_assistant import ANALYSIS_TYPES  # noqa: E402

STYLE_FLAGS = {
    "latex": r"\\\(|\\\[|\\frac|\$\$|\\text\{",
    "table": r"^\s*\|.*\|\s*$",
    "code_leak": r"Mixin\b|\bself\.\w|def \w+\(",
    "refusal": r"no (provided )?context|not (mentioned|provided) in the (provided )?context|I (can't|cannot) (help|answer)",
}


def score(case, reply, sources):
    checks = case.get("checks", {})
    must = checks.get("must", [])
    found = [any(re.search(p, reply, re.I | re.M) for p in group) for group in must]
    violations = [p for p in checks.get("must_not", []) if re.search(p, reply, re.I | re.M)]
    words = len(reply.split())
    flags = [name for name, p in STYLE_FLAGS.items() if re.search(p, reply, re.I | re.M)]
    if words > checks.get("max_words", 10 ** 9):
        flags.append("too_long")
    accuracy = (sum(found) / len(found)) if found else 1.0
    if violations:
        accuracy = 0.0
    result = {"accuracy": accuracy, "missing": [g[0] for g, ok in zip(must, found) if not ok],
              "violations": violations, "words": words, "style_flags": flags}
    if case.get("expect_source"):
        result["retrieved_expected"] = any(case["expect_source"].lower() in s.lower() for s in sources)
    return result


def answer(case, model):
    """Same routing as AssistantMixin._chat_worker. Returns (reply, metrics, sources)."""
    question = case["question"]
    started = time.perf_counter()
    local = small_talk_reply(question) or checked_calculation_reply(question)
    if local:
        return local, {"model": "local", "retrieval_s": 0.0, "total_s": time.perf_counter() - started}, []
    hits = rag_store.query(question)
    retrieval_s = time.perf_counter() - started
    screen = case.get("screen") if wants_screen_context(question) else None
    messages = build_messages(question, screen, hits, [], ANALYSIS_TYPES)
    reply, metrics = llm_client.stream_chat(messages, model, MAX_TOKENS)
    metrics.update(retrieval_s=retrieval_s, total_s=time.perf_counter() - started,
                   prompt_chars=sum(len(m["content"]) for m in messages))
    return reply, metrics, [h["source"] for h in hits]


def run(models, cases, output):
    for model in models:
        # Free the GPU for this model, then warm up exactly like the panel does
        # (load + cache the system prompt); the cold cost is reported separately.
        for other in models:
            subprocess.run(["ollama", "stop", other], capture_output=True)
        t0 = time.perf_counter()
        try:
            llm_client.warm_up(model, system_prompt(ANALYSIS_TYPES))
            load = time.perf_counter() - t0
        except llm_client.OllamaError as exc:
            print(f"{model}: warm-up failed: {exc}")
            continue
        placement = next((" ".join(line.split()[3:6]) for line in subprocess.run(
            ["ollama", "ps"], capture_output=True, text=True).stdout.splitlines() if line.startswith(model)), "?")
        print(f"== {model}: cold load + system prompt {load:.1f}s, {placement}", flush=True)
        for case in cases:
            record = {"timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
                      "model": model, "case": case["id"], "category": case["category"],
                      "question": case["question"], "cold_load_s": load, "placement": placement}
            try:
                reply, metrics, sources = answer(case, model)
                record.update(status="ok", answer=reply, metrics=metrics, sources=sources,
                              score=score(case, reply, sources))
                s = record["score"]
                print(f"  {case['id']:<20} {metrics['total_s']:6.1f}s acc={s['accuracy']:.2f} "
                      f"words={s['words']} {' '.join(s['style_flags'])} {s['missing'] or ''}", flush=True)
            except Exception as exc:
                record.update(status="error", error=str(exc))
                print(f"  {case['id']:<20} ERROR {exc}", flush=True)
            with output.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")


def report(paths):
    rows = [json.loads(line) for p in paths for line in open(p, encoding="utf-8") if line.strip()]
    print(f"{'model':<24}{'cat':<10}{'n':>3}{'acc':>6}{'style ok':>9}{'med s':>7}{'p90 s':>7}{'tok/s':>7}{'load':>6}")
    for model in dict.fromkeys(r["model"] for r in rows):
        for cat in ("casual", "motor", "software", "ALL"):
            sel = [r for r in rows if r["model"] == model and (cat == "ALL" or r["category"] == cat)]
            ok = [r for r in sel if r["status"] == "ok"]
            if not sel:
                continue
            acc = statistics.mean(r["score"]["accuracy"] for r in ok) if ok else 0
            style = sum(not r["score"]["style_flags"] for r in ok) / len(sel)
            times = sorted(r["metrics"]["total_s"] for r in ok) or [float("nan")]
            rates = [r["metrics"]["tokens_per_s"] for r in ok if r["metrics"].get("tokens_per_s")]
            print(f"{model:<24}{cat:<10}{len(sel):>3}{acc:>6.2f}{style:>9.0%}{statistics.median(times):>7.1f}"
                  f"{times[int(0.9 * (len(times) - 1))]:>7.1f}{(statistics.mean(rates) if rates else 0):>7.1f}"
                  f"{sel[0].get('cold_load_s', 0):>6.1f}")
    rag = [r for r in rows if r["status"] == "ok" and "retrieved_expected" in r["score"]]
    retr = [r["metrics"]["retrieval_s"] for r in rows if r["status"] == "ok" and r["metrics"].get("model") != "local"]
    if retr:
        print(f"\nRAG: median retrieval {statistics.median(retr):.2f}s, max {max(retr):.2f}s; expected document "
              f"retrieved {sum(r['score']['retrieved_expected'] for r in rag)}/{len(rag)}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+")
    parser.add_argument("--cases", nargs="*", help="case ids or categories")
    parser.add_argument("--output")
    parser.add_argument("--report", nargs="+", help="summarize existing result files instead of running")
    args = parser.parse_args()
    if args.report:
        return report(args.report)
    cases = json.loads((ROOT / "tests/assistant_questionnaire.json").read_text(encoding="utf-8"))
    if args.cases:
        cases = [c for c in cases if c["id"] in args.cases or c["category"] in args.cases]
    output = pathlib.Path(args.output or ROOT / "reports" /
                          f"questionnaire_{datetime.datetime.now():%Y%m%d_%H%M%S}.jsonl")
    output.parent.mkdir(parents=True, exist_ok=True)
    run(args.models, cases, output)
    report([output])


if __name__ == "__main__":
    main()
