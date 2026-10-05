"""Local RAG knowledge base: file ingestion + similarity search over Chroma.

Everything the assistant can retrieve lives under `knowledge_base/`
(standards/, datasheets/, products/, scenarios/) -- the user's own drop folder.
Add a file, click "Rebuild Knowledge Base"; delete a file, rebuild again -- it
disappears from the index. "How does the app work" questions are answered from
assistant_core.APP_GUIDE instead of indexing developer notes (CLAUDE.md made
the model answer with class names).

No embeddings/text ever leaves the machine -- chunking + embedding + storage
are all local (embeddings via Ollama through llm_client, storage via Chroma's
on-disk PersistentClient).
"""

import hashlib
import json
import os
import re

import chromadb

from . import llm_client

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB_ROOT = os.path.join(PROJECT_ROOT, "knowledge_base")
INDEX_DIR = os.path.join(KB_ROOT, ".index")
MANIFEST_PATH = os.path.join(INDEX_DIR, "manifest.json")
COLLECTION_NAME = "vmi_knowledge"

CHUNK_WORDS = 300
CHUNK_OVERLAP = 50
# Bump when chunking/embedding changes: the next rebuild re-indexes everything.
INDEX_VERSION = 4
# Cosine distance above which a chunk is treated as unrelated to the question.
# ponytail: fixed cutoff measured on this KB (relevant 0.20-0.33, unrelated 0.334+);
# re-measure if the embedding model changes or relevant chunks go missing.
MAX_DISTANCE = 0.33


def _iter_index_files():
    """Yield absolute paths of every file that should be in the index."""
    seen = set()
    for root, _dirs, files in os.walk(KB_ROOT):
        if os.path.abspath(root).startswith(os.path.abspath(INDEX_DIR)):
            continue
        for fname in files:
            if os.path.splitext(fname)[1].lower() not in {".pdf", ".docx", ".xlsx", ".xls", ".txt", ".md", ".csv", ".json"}:
                continue
            path = os.path.join(root, fname)
            if path not in seen:
                seen.add(path)
                yield path


def _extract_text(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(path)
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if ext == ".docx":
        import docx
        doc = docx.Document(path)
        paragraphs = [p.text for p in doc.paragraphs]
        tables = [" | ".join(cell.text for cell in row.cells)
                  for table in doc.tables for row in table.rows]
        return "\n".join(paragraphs + tables)
    if ext in (".xlsx", ".xls"):
        import pandas as pd
        sheets = pd.read_excel(path, sheet_name=None)
        parts = []
        for name, sheet in sheets.items():
            ranges = ", ".join(f"{col}: {sheet[col].min():.4g} to {sheet[col].max():.4g}"
                               for col in sheet.columns if pd.api.types.is_numeric_dtype(sheet[col])
                               and sheet[col].notna().any())
            parts.append(f"Table {os.path.basename(path)} sheet {name}, {len(sheet)} rows. "
                         f"Column ranges: {ranges}\n{_table_facts(sheet)}"
                         f"{sheet.to_csv(index=False, float_format='%.4g')}")
        return "\n\n".join(parts)
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def _table_facts(sheet):
    """Plain-sentence peaks, because small models misread raw CSV (e.g. called a
    flat-torque region "constant power"). Curve tables: where each column peaks,
    against the first column. Map tables (numeric headers): the largest cell."""
    import pandas as pd
    numeric = [c for c in sheet.columns if pd.api.types.is_numeric_dtype(sheet[c]) and sheet[c].notna().any()]
    if len(numeric) < 2:
        return ""
    key, facts = numeric[0], []
    if all(isinstance(c, (int, float)) for c in numeric[1:]):
        body = sheet[numeric[1:]]
        row, col = body.stack().idxmax()
        facts.append(f"Largest value in the map: {body.loc[row, col]:.4g}, in column header {col} "
                     f"on the row whose first-column value is {sheet.loc[row, key]:.4g}.")
    else:
        for col in numeric[1:]:
            peak = sheet[col].max()
            at = sheet.loc[sheet[col] >= peak - 1e-6 * abs(peak), key]
            where = f"{at.min():.4g}" if at.min() == at.max() else f"{at.min():.4g} to {at.max():.4g}"
            facts.append(f"{col} peaks at {peak:.4g} for {key} {where}.")
    return "Key facts: " + " ".join(facts) + "\n"


def _chunk_text(text, chunk_words=CHUNK_WORDS, overlap=CHUNK_OVERLAP):
    words = text.split()
    if not words:
        return []
    step = max(chunk_words - overlap, 1)
    return [
        " ".join(words[i:i + chunk_words])
        for i in range(0, len(words), step)
        if words[i:i + chunk_words]
    ]


def _document_chunks(path, text):
    """Return (chunk, page) pairs; PDFs retain their source page metadata."""
    if os.path.splitext(path)[1].lower() != ".pdf":
        return [(chunk, None) for chunk in _chunk_text(text)]
    from pypdf import PdfReader
    chunks = []
    for page_number, page in enumerate(PdfReader(path).pages, 1):
        chunks.extend((chunk, page_number) for chunk in _chunk_text(page.extract_text() or ""))
    return chunks


def _load_manifest():
    if os.path.isfile(MANIFEST_PATH):
        try:
            with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_manifest(manifest):
    os.makedirs(INDEX_DIR, exist_ok=True)
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)


def _get_collection(reset=False):
    os.makedirs(INDEX_DIR, exist_ok=True)
    client = chromadb.PersistentClient(path=INDEX_DIR)
    if reset:
        try:
            client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass
    return client.get_or_create_collection(COLLECTION_NAME, metadata={"hnsw:space": "cosine"})


def _embed_document(text):
    # nomic-embed-text is trained with these task prefixes; without them
    # questions and chunks land in different regions of the space.
    return llm_client.embed("search_document: " + text, gpu=True)


def rebuild_index(progress=None):
    """Diff the knowledge-base folder + fixed extra files against the last
    run, re-embedding only what's new/changed and dropping what's gone.

    progress: optional callable(str) for status updates (called from
    whatever thread rebuild_index runs on -- caller must marshal to the UI
    thread itself).
    Returns (n_files_indexed, n_chunks, warnings: list[str]).
    """
    manifest = _load_manifest()
    fresh = manifest.get("__version__") != INDEX_VERSION
    collection = _get_collection(reset=fresh)
    if fresh:
        manifest = {"__version__": INDEX_VERSION}
    current_paths = set()
    seen_text = {}
    warnings = []
    n_files, n_chunks = 0, 0

    for path in _iter_index_files():
        current_paths.add(path)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        prev = manifest.get(path)
        if prev is not None and prev.get("mtime") == mtime:
            continue  # unchanged since last rebuild

        if progress:
            progress(f"Indexing {os.path.relpath(path, PROJECT_ROOT)}...")

        try:
            text = _extract_text(path)
            digest = hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()
            if digest in seen_text or any(isinstance(v, dict) and v.get("digest") == digest
                                          for k, v in manifest.items() if k != path and os.path.isfile(k)):
                # A changed file can become a duplicate of another file. Its
                # old indexed chunks must be removed or stale claims survive.
                if prev:
                    old_ids = [f"{path}::{i}" for i in range(prev.get("n_chunks", 0))]
                    if old_ids:
                        collection.delete(ids=old_ids)
                    manifest.pop(path, None)
                continue  # identical copy of a file already indexed
            seen_text[digest] = path
            pairs = _document_chunks(path, text)
            chunks = [chunk for chunk, _page in pairs]
            embeddings = [_embed_document(f"{os.path.basename(path)}: {c}") for c in chunks]
        except Exception as e:
            warnings.append(f"{os.path.relpath(path, PROJECT_ROOT)}: {e}")
            continue

        if chunks:
            ids = [f"{path}::{i}" for i in range(len(chunks))]
            metadatas = [{"source": os.path.relpath(path, PROJECT_ROOT), "chunk": i + 1,
                          **({"page": page} if page is not None else {})}
                         for i, (_chunk, page) in enumerate(pairs)]
            collection.upsert(ids=ids, embeddings=embeddings, documents=chunks, metadatas=metadatas)
        if prev and prev.get("n_chunks", 0) > len(chunks):
            collection.delete(ids=[f"{path}::{i}" for i in range(len(chunks), prev["n_chunks"])])

        manifest[path] = {"mtime": mtime, "n_chunks": len(chunks), "digest": digest}
        n_files += 1
        n_chunks += len(chunks)

    # Anything in the old manifest that no longer exists on disk: drop its chunks.
    for path in list(manifest.keys()):
        if path != "__version__" and path not in current_paths:
            old_ids = [f"{path}::{i}" for i in range(manifest[path].get("n_chunks", 0))]
            if old_ids:
                try:
                    collection.delete(ids=old_ids)
                except Exception:
                    pass
            del manifest[path]

    _save_manifest(manifest)
    return n_files, n_chunks, warnings


def _source_label(meta, doc_id):
    meta = meta or {}
    chunk = meta.get("chunk")
    if chunk is None:
        try:
            chunk = int(doc_id.rsplit("::", 1)[1]) + 1
        except (ValueError, IndexError):
            pass
    source = str(meta.get("source", "unknown")).replace("\\", "/")
    page = meta.get("page")
    return source + (f"#page-{page}" if page is not None else "") + (f"#chunk-{chunk}" if chunk is not None else "")


def query(question, top_k=3, max_distance=MAX_DISTANCE):
    """Returns up to top_k {"text", "source"} chunks relevant to the question:
    chunks of any file whose name is mentioned (e.g. "U546"), then the nearest
    chunks within max_distance. Empty list when nothing is relevant."""
    manifest = _load_manifest()
    # A request for a named TSI must not be answered from a vaguely similar
    # motor-testing note. The actual named document has to be indexed.
    if re.search(r"\bTSI\b", question, re.I) and not any(
            "tsi" in os.path.basename(path).lower() for path in manifest if path != "__version__"):
        return []
    collection = _get_collection()
    count = collection.count()
    if count == 0:
        return []
    hits, seen = [], set()
    named_code = {w for w in re.findall(r"[a-z0-9]{3,}", question.lower())
                  if re.search(r"[a-z]", w) and re.search(r"\d", w)}
    # Questions naming the testing catalogue or its hub/mid-mount sections
    # should search that file, even when a short generic question embeds near
    # an unrelated motor-design formula. Restricting to the named source also
    # keeps an unrelated excerpt from becoming the citation fallback.
    testing_request = bool(re.search(
        r"\b(?:testing document|motor-testing document|test catalogue|testing plan|"
        r"listed (?:india )?reference|hub-motor-specific|mid-mount motor|"
        r"hub motor)\b", question, re.I))
    preferred = set()
    for path in manifest:
        if path == "__version__":
            continue
        stem = os.path.splitext(os.path.basename(path))[0].lower()
        if named_code & set(re.findall(r"[a-z0-9]{3,}", stem)):
            preferred.add(os.path.relpath(path, PROJECT_ROOT))
        if testing_request and "motor_testing" in stem and "india" in stem:
            preferred.add(os.path.relpath(path, PROJECT_ROOT))
    terms = set(re.findall(r"[a-z0-9]{4,}", question.lower())) - {
        "what", "which", "does", "about", "this", "that", "from", "with", "find",
        "give", "show", "tell", "source", "document", "requirement", "motor"}
    phrase_words = [w for w in re.findall(r"[a-z0-9]+", question.lower())
                    if w not in {"the", "a", "an", "in", "on", "for", "with", "and", "or",
                                 "what", "which", "how", "does", "is", "are", "of", "to",
                                 "motor", "testing", "document", "tested", "test"}]
    phrases = {phrase_words[i] + " " + phrase_words[j]
               for i in range(len(phrase_words))
               for j in range(i + 1, min(i + 4, len(phrase_words)))
               if len(phrase_words[i]) >= 3 and len(phrase_words[j]) >= 3}
    section = re.search(r"\bsection\s+(\d+(?:\.\d+)?)\b", question, re.I)
    if "goodman" in terms:
        terms.update({"alternating", "mean", "stress", "safety", "factor", "formula"})
    # Only product-code-like words (letters + digits, e.g. "u546") pick a file by
    # name; plain words like "torque" or "mechanical" matched unrelated files.
    words = named_code
    for path, entry in manifest.items():
        if path == "__version__" or not isinstance(entry, dict):
            continue
        stem_words = set(re.findall(r"[a-z0-9]{3,}", os.path.splitext(os.path.basename(path))[0].lower()))
        if stem_words & words:
            ids = [f"{path}::{i}" for i in range(min(entry.get("n_chunks", 0), 2))]
            got = collection.get(ids=ids) if ids else {"ids": []}
            for doc_id, doc, meta in zip(got["ids"], got["documents"], got["metadatas"]):
                seen.add(doc_id)
                hits.append({"text": doc, "source": _source_label(meta, doc_id), "_distance": 0.5})
    if preferred:
        for source in preferred:
            try:
                got = collection.get(where={"source": source}, include=["documents", "metadatas"])
            except TypeError:
                # Minimal collection fakes used by offline route tests expose
                # only ID lookup; the named-file shortcut above still works.
                continue
            for doc_id, doc, meta in zip(got["ids"], got["documents"], got["metadatas"]):
                if doc_id not in seen:
                    seen.add(doc_id)
                    hits.append({"text": doc, "source": _source_label(meta, doc_id), "_distance": 0.49})
    semantic_question = (question + " alternating mean stress safety factor formula"
                         if re.search(r"\bgoodman\b", question, re.I) else question)
    result = collection.query(query_embeddings=[llm_client.embed("search_query: " + semantic_question)],
                              n_results=min(max(top_k * 4, 12), count),
                              include=["documents", "metadatas", "distances"])
    for doc_id, doc, meta, dist in zip(result["ids"][0], result["documents"][0],
                                       result["metadatas"][0], result["distances"][0]):
        if dist <= max_distance and doc_id not in seen and (not preferred or meta.get("source") in preferred):
            hits.append({"text": doc, "source": _source_label(meta, doc_id), "_distance": dist})
    def rank(hit):
        # A high-dimensional match can favor a generic formula handbook over
        # the actual test catalogue. Rerank candidates by distinctive query
        # terms while retaining vector distance as the tie-breaker.
        haystack = (hit["source"] + " " + hit["text"][:1500]).lower()
        overlap = sum(term in haystack for term in terms)
        body = hit["text"][:1800].lower().replace("-", " ")
        overlap += 3 * sum(phrase.replace("-", " ") in body for phrase in phrases)
        overlap += sum(min(body.count(term), 3) * 0.5 for term in terms)
        if "inputs" in terms and "measurements" in terms and "inputs" in body and "measurements" in body:
            overlap += 8
        if section and section.group(1) in body:
            overlap += 8
        if preferred and any(hit["source"].replace("\\", "/").startswith(p.replace("\\", "/")) for p in preferred):
            overlap += 2
        source_lower = hit["source"].lower()
        if "efficiency" in terms and "_eff_" in source_lower:
            overlap += 3
        if {"torque", "speed"} <= terms and "_torque_speed_" in source_lower:
            overlap += 3
        standards_bonus = (0.5 if re.search(r"\b(?:test|testing|standard|approval|procedure)\b", question, re.I)
                           and "/standards/" in hit["source"].replace("\\", "/").lower() else 0)
        return (-(overlap + standards_bonus), hit["_distance"])
    hits.sort(key=rank)
    return [{"text": h["text"], "source": h["source"]} for h in hits[:top_k]]
