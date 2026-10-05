"""Build the fixed, source-backed first evaluation set. No model writes or judges truth."""
from __future__ import annotations

import json
from pathlib import Path

from evaluation.reference_case import calculate_reference

ROOT = Path(__file__).resolve().parent
TEST_DOC = "knowledge_base/standards/EV_Motor_Testing_India_2W_3W_Hub_MidMount.docx"
CURVE_DOC = "knowledge_base/datasheets/U546_torque_speed_map.xlsx"
EFF_DOC = "knowledge_base/datasheets/U546_eff_map.xlsx"


def build():
    ref = calculate_reference()
    top = ref["top_speed_report_kmh"]
    grade = ref["max_startable_gradient_pct"]
    accel = ref["acceleration_report_0_60_s"]
    rows = []

    def add(category, question, answer, facts=(), *, value=None, unit=None,
            tolerance=None, source="application", location="", difficulty="easy",
            reasoning=False, unavailable=False, forbidden=(), history=(), role="both"):
        qid = f"Q{len(rows)+1:03d}"
        rows.append({
            "question_id": qid, "category": category, "question": question,
            "expected_answer": answer, "expected_numerical_values":
                [{"value": value, "unit": unit, "tolerance_abs": tolerance}]
                if value is not None else [],
            "required_facts": list(facts), "forbidden_claims": list(forbidden),
            "expected_source": source, "relevant_document_or_chunk": location,
            "difficulty": difficulty, "reasoning_required": reasoning,
            "expected_behavior_if_unavailable":
                "State that the available data is insufficient; identify the missing value without inventing it."
                if unavailable else None,
            "scenario": "reference_vehicle" if (source == "application" or history
                        or category in ("unavailable", "ambiguous")
                        or "this gearing" in question.lower()) else None,
            "history": list(history), "model_role": role,
        })

    S = "application: vmi/enhancements.py:_report_vehicle_capability; evaluation/reference_case.py"
    P = "application: vmi/torque_force.py; vmi/parametric.py; evaluation/reference_case.py"
    A = "application: vmi/torque_force.py:plot_vehicle_max_speed_vs_time; vmi/calc_ext.py:simulate_acceleration"
    for question, answer, value, unit, facts in [
        ("What is the calculated flat-road top speed for this vehicle?", f"The app estimates {top:.1f} km/h on a flat road.", top, "km/h", ["flat-road", "top speed"]),
        ("What's my top speed?", f"About {top:.1f} km/h, based on the current peak capability curve.", top, "km/h", ["top speed"]),
        ("What maximum startable gradient does the app estimate?", f"{grade:.1f}% with the peak curve.", grade, "%", ["startable", "peak"]),
        ("Can the current setup start on a 20% grade?", f"Yes. The app's peak-curve startable limit is {grade:.1f}%; sustained climbing is not verified.", grade, "%", ["startable", "sustained"]),
        ("What is the input peak motor torque?", "30 N m.", 30, "N m", ["peak", "torque"]),
        ("What is the input peak motor power?", "4.4 kW.", 4.4, "kW", ["peak", "power"]),
        ("What is the input continuous motor power?", "3.0 kW.", 3, "kW", ["continuous", "power"]),
        ("What wheel torque is available at launch from the peak curve?", "228 N m from 30 N m × 8 × 0.95.", 228, "N m", ["wheel", "torque"]),
        ("What tractive force is available at launch?", "Approximately 814.3 N at the 0.28 m wheel radius.", ref["launch_wheel_force_n"], "N", ["force"]),
        ("At what motor RPM does the theoretical peak curve enter constant-power operation?", "About 1400.6 rpm, from peak power divided by peak torque.", ref["peak_motor_base_rpm"], "rpm", ["base", "power"]),
        ("What gear ratio is configured?", "8:1 reduction.", 8, "ratio", ["gear"]),
        ("What wheel radius is configured?", "0.28 m.", 0.28, "m", ["wheel", "radius"]),
        ("What rolling-resistance coefficient is configured?", "Crr is 0.018.", 0.018, "dimensionless", ["Crr"]),
        ("What drag area is configured?", "CdA is 0.6 m².", 0.6, "m²", ["CdA"]),
    ]:
        add("powertrain_direct", question, answer, facts, value=value, unit=unit,
            tolerance=max(abs(value)*0.02, 0.02), source="application", location=S)

    for question, answer, value, unit, facts in [
        ("What is the calculated 0–60 km/h time in the current report?", f"About {accel:.2f} s.", accel, "s", ["0", "60"]),
        ("What's my zero to sixty?", f"About {accel:.1f} seconds.", accel, "s", ["60"]),
        ("What is the 0–60 km/h time on the Acceleration plot?", f"About {ref['acceleration_plot_0_60_s']:.3f} s.", ref["acceleration_plot_0_60_s"], "s", ["60"]),
        ("What top speed does the Acceleration force crossing predict?", f"About {ref['acceleration_plot_top_speed_kmh']:.1f} km/h.", ref["acceleration_plot_top_speed_kmh"], "km/h", ["top speed"]),
        ("What speed does the simulation reach after 60 seconds?", f"About {ref['acceleration_plot_final_speed_kmh']:.1f} km/h.", ref["acceleration_plot_final_speed_kmh"], "km/h", ["60"]),
        ("When does the acceleration simulation settle near top speed?", f"At about {ref['acceleration_plot_settled_at_s']:.1f} s under its 0.01 m/s² threshold.", ref["acceleration_plot_settled_at_s"], "s", ["settle"]),
        ("What target speed is configured for the acceleration run?", "60 km/h.", 60, "km/h", ["target"]),
        ("How long is the configured acceleration simulation?", "60 seconds.", 60, "s", ["simulation"]),
    ]:
        add("acceleration_direct", question, answer, facts, value=value, unit=unit,
            tolerance=max(abs(value)*0.02, 0.03), source="application", location=A)

    rules = [
        ("Why does acceleration fall after the motor's base speed?", "Above base speed, the theoretical motor curve holds power constant and torque falls as P/ω; available wheel force falls.", ["power", "torque", "speed"], P),
        ("How does the app calculate road load on a flat road?", "It adds rolling force m g Crr and aerodynamic drag 0.5 ρ CdA v².", ["rolling", "drag"], "vmi/parametric.py:_compute_resistive_force"),
        ("Why can a higher CdA lower top speed?", "A larger CdA raises drag quadratically with speed, so force balance occurs sooner.", ["drag", "speed"], P),
        ("Why does wheel inertia change the acceleration time?", "It adds J/r² to inertial mass, reducing acceleration for the same net force.", ["inertia", "mass"], "vmi/calc_ext.py:effective_mass"),
        ("Does wheel inertia change steady flat-road top speed in this model?", "No. It changes the acceleration inertial term but not steady force balance.", ["no", "acceleration"], "vmi/calc_ext.py:effective_mass"),
        ("How does a smaller wheel radius affect launch force?", "For fixed wheel torque, force rises because F = T/r; it also raises motor RPM at a given road speed.", ["force", "rpm"], P),
        ("What force balance defines the app's estimated top speed?", "The first speed where available wheel force falls to road-load force.", ["force", "road"], "vmi/parametric.py:_estimate_top_speed"),
        ("What does the app mean by maximum startable gradient?", "The highest searched grade with any speed at which peak wheel force meets resistance. It is not a thermal or traction guarantee.", ["grade", "peak"], "vmi/parametric.py:_estimate_max_gradability"),
        ("Why can more peak torque improve launch but not high-speed acceleration?", "Peak torque raises the low-speed region; above base speed the fixed power limit sets torque.", ["torque", "power"], P),
        ("Why may higher peak power improve high-speed acceleration?", "Above base speed, more available shaft power yields more torque and wheel force at a given RPM.", ["power", "force"], P),
        ("Does the Acceleration view integrate distance versus time?", "No. It plots speed versus time and calculates target time and final speed; distance is not integrated in this view.", ["no", "speed", "time"], A),
        ("Is the current top-speed estimate a measured vehicle road test?", "No. It is a model estimate from the configured capability curve and road resistance.", ["estimate", "force"], S),
    ]
    for q, answer, facts, loc in rules:
        add("engineering_explanation", q, answer, facts, source="implemented_rule", location=loc,
            difficulty="medium", reasoning=True)

    motor = [
        ("How is the theoretical motor torque-speed envelope built?", "Peak torque is flat up to P/T base speed, then torque is P/ω.", ["peak torque", "power"], "vmi/torque_force.py:plot_torque_graph"),
        ("How is the continuous motor curve limited?", "It is the smaller of peak torque divided by the peak-to-rated ratio and continuous power divided by ω.", ["ratio", "continuous power"], "vmi/torque_force.py:plot_torque_graph"),
        ("What happens to the acceleration calculation when a motor torque-speed file is uploaded?", "The uploaded RPM–torque curve replaces the theoretical envelope, with interpolation of torque at motor RPM.", ["uploaded", "interpolat"], "vmi/torque_force.py:_accel_wheel_force_fn"),
        ("What is the motor RPM at 60 km/h for this gearing and wheel radius?", "Approximately 4547 rpm from wheel speed times the 8:1 ratio.", ["rpm", "gear"], "vmi/torque_force.py:_accel_wheel_force_fn"),
        ("Does the Powertrain Sizing tab implement a motor maximum-RPM cutoff?", "No explicit maximum-RPM input or cutoff is applied in this tab.", ["no", "rpm"], "vmi/torque_force.py:plot_torque_graph"),
        ("What is the purpose of peak-to-rated torque ratio?", "It sets the continuous torque plateau as peak torque divided by that ratio.", ["continuous", "ratio"], "vmi/torque_force.py:plot_torque_graph"),
        ("Which motor data columns does the upload expect?", "A motor speed and motor torque column; the app normalizes them as motor_speed and motor_torque.", ["speed", "torque"], "vmi/data_io.py:load_motor_data_excel"),
    ]
    for q, answer, facts, loc in motor:
        rpm_value = (60 / 3.6 / ref["inputs"]["wheel_radius"] * 60
                     / (2 * 3.141592653589793) * ref["inputs"]["gear_ratio"]
                     if "motor RPM at 60" in q else None)
        add("motor", q, answer, facts, value=rpm_value,
            unit="rpm" if rpm_value is not None else None,
            tolerance=round(rpm_value * 0.02, 3) if rpm_value is not None else None,
            source="implemented_rule", location=loc,
            difficulty="medium", reasoning=True)

    controller = [
        ("How is a configured battery DC current cap applied to motor torque?", "The shaft-power ceiling is voltage times DC current limit times motor/controller efficiency; torque is clipped at each RPM.", ["voltage", "current", "efficien"], "vmi/calc_ext.py:battery_power_cap_w; vmi/ui_helpers.py:cap_torque_to_battery"),
        ("Which efficiency data does the battery cap use when maps are loaded?", "It uses the motor and controller efficiency maps at the operating point; the constant fallback applies without maps.", ["motor", "controller", "map"], "vmi/ui_helpers.py:_battery_eta_fn"),
        ("Is the controller phase-current limit independently configured in Powertrain Sizing?", "No. The tab has an optional battery DC current limit, not a separate controller phase-current limit.", ["no", "DC", "phase"], "vmi/app.py:Motor Performance Parameters"),
        ("Can the optional battery DC power cap affect the calculated top speed?", "Yes, if it clips available shaft torque/power near the force crossing.", ["yes", "cap"], "vmi/parametric.py:_compute_available_wheel_force"),
        ("Is thermal derating of the controller simulated in the Acceleration plot?", "No controller thermal derating curve is applied there.", ["no", "thermal"], "vmi/torque_force.py:_accel_wheel_force_fn"),
        ("Can the app establish whether this controller's phase current is safe from the shown inputs?", "No. It needs phase-current and controller ratings/test evidence; the optional DC current field alone is insufficient.", ["no", "phase", "rating"], "vmi/app.py:Motor Performance Parameters"),
    ]
    for q, answer, facts, loc in controller:
        add("motor_controller", q, answer, facts, source="implemented_rule", location=loc,
            difficulty="medium", reasoning=True)

    cross = [
        ("If I raise the reduction ratio, what should happen to launch force and motor RPM at a fixed road speed?", "Launch force and motor RPM both rise, until another cap changes the result.", ["force", "rpm", "rise"], P),
        ("Why might a higher reduction ratio improve gradeability but hurt high-speed performance?", "It multiplies low-speed wheel torque but moves the motor farther along its falling-torque/high-RPM envelope at a given vehicle speed.", ["wheel torque", "rpm", "high"], P),
        ("If I increase mass, which of acceleration and steady top speed are affected?", "Acceleration worsens because inertial mass and rolling/grade load rise; flat-road top speed can also fall because rolling resistance rises.", ["acceleration", "rolling", "top speed"], P),
        ("If CdA falls while motor power stays fixed, which part of the run benefits most?", "Higher speeds benefit most because aerodynamic drag grows with v².", ["high", "drag"], P),
        ("Could raising peak torque leave 0–60 nearly unchanged if peak power remains fixed?", "Yes. Raising peak torque moves base speed lower; power-limited portions of 0–60 may stay similar.", ["yes", "base", "power"], P),
        ("Why can an uploaded curve change both acceleration and top-speed estimates?", "Both use available wheel force derived from the uploaded RPM–torque curve, compared with road load.", ["curve", "force", "road"], P),
    ]
    for q, answer, facts, loc in cross:
        add("cross_analysis", q, answer, facts, source="implemented_rule", location=loc,
            difficulty="hard", reasoning=True, role="reasoning")

    docs = [
        ("According to the motor-testing document, how is winding resistance measured?", "Four-wire Kelvin measurement at controlled temperature, corrected to a reference temperature.", ["Kelvin", "temperature"], TEST_DOC),
        ("What does the testing document say to measure for a torque-speed curve?", "A dynamometer sweep across speed and torque points under controlled temperature.", ["dynamometer", "temperature"], TEST_DOC),
        ("How is the motor efficiency map tested in the motor-testing document?", "Grid testing over speed and torque, with motoring and regeneration if supported.", ["speed", "torque", "regen"], TEST_DOC),
        ("In the motor-testing document, which test confirms peak power and continuous power?", "Rated thermal soak followed by peak-current or overload windows per specification.", ["thermal", "peak", "specification"], TEST_DOC),
        ("What does the motor-testing document prescribe for temperature rise at rated duty?", "Thermal soak on a dynamometer until steady state, measuring winding, magnet, bearing and housing temperatures.", ["thermal soak", "steady", "temperature"], TEST_DOC),
        ("What is measured in the motor-testing document's field-weakening or overspeed-region test?", "Current, voltage and torque at high speed.", ["current", "voltage", "torque"], TEST_DOC),
        ("How should the testing plan validate controller over-current protection?", "Controlled fault injection and measurement of protection reaction timing.", ["fault", "timing"], TEST_DOC),
        ("How does the motor-testing document say to check controller over-voltage and under-voltage behavior?", "Voltage sweep and fault logging.", ["voltage sweep", "fault"], TEST_DOC),
        ("What are the hub-motor-specific test priorities in section 5.1?", "IP, salt spray, mud splash, wheel-end shock, bearing endurance, tyre/rim interface and wheel balance.", ["IP", "salt", "bearing", "wheel"], TEST_DOC),
        ("What additional test emphasis does section 5.2 of the motor-testing document give a mid-mount motor?", "Gearbox efficiency, lubricant ageing, backlash, coupling fatigue, controller packaging, ducting and frame vibration.", ["gearbox", "coupling", "vibration"], TEST_DOC),
        ("Which reference covers motor ingress protection in the testing document?", "IS/IEC 60529.", ["60529"], TEST_DOC),
        ("Which India reference in the motor-testing document addresses EMC for the motor controller and harness?", "AIS-004 (Part 3).", ["AIS-004", "Part 3"], TEST_DOC),
        ("Which standard family listed in the motor-testing document covers rotating-machine ratings and performance?", "IS/IEC 60034.", ["60034"], TEST_DOC),
        ("What test detects rotor rub, bearing drag or winding anomalies at end of line?", "A no-load current screen at fixed speed against limits.", ["no-load current", "fixed speed"], TEST_DOC),
        ("What does the motor-testing document's recommended test-record schema require for inputs and measurements?", "Inputs include voltage, current, speed, torque, temperature and load profile; measurements record what was observed.", ["voltage", "current", "speed", "torque"], TEST_DOC),
        ("Compare the motor-testing document's torque-speed and efficiency-map test setups.", "Both use speed and torque points; the torque-speed curve is a dynamometer sweep, while the efficiency map is a grid with input/output power and possible regeneration.", ["dynamometer", "grid", "power"], TEST_DOC),
        ("Which mechanical and environmental tests are especially important for a hub motor?", "Wheel hub durability and bearing endurance, plus ingress, salt-spray and mud exposure.", ["bearing", "ingress", "salt"], TEST_DOC),
        ("What is the highest torque in the U546 torque-speed file?", "120 N m, maintained from 0 through 200 rpm in the file.", ["120", "200"], CURVE_DOC),
        ("At what RPM is U546 power highest in its torque-speed file?", "2513.274 W at 200 rpm.", ["200", "2513"], CURVE_DOC),
        ("What U546 torque is listed at 900 rpm?", "26.5258 N m.", ["900", "26.5"], CURVE_DOC),
        ("What is the U546 efficiency-map maximum and its location?", "0.8966 (89.66%) at 35 N m and 500 rpm.", ["89.66", "35", "500"], EFF_DOC),
    ]
    for q, answer, facts, loc in docs:
        value = unit = tol = None
        if "highest torque" in q: value, unit, tol = 120, "N m", 0.1
        elif "power highest" in q: value, unit, tol = 200, "rpm", 1.0
        elif "900 rpm" in q: value, unit, tol = 26.52582385, "N m", 0.2
        elif "efficiency-map maximum" in q: value, unit, tol = 89.66, "%", 0.1
        add("rag_document", q, answer, facts, value=value, unit=unit, tolerance=tol,
            source="document", location=loc, difficulty="hard" if "Compare" in q or "Which mechanical" in q else "medium",
            reasoning="Compare" in q or "Which mechanical" in q)

    missing = [
        ("What is this vehicle's certified maximum speed?", "No certification or road-test result is available; the app only estimates speed."),
        ("What is the motor manufacturer's maximum safe RPM for this configuration?", "No manufacturer maximum-RPM rating is loaded for the reference vehicle."),
        ("At what temperature will this controller derate?", "No controller thermal derating threshold is provided."),
        ("What is the controller's maximum phase current?", "The current vehicle state only has an optional battery DC current limit, not a controller phase-current rating."),
        ("How many metres does this vehicle cover during the 0–60 run?", "The current acceleration output does not integrate distance; a verified distance result is unavailable."),
        ("What exact IP67 pass criterion does the motor-testing document state for this motor?", "The document lists ingress testing and IS/IEC 60529 but gives no product-specific IP67 acceptance criterion."),
        ("Has the U546 motor passed the document's overspeed test?", "The indexed data has no U546 overspeed test result."),
        ("Does this vehicle meet AIS-156 approval?", "Approval cannot be determined from the available app inputs and test catalogue; program-specific evidence is needed."),
    ]
    for q, answer in missing:
        add("unavailable", q, answer, ["not|no|cannot|unavailable|insufficient"],
            source="unavailable", location=TEST_DOC if "document" in q or "AIS" in q else S,
            unavailable=True, forbidden=["certified", "passed", "compliant"], difficulty="medium")

    for q, answer in [
        ("Why is performance low?", "Ask which result or operating point is low, then inspect the current top-speed, grade or acceleration outputs."),
        ("Is the motor limiting it?", "Ask what 'it' refers to, or use the preceding turn; current motor/controller limits are insufficient for a definitive component diagnosis."),
        ("Should I increase it?", "Clarify which parameter, then explain the expected tradeoff rather than assuming a change."),
    ]:
        add("ambiguous", q, answer, ["which|what|clarif|mean|result|parameter"], source="unavailable",
            unavailable=True, difficulty="medium")

    turns = [
        ("What's my top speed?", f"About {top:.1f} km/h.", ["74.6"], []),
        ("Why is it that low?", "Road load meets the current peak wheel-force curve around 74.6 km/h; CdA, power and gearing all warrant review.", ["road", "force"], [{"role":"user","content":"What's my top speed?"},{"role":"assistant","content":f"About {top:.1f} km/h."}]),
        ("What if I change the gearing?", "A higher reduction ratio raises low-speed wheel force but raises motor RPM at a given road speed; rerun for a quantitative change.", ["gear", "rpm"], [{"role":"user","content":"What's my top speed?"},{"role":"assistant","content":f"About {top:.1f} km/h."}]),
        ("And what happens to acceleration?", "Higher reduction can improve launch acceleration; the high-speed effect depends on the power-limited envelope.", ["acceleration", "launch"], [{"role":"user","content":"What if I raise the gear ratio?"},{"role":"assistant","content":"It raises low-speed wheel force and motor RPM."}]),
        ("What about the zero-to-sixty time?", f"The current app estimate is about {accel:.1f} seconds; a new gearing value requires recalculation.", ["60", "13"], [{"role":"user","content":"What's my top speed?"},{"role":"assistant","content":f"About {top:.1f} km/h."}]),
    ]
    for q, answer, facts, history in turns:
        add("conversation", q, answer, facts, source="application", location=S,
            history=history, difficulty="medium", role="fast")

    return rows, ref


if __name__ == "__main__":
    rows, ref = build()
    ROOT.mkdir(exist_ok=True)
    (ROOT / "questions.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    (ROOT / "ground_truth.json").write_text(json.dumps({"reference_vehicle": ref,
        "question_ground_truth": {r["question_id"]: {k: v for k, v in r.items()
          if k not in ("question", "history", "scenario", "model_role")}
          for r in rows}}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(rows)} source-backed questions")
