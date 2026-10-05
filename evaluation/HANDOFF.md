# Powertrain and Acceleration assistant evaluation handoff

Updated: 2026-10-05. Scope: the existing Python desktop application, focused on **Powertrain Sizing** and **Acceleration**. This file is the durable work log for continuing after a session or account interruption. The full benchmark stores one flushed and `fsync`ed JSON row per question.

## Completed work

1. Inspected the application before changing it. The pre-optimization architecture and 58-entry feature inventory are in `docs/powertrain_acceleration_architecture.md` and `evaluation/capability_map.json`.
2. Built the 90-question scenario and oracle from application calculations and local reference files: `evaluation/questions.json`, `evaluation/ground_truth.json`, `evaluation/reference_case.py`. The questions cover direct results, engineering explanations, motor/controller behavior, cross-analysis reasoning, document answers, unavailable facts, ambiguity, and conversation.
3. Completed the original Qwen3 4B baseline: `evaluation/results/baseline/qwen3_4b_full.jsonl` and companion summary. Rule score 0.5849; median end-to-end 10.41 s. This is a triage score, not a verified engineering accuracy percentage.
4. Improved state reads, checked numerical answers, ambiguity handling, source selection, evidence-limited document replies, concise voice prompts, and the live acceleration plot snapshot. Changed `vmi/assistant.py`, `vmi/assistant_core.py`, `vmi/rag_store.py`, and `vmi/torque_force.py`. The app still has no ASR/TTS voice agent or true UI token streaming.
5. Added retrieval diagnostics, paired model benchmarking, rule scoring, resumable JSONL checkpoints, and result analysis under `evaluation/`. The 21 reference questions now retrieve the expected document first in 21/21 cases (`evaluation/results/retrieval_final.json`). This measures document selection, not passage-level answer accuracy.
6. Benchmarked fast candidates on the same 46-question voice profile: Qwen3 4B scored 0.8098 overall (0.5294 on 17 model-called cases), Llama3.2 3B scored 0.7663 (0.4118 model-called). Warm model-only median end-to-end was 0.949 s vs 0.988 s; median first-token 0.177 s vs 0.199 s. See `evaluation/results/models/voice_comparison.json` and raw JSONL traces.
7. Benchmarked 15 hard engineering/document questions: Qwen3 4B score 0.8722, median 10.64 s; Gemma3 4B score 0.7444, median 17.52 s. See `evaluation/results/models/*_hard.jsonl` and summaries. Earlier manual review caught a reversed gear/RPM explanation and an invented 10 ms protection time; targeted guards were added afterward.
8. Completed the final 90-question run at `evaluation/results/production/qwen3_4b_full_v3.jsonl`. Rule score 0.9708 versus 0.5849 baseline; median end-to-end 0.0007 s versus 10.41 s, p90 5.98 s versus 14.84 s. Only 15 final cases called the model; their median end-to-end latency was 6.53 s. The 21 document questions used retrieval and checked source/table replies with no chat-model calls. Full context is in `docs/powertrain_acceleration_model_evaluation.md` and `evaluation/results/production/comparison_final.json`.
9. Verified **417 tests pass** under the repository's Tk environment. The runner's `--resume` path was smoke tested with Q001: it appended once and made no new model call on rerun. A malformed trailing row was saved as `evaluation/results/smoke/trailing_v3.jsonl.incomplete` and the complete prefix resumed without a repeat. The final 90/90 run was also reopened with `--resume` and made no model calls.
10. Wrote the final architecture, dataset and model-choice report at `docs/powertrain_acceleration_model_evaluation.md`. The recommendation is Qwen3 4B instruct for both voice-text and engineering roles on this 4 GB GPU, with different request profiles; Llama3.2 3B is a conditional distinct voice candidate if served resident on separate hardware. The app has no ASR/TTS or interactive voice playback yet.

## Holdout (paraphrase) run — started 2026-10-05

`evaluation/holdout.json` = the same 90 cases (same oracle, facts, history) with reworded questions (`H001..H090`, `source_question_id` links back). Tests whether the rule routing generalises beyond the exact wording it was tuned on. The runner gained `--questions` (default `questions.json`; dataset hash covers the chosen file). Editing the runner changed `pipeline_sha256`, so the old v3 file can no longer be `--resume`d — it is complete, so that is harmless. Resume command:

```powershell
.venv\Scripts\python.exe -m evaluation.benchmark_runner --model qwen3:4b-instruct --questions evaluation/holdout.json --output evaluation/results/holdout/qwen3_4b_holdout_v1.jsonl --resume --diagnostics --warmup
```

**Result (v1, pre-fix pipeline): 0.729 vs 0.971 on the original wording**; median latency 6.55 s vs 0.0007 s; 39/90 routes changed. Manual review: real faults, not just rubric — value questions ("whats the max speed", "0-60 time?", "traction force from standstill") reached the model with NO app state and it invented inputs (m_ref=100 kg, 30 kW); model did its own RPM arithmetic wrong (1042 then 130 rpm vs 4547); "acceleration test"/"road test" misrouted to RAG; model declared the vehicle "not AIS-156 compliant"; "why is it so slow" read as app latency.

**Fixes (vmi/assistant_core.py, shared by GUI, `answer_case`, and the runner):** `normalize_question()` + `_SYNONYMS` map paraphrases onto the vocabulary the checked rules use, applied in `plan_request`, `read_state_reply`, `checked_document_reply`, `document_evidence_limit`, `checked_concept_reply`; `_STATE_QUANTITY` (value-of-my-vehicle questions route to state unless `_CONCEPTUAL`) and `_EXPLICIT_DOC` (generic "test" words only go to RAG when a document is actually named); `verdict_reply` (first helper in `checked_calculation_reply`) refuses certification/homologation/compliance verdicts locally when no document is named; generalized vague-diagnosis clarification; state-reader prefix gate replaced with a why/how check; top-speed read falls back to the plot's force-crossing. Tests: 3 new cases in tests/test_powertrain_assistant_grounding.py; 420 pass.

`evaluation/holdout2.json` (`K001..K090`, terse/formal/typo/Hinglish wording) was written BEFORE looking at any post-fix results and was not used for tuning — it is the blind set. holdout.json is now a tuning set, not a holdout.

Post-fix runs (new files, pipeline hash changed): `results/production/qwen3_4b_full_v4.jsonl`, `results/holdout/qwen3_4b_holdout_v2.jsonl`, `results/holdout/qwen3_4b_holdout2_v1.jsonl`.

## Completed production benchmark and resume

`evaluation/results/production/qwen3_4b_full_v3.jsonl` is the completed 90-question Qwen3 run on the final pipeline. Its companion `.summary.json` records the aggregate. The command below is safe to rerun: it validates all 90 saved rows and exits without model calls. If a later run is interrupted, use the same `--resume` pattern with that run's output path and unchanged code/configuration.

```powershell
.venv\Scripts\python.exe -m evaluation.benchmark_runner --model qwen3:4b-instruct --output evaluation/results/production/qwen3_4b_full_v3.jsonl --resume --diagnostics --warmup
```

The runner checks question order/text, repetition, model digest, prompt, pipeline and dataset hashes. It refuses to append if any changed. In that case, keep the old result as evidence and start a **new output path**. Do not use `--overwrite` on an interrupted run. It preserves a malformed trailing line in a `.incomplete` backup before continuing. Each complete row has the answer, raw model answer, app context, retrieved chunks, model configuration, timing and rule score.

The working tree contains modified `vmi/` files and untracked `docs/`, `evaluation/` and test artifacts from this task. Nothing was committed. `.claude/` was already untracked and was not changed.

## Optional next validation work

1. Add a blinded holdout with new vehicle fixtures, unseen manuals and paraphrased questions. The present rule score is on the same 90 cases used to guide fixes and is not a generalization estimate.
2. Add human grading for engineering correctness and citation-level support. Four final-run refusal answers are semantically sound but the rubric's narrow regex marks them as failures; do not equate the label `HALLUCINATION` with an actual invented certification result without inspecting the answer.
3. If implementing a true voice agent, add ASR, TTS, incremental playback and interruption handling, then measure end-to-end speech latency. Keep models resident on separate hardware before testing a distinct voice checkpoint.

## Copyable prompt for another Codex ID/session

> Continue my PowertrainTool assistant evaluation in `C:\Users\vibhe\OneDrive\Desktop\PowertrainTool\vehicle_motor_suite\vehicle_motor_suite`. First read `evaluation/HANDOFF.md`, `docs/powertrain_acceleration_architecture.md`, and `docs/powertrain_acceleration_model_evaluation.md`, then inspect git status. The final 90-case benchmark is complete at `evaluation/results/production/qwen3_4b_full_v3.jsonl`; do not overwrite or rerun it. For any new long benchmark, choose a new JSONL output and use `--resume` with the exact same model/profile/IDs/configuration after interruption. Continue with a blinded holdout and human engineering/citation grading, or implement and measure the actual voice stack if that is the next request. Keep logging progress and preserve `.claude/` and unrelated user files.

The assistant cannot read account usage percentage, so this prompt is stored here in advance instead of waiting for a 5% threshold notification.
