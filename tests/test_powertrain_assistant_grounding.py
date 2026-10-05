"""Regression checks for app-state answers and extractive RAG fallback."""
from vmi.assistant_core import (_relevant_excerpt, action_reply,
                                checked_calculation_reply, checked_document_reply,
                                ground_rag_reply, plan_request, read_state_reply)


SCREEN = {
    "analysis": "Acceleration",
    "inputs": {"peak_torque": "30", "peak_power": "4.4", "gear_ratio": "8",
               "gear_efficiency": "0.95", "wheel_radius": "0.28"},
    "datasets": {"motor_curve": False},
    "results": [
        "Maximum startable gradient ≈ 33.0% with the peak curve.",
        "0–60 km/h in ≈ 13.1 s (flat road, peak torque available).",
        "Last Acceleration plot: 74.4 km/h at 60.0 s; 60 km/h at 13.06 s; "
        "force-crossing top speed 74.6 km/h; settled near top speed at 49.9 s.",
    ],
}


def test_direct_result_reads_actual_snapshot():
    questions = {
        "Can the current setup start on a 20% grade?": "33",
        "What tractive force is available at launch?": "814.3 N",
        "What top speed does the Acceleration force crossing predict?": "74.6 km/h",
        "What is the motor RPM at 60 km/h for this gearing and wheel radius?": "4547 rpm",
    }
    for question, expected in questions.items():
        assert plan_request(question)["route"] == "state"
        assert expected in read_state_reply(question, SCREEN)


def test_distance_and_ambiguous_followup_do_not_invent_results():
    assert "unavailable" in read_state_reply(
        "How many metres does this vehicle cover during the 0–60 run?", SCREEN)
    assert plan_request("Is the motor limiting it?")["route"] == "clarification"
    assert "Which result" in action_reply("clarification")


def test_fallback_excerpt_finds_fact_beyond_chunk_start():
    text = "Unrelated overview. " * 30 + (
        "Winding resistance and phase balance: 4-wire Kelvin measurement "
        "at controlled temperature, corrected to reference temperature.")
    excerpt = _relevant_excerpt(text, "How is winding resistance measured?")
    assert "Kelvin" in excerpt and "temperature" in excerpt


def test_cited_document_claim_cannot_add_an_unsupported_timing():
    source = "knowledge_base/standards/motor_testing.docx#chunk-2"
    hits = [{"source": source,
             "text": "Over-current protection: controlled fault injection and protection reaction timing."}]
    reply = f"Protection must trip within 10 ms [{source}]."
    checked = ground_rag_reply(reply, hits, "How is over-current protection tested?")
    assert "10 ms" not in checked
    assert "controlled fault injection" in checked


def test_reduction_ratio_answer_is_checked():
    question = "If I raise the reduction ratio, what happens to launch force and motor RPM at a fixed road speed?"
    assert plan_request(question)["route"] == "local"


def test_controller_protection_reply_uses_only_documented_method():
    source = "knowledge_base/standards/EV_Motor_Testing_India_2W_3W_Hub_MidMount.docx#chunk-10"
    hits = [{"source": source,
             "text": "Over-current and short-circuit protection | Controlled fault injection and protection reaction timing. | AIS-156 / AIS-038 system safety context"}]
    reply = checked_document_reply("How should the testing plan validate controller over-current protection?", hits)
    assert "controlled fault injection" in reply.lower()
    assert "no numerical trip-time limit" in reply
    assert f"[{source}]" in reply


def test_uploaded_curve_and_mass_explanations_avoid_invented_details():
    curve = checked_calculation_reply(
        "What happens to the acceleration calculation when a motor torque-speed file is uploaded?")
    assert "interpolates torque" in curve
    assert "0.1 km/h" not in curve
    mass = checked_calculation_reply(
        "If I increase mass, which of acceleration and steady top speed are affected?")
    assert "rolling" in mass
    assert "aerodynamic drag is unchanged" in mass


def test_small_wheel_and_top_speed_followup_are_consistent():
    reply = checked_calculation_reply("How does a smaller wheel radius affect launch force?")
    assert "increases launch force" in reply
    assert "motor RPM also rises" in reply
    history = [{"role": "user", "content": "What's my top speed?"},
               {"role": "assistant", "content": "About 74.6 km/h."}]
    reply = read_state_reply("Why is it that low?", SCREEN, history)
    assert "wheel force" in reply and "road resistance" in reply


def test_named_test_section_uses_its_actual_excerpt():
    source = "knowledge_base/standards/EV_Motor_Testing_India_2W_3W_Hub_MidMount.docx#chunk-2"
    hits = [{"source": source,
             "text": "5.2 Mid-mount / mid-drive emphasis Prioritize torsional durability, "
                     "gearbox / chain / belt interface checks, mounting bracket stiffness. "
                     "Include coupling fatigue and vibration transfer into the frame."}]
    reply = checked_document_reply(
        "What additional test emphasis does section 5.2 of the motor-testing document give a mid-mount motor?", hits)
    assert "coupling fatigue" in reply and "vibration transfer" in reply
    assert f"[{source}]" in reply


def test_paraphrased_value_questions_read_the_snapshot():
    # Reworded holdout (evaluation/holdout.json): these fell through to an
    # un-grounded model call that invented inputs ("m_ref = 100 kg").
    questions = {
        "whats the max speed i can hit": "74.6 km/h",
        "how fast does it go?": "74.6 km/h",
        "0-60 time?": "13.1 s",
        "How much traction force do I get from standstill?": "814.3 N",
        "Will it manage a hill start on a 20 percent incline?": "33",
        "At 60 km/h how many rpm is the motor turning with this gear ratio and wheel?": "4547 rpm",
        "How big is the wheel radius in the inputs?": "0.28 m",
        "Where in motor rpm does the peak envelope switch from constant torque to constant power?": "1400.6 rpm",
    }
    for question, expected in questions.items():
        assert plan_request(question)["route"] == "state", question
        assert expected in read_state_reply(question, SCREEN), question


def test_why_questions_never_get_a_bare_field_value():
    for question in ("More peak torque helps take-off, so why doesn't it help much at high speed?",
                     "What force balance defines the app's estimated top speed?"):
        assert read_state_reply(question, SCREEN) == ""


def test_unsourced_verdicts_and_vague_turns_are_not_answered_by_the_model():
    for question in ("Is this vehicle compliant?", "What's the homologated top speed of this vehicle?"):
        assert "can't be determined" in checked_calculation_reply(question)
    for question in ("why is it so slow", "should i make it bigger?", "is the motor the bottleneck?"):
        assert plan_request(question)["route"] == "clarification"
    assert plan_request("Is this vehicle AIS-156 compliant?")["route"] == "rag"
