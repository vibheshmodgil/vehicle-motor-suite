# Powertrain and Acceleration assistant evaluation

This is a reproducible first benchmark of the implemented **Powertrain Sizing** and **Acceleration** paths, shared motor/controller inputs, and local test/reference files. Start with the [architecture snapshot](../docs/powertrain_acceleration_architecture.md), [capability inventory](capability_map.json), and [handoff/work log](HANDOFF.md).

## Dataset and oracle

`questions.json` has 90 fixed questions across direct results, engineering explanations, motor/controller behavior, cross-analysis comparisons, document retrieval, unavailable information, ambiguous queries and conversation. `ground_truth.json` includes the numerical reference fixture. `build_dataset.py` constructs values from the application's unmodified calculation methods through `reference_case.py`; document expectations come from the files in `knowledge_base/`. Both builders are deterministic. Rebuilding the dataset changes its hash and requires a new benchmark output file.

The fixed case uses a 250 kg reference mass, 0.28 m wheel radius, 8:1 gearing at 0.95 efficiency, 30 N m peak torque, 4.4 kW peak power, 0.018 Crr and 0.6 m² CdA. No motor curve, efficiency map, or battery DC cap is loaded. The report and plotted acceleration estimators use different time steps and are checked separately.

## Run and resume

Run from the repository root with `.venv` and the local Ollama service:

```powershell
.venv\Scripts\python.exe -m evaluation.build_capability_map
.venv\Scripts\python.exe -m evaluation.build_dataset
.venv\Scripts\python.exe -m evaluation.retrieval_eval --output evaluation/results/retrieval_final_v2.json
.venv\Scripts\python.exe -m evaluation.benchmark_runner --model qwen3:4b-instruct --output evaluation/results/production/qwen3_4b_full_v3.jsonl --resume --diagnostics --warmup
```

The runner flushes and syncs every JSONL row. Reissue the **same command** after interruption; `--resume` validates the completed prefix and continues at the next question. Existing output is otherwise protected; `--overwrite` must be explicit. If source, prompt, model, retrieval configuration or dataset changed, use a new output path. `--ids Q001 Q002` selects a diagnostic subset, `--repeat N` repeats it, `--profile voice --max-tokens 120` uses the compact voice prompt. Keep the same IDs, profile, repetition count and token cap for paired model comparisons.

Summarize saved runs without new model calls:

```powershell
.venv\Scripts\python.exe -m evaluation.analyze_results evaluation/results/production/qwen3_4b_full_v3.jsonl evaluation/results/models/qwen3_4b_voice.jsonl evaluation/results/models/llama3_2_3b_voice.jsonl
```

## Results and interpretation

Each result row contains the question, route, state, raw and selected retrieval hits, exact messages supplied to the model, raw and guarded answer, model configuration and hash, timing, automatic score, and first failure label. The companion `.summary.json` reports score and latency. `retrieval_diagnostics.py` adds a separate raw-query diagnostic; its timing is excluded from end-to-end latency. Production retrieval combines search and heuristic reranking, so no separate reranker timer is available.

The rule score checks numerical error with unit normalization, required fact words, expected document retrieval, citation syntax and refusals on unavailable data. It is a **triage measure, not a certified engineering accuracy rate**. Document-source recall does not prove that the chosen passage contains the answer. Inspect the saved answers against the oracle and excerpts, especially safety or standards statements. Latency is measured on one local 4 GB GPU, with a warmup; voice measurements are text response timings and exclude speech recognition, synthesis and interruption handling.

The baseline, paired voice and hard-question model comparisons, retrieval run, and final production run are under `results/`. Read [HANDOFF.md](HANDOFF.md) for their paths and current status.
