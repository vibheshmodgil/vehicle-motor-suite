"""Local RAG knowledge base: file ingestion + similarity search over Chroma.

Everything the assistant can retrieve lives under two kinds of sources:
  - `knowledge_base/` (standards/, datasheets/, products/, scenarios/) -- the
    user's own drop folder. Add a file, click "Rebuild Knowledge Base"; delete
    a file, rebuild again -- it disappears from the index. That add/rebuild/
    delete/rebuild cycle is the entire maintenance workflow.
  - A small fixed set of files the app already produces: CLAUDE.md (so the
    assistant can answer "how does this analysis work"), std_motor_data_sample.json
    (the standard-motor library) and the sample scenario JSONs.

No embeddings/text ever leaves the machine -- chunking + embedding + storage
are all local (embeddings via Ollama through llm_client, storage via Chroma's
on-disk PersistentClient).
"""

import json
import os

import chromadb

from . import llm_client

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB_ROOT = os.path.join(PROJECT_ROOT, "knowledge_base")
INDEX_DIR = os.path.join(KB_ROOT, ".index")
MANIFEST_PATH = os.path.join(INDEX_DIR, "manifest.json")
COLLECTION_NAME = "vmi_knowledge"

CHUNK_WORDS = 300
CHUNK_OVERLAP = 50

EXTRA_FILES = [
    os.path.join(PROJECT_ROOT, "CLAUDE.md"),
    os.path.join(PROJECT_ROOT, "std_motor_data_sample.json"),
]
EXTRA_GLOBS = [
    os.path.join(PROJECT_ROOT, "sample_data", "vmi_scenario*.json"),
]


def _iter_index_files():
    """Yield absolute paths of every file that should be in the index."""
    seen = set()
    for root, _dirs, files in os.walk(KB_ROOT):
        if os.path.abspath(root).startswith(os.path.abspath(INDEX_DIR)):
            continue
        for fname in files:
            path = os.path.join(root, fname)
            if path not in seen:
                seen.add(path)
                yield path
    for path in EXTRA_FILES:
        if os.path.isfile(path) and path not in seen:
            seen.add(path)
            yield path
    import glob
    for pattern in EXTRA_GLOBS:
        for path in glob.glob(pattern):
            path = os.path.abspath(path)
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
        return "\n".join(p.text for p in doc.paragraphs)
    if ext in (".xlsx", ".xls"):
        import pandas as pd
        sheets = pd.read_excel(path, sheet_name=None)
        return "\n\n".join(
            f"[sheet: {name}]\n{sheet.to_string(index=False)}"
            for name, sheet in sheets.items()
        )
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


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


def _get_collection():
    os.makedirs(INDEX_DIR, exist_ok=True)
    client = chromadb.PersistentClient(path=INDEX_DIR)
    return client.get_or_create_collection(COLLECTION_NAME)


def rebuild_index(progress=None):
    """Diff the knowledge-base folder + fixed extra files against the last
    run, re-embedding only what's new/changed and dropping what's gone.

    progress: optional callable(str) for status updates (called from
    whatever thread rebuild_index runs on -- caller must marshal to the UI
    thread itself).
    Returns (n_files_indexed, n_chunks, warnings: list[str]).
    """
    collection = _get_collection()
    manifest = _load_manifest()
    current_paths = set()
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

        if prev is not None:
            old_ids = [f"{path}::{i}" for i in range(prev.get("n_chunks", 0))]
            if old_ids:
                try:
                    collection.delete(ids=old_ids)
                except Exception:
                    pass

        try:
            text = _extract_text(path)
        except Exception as e:
            warnings.append(f"{os.path.relpath(path, PROJECT_ROOT)}: {e}")
            manifest.pop(path, None)
            continue

        chunks = _chunk_text(text)
        if chunks:
            ids = [f"{path}::{i}" for i in range(len(chunks))]
            embeddings = [llm_client.embed(c) for c in chunks]
            metadatas = [{"source": os.path.relpath(path, PROJECT_ROOT)} for _ in chunks]
            collection.upsert(ids=ids, embeddings=embeddings, documents=chunks, metadatas=metadatas)

        manifest[path] = {"mtime": mtime, "n_chunks": len(chunks)}
        n_files += 1
        n_chunks += len(chunks)

    # Anything in the old manifest that no longer exists on disk: drop its chunks.
    for path in list(manifest.keys()):
        if path not in current_paths:
            old_ids = [f"{path}::{i}" for i in range(manifest[path].get("n_chunks", 0))]
            if old_ids:
                try:
                    collection.delete(ids=old_ids)
                except Exception:
                    pass
            del manifest[path]

    _save_manifest(manifest)
    return n_files, n_chunks, warnings


def query(question, top_k=5):
    """Returns a list of {"text": str, "source": str} for the closest chunks."""
    collection = _get_collection()
    if collection.count() == 0:
        return []
    q_embedding = llm_client.embed(question)
    result = collection.query(query_embeddings=[q_embedding], n_results=min(top_k, collection.count()))
    hits = []
    docs = result.get("documents", [[]])[0]
    metas = result.get("metadatas", [[]])[0]
    for doc, meta in zip(docs, metas):
        hits.append({"text": doc, "source": (meta or {}).get("source", "unknown")})
    return hits
