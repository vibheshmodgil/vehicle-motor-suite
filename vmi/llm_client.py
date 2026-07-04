"""Thin HTTP client for a local Ollama server -- chat + embeddings.

No cloud calls: everything talks to http://localhost:11434, Ollama's default
local address. Two local models are expected to already be pulled:
  ollama pull llama3.1:8b        (chat)
  ollama pull nomic-embed-text   (embeddings)
Swap CHAT_MODEL / EMBED_MODEL below to use different local models.
"""

import requests

OLLAMA_URL = "http://localhost:11434"
CHAT_MODEL = "llama3.1:8b"
EMBED_MODEL = "nomic-embed-text"
TIMEOUT_S = 120


class OllamaError(Exception):
    """Raised when the local Ollama server can't be reached or errors out."""


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


def embed(text, model=EMBED_MODEL):
    """Returns a single embedding vector (list[float]) for text."""
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": model, "prompt": text},
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
