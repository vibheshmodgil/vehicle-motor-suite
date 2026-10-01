"""Deterministic checks for assistant routing, state, grounding and UI safety."""
import json
import hashlib
import os
import queue
from pathlib import Path

import pytest

from vmi import assistant, llm_client, rag_store
from vmi.assistant_core import (action_reply, build_messages, checked_calculation_reply,
                                document_evidence_limit, ground_rag_reply, guard_unsourced_citations,
                                guard_state_numbers, checked_state_suggestions,
                                normalize_model_reply, plan_request,
                                read_state_reply, PROMPT_CHAR_BUDGET)


CASES = json.loads((Path(__file__).parent / "assistant_qa_cases.json").read_text(encoding="utf-8"))


def test_questionnaire_has_unique_meaningful_coverage():
    assert len(CASES) >= 300
    assert len({c["id"] for c in CASES}) == len(CASES)
    counts = {category: sum(c["category"] == category for c in CASES)
              for category in {c["category"] for c in CASES}}
    for category in ("general_conversation", "vehicle_calculation", "motor_analysis",
                     "screen_analysis", "rag", "tool_calling", "multi_turn",
                     "parameter_state", "plot_generation", "voice_behavior",
                     "error_edge", "unknown_information", "ambiguous_request",
                     "topic_switching"):
        assert category in counts
        assert counts[category] >= 20


@pytest.mark.parametrize("case", [c for c in CASES if c.get("expected_route")], ids=lambda c: c["id"])
def test_offline_routing_matrix(case):
    assert plan_request(case["input"], case["history"])["route"] == case["expected_route"]


@pytest.mark.parametrize("question,route", [
    ("Hello", "local"),
    ("Change mass to 220 kg", "state_action"),
    ("Plot torque versus speed", "plot_action"),
    ("What does the TSI require?", "rag"),
    ("What is the current motor power?", "state"),
    ("Explain MTPA", "model"),
    ("Can you change my mass to 220 kg?", "state_action"),
    ("Please plot torque against speed", "plot_action"),
    ("Could you export the current plot?", "plot_action"),
    ("Run the simulation", "state_action"),
    ("Calculate gradeability at 60 km/h", "calculation_request"),
    ("What is the maximum motor torque?", "model"),
    ("What torque is required for a 12 degree gradient?", "calculation_request"),
    ("How much motor power is required?", "calculation_request"),
])
def test_route_operations_are_honest(question, route):
    plan = plan_request(question)
    assert plan["route"] == route
    if route.endswith("action"):
        assert plan["operations"] == []
        assert "can't" in action_reply(route)


def test_read_state_uses_live_snapshot_not_history():
    screen = {"analysis": "Powertrain Sizing", "inputs": {"m_ref": "220", "peak_power": "10",
                                                       "continuous_power": "5"},
              "results": ["Estimated flat-road top speed ≈ 71.3 km/h (peak curve)."]}
    assert read_state_reply("What is the current vehicle mass?", screen) == "The current vehicle mass is 220 kg."
    assert read_state_reply("Read the motor peak power", screen) == "The current motor peak power is 10 kW."
    assert "not available" in read_state_reply("What is the wheel radius?", screen)
    assert not read_state_reply("Why is the mass important?", screen)
    assert "invalid" in read_state_reply("What is the vehicle mass?", {"inputs": {"m_ref": "NaN"}})
    assert "10 kW peak and 5 kW continuous" in read_state_reply("What is the current motor power?", screen)
    assert "71.3 km/h" in read_state_reply("What top speed can this vehicle achieve?", screen)


def test_grade_comparison_uses_only_current_startable_result():
    screen = {"results": ["Maximum startable gradient ≈ 19.4% (11.0°) with the peak curve."]}
    reply = read_state_reply("Is my motor enough to climb a 20% grade?", screen)
    assert "19.4%" in reply and "No." in reply and "0.6 percentage points" in reply
    assert "sustained" not in reply or "thermal" in reply
    assert not read_state_reply("Can it climb a 20% grade at 60 km/h?", screen)
    assert not read_state_reply("Is it enough to climb a 20% grade?", {"results": []})


def test_explicit_torque_constant_uses_deterministic_units():
    question = "A motor has a torque constant of 0.5 N m/A. What torque does it make at 40 A?"
    assert plan_request(question)["route"] == "local"
    reply = checked_calculation_reply(question)
    assert "20 N m" in reply and "peak/RMS convention" in reply


def test_state_guidance_uses_results_without_inventing_projections():
    screen = {"analysis": "Powertrain Sizing",
              "inputs": {"cd_a": "0.6", "peak_power": "4.4", "gear_ratio": "8"},
              "results": ["Estimated flat-road top speed ≈ 71.3 km/h."]}
    reply = checked_state_suggestions("Suggest three changes to my current analysis", screen)
    assert "71.3 km/h" in reply and "4.4 kW" in reply and "gear ratio=8" in reply
    assert "Missing" in reply and ">100 km/h" not in reply
    assert "CdA=0.6" in checked_state_suggestions("I want to increase top speed", screen)
    bad = "Increase to 15 kW and expect 100 km/h."
    guarded = guard_state_numbers(bad, "Suggest improvements", screen)
    assert "can't verify" in guarded and "100 km/h" not in guarded


def test_document_answer_requires_an_actual_source_identifier():
    hits = [{"source": "standards/test.docx#chunk-2", "text": "Limit is 80 C."}]
    assert ground_rag_reply("The limit is 80 C [standards/test.docx#chunk-2].", hits).startswith("The limit")
    guarded = ground_rag_reply("The limit is 80 C [made up].", hits)
    assert "indexed excerpt" in guarded and "Limit is 80 C" in guarded
    assert "indexed excerpt" in ground_rag_reply(
        "The motor is certified [standards/test.docx#chunk-2].", hits)


def test_conflicting_ratings_are_reported_without_choosing_one():
    hits = [{"source": "A#chunk-1", "text": "Continuous current 60 A RMS. Revision unknown."},
            {"source": "B#chunk-1", "text": "Continuous current 85 A RMS. Revision unknown."}]
    reply = ground_rag_reply("Use 60 A RMS [A#chunk-1].", hits, "What continuous current should I use?")
    assert "conflict" in reply and "60 A RMS [A#chunk-1]" in reply
    assert "85 A RMS [B#chunk-1]" in reply and "revision" in reply


def test_continuous_torque_comparison_uses_documented_rating():
    hits = [{"source": "spec#chunk-1", "text": "Continuous torque is 24 N m at 25 C."}]
    reply = ground_rag_reply("40 N m is fine [spec#chunk-1].", hits,
                             "Is 40 N m continuous acceptable?")
    assert "24 N m" in reply and "40 N m exceeds" in reply


def test_peak_efficiency_is_read_from_map_facts_without_model():
    hits = [{"source": "knowledge_base/datasheets/U546_eff_map.xlsx#chunk-1",
             "text": "Key facts: Largest value in the map: 0.8966, in column header 500."}]
    from vmi.assistant_core import checked_document_reply
    reply = checked_document_reply("What is the peak efficiency of U546?", hits)
    assert "89.66%" in reply and "U546_eff_map" in reply


def test_goodman_formula_is_extracted_from_cited_document():
    from vmi.assistant_core import checked_document_reply
    hits = [{"source": "handbook#chunk-22",
             "text": "Modified Goodman: σ_a/S_e + σ_m/Sut = 1/n. Mean and alternating stress."}]
    reply = checked_document_reply("How do I check a shaft using Goodman?", hits)
    assert "alternating and mean" in reply and "safety factor" in reply
    assert "[handbook#chunk-22]" in reply


def test_generic_model_cannot_display_invented_document_citation():
    reply = guard_unsourced_citations("The limit is 85 A. [Document Excerpt: BMS Guide, 2023]")
    assert "couldn't verify" in reply and "85 A" not in reply


def test_stable_concepts_bypass_model():
    questions = ["How can you help me?", "What happens if I leave Crr and CdA blank?",
                 "What is motor torque? Explain it simply.",
                 "What is the difference between a PMSM and a BLDC motor?",
                 "Should I use an induction motor or a PM motor for an electric scooter?",
                 "What does the battery DC limit input do?",
                 "How does the app find the vehicle top speed?",
                 "Are 100 A phase peak and 100 A phase RMS the same sinusoidal current?",
                 "Explain MTPA versus field weakening for an IPMSM in simple terms.",
                 "Why might regenerative braking torque be limited at high battery SOC?",
                 "How does field oriented control of a PMSM work?",
                 "Why is a motor's continuous torque lower than its peak torque?",
                 "How does the range analysis calculate range?"]
    for question in questions:
        assert plan_request(question)["route"] == "local"
        assert checked_calculation_reply(question)
    comparison = checked_calculation_reply("What is the difference between a PMSM and a BLDC motor?")
    assert "permanent-magnet rotor" in comparison and "trapezoidal" in comparison
    assert "efficiency depends" in comparison
    torque = checked_calculation_reply("What is motor torque? Explain it simply.")
    assert "P(W) =" in torque and "/ 9549" in torque
    selection = checked_calculation_reply("Should I use an induction motor or a PM motor for an electric scooter?")
    assert "Neither motor type guarantees" in selection and "drive cycle" in selection
    battery = checked_calculation_reply("What does the battery DC limit input do?")
    assert "Pdc = Vdc" in battery and "no battery DC power cap" in battery
    top_speed = checked_calculation_reply("How does the app find the vehicle top speed?")
    assert "net force" in top_speed and "battery DC power limit" in top_speed


def test_named_standard_and_multi_source_limits():
    hits = [{"source": "knowledge_base/standards/motor_guidance.docx#chunk-2", "text": "AIS 156 is mentioned."}]
    assert "not the AIS 156 standard itself" in document_evidence_limit("What does AIS 156 require?", hits)
    assert "fewer than two" in document_evidence_limit("Compare the two testing standards", hits)
    assert "do not carry page numbers" in document_evidence_limit("Which page states the limit?", hits)


def test_rag_history_cannot_become_document_evidence():
    history = [{"role": "user", "content": "Find the limit."},
               {"role": "assistant", "content": "An old unsupported claim says 200 C."}]
    messages = build_messages("Search again", hits=[{"source": "a.md#chunk-1", "text": "80 C."}], history=history)
    assert "Find the limit" in str(messages)
    assert "old unsupported claim" not in str(messages)


def test_prompt_has_conservative_context_budget():
    history = [{"role": "user", "content": "x" * 800}] * 6
    hits = [{"source": f"file{i}.md#chunk-1", "text": "y" * 2600} for i in range(3)]
    screen = {"analysis": "Powertrain Sizing", "inputs": {f"field{i}": "z" * 80 for i in range(40)}}
    messages = build_messages("Why?" * 400, screen, hits, history)
    assert sum(len(m["content"]) for m in messages) <= PROMPT_CHAR_BUDGET + 100


def test_named_tsi_requires_an_indexed_tsi_file(monkeypatch):
    monkeypatch.setattr(rag_store, "_load_manifest", lambda: {"__version__": 4, "standard/motor_testing.md": {}})
    monkeypatch.setattr(rag_store, "_get_collection", lambda: pytest.fail("must not search unrelated files"))
    assert rag_store.query("What does the TSI require?") == []


def test_pdf_source_label_includes_page():
    label = rag_store._source_label({"source": "knowledge_base\\MTPA.pdf", "page": 6, "chunk": 11}, "x::10")
    assert label == "knowledge_base/MTPA.pdf#page-6#chunk-11"


def test_rebuild_removes_old_chunks_when_file_becomes_duplicate(monkeypatch, tmp_path):
    original = tmp_path / "original.txt"
    changed = tmp_path / "changed.txt"
    original.write_text("same", encoding="utf-8")
    changed.write_text("same", encoding="utf-8")
    digest = hashlib.sha1(b"same").hexdigest()
    manifest = {"__version__": rag_store.INDEX_VERSION,
                str(original): {"mtime": os.path.getmtime(original), "n_chunks": 1, "digest": digest},
                str(changed): {"mtime": 0, "n_chunks": 2, "digest": "old"}}
    deleted, saved = [], []

    class Collection:
        def delete(self, ids): deleted.extend(ids)

    monkeypatch.setattr(rag_store, "_get_collection", lambda reset=False: Collection())
    monkeypatch.setattr(rag_store, "_iter_index_files", lambda: iter([str(original), str(changed)]))
    monkeypatch.setattr(rag_store, "_load_manifest", lambda: manifest.copy())
    monkeypatch.setattr(rag_store, "_save_manifest", saved.append)
    rag_store.rebuild_index()
    assert deleted == [f"{changed}::0", f"{changed}::1"]
    assert str(changed) not in saved[0]


@pytest.mark.parametrize("raw", [
    '<think>secret tool plan</think>{"analysis":"hidden","final":"hello"}',
    '{"tool_calls":[{"name":"change_mass"}],"final":"Done"}',
    '{"parameters":{"mass":220},"answer":"Changed"}',
    'analysis: call tool\nfinal: Done',
    '```json\n{"tool_calls":[{"name":"change_mass"}]}\n```',
    '<think>unfinished reasoning',
])
def test_internal_payloads_never_render(raw):
    assert normalize_model_reply(raw) == "I couldn't produce a reliable answer. Please try again."


def test_ollama_tool_call_event_is_rejected(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def raise_for_status(self): pass
        def iter_lines(self):
            yield json.dumps({"message": {"tool_calls": [{"function": {"name": "change_mass"}}]}}).encode()

    monkeypatch.setattr(llm_client.requests, "post", lambda *a, **k: Response())
    with pytest.raises(llm_client.OllamaError, match="unsupported tool call"):
        llm_client._stream_chat([], "test-model", 20, None, False)


def test_plain_final_json_can_be_unwrapped():
    assert normalize_model_reply('{"final":"The motor peak power is 10 kW."}') == "The motor peak power is 10 kW."


def test_checked_engineering_arithmetic():
    power = checked_calculation_reply("Calculate mechanical shaft power for 30 Nm at 3000 rpm")
    assert "9.4248 kW" in power
    gearing = checked_calculation_reply("Calculate wheel torque: motor torque 20 Nm, gear ratio 6:1, gearbox efficiency 0.95, wheel radius 0.3 m.")
    assert "114 N m" in gearing and "380 N" in gearing
    assert "13.8889 m/s" in checked_calculation_reply("Convert 50 km/h to m/s")
    assert "314.159 rad/s" in checked_calculation_reply("Convert 3000 rpm to rad/s")
    assert "47.480 N" in checked_calculation_reply("Calculate rolling force for 220 kg and Crr 0.022")
    assert "102.083 N" in checked_calculation_reply("Calculate aero drag at 60 km/h with CdA 0.6 square metres")
    assert checked_calculation_reply("Calculate shaft power for -30 Nm at 3000 rpm") == ""
    assert checked_calculation_reply("Calculate rolling force for -220 kg and Crr 0.022") == ""


class WorkerHarness:
    def __init__(self):
        self._assistant_queue = queue.Queue()
        self._assistant_session_id = "isolated-test-session"
        self.logged = []

    def _log_chat_exchange(self, *args):
        self.logged.append(args)


def _run_worker(question, plan, monkeypatch, screen=None, reply=None, hits=None):
    harness = WorkerHarness()
    monkeypatch.setattr(rag_store, "query", lambda q: hits if hits is not None else pytest.fail("unexpected retrieval"))
    monkeypatch.setattr(llm_client, "stream_chat", lambda *a, **k: (reply, {"model": "test-model", "truncated": False})
                        if reply is not None else pytest.fail("unexpected model call"))
    local = action_reply(plan["route"])
    assistant.AssistantMixin._chat_worker(harness, question, "test-model", screen or {}, [], (), local, plan)
    events = []
    while not harness._assistant_queue.empty():
        events.append(harness._assistant_queue.get())
    return events, harness.logged


def test_action_request_never_invokes_model_or_changes_state(monkeypatch):
    question = "Change mass to 220 kg"
    events, logged = _run_worker(question, plan_request(question), monkeypatch)
    answer = [payload[1] for kind, payload in events if kind == "chat_reply"][0]
    assert "can't change" in answer
    assert logged[0][2] == "ok"
    assert logged[0][3]["trace"][-1]["event"] == "FINAL_RESPONSE"


def test_missing_rag_evidence_never_invokes_model(monkeypatch):
    question = "What does the TSI require for this test?"
    events, _ = _run_worker(question, plan_request(question), monkeypatch, hits=[])
    answer = [payload[1] for kind, payload in events if kind == "chat_reply"][0]
    assert "couldn't find" in answer


def test_rag_model_without_citation_is_guarded(monkeypatch):
    question = "What does the standard require?"
    hits = [{"source": "standards/a.md#chunk-1", "text": "Torque must be measured."}]
    events, _ = _run_worker(question, plan_request(question), monkeypatch,
                            reply="The limit is 120 C.", hits=hits)
    answer = [payload[1] for kind, payload in events if kind == "chat_reply"][0]
    assert "indexed excerpt" in answer and "Torque must be measured" in answer


def test_state_read_does_not_invoke_model(monkeypatch):
    question = "What is the current vehicle mass?"
    screen = {"analysis": "Powertrain Sizing", "inputs": {"m_ref": "220"}}
    events, _ = _run_worker(question, plan_request(question), monkeypatch, screen=screen)
    answer = [payload[1] for kind, payload in events if kind == "chat_reply"][0]
    assert answer == "The current vehicle mass is 220 kg."
