"""Run the fixed questions through the existing assistant route and record full traces."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import time
from pathlib import Path

import requests

from evaluation.reference_case import calculate_reference
from evaluation.retrieval_diagnostics import diagnose
from evaluation.scoring import score
from vmi import assistant_core as core, llm_client, rag_store

ROOT = Path(__file__).resolve().parent
ANALYSES = ("Powertrain Sizing", "Acceleration", "Parametric Study", "Drive Cycle",
            "Drive Cycle Efficiency", "Compare Standard Motor Data", "Engine analysis",
            "Range analysis", "MTPA / MTPV (PMSM)", "Mechanical Design (Motor)",
            "Motor BOM (Cost & Weight)")


def screen_for_reference(ref):
    i = ref["inputs"]
    return {"analysis": "Powertrain Sizing", "inputs": {k: str(v) for k,v in i.items()},
            "selectors": {"output_combo": "Torque", "plot_part_combo": "At Wheel",
                          "speed_unit_combo": "Km/hr"},
            "datasets": {"motor_curve": False, "motor_efficiency_map": False,
                         "controller_efficiency_map": False},
            "results": [
                f"Estimated flat-road top speed ≈ {ref['top_speed_report_kmh']:.1f} km/h (peak capability curve vs. total resistive force).",
                f"Maximum startable gradient ≈ {ref['max_startable_gradient_pct']:.1f}% with the peak curve.",
                f"0–{i['target_speed']:.0f} km/h in ≈ {ref['acceleration_report_0_60_s']:.1f} s (flat road, peak torque available).",
            ], "plots": []}


def acceleration_screen(screen, ref):
    """Mirror the current Acceleration snapshot, including last plotted metrics."""
    view={**screen, "analysis": "Acceleration", "results": list(screen["results"])}
    view["results"].append(
        f"Last Acceleration plot: {ref['acceleration_plot_final_speed_kmh']:.1f} km/h at "
        f"{ref['inputs']['max_time']:.1f} s; {ref['inputs']['target_speed']:.0f} km/h at "
        f"{ref['acceleration_plot_0_60_s']:.2f} s; force-crossing top speed "
        f"{ref['acceleration_plot_top_speed_kmh']:.1f} km/h; settled near top speed at "
        f"{ref['acceleration_plot_settled_at_s']:.1f} s. Plot values may predate input edits."
    )
    return view


def model_metadata(model, profile="engineering", max_tokens=core.MAX_TOKENS, questions_path=None):
    try:
        tags = requests.get(llm_client.OLLAMA_URL+"/api/tags", timeout=5).json()["models"]
        entry = next((x for x in tags if x["name"] == model or x["name"] == model+":latest"), {})
    except Exception:
        entry = {}
    pipeline_files = ("vmi/assistant_core.py", "vmi/rag_store.py", "vmi/llm_client.py",
                      "vmi/assistant.py", "vmi/torque_force.py", "vmi/calc_ext.py",
                      "vmi/parametric.py", "vmi/physics.py", "evaluation/benchmark_runner.py",
                      "evaluation/scoring.py", "evaluation/reference_case.py")
    pipeline_hash = hashlib.sha256()
    for file in pipeline_files:
        pipeline_hash.update(file.encode())
        pipeline_hash.update((ROOT.parent / file).read_bytes())
    dataset_hash = hashlib.sha256((questions_path or ROOT / "questions.json").read_bytes() +
                                  (ROOT / "ground_truth.json").read_bytes()).hexdigest()
    return {"name": model, "digest": entry.get("digest"), "details": entry.get("details", {}),
            "profile": profile,
            "pipeline_sha256": pipeline_hash.hexdigest(), "dataset_sha256": dataset_hash,
            "options": {"temperature": 0.2, "num_predict": max_tokens,
                        "num_ctx": llm_client.NUM_CTX, "think": False,
                        "force_gpu_if_default": model == llm_client.CHAT_MODEL},
            "embedding_model": llm_client.EMBED_MODEL,
            "retrieval": {"chunk_words": rag_store.CHUNK_WORDS, "overlap": rag_store.CHUNK_OVERLAP,
                          "top_k": 3, "max_distance": rag_store.MAX_DISTANCE},
            "prompt_sha256": hashlib.sha256(core.system_prompt(ANALYSES, profile=profile).encode()).hexdigest()}


def execute_case(case, model, screen, with_diagnostics, ref, profile="engineering", max_tokens=core.MAX_TOKENS):
    started = time.perf_counter()
    question, history = case["question"], case.get("history", [])
    plan = core.plan_request(question, history)
    local = (core.small_talk_reply(question) or core.checked_calculation_reply(question)
             or core.action_reply(plan["route"]))
    use_rag = not local and plan["route"] == "rag"
    hits = []
    if use_rag:
        try:
            hits = rag_store.query(question)
        except Exception as exc:
            return {"error": f"retrieval: {exc}", "route": plan["route"],
                    "end_to_end_s": time.perf_counter()-started}
    retrieval_s = time.perf_counter()-started if use_rag else 0.0
    diagnostic = diagnose(question,hits) if use_rag and with_diagnostics else None
    case_screen = (acceleration_screen(screen, ref)
                   if case["category"] == "acceleration_direct" else screen)
    shown_screen = (case_screen if (plan["route"] == "state" or
                    core.wants_screen_context(question,history)) else None)
    messages = None
    raw_model_answer = None
    metrics = {"model": "local", "first_token_s": None, "total_s": 0,
               "generation_s": 0, "tokens": 0, "tokens_per_s": None,
               "prompt_eval_count": None, "eval_count": None}
    if local:
        reply = local
    elif use_rag and not hits:
        reply = "I couldn't find that requirement in the available indexed documents."
    elif use_rag and core.document_evidence_limit(question,hits):
        reply = core.document_evidence_limit(question,hits)
    elif use_rag and core.checked_document_reply(question,hits):
        reply = core.checked_document_reply(question,hits)
    elif plan["route"] == "state" and not shown_screen:
        reply = "I can't see the current analysis without an application snapshot."
    elif plan["route"] == "state" and core.read_state_reply(question,shown_screen,history):
        reply = core.read_state_reply(question,shown_screen,history)
    elif plan["route"] == "state" and core.checked_state_suggestions(question,shown_screen):
        reply = core.checked_state_suggestions(question,shown_screen)
    else:
        messages = core.build_messages(question,shown_screen,hits,history,ANALYSES,profile=profile)
        raw, metrics = llm_client.stream_chat(messages,model,max_tokens)
        raw_model_answer = raw
        reply = core.normalize_model_reply(raw)
        if use_rag:
            reply = core.ground_rag_reply(reply,hits,question)
        else:
            reply = core.guard_unsourced_citations(reply)
            if shown_screen and plan["route"] == "state":
                reply = core.guard_state_numbers(reply,question,shown_screen)
    elapsed = time.perf_counter()-started
    result = {"question_id":case["question_id"], "category":case["category"],
              "question":question, "answer":reply, "route":plan["route"],
              "model_requested":model, "model_actual":metrics.get("model"),
              "history":history, "used_app_state":bool(shown_screen),
              "search_query":question if use_rag else None,
              "retrieved_chunks":hits, "retrieval_diagnostics":diagnostic,
              "context_supplied_to_llm":messages,
              "raw_model_answer":raw_model_answer,
              "retrieval_latency_s":retrieval_s,
              "reranking_latency_s":None,  # included in retriever; currently not separately timed
              "llm_latency_s":metrics.get("total_s",0),
              "end_to_end_latency_s":elapsed,
              "ttft_s":metrics.get("first_token_s"),
              "generation_time_s":metrics.get("generation_s"),
              "input_tokens":metrics.get("prompt_eval_count"),
              "output_tokens":metrics.get("tokens"),
              "tokens_per_s":metrics.get("tokens_per_s"),
              "load_s":metrics.get("load_s"), "prompt_s":metrics.get("prompt_s")}
    result["score"] = score(case,reply,hits)
    result["primary_failure"] = failure_label(case,result)
    return result


def failure_label(case, result):
    s = result["score"]
    if s["expected_source_retrieved"] is False:
        return "RETRIEVAL_FAILURE"
    if case.get("expected_behavior_if_unavailable") and not s["unavailable_refusal"]:
        return "HALLUCINATION"
    if s["expected_source_retrieved"] and not s["expected_source_cited"]:
        return "CONTEXT_FAILURE"
    if s["numerical_accuracy"] is not None and s["numerical_accuracy"] < 1:
        return "MODEL_FAILURE" if result["context_supplied_to_llm"] else "CALCULATION_FAILURE"
    if s["required_fact_recall"] is not None and s["required_fact_recall"] < 1:
        return "MODEL_FAILURE" if result["context_supplied_to_llm"] else "PROMPT_FAILURE"
    return None


def percentile(values,p):
    if not values: return None
    values=sorted(values)
    position=(len(values)-1)*p
    lo=int(position); hi=min(lo+1,len(values)-1)
    return values[lo]+(values[hi]-values[lo])*(position-lo)


def summarize(rows):
    ok=[x for x in rows if "score" in x]
    def stats(field):
        vals=[x[field] for x in ok if isinstance(x.get(field),(int,float))]
        return {"n":len(vals),"mean":statistics.mean(vals) if vals else None,
                "median":statistics.median(vals) if vals else None,
                "p90":percentile(vals,.9),"p95":percentile(vals,.95)}
    scores=[x["score"]["rule_score"] for x in ok if x["score"]["rule_score"] is not None]
    return {"questions":len(rows),"successful":len(ok),"rule_score_mean":statistics.mean(scores) if scores else None,
            "end_to_end_latency_s":stats("end_to_end_latency_s"),"ttft_s":stats("ttft_s"),
            "llm_latency_s":stats("llm_latency_s"),
            "failures":{label:sum(x.get("primary_failure")==label for x in ok)
                        for label in sorted({x.get("primary_failure") for x in ok if x.get("primary_failure")})}}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--model",default=llm_client.CHAT_MODEL)
    parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--ids",nargs="*")
    parser.add_argument("--repeat",type=int,default=1)
    parser.add_argument("--diagnostics",action="store_true")
    parser.add_argument("--warmup",action="store_true")
    parser.add_argument("--profile", choices=("engineering", "voice"), default="engineering")
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument("--questions", type=Path, default=ROOT/"questions.json",
                        help="Question set, e.g. evaluation/holdout.json")
    parser.add_argument("--resume",action="store_true",
                        help="Continue an interrupted JSONL run after validating its completed prefix")
    parser.add_argument("--overwrite",action="store_true",
                        help="Explicitly replace an existing result file")
    args=parser.parse_args()
    if args.resume and args.overwrite:
        parser.error("--resume and --overwrite cannot be combined")
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")
    max_tokens = args.max_tokens or (120 if args.profile == "voice" else core.MAX_TOKENS)
    if max_tokens < 1:
        parser.error("--max-tokens must be at least 1")
    cases=json.loads(args.questions.read_text(encoding="utf-8"))
    if args.ids: cases=[x for x in cases if x["question_id"] in args.ids]
    if not cases:
        parser.error("No matching question IDs")
    ref=calculate_reference()
    screen=screen_for_reference(ref)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    meta=model_metadata(args.model,args.profile,max_tokens,args.questions)
    expected_ids=[case["question_id"] for _ in range(args.repeat) for case in cases]
    rows=[]
    if args.output.exists():
        if args.resume:
            try:
                existing_text = args.output.read_text(encoding="utf-8")
                existing_lines = existing_text.splitlines(keepends=True)
                for pos, line in enumerate(existing_lines):
                    if not line.strip():
                        continue
                    try:
                        rows.append(json.loads(line))
                    except ValueError as exc:
                        if pos != len(existing_lines) - 1:
                            raise ValueError(f"Malformed row {pos+1}: {exc}") from exc
                        backup = args.output.with_suffix(args.output.suffix + ".incomplete")
                        backup.write_bytes(args.output.read_bytes())
                        args.output.write_text("".join(existing_lines[:pos]), encoding="utf-8")
                        print(f"Saved incomplete trailing row to {backup}; resuming from last complete row",
                              flush=True)
                if existing_lines and not existing_text.endswith("\n") and len(rows) == len(existing_lines):
                    with args.output.open("a", encoding="utf-8") as checkpoint:
                        checkpoint.write("\n")
                        checkpoint.flush()
                        os.fsync(checkpoint.fileno())
            except (ValueError, UnicodeError) as exc:
                parser.error(f"Existing JSONL is invalid; inspect it before resuming: {exc}")
            if len(rows)>len(expected_ids):
                parser.error("Existing result has more rows than this question selection")
            for pos,row in enumerate(rows):
                if row.get("question_id")!=expected_ids[pos] or row.get("repeat")!=pos//len(cases)+1:
                    parser.error(f"Existing row {pos+1} does not match the requested question order")
                if row.get("question")!=cases[pos % len(cases)]["question"]:
                    parser.error(f"Existing row {pos+1} has different question text; use a new output file")
                old=row.get("configuration",{})
                for key in ("name","digest","profile","embedding_model","retrieval","prompt_sha256",
                            "pipeline_sha256","dataset_sha256","options"):
                    if old.get(key)!=meta.get(key):
                        parser.error(f"Existing row {pos+1} has a different {key}; use a new output file")
            print(f"Resuming {args.output}: {len(rows)}/{len(expected_ids)} rows complete",flush=True)
        elif not args.overwrite:
            parser.error("Output already exists; use --resume or --overwrite")
    elif args.resume:
        print("No previous output found; starting a new checkpointed run",flush=True)
    if len(rows) == len(expected_ids):
        summary = summarize(rows)
        args.output.with_suffix(".summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print("Run already complete; no model calls needed")
        print(json.dumps(summary, indent=2))
        return
    if args.warmup:
        llm_client.warm_up(args.model,core.system_prompt(ANALYSES,profile=args.profile))
    with args.output.open("a" if args.resume else "w",encoding="utf-8") as out:
        for rep in range(args.repeat):
            for idx,case in enumerate(cases,1):
                if (rep*len(cases)+idx-1)<len(rows):
                    continue
                try:
                    row=execute_case(case,args.model,screen,args.diagnostics,ref,
                                     args.profile,max_tokens)
                except Exception as exc:
                    row={"question_id":case["question_id"],"question":case["question"],
                         "error":repr(exc)}
                row.update({"repeat":rep+1,"configuration":meta})
                rows.append(row)
                out.write(json.dumps(row,ensure_ascii=False)+"\n")
                out.flush()
                os.fsync(out.fileno())
                # Each completed question is a durable checkpoint, including errors.
                print(f"{rep+1}/{args.repeat} {idx}/{len(cases)} {case['question_id']} "
                      f"{row.get('primary_failure') or row.get('error') or 'OK'}",flush=True)
    summary=summarize(rows)
    args.output.with_suffix(".summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    print(json.dumps(summary,indent=2))


if __name__=="__main__": main()
