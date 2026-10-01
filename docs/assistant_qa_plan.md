# Assistant architecture map and QA plan

## Observed architecture (before stabilization)

`TorqueSpeedApp` combines the simulation mixins and `AssistantMixin` in one Tk
process. The sidebar captures a text snapshot on the Tk thread, starts a worker,
and polls a queue to render streamed Ollama text. There is one chat model,
`qwen3:4b-instruct` by default, selectable in the UI. `nomic-embed-text` only
embeds documents and queries. There are no voice, speech recognition, speaker,
vision, screenshot, agent, function-calling, or assistant-controlled plotting
components in this repository. Any installed chat model gets the same system
prompt; there is no separate engineering, RAG, screen, or summarization model.

The assistant's actual callable operations are: `small_talk_reply(str)->str`,
the checked shaft-power, drivetrain, grade, and range helpers (`str->str`),
`wants_screen_context(str, history)->bool`, `_screen_snapshot()->dict`,
`rag_store.query(str)->list[{source,text}]`, `build_messages(...)->list[role,
content]`, and `llm_client.stream_chat(...)->(text,metrics)`. Only the snapshot
reads app widgets, report observations, and sampled Matplotlib line data. RAG
reads local files through a Chroma index and Ollama embeddings. All are
read-only for the assistant. The rebuild button modifies the index, not the
simulation. App plotting and engineering methods are called by UI controls,
not by assistant tool calls. There is no assistant tool schema or tool output
message protocol. Tk calls stay on the UI thread; retrieval/model work is in
workers.

The snapshot includes at most 40 *visible* entry values, current analysis,
some displayed labels and report-derived observations, and up to five lines
on each of three axes with six samples per line. It omits hidden inputs, most
combo/switch selections, map grids, drive-cycle rows, contour/image data, and
screen pixels. It is a compact summary, not raw or complete structured state.
The in-memory conversation is one list per app instance, truncated to six
messages; only the current request gets a fresh snapshot. A chat log persists
to JSONL but is not reloaded as memory.

`rag_store` chunks extracted PDF, Word, spreadsheet, text, Markdown, CSV and
JSON content by words. Chroma stores source path and chunk number. Query uses
an embedding cosine threshold plus filename matches for codes with digits.
The response prompt can cite `path#chunk-N`; PDF page, heading, clause and
revision metadata are not indexed. There is no citation verifier. Retrieval
runs for every nonlocal message, including many casual turns. A read or Ollama
failure can become a visible meta/error row. Raw streamed content is rendered
as it arrives without validation of role, tool payload, or internal text.

## Root risks to test first

1. An action request (change a parameter, calculate from current state, make a
   plot) can receive persuasive prose although no action interface exists.
2. Text from a missing or unrelated RAG hit can be answered as a sourced
   standard; citations are not mechanically verified.
3. Visible-entry-only snapshots can omit a requested parameter while the
   model guesses it. A plot may be stale after entry edits.
4. Retrieval for casual messages adds latency and can contaminate the answer.
5. Streaming can expose model-generated internal or structured payloads.
6. A model choice can exceed the fixed 4096-token Ollama context; messages
   are clipped by characters without a token budget.

## Test plan and evidence rules

Run the existing pytest suite as a baseline. Add machine-readable cases that
exercise the actual router, context assembly, state/action honesty, checked
arithmetic, retrieval decisions, and output handling without requiring a live
model. Keep live Ollama and GUI checks separate and report them as unverified
when those dependencies are unavailable. Use numerical tolerances for physics
and semantic properties for prose. Record route, model, tool operations,
latency, output and failure category for each case. Count a capability that
does not exist as unsupported, not as a passing calculation or plot.

After root-cause fixes, rerun the same corpus and all existing tests. Manual
checks must cover GUI rendering, real loaded maps, plot axes, microphone and
speaker only if those capabilities are later added to the repository.
