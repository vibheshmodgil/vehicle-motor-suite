"""Read-only raw Chroma evidence trace alongside the production retriever."""
from __future__ import annotations

import time

from vmi import llm_client, rag_store


def diagnose(question, selected, top_k=12):
    started = time.perf_counter()
    collection = rag_store._get_collection()
    count = collection.count()
    if not count:
        return {"search_query": question, "raw_chunks": [], "reranked_chunks": selected,
                "diagnostic_latency_s": time.perf_counter()-started}
    emb = llm_client.embed("search_query: " + question)
    raw = collection.query(query_embeddings=[emb], n_results=min(top_k,count),
                           include=["documents", "metadatas", "distances"])
    chunks = []
    for doc_id, doc, meta, dist in zip(raw["ids"][0], raw["documents"][0],
                                       raw["metadatas"][0], raw["distances"][0]):
        chunks.append({"id": doc_id, "source": rag_store._source_label(meta,doc_id),
                       "distance": float(dist), "text": doc})
    by_source = {c["source"]: c["distance"] for c in chunks}
    return {"search_query": question, "raw_chunks": chunks,
            "reranked_chunks": [{**h, "raw_distance": by_source.get(h["source"])} for h in selected],
            "diagnostic_latency_s": time.perf_counter()-started}
