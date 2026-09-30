"""Thin HTTP client for a local Ollama server -- chat + embeddings.

No cloud calls: everything talks to http://localhost:11434, Ollama's default
local address. Two local models are expected to already be pulled:
  ollama pull qwen3:4b-instruct  (chat)
  ollama pull nomic-embed-text   (embeddings)
Swap CHAT_MODEL / EMBED_MODEL below to use different local models.
"""

import requests
import json
import time

OLLAMA_URL = "http://localhost:11434"
CHAT_MODEL = "qwen3:4b-instruct"
# 4096 keeps a 4B Q4 model + KV cache inside a 4 GB GPU (GTX 1650); 8192
# spilled half the layers to CPU and made every answer take 1-2 minutes.
NUM_CTX = 4096
KEEP_ALIVE = "30m"
EMBED_MODEL = "nomic-embed-text"
# A cold model load is ~5 s on the GPU but can exceed 120 s for models that
# spill to CPU; keep the generous limit.
TIMEOUT_S = 300


class OllamaError(Exception):
    """Raised when the local Ollama server can't be reached or errors out."""


def list_models():
    try:
        response = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        response.raise_for_status()
        return [m["name"] for m in response.json()["models"]
                if "embedding" not in m.get("capabilities", []) and "embed" not in m["name"]]
    except (requests.RequestException, ValueError, KeyError) as exc:
        raise OllamaError(f"Cannot list local models: {exc}") from exc


def stream_chat(messages, model, max_tokens=1200, on_chunk=None):
    streamed = []

    def relay(chunk):
        streamed.append(chunk)
        if on_chunk:
            on_chunk(chunk)
    try:
        return _stream_chat(messages, model, max_tokens, relay, force_gpu=model == CHAT_MODEL)
    except OllamaError:
        # Forcing every layer onto the GPU fails on smaller/no GPUs (e.g. an
        # office laptop); retry once with Ollama's own CPU/GPU placement --
        # unless text was already shown, which a retry would duplicate.
        if model != CHAT_MODEL or streamed:
            raise
        return _stream_chat(messages, model, max_tokens, on_chunk, force_gpu=False)


def _stream_chat(messages, model, max_tokens, on_chunk, force_gpu):
    started = time.perf_counter()
    first = None
    parts = []
    final = None
    options = {"temperature": 0.2, "num_predict": max_tokens, "num_ctx": NUM_CTX}
    if force_gpu:
        # ponytail: Ollama's VRAM estimate is conservative and put only 2.2 of
        # 3.0 GB on the GPU (10 tok/s); all layers fit and give ~26 tok/s.
        # Only forced for the default model -- a larger one would run out of VRAM.
        options["num_gpu"] = 99
    try:
        with requests.post(f"{OLLAMA_URL}/api/chat", json={
            "model": model, "messages": messages, "stream": True,
            # think=False: thinking variants (e.g. qwen3-vl:2b) otherwise put the
            # whole answer in a hidden "thinking" field and return empty content.
            # keep_alive: stay loaded between questions (reload costs seconds).
            "think": False, "keep_alive": KEEP_ALIVE,
            "options": options,
        }, stream=True, timeout=(5, TIMEOUT_S)) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if time.perf_counter() - started > TIMEOUT_S:
                    raise OllamaError("Response exceeded the time limit. Try a smaller model.")
                if not line:
                    continue
                event = json.loads(line)
                if event.get("error"):
                    raise OllamaError(str(event["error"]))
                chunk = event.get("message", {}).get("content", "")
                if chunk:
                    if first is None:
                        first = time.perf_counter() - started
                    parts.append(chunk)
                    if on_chunk:
                        on_chunk(chunk)
                if event.get("done"):
                    final = event
                    break
        if final is None or not parts:
            raise OllamaError("Model returned an incomplete or empty response.")
        metrics = {"model": model, "first_token_s": first,
                   "total_s": time.perf_counter() - started,
                   "tokens": final.get("eval_count", 0),
                   "load_s": final.get("load_duration", 0) / 1e9,
                   "prompt_s": final.get("prompt_eval_duration", 0) / 1e9,
                   "generation_s": final.get("eval_duration", 0) / 1e9,
                   "truncated": final.get("done_reason") == "length"}
        seconds = final.get("eval_duration", 0) / 1e9
        metrics["tokens_per_s"] = metrics["tokens"] / seconds if seconds else None
        return "".join(parts), metrics
    except (requests.RequestException, ValueError, KeyError) as exc:
        raise OllamaError(f"Ollama request failed for {model}: {exc}") from exc


def chat(messages, model=CHAT_MODEL):
    """messages: list of {"role": "system"|"user"|"assistant", "content": str}.
    Returns the assistant's reply text."""
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={"model": model, "messages": messages, "stream": False},
            timeout=TIMEOUT_S,
        )
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        raise OllamaError(
            "Can't reach Ollama at localhost:11434. Is it installed and running?"
        )
    except requests.exceptions.RequestException as e:
        raise OllamaError(f"Ollama chat request failed: {e}")
    return resp.json()["message"]["content"]


def warm_up(model, system_prompt):
    """Load the chat model and cache the system prompt's KV state, so the first
    real question starts answering in well under a second instead of ~15 s."""
    stream_chat([{"role": "system", "content": system_prompt},
                 {"role": "user", "content": "Reply with OK."}], model, 1)


def embed(text, model=EMBED_MODEL, gpu=False):
    """Returns a single embedding vector (list[float]) for text.
    gpu=False (questions): on a 4 GB GPU the embedding model evicted the chat
    model, so every question paid a ~7 s reload + ~8 s prompt re-evaluation;
    on the CPU a query embeds in ~0.05 s and the chat model stays put.
    gpu=True (index rebuilds): 300-word chunks are ~1.6 s each on the CPU."""
    options = {} if gpu else {"num_gpu": 0}
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": model, "prompt": text, "keep_alive": KEEP_ALIVE, "options": options},
            timeout=TIMEOUT_S,
        )
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        raise OllamaError(
            "Can't reach Ollama at localhost:11434. Is it installed and running?"
        )
    except requests.exceptions.RequestException as e:
        raise OllamaError(f"Ollama embeddings request failed: {e}")
    return resp.json()["embedding"]
