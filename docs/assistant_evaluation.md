# Assistant evaluation — local models and RAG pipeline

Measured 2026-09-30 on the development PC: Windows 11, NVIDIA GTX 1650
(4 GB), 8 CPU threads, 17 GB RAM, Ollama 0.34.4.

## Method

`tests/assistant_questionnaire.json` has 41 questions in three groups:

| Group | Examples |
|---|---|
| **Casual** (10) | hi, how are you, how r u, what are you doing, how can you help me, who are you, thanks, a joke, the weather |
| **Motor design & control** (16) | torque, PMSM vs BLDC, field-oriented control, MTPA vs field weakening, Kt·I, peak vs RMS current, peak vs continuous torque, max-speed limits, induction vs PM, regen at high SOC, and five questions answered from the knowledge base (mechanical tests, hub vs mid-mount testing, Indian standards, Goodman shaft fatigue, the MTPA document) |
| **This software** (15) | available analyses, how acceleration / top speed / range are computed, blank Crr/CdA, gear-ratio effects, MTPA inputs, battery DC limit, wheel inertia, the U546 curve and efficiency map, and four questions about a live analysis snapshot (top-speed levers, 20 % grade, design suggestions, no snapshot available) |

Each question has automatic checks: facts that must appear (regex
alternatives), content that must not appear, a word budget, and — for
document questions — the file retrieval should return. `tools/questionnaire.py`
sends each question through the same routing as the chat panel (instant local
reply → document search → live app state when relevant → model) and records
load time, retrieval time, time to first word, total time, words/s, accuracy
(fraction of required facts present; 0 if forbidden content appears) and style
flags (too long, LaTeX, tables, code names, refusals). Automatic scores are a
first filter; the answers were also read by hand.

## Results (after the fixes below)

| Model | Size | GPU placement | Accuracy | Casual | Motor | Software | Median time | Slowest 10 % | Words/s* | Cold load |
|---|---|---|---|---|---|---|---|---|---|---|
| **qwen3:4b-instruct** ✅ | 2.5 GB | 100 % GPU | **0.95** | 0.97 | **0.90** | **1.00** | 8.1 s | 28.8 s | **34.5** | 14.6 s |
| gemma3:4b | 3.3 GB | 46 % GPU | 0.80 | 0.97 | 0.62 | 0.87 | 18.5 s | 35.8 s | 9.9 | 25.1 s |
| llama3.2:3b | 2.0 GB | 80 % GPU | 0.77 | 0.97 | 0.63 | 0.79 | 6.8 s | 20.9 s | 27.1 | 15.4 s |
| phi4-mini | 2.5 GB | 64 % GPU | 0.76 | 0.97 | 0.62 | 0.78 | 7.7 s | 39.5 s | 13.1 | 21.8 s |

\* tokens/s from Ollama. qwen3-vl:4b-instruct, qwen3-vl:2b-instruct and
llama3.1:8b were still running when this was written; qwen3-vl:4b looks
similar to qwen3:4b on accuracy so far but runs half on the CPU (~8 tokens/s),
and llama3.1:8b (4.9 GB) cannot fit a 4 GB GPU.

**Choice: `qwen3:4b-instruct` stays the default.** It is the most accurate
in every group and, being the only candidate that fits entirely on a 4 GB GPU,
also the fastest. Its remaining misses are minor omissions (e.g. naming
Clarke/PWM in the FOC answer, "negative d-axis current" in MTPA). The other
models' misses were real errors, e.g. saying wheel inertia changes top speed,
failing the "is my motor enough for 20 %" check against the app's 19.4 %
result, or confusing BLDC and PMSM back-EMF shapes.

**RAG:** the expected document was retrieved for 28 of 28 document questions;
median retrieval 0.09 s (max 0.9 s, first search after start).

## What the evaluation found and what was changed

| Problem (measured) | Cause | Fix |
|---|---|---|
| Every question waited ~14 s before the first word (6.5 s reload + 7.5 s prompt) | The document search loaded the embedding model onto the 4 GB GPU and evicted the chat model, losing its cached system prompt | Question embeddings run on the CPU (0.05 s); the chat model stays on the GPU. Rebuilding the index still uses the GPU |
| First question after opening the panel was slow | Model load + system prompt processing | The model is pre-warmed with the system prompt when the panel opens or the model is changed |
| A per-question system prompt defeated prompt caching | Checked calculations were appended to the system prompt | They now go in the user turn; the system prompt is identical for every question |
| "how r u", "who are you", "good morning", "thanks, that was helpful" took ~20 s and came back with emojis and 40–110 words | Only exact phrases were handled locally; punctuation inside a message broke matching | Broader instant replies for common casual turns; prompt forbids emojis and lists in casual replies |
| Every retrieved excerpt lost its last ~30 % | Excerpts cut at 1500 characters, chunks are ~2100 | Limit raised to 2600 |
| "Peak efficiency of U546" answered 89.14 % at 45 rpm (real: 89.66 % at 500 rpm); torque curve described as "constant power 0–220 rpm" | Truncated excerpt + small models misread raw CSV | Excel files are indexed with plain "key facts" (where each column peaks, and the map's maximum cell with its location) |
| Generic questions ("what is torque") were given the U546 datasheet | Any file whose name shared a word with the question ("torque", "speed", "mechanical") was attached | Only product-code words containing digits (e.g. `U546`) select a file by name |
| Invented citation marks like `[35†L424-L432]` | Prompt | Prompt: cite only the exact [names] given |
| LaTeX (`$$…$$`) shown as raw symbols in the chat | Models ignore "no LaTeX" sometimes | Prompt tightened, and the chat renderer converts `$…$`, `\frac`, `\left(` etc. to plain text |

Before → after for qwen3:4b-instruct on the same questions:

| | Accuracy | Style OK | Casual median | Overall median | Slowest 10 % |
|---|---|---|---|---|---|
| Before | 0.89 | 88 % | 21.3 s | 28.9 s | 45.5 s |
| After | **0.95** | **95 %** | **0.0 s** | **8.1 s** | **28.8 s** |

## Known limits

- Questions that retrieve three document excerpts still take 20–30 s on a
  GTX 1650: prompt processing runs at ~100 tokens/s on this GPU, so ~1700
  excerpt tokens cost ~15 s before the first word. Generation itself is fast.
- Scores are regex-based; a correct answer phrased differently can lose
  points (e.g. the "how can you help me" check wanted the word "document").
