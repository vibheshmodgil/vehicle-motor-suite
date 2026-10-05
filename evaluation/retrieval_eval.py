"""Check fixed document-source recall without invoking a chat model."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from vmi import rag_store

ROOT = Path(__file__).resolve().parent


def evaluate():
    questions = json.loads((ROOT / "questions.json").read_text(encoding="utf-8"))
    rows = []
    for case in questions:
        if case["expected_source"] != "document":
            continue
        expected = Path(case["relevant_document_or_chunk"]).name.casefold()
        hits = rag_store.query(case["question"])
        sources = [h["source"] for h in hits]
        ranks = [i + 1 for i, source in enumerate(sources) if expected in source.casefold()]
        rows.append({"question_id": case["question_id"], "question": case["question"],
                     "expected_document": expected, "selected_sources": sources,
                     "expected_rank": ranks[0] if ranks else None})
    n = len(rows)
    return {"questions": n, "recall_at_1": sum(r["expected_rank"] == 1 for r in rows) / n,
            "recall_at_3": sum(r["expected_rank"] is not None for r in rows) / n,
            "rows": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = evaluate()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Document recall@1={result['recall_at_1']:.3f}, recall@3={result['recall_at_3']:.3f}")
    for row in result["rows"]:
        print(row["question_id"], row["expected_rank"], row["selected_sources"][0] if row["selected_sources"] else "NO HIT")
